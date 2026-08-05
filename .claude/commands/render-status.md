Check this bot's live deployment status on Render without the user having
to paste dashboard screenshots or logs.

## 1. Confirm the `render` MCP is connected

Check the available tools list for anything namespaced like
`mcp__render__*`. If nothing is there, the MCP server isn't connected —
tell the user to confirm `RENDER_API_KEY` is set (shell env or this repo's
`.env`) and restart the Claude Code session, since MCP servers only load at
startup (see `CLAUDE.md`'s MCP servers section). Don't try to work around a
missing connection by guessing at Render's REST API with raw `curl` unless
the user explicitly asks for that instead.

## 2. Find the right service

List services via the MCP's list/get-services tool and match the one whose
name/repo corresponds to this project (`telegram-bot-filter` /
`sergeyitaly/telegram-bot-filter`). If more than one plausible match comes
back, ask the user which one rather than guessing — this deployment
mechanism (`render-bot-quick-deploy`) is explicitly designed to spin up
*other* bots too, so "the Render service" is not guaranteed to be unique.

## 3. Report status

Using the MCP's deploy-status/logs tools, report:
- Current deploy status (live, building, failed) and when it last deployed
- The most recent handful of log lines, especially anything at ERROR/WARN
  level or matching a Python traceback
- Whether the service is on a plan that sleeps after 15 min idle (relevant
  context if `UPTIMEROBOT_MONITOR_ID` isn't configured for it)

If the MCP tools don't expose something needed (e.g. historical deploy
list beyond the latest), say so plainly rather than fabricating it, and
point the user to `https://dashboard.render.com` for anything not
available through the tool surface.
