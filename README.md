# Khata 👻📒

A paper khata stitched to voice, vision, chat and a Google Sheet, with ghosts that collect the dues.
Kiroween 2026 entry, Frankenstein category. Runs entirely on free services.

Small Indian shops run on a paper khata. Khata keeps the habit (talk, write, photograph a bill)
and stitches it to a Telegram bot, a Whisper speech model, a vision model and a Google Sheet that
the shopkeeper can edit directly.

**Try it:** message [@Ledgy637Bot](https://t.me/Ledgy637Bot) on Telegram, or open the web demo
(link in the Devpost entry). Each Telegram chat gets its own private khata.

## Three modes

Every new message on Telegram gets three buttons, so the bot never guesses what you meant:

| Mode | For | Example |
|---|---|---|
| 🧾 **Bill** | Making a bill for a customer | `atta 2 kg 45, sabun 2 x 35, cheeni 3 kg`, then edit with `atta 50`, `hatao sabun`, tap ✅ Done, choose 💵 Cash or 🤝 Udhaar, and get a **PDF bill** |
| 📒 **Entry** | Money in or out with nobody owing | `aaj 5000 ki bikri`, `bijli 900`, a supplier's bill photo |
| 🤝 **Udhaar** | Credit given or taken, and money returned | `Ramesh ko 500 udhaar`, `Ramesh ne 200 diye`; the balance after is shown each time |

Input can be typed, a **voice note** (Hindi, Punjabi, Hinglish, English) or a **bill photo**,
printed or *kacha* (handwritten). For a kacha bill the bot reads every line, checks
quantity × rate and the written total, and warns about mistakes before anything is saved.

Nothing is ever saved without a confirm tap.

## Commands

`/dues` (who owes you and whom you owe) · `/today` · `/expenses` · `/rates` · `/remind` (drafted
WhatsApp reminders for customers) · `/export` (any table as PDF or Excel) · `/undo` · `/invite` and
`/join` (several phones, one shop) · `/sheet <link>` (keep the khata in your own Google Sheet; send a
new link to move, `/sheet off` to stop) · `/reset` (start fresh after a confirm tap; the old sheet is kept)
· `/help`

## The Google Sheet is two-way

The shopkeeper may only ever open the sheet, so it is a working copy, not just a report:

| Tab | |
|---|---|
| **Udhaar**, **Entries** | Editable: change a cell, delete a row to cancel it, type a new row to add one |
| **Rates**, **Customers** | Editable: item rates for bills, customers' WhatsApp numbers |
| **Bills**, **Dues** | Calculated, redrawn on every change |
| **Settings** | Linked phones (do not edit) |

Rows the bot can't read stay in the sheet with `⚠️ Not saved: <reason>`. On free hosting the disk is
wiped on restart, so the bot rebuilds its database from the sheet on boot.

## The stitched parts (see the Lab panel on the web page)

| Part | File | Free service |
|---|---|---|
| Ledger: SQLite, integer paise, per-shop isolation, undo | `khata/ledger.py` | local |
| Text brain, rule-based, 4 languages, who-paid-whom | `khata/extract.py`, `khata/flow.py` | local |
| Cross-script names (Ramesh = रमेश = ਰਮੇਸ਼, Mohan ≠ Mohini) | `khata/names.py` | local |
| Bill generator, rate memory, PDF | `khata/billing.py` | local |
| Ears: voice notes | `khata/voice.py` | Groq `whisper-large-v3` |
| Eyes: bill and kacha-bill photos | `khata/bills.py` | Groq `qwen/qwen3.8-27b` |
| Telegram bot (polling locally, webhook on Render), stale-button safety | `khata/telegram.py` | Telegram Bot API |
| Two-way Google Sheet | `khata/sheets.py`, `khata/render.py` | Google Sheets API |
| PDF and Excel export | `khata/export.py` | local |
| khata-mcp, read-only tools for AI agents | `khata/mcp_server.py` | local |

## Safety

- Every record needs a confirm tap. On Telegram only the newest buttons work, so an old **Yes** can't
  confirm a different entry or become a customer called "Yes" (proved by property-based tests).
- Text inside photos and voice notes is data, never instructions; a bill never decides the customer.
- Money is integer paise everywhere, including bill maths. Single entries above ₹1 crore are refused.
- Each chat or shop has its own khata; owners are salted hashes, not chat ids or phone numbers.
- The bot token never appears in logs or errors. Audio is deleted after transcription.

## Extraction accuracy (rule-based, typed text)

`python -m evals.run`: golden **32/32**, dev2 **15/15**, blind **16/20** (80%, never tuned on).
No case proposed a wrong entry straight to confirmation; misses become a follow-up question.
Voice accuracy on real recordings has not been measured at scale yet.

## Run locally

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # add GROQ_API_KEY and TELEGRAM_BOT_TOKEN (both free)
.venv/bin/uvicorn khata.app:app --env-file .env --port 8765
scripts/smoke.sh            # prints SMOKE OK
```

Full setup (Telegram, Google Sheet, Render): [docs/SETUP.md](docs/SETUP.md).

## Built with Kiro

Two specs, steering, hooks, a custom agent, an MCP server and property-based tests:
[docs/KIRO.md](docs/KIRO.md).

## Tests

`python -m pytest -q`: 270+ tests, including Hypothesis property tests for the ledger, the export,
the Telegram adapter (stale buttons, isolation, rate limits, token leaks, poller backoff) and the
webhook secret.

MIT licensed. Noto Sans fonts in `khata/fonts` are under the SIL Open Font License.
