#!/usr/bin/env python3
"""Create a new Render web service for a Telegram bot repo, provision it a
dedicated Upstash Redis database, wire up its secrets, and (optionally) an
UptimeRobot monitor for its /health endpoint.

Defaults to a DRY RUN: prints exactly what it would send, with every secret
masked, and makes zero API calls. Pass --yes to actually execute. This is
the mechanical half of "confirm before calling a real API with secrets in
the payload" -- the agent invoking this script still owes the user a plain-
language confirmation first; this flag is the second gate, not a
replacement for asking.

Any of the env vars below can also come from a `.env` file in the current
directory (KEY=VALUE per line) instead of being exported in the shell --
loaded as a fallback only, so an already-exported value always wins. Point
this at the target bot repo's own .env (if it has RENDER_API_KEY etc.
already) or wherever you keep those account-level secrets.

Required env vars: RENDER_API_KEY, BOT_TOKEN, OWNER_IDS, REPO_URL,
SERVICE_NAME
Optional env vars:
  CHAT_ADMINS, UPTIMEROBOT_API_KEY, BRANCH (default main)
  OWNER_ID_OVERRIDE  -- Render workspace/owner id, only needed if the
                        account has zero existing services to copy it from
  UPSTASH_API_KEY, UPSTASH_EMAIL -- account-level Upstash Developer API
                        credentials (Upstash Console -> Account -> API
                        Keys), used to provision a NEW dedicated database
                        for this bot. Different from UPSTASH_REDIS_REST_URL/
                        TOKEN, which are per-database runtime credentials --
                        this script creates those for you and wires them
                        into the new service's env vars. Skipped entirely
                        (bot runs in-memory only) if UPSTASH_API_KEY isn't set.
  UPSTASH_REGION (default us-east-1), UPSTASH_PLATFORM (default aws)

Usage:
    python render_deploy.py            # dry run, prints the plan
    python render_deploy.py --yes      # actually creates everything
"""
import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx

RENDER_API = "https://api.render.com/v1"
UPTIMEROBOT_API = "https://api.uptimerobot.com/v2"
UPSTASH_API = "https://api.upstash.com/v2"

REQUIRED = ["RENDER_API_KEY", "BOT_TOKEN", "OWNER_IDS", "REPO_URL", "SERVICE_NAME"]


def load_dotenv_fallback(path: Path = Path(".env")) -> None:
    """Fill in os.environ from a .env file for keys not already set --
    shell-exported values always win. Pure convenience so these don't need
    re-exporting every run; silently does nothing if the file is absent."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def mask(value: str) -> str:
    """Show enough to eyeball "did I paste the right thing", nothing more."""
    if not value:
        return "(not set)"
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}...{value[-4:]} ({len(value)} chars)"


@dataclass
class Config:
    render_key: str
    bot_token: str
    owner_ids: str
    repo_url: str
    service_name: str
    chat_admins: str = ""
    uptimerobot_key: str = ""
    branch: str = "main"
    owner_id_override: str = ""
    upstash_api_key: str = ""
    upstash_email: str = ""
    upstash_region: str = "us-east-1"
    upstash_platform: str = "aws"
    extra_env: list = field(default_factory=list)


def load_config() -> Config | None:
    load_dotenv_fallback()
    missing = [v for v in REQUIRED if not os.environ.get(v)]
    if missing:
        print(f"Missing required env var(s): {', '.join(missing)}", file=sys.stderr)
        return None
    extra_env = json.loads(os.environ["EXTRA_ENV_JSON"]) if os.environ.get("EXTRA_ENV_JSON") else []
    return Config(
        render_key=os.environ["RENDER_API_KEY"],
        bot_token=os.environ["BOT_TOKEN"],
        owner_ids=os.environ["OWNER_IDS"],
        repo_url=os.environ["REPO_URL"],
        service_name=os.environ["SERVICE_NAME"],
        chat_admins=os.environ.get("CHAT_ADMINS", ""),
        uptimerobot_key=os.environ.get("UPTIMEROBOT_API_KEY", ""),
        branch=os.environ.get("BRANCH", "main"),
        owner_id_override=os.environ.get("OWNER_ID_OVERRIDE", ""),
        upstash_api_key=os.environ.get("UPSTASH_API_KEY", ""),
        upstash_email=os.environ.get("UPSTASH_EMAIL", ""),
        upstash_region=os.environ.get("UPSTASH_REGION", "us-east-1"),
        upstash_platform=os.environ.get("UPSTASH_PLATFORM", "aws"),
        extra_env=extra_env,
    )


def print_plan(cfg: Config, dry_run: bool) -> None:
    mode = "DRY RUN - no API calls will be made" if dry_run else "LIVE - will call Render + Upstash + UptimeRobot APIs"
    print("== Plan ==========================================================")
    print(f"Service name:        {cfg.service_name}")
    print(f"Repo:                 {cfg.repo_url} (branch: {cfg.branch})")
    print(f"RENDER_API_KEY:       {mask(cfg.render_key)}")
    print(f"BOT_TOKEN:            {mask(cfg.bot_token)}")
    print(f"OWNER_IDS:            {cfg.owner_ids}")
    print(f"CHAT_ADMINS:          {cfg.chat_admins or '(not set)'}")
    print(f"UPSTASH_API_KEY:      {mask(cfg.upstash_api_key)}")
    print(f"UPSTASH_EMAIL:        {cfg.upstash_email or '(not set)'}")
    print(f"UPTIMEROBOT_API_KEY:  {mask(cfg.uptimerobot_key)}")
    print(f"Extra env vars:       {[e['key'] for e in cfg.extra_env] or '(none)'}")
    print(f"Mode:                 {mode}")
    print("===================================================================\n")


def lookup_owner_and_plan(cfg: Config, dry_run: bool) -> tuple[str, str] | None:
    """Render's plan naming has shifted before (see SKILL.md); rather than
    trust a hardcoded guess, copy the ownerId/plan a service already running
    on this account actually uses."""
    print("-> Looking up an existing service to copy ownerId/plan from...")
    if dry_run:
        print(f"   (dry run) GET {RENDER_API}/services?limit=20")
        return cfg.owner_id_override or "<owner-id-from-account>", "free"

    headers = {"Authorization": f"Bearer {cfg.render_key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=15) as client:
        resp = client.get(f"{RENDER_API}/services", headers=headers, params={"limit": 20})
        resp.raise_for_status()
        services = resp.json()

    owner_id, plan = cfg.owner_id_override, "free"
    if services:
        svc = services[0]["service"]
        owner_id = owner_id or svc.get("ownerId", "")
        plan = svc.get("serviceDetails", {}).get("plan", "free")
    if not owner_id:
        print(
            "No existing services on this account, and OWNER_ID_OVERRIDE not set.\n"
            "Find your workspace's owner id at https://dashboard.render.com "
            "(Account Settings), set OWNER_ID_OVERRIDE, and re-run.",
            file=sys.stderr,
        )
        return None
    print(f"   Using ownerId={owner_id} plan={plan}")
    return owner_id, plan


def create_upstash_database(cfg: Config, dry_run: bool) -> tuple[str, str] | None:
    """Provision a dedicated free-tier Redis database for the new bot via
    Upstash's account-level Developer API -- distinct from the per-database
    REST URL/token the bot uses at runtime, which this call produces.
    Returns (rest_url, rest_token), or None if skipped/unavailable."""
    if not cfg.upstash_api_key:
        print("\n-> Skipping Upstash database (UPSTASH_API_KEY not set) -- new bot will run in-memory only.")
        return None
    if not cfg.upstash_email:
        print("\nUPSTASH_API_KEY is set but UPSTASH_EMAIL is missing -- Upstash's API needs both "
              "(Basic auth as email:api_key). Skipping database creation.", file=sys.stderr)
        return None

    print(f"\n-> Creating Upstash Redis database '{cfg.service_name}'...")
    payload = {
        "database_name": cfg.service_name,
        "platform": cfg.upstash_platform,
        "primary_region": cfg.upstash_region,
    }
    if dry_run:
        print(f"   (dry run) POST {UPSTASH_API}/redis/database  (Basic auth: {cfg.upstash_email})")
        print(json.dumps(payload, indent=2))
        return "https://<new-db>.upstash.io", mask(cfg.upstash_api_key)

    with httpx.Client(timeout=30, auth=(cfg.upstash_email, cfg.upstash_api_key)) as client:
        resp = client.post(f"{UPSTASH_API}/redis/database", json=payload)
        resp.raise_for_status()
        db = resp.json()

    endpoint = db.get("endpoint", "")
    rest_token = db.get("rest_token")
    if not endpoint or not rest_token:
        # Upstash's response shape isn't pinned down as confidently as
        # Render's -- see the SKILL.md caveat. Fail loud with the raw
        # response rather than silently skipping Redis persistence.
        print(
            "   Database was created but the response didn't include the "
            "expected 'endpoint'/'rest_token' fields (Upstash's API may have "
            "changed since this script was written). Grab the REST URL/token "
            "for this database from the Upstash Console instead. Raw response:",
            file=sys.stderr,
        )
        print(f"   {db}", file=sys.stderr)
        return None

    rest_url = f"https://{endpoint}"
    print(f"   Created: {db.get('database_id', '?')} -> {rest_url}")
    return rest_url, rest_token


def build_create_payload(cfg: Config, owner_id: str, plan: str) -> dict:
    env_vars = [
        {"key": "BOT_TOKEN", "value": cfg.bot_token},
        {"key": "OWNER_IDS", "value": cfg.owner_ids},
    ]
    if cfg.chat_admins:
        env_vars.append({"key": "CHAT_ADMINS", "value": cfg.chat_admins})
    env_vars.extend(cfg.extra_env)

    return {
        "type": "web_service",
        "name": cfg.service_name,
        "ownerId": owner_id,
        "repo": cfg.repo_url,
        "branch": cfg.branch,
        "autoDeploy": "yes",
        "envVars": env_vars,
        "serviceDetails": {
            "runtime": "docker",
            "plan": plan,
            "envSpecificDetails": {"dockerfilePath": "./Dockerfile", "dockerContext": "."},
        },
    }


def create_service(cfg: Config, payload: dict, dry_run: bool) -> str:
    """Returns the new service's URL."""
    print("\n-> Creating web service...")
    fallback_url = f"https://{cfg.service_name}.onrender.com"
    if dry_run:
        print(f"   (dry run) POST {RENDER_API}/services")
        masked_payload = json.loads(json.dumps(payload))
        for ev in masked_payload["envVars"]:
            ev["value"] = mask(ev["value"])
        print(json.dumps(masked_payload, indent=2))
        return fallback_url

    headers = {"Authorization": f"Bearer {cfg.render_key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=30) as client:
        resp = client.post(f"{RENDER_API}/services", headers=headers, json=payload)
        resp.raise_for_status()
        created = resp.json()
    service_id = created["service"]["id"]
    service_url = created["service"].get("serviceDetails", {}).get("url") or fallback_url
    print(f"   Created: {service_id} -> {service_url}")
    return service_url


def create_uptimerobot_monitor(cfg: Config, service_url: str, dry_run: bool) -> None:
    if not cfg.uptimerobot_key:
        print("\n-> Skipping UptimeRobot monitor (UPTIMEROBOT_API_KEY not set).")
        return

    print(f"\n-> Creating UptimeRobot monitor for {service_url}/health...")
    monitor_data = {
        "api_key": cfg.uptimerobot_key,
        "format": "json",
        "type": "1",
        "url": f"{service_url}/health",
        "friendly_name": cfg.service_name,
        "interval": "300",
    }
    if dry_run:
        print(f"   (dry run) POST {UPTIMEROBOT_API}/newMonitor")
        print(json.dumps(dict(monitor_data, api_key=mask(cfg.uptimerobot_key)), indent=2))
        return

    with httpx.Client(timeout=15) as client:
        resp = client.post(f"{UPTIMEROBOT_API}/newMonitor", data=monitor_data)
        resp.raise_for_status()
    print(f"   {resp.json()}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="Actually call the APIs (default: dry run)")
    args = parser.parse_args()
    dry_run = not args.yes

    cfg = load_config()
    if cfg is None:
        return 1
    print_plan(cfg, dry_run)

    owner_and_plan = lookup_owner_and_plan(cfg, dry_run)
    if owner_and_plan is None:
        return 1
    owner_id, plan = owner_and_plan

    upstash = create_upstash_database(cfg, dry_run)
    if upstash:
        rest_url, rest_token = upstash
        cfg.extra_env.append({"key": "UPSTASH_REDIS_REST_URL", "value": rest_url})
        cfg.extra_env.append({"key": "UPSTASH_REDIS_REST_TOKEN", "value": rest_token})

    payload = build_create_payload(cfg, owner_id, plan)
    service_url = create_service(cfg, payload, dry_run)
    create_uptimerobot_monitor(cfg, service_url, dry_run)

    print("\n== Done ===========================================================")
    print(f"Service URL: {service_url}")
    if dry_run:
        print("This was a DRY RUN. Re-run with --yes to actually create/deploy.")
    print("===================================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
