# Design: Khata

## Shape
One FastAPI process. Every channel feeds the same conversation `Bot`, and only the `Bot` writes to the ledger, after a confirm step.

```
 web chat ─┐                                   ┌─> SQLite ledger (source of truth)
 WhatsApp ─┼─> [voice: ffmpeg→Groq Whisper] ─┐ │        │
 bill photo┘   [bill: Groq vision→3 fields] ─┼─> Bot ───┘        ├─> Google Sheet mirror (+ restore on boot)
                                             │  (extract → ask → confirm → save)  └─> .xlsx export
 typed text ─────────────────────────────────┘
                                         ghosts / séance (wa.me drafts) ← ledger
                                         khata-mcp (read-only) ← ledger
```

## Parts (each swappable, see the Lab panel)
| Part | Module | Notes |
|---|---|---|
| Ledger | `ledger.py` | Integer paise. `owner` on every query. One connection behind an `RLock`. AUTOINCREMENT ids so sheet rows never collide. |
| Names | `names.py` | Transliterates Devanagari and Gurmukhi, drops only the inherent "a", merges long and short vowels. |
| Extractor | `extract.py` | Rule-based, no AI. Finds the name next to a postposition (ko, ne, ਨੂੰ) or an English preposition. |
| Flow | `flow.py` | `Pending` per owner: awaiting customer, amount, kind or confirm. Three languages. |
| Guard | `guard.py` | Counts AI calls against a budget; kill switch; no key means demo mode. |
| Voice | `voice.py` | Temp dir deleted in `finally`. Whisper prompt biases khata spellings. |
| Bills | `bills.py` | Sniffs real image type, downsizes to 1600 px, validates fields, never takes customer or kind from the bill. |
| WhatsApp | `whatsapp.py` | HMAC check on the raw body, ack fast, handle in a background task, dedupe by message id. |
| Sheets | `sheets.py` | Diff-based sync with backoff. Lookup before re-append. `boot()` restores on an empty DB. |
| Reminders | `reminders.py` | Most overdue first. wa.me links; the shopkeeper sends. |
| MCP | `mcp_server.py` | Read-only tools for an AI agent. |

## Key decisions
- **Rules, not an LLM, for text.** Free, instant, works in demo mode, and every miss becomes a follow-up question instead of a wrong entry. Measured: 15/20 on an untuned blind set, 0 wrong entries sent straight to confirm.
- **Confirm everything.** Wrong udhaar is worse than slow udhaar.
- **Sheet as backup on free hosting.** Render's free disk is wiped; the sheet is the durable copy. Restore keeps original ids, so undo after a restore still updates the right row.
- **Read-only MCP.** Keeps the confirm rule true for AI agents too.

## Failure modes
| Failure | Behaviour |
|---|---|
| Groq down, 429 or budget spent | Ask the user to type; text flow unaffected |
| Sheet API down | Entries queue and retry with backoff |
| Restore fails on boot | Sync pauses; `/health` and the Lab show it |
| Duplicate webhook | Dropped by message id |
| Concurrent requests | Serialized by the ledger lock |
