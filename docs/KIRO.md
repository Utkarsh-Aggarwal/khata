# How Kiro was used

Every file in this repo was written in Kiro agent sessions. These are the Kiro features the project used, and where to find them.

## Spec
`.kiro/specs/khata/` holds the requirements (EARS-style, R1–R10), the design, and the task list. Tasks 1–11 are done; 12–15 need the owner's accounts and are left open rather than marked done.

## Steering
`.kiro/steering/khata.md` holds the project rules the agent followed in every session: integer paise, owner isolation, the confirm step, untrusted bill text, free services only, and a rule never to tune the extractor on the blind eval set. Several bugs were caught by checking code against these rules. For example, a stale unsaved entry could be confirmed by a later "yes", which broke the confirm rule.

## Hooks
`.kiro/hooks/`:
- `tests-on-save`: runs the test suite when a file in `khata/` or `tests/` is saved.
- `evals-on-extractor-save`: prints golden, dev2 and blind scores when the extractor or name matcher changes, so a regression shows up immediately.

## MCP
`khata/mcp_server.py` is khata-mcp, a read-only MCP server (balances, customer history, drafted reminders, dry-run parse). It is read-only so the confirm rule also holds for AI agents. `tests/test_mcp.py` launches it over stdio with the official SDK client. To use it in Kiro, copy `docs/kiro-mcp.example.json` into `.kiro/settings/mcp.json`.

## Agent workflow
- Bugs were reproduced with a probe script before being fixed, and each fix got a regression test (`tests/test_review_fixes.py`).
- The extractor's accuracy is reported on three sets. The held-out set was demoted to a dev set once it had been tuned on, and a new blind set was written, so the published number (15/20) is not overfit.
- Provider docs (Groq speech-to-text, vision) were checked on the web before code was written against them. The MCP SDK's API was read from the installed package, which caught a 2.x rename.
