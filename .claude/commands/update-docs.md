Synchronize all agent documentation files after changes to the bot. Run this command whenever you make significant changes to ensure future sessions start with accurate context.

Do the following in order:

## 1. Summarize what changed

Run `git log --oneline origin/main..HEAD` (or `git log --oneline -10` if already pushed) and read the diff of changed files to understand what was modified.

## 2. Update CLAUDE.md

Read `CLAUDE.md`. Update these sections if they are out of date:
- **Key design decisions** — add any new architectural decisions
- **File map** — add any new files
- **Common development tasks** — add recipes for new features
- **Environment variables** — add any new vars from `bot/config.py`
- **MCP servers** section if any new MCP was added

Do NOT rewrite the whole file — make targeted edits to the sections that changed.

## 3. Update the project skill

Read `.claude/skills/telegram-bot-filter/SKILL.md`. Update:
- **Architecture quick reference** table if any file roles changed
- **Known attack surfaces** table — mark newly fixed gaps, add any newly discovered ones
- **Common tasks** section if new recipes are needed
- **Security checklist** if new rules apply

## 4. Update README.md

Read `README.md`. Update:
- The opening feature list if new message types are handled
- The **Commands** table if `/removeadmin` or other commands changed
- The **Tuning the filter** section for any new detection capabilities
- The **Auto-arming** section if poll interval or behavior changed

Do NOT rewrite existing sections that are still accurate.

## 5. Commit and push

Stage and commit the documentation files:
```
git add CLAUDE.md README.md .claude/skills/telegram-bot-filter/SKILL.md
git commit -m "docs: sync agent documentation after <brief description of changes>"
git push
```

## Why this matters

CLAUDE.md and SKILL.md are loaded at the start of every session. If they're stale, future sessions make decisions based on wrong architecture assumptions. The README is what human operators read. All three should reflect the current state of the code, not the state it was in when the files were last written.
