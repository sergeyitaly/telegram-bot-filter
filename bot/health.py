"""Minimal HTTP server so UptimeRobot/cron-job.org has something to ping.

Runs on its own thread + event loop, independent of the PTB polling loop,
so a slow Telegram API call never blocks the health check (and vice versa).
"""
import asyncio
import logging
import threading

from aiohttp import web

log = logging.getLogger(__name__)


async def _health(_request: web.Request) -> web.Response:
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
