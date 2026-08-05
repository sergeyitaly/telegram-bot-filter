Pre-push smoke test — run before every push to `main`, bundling the checks
that have caught real production crashes in this repo before (an
`asyncio`/`post_init` event-loop bug, a `ChatPermissions` constructor
mismatch) so they don't need to be strung together by hand each time.

Run these in order, in the project's `.venv`, and report the actual
pass/fail result of each — do not claim success without running them:

## 1. Compile-check everything

```bash
python -m py_compile bot/*.py main.py
```

## 2. Full test suite

```bash
pip install -r requirements-dev.txt  # only needed once per venv
pytest tests/ -v
```

## 3. Application build + startup regression test

Confirms `build_application()` wires all handlers without error and that
the process reaches a real Telegram API call on startup (catches
event-loop/`post_init` regressions specifically — this is the exact
pattern that caught the original crash):

```bash
BOT_TOKEN="123456:test-dummy-token-not-real" CHAT_ADMINS="-100111:11,22" python -c "
from main import build_application
app = build_application()
print('build_application() OK -', len(app.handlers[0]), 'handlers in group 0')
"
```

A dummy token failing with `InvalidToken`/`401 Unauthorized` on a live API
call is the CORRECT and expected result here — it proves the process reached
a real network call, not that something is broken. A `RuntimeError` about
the event loop, or any traceback before reaching the network call, is the
actual failure mode this step exists to catch.

## 4. Report

Summarize pass/fail for each of the three steps. If any step fails, do not
proceed to commit/push — fix the failure first. If all three pass, note
that `git push` will additionally run the full suite again via the
`pre-push` git hook (`.githooks/pre-push`, enabled via
`git config core.hooksPath .githooks`) as a final backstop.
