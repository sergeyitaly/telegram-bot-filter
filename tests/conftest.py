import os

# bot/config.py reads BOT_TOKEN at import time, before any test fixture runs.
os.environ.setdefault("BOT_TOKEN", "123456:test-dummy-token-not-real")
