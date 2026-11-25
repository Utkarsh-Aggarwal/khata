# How Kiro was used

Khata was built in Kiro IDE agent sessions between November 19 and December 2026. This page
covers each Kiro feature, what it was used for, and what difference it made.

## Spec-driven development
Two specs live in `.kiro/specs/`:

- **`khata/`**: the first spec, written as EARS requirements (R1–R10), a design and a task list.
  It drove the core: ledger, extraction, confirm flow, voice, bills, the sheet mirror and deployment.
- **`telegram-channel/`**: written when WhatsApp turned out to be closed to new Meta accounts. It went
  through the full requirements → design → tasks flow: 10 requirements, a design with 16 correctness
  properties, and 54 tasks. Each task was executed by a spec-task agent and checked off.

**Spec vs vibe coding.** The Telegram channel was built from the spec. The stale-button rule, for example,
was written as a requirement before any code existed, then became Properties 7–9 and 16 in the design, then
tests. Later features (three modes, the bill generator, the two-way sheet, export) were vibe-coded in
response to the shopkeeper's feedback in chat, where fast iteration mattered more. The steering file kept
those quick changes inside the same rules.

## Steering
`.kiro/steering/khata.md` is always included. Its rules: integer paise, owner-scoped queries,
`ledger.lock` on every SQLite access, nothing saved without confirmation, untrusted text from images and
audio, free services only, and never tuning on the blind eval set.

These rules caught real bugs. A stale unsaved entry could be confirmed by a later "yes", which broke the
confirm rule. A plain reading of the money rule found float arithmetic in the bill parser.

## Agent hooks
`.kiro/hooks/`:
- **tests-on-save**: runs the test suite when anything in `khata/` or `tests/` is saved.
- **evals-on-extractor-save**: prints golden, dev2 and blind scores when the extractor or name matcher
  changes, so a regression shows up straight away.

## Custom agent
`.kiro/agents/khata-eval-guard.md` is a read-only reviewer for extractor changes. It runs the evals and
tests, requires golden and dev2 at 100%, refuses to tune on the blind set, checks the diff against the
money-safety rules, and returns ACCEPT or REJECT with file and line references.

On its first run it refused a request to edit `blind.json` so a failing case would pass. It also flagged
that bill amounts were computed with floats (`round(float(s) * 100)`). That was fixed with exact decimal
maths and a test.

## MCP
`khata/mcp_server.py` is **khata-mcp**, a read-only MCP server. Its tools: balances, customer
history, drafted reminders, expenses, money in, bills, rates, and a dry-run parse. It is read-only on purpose,
so the confirm rule holds for AI agents too. `tests/test_mcp.py` launches it over stdio with the official SDK
client, the same way Kiro does. To use it, copy `docs/kiro-mcp.example.json` into `.kiro/settings/mcp.json`.

## Property-based testing
Hypothesis tests cover the rules where an example-based test would miss the bad sequence:
- the ledger: balance equals the signed sum, undo restores the balance;
- export round trips;
- Telegram (16 properties from the spec):
  - text caps, token never leaking into errors or logs, owner-id privacy, command normalisation, photo
    size choice;
  - allow-list, rate limit, duplicate updates;
  - **nothing saved without an accepted confirmation**, at most one live keyboard per chat, stale callbacks
    have no effect, chats are isolated;
  - poller offset and backoff, the webhook secret.

The stale-callback property is what proved that an old "Yes" can't become a customer name.

## Sub-agents
Context-gatherer and spec-task-execution agents handled codebase exploration and the 54 spec tasks.
The custom-agent-creator built the eval guard.

## What didn't work, honestly
- Long sessions with many small shell commands were slow. Batching work into fewer, larger commands helped.
- Some features were built ahead of the shopkeeper's feedback and had to be reshaped: the sheet layout
  changed four times before it matched how a shopkeeper reads a khata. Restore still reads every older
  layout, so no data was lost along the way.
