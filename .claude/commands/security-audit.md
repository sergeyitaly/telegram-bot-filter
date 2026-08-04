You are a security engineer reviewing recent changes to the Air-Alarm Content Filter Bot — a Ukrainian wartime Telegram moderation bot whose security directly affects civilian safety.

Run a targeted red-team audit focused on what changed since the last audit (2026-08-05). Do the following:

1. **Read recent commits**: `git log --oneline -20` to see what changed.
2. **Read the changed files**: focus on `bot/filters.py`, `bot/keywords.py`, `bot/handlers.py`, `main.py`.
3. **Check the known attack surface** in `.claude/skills/telegram-bot-filter/SKILL.md` — are all previously fixed gaps still fixed?
4. **Hunt for new gaps**: look for any new message type that has no handler, any new text path that skips `_normalize()`, any new admin command that bypasses `_admins_for()`, any new media handler that doesn't follow delete-before-repost order.
5. **Check PTB filter coverage**: list every `filters.*` type registered in `main.py` and identify any Telegram message type that has no handler.
6. **Check coordinate detection**: is the regex still correct? Are DMS, Plus Code, and map URL patterns intact?
7. **Check normalization**: is `_normalize()` still called before every keyword/coordinate match?

Report format:
- **NEW GAPS FOUND**: step-by-step attack path + severity + proposed fix for each
- **CONFIRMED FIXED**: list of previously documented gaps that are still properly closed
- **RECOMMENDATIONS**: any other improvements worth making

After the report, update `.claude/skills/telegram-bot-filter/SKILL.md` attack surface table with any new findings, commit and push.
