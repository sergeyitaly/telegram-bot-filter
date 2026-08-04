"""Minimal HTTP server so UptimeRobot/cron-job.org has something to ping.

Runs on its own thread + event loop, independent of the PTB polling loop,
so a slow Telegram API call never blocks the health check (and vice versa).

This is the bot's only public HTTP surface (the Telegram side is outbound
long-polling, nothing inbound to defend there), so it gets a basic per-IP
rate limit. This is NOT real DDoS protection — a genuine distributed flood
exhausts bandwidth/connections before a request ever reaches this code, and
no application-level check can do anything about that. It only blocks a
single-source scripted flood from pegging CPU on repeated health checks.
Real DDoS mitigation happens at the network edge (e.g. Cloudflare in front
of Render), not here.
"""
import asyncio
import logging
import threading
import time

from aiohttp import web

log = logging.getLogger(__name__)

RATE_LIMIT_MAX_REQUESTS = 30
RATE_LIMIT_WINDOW_SECONDS = 10

# ip -> recent request timestamps (monotonic), pruned to the trailing window.
_hits: dict[str, list[float]] = {}


def _client_ip(request: web.Request) -> str:
    # Render sits behind a proxy; the real client IP is in this header.
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote or "unknown"


# Caps the tracking dict itself from becoming a memory-exhaustion vector
# under a distributed flood (many source IPs) — a crude but bounded backstop.
_MAX_TRACKED_IPS = 5000


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    recent = [t for t in _hits.get(ip, []) if now - t < RATE_LIMIT_WINDOW_SECONDS]
    recent.append(now)
    _hits[ip] = recent

    if len(_hits) > _MAX_TRACKED_IPS:
        stale = [k for k, v in _hits.items() if now - v[-1] >= RATE_LIMIT_WINDOW_SECONDS]
        for k in stale:
            del _hits[k]
        if len(_hits) > _MAX_TRACKED_IPS:
            _hits.clear()  # still over cap under sustained distributed load; reset rather than leak

    return len(recent) > RATE_LIMIT_MAX_REQUESTS


async def _health(request: web.Request) -> web.Response:
    if _rate_limited(_client_ip(request)):
        return web.Response(status=429, text="rate limited")
    return web.Response(text="ok")


async def _run(port: int) -> None:
    app = web.Application()
    app.router.add_get("/", _health)
    app.router.add_get("/health", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("health server listening on :%s", port)
    await asyncio.Event().wait()  # run forever


def start_in_background(port: int) -> None:
    def _target() -> None:
        asyncio.run(_run(port))

    thread = threading.Thread(target=_target, daemon=True, name="health-server")
    thread.start()
