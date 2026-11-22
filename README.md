# Khata 👻📒

A paper khata stitched to voice, vision and chat, with ghosts that collect the dues.
Kiroween 2026 entry, Frankenstein category. Runs entirely on free services.

Shopkeepers record udhaar (credit) by typing, sending a voice note, or photographing a bill,
in Hindi, Punjabi, Hinglish or English. Every entry is confirmed before it is saved.

- `Ramesh ko 500 udhaar` proposes a credit entry and asks you to confirm
- `रमेश ने 200 दिए` proposes a payment, matched to the same Ramesh across scripts
- A bill photo reads the total, then asks who the customer is and whether it's credit or payment
- `Ramesh ka kitna baaki`, `list`, `undo`, `export`
- `Ramesh ka number 98765 43210` stores a WhatsApp number; `remind` lists drafted reminders

Each customer who owes money appears as a ghost, most overdue first. **Hold a séance** to see
a drafted reminder for each one in Hinglish, Hindi, Punjabi or English. Tapping it opens
WhatsApp with the message typed out. The app never sends anything itself.

## The stitched parts (see the Lab panel)

| Part | File | Free service |
|---|---|---|
| Ledger: SQLite, integer paise, per-owner isolation, undo | `khata/ledger.py` | local |
| Cross-script names (Ramesh = रमेश = ਰਮੇਸ਼, Mohan ≠ Mohini) | `khata/names.py` | local |
| Text understanding, rule-based | `khata/extract.py` | local |
| Confirm / edit / cancel flow in en, hi, pa | `khata/flow.py` | local |
| Voice notes: ffmpeg, then Whisper | `khata/voice.py` | Groq `whisper-large-v3` |
| Bill photos: 3 validated fields | `khata/bills.py` | Groq `qwen/qwen3.8-27b` |
| WhatsApp webhook | `khata/whatsapp.py` | Meta test number |
| Google Sheet mirror and restore on boot | `khata/sheets.py` | Google Sheets API |
| Séance reminders | `khata/reminders.py` | wa.me links |
| AI budget and kill switch | `khata/guard.py` | local |
| khata-mcp, read-only | `khata/mcp_server.py` | local |

## Demo mode

With no Groq key, a spent budget, or `KHATA_KILL=1`, the app keeps working. Text chat, the
ghosts, the séance, export, undo and a pre-read sample bill all work. Voice notes and real bill
photos reply asking the user to type instead. `/health` reports `"mode": "demo"`.

## Safety

- Nothing is saved without a confirm step. Voice, bills and WhatsApp all go through the same flow.
  An unrelated message discards an unsaved entry, and the bot says so.
- Text inside bill images is data. The customer and credit/payment are never taken from a bill.
- WhatsApp webhooks are rejected unless the `X-Hub-Signature-256` HMAC matches. Repeated deliveries are ignored.
- Audio is deleted right after transcription. WhatsApp owners are stored as salted hashes of the phone number.
- Demo data has no phone numbers, so tapping a ghost can't message a stranger.
- Rate limits on chat and demo login. Idle demo accounts are purged after 7 days. Text colours pass WCAG AA (`scripts/contrast.py`).

## Extraction accuracy (rule-based, typed text)

From `python -m evals.run` (full report in `evals/REPORT.md`):

| set | result | note |
|---|---|---|
| golden | 32/32 | developed against, overfit by design |
| dev2 | 15/15 | scored 6/15 when first written blind, then tuned on |
| blind | 15/20 (75%) | never tuned on |

None of the 67 cases produced a wrong entry that went straight to confirmation. Failures make
the bot ask a follow-up question. Voice accuracy on real recordings has not been measured yet.

## Run locally

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn khata.app:app --port 8765
scripts/smoke.sh            # in another terminal; prints SMOKE OK
```

Open http://localhost:8765. A demo account with sample data is created automatically.
For real voice and bill reading, start it with `GROQ_API_KEY=gsk_...` (free at console.groq.com).

## Deploy and set up services

Everything is free and needs no card. Follow **[docs/SETUP.md](docs/SETUP.md)**: GitHub, Groq,
Render (`render.yaml` blueprint plus a keepalive ping), the WhatsApp test number, and the Google Sheet.

## Built with Kiro

Spec, steering, hooks and the MCP server are described in **[docs/KIRO.md](docs/KIRO.md)**.
Submission material: [docs/DEVPOST.md](docs/DEVPOST.md), [docs/VIDEO.md](docs/VIDEO.md),
[docs/subtitles.en.srt](docs/subtitles.en.srt).

## Tests

`python -m pytest -q` covers hypothesis property tests for the ledger, eval regression, voice,
WhatsApp, bills, the Groq clients (HTTP mocked), the sheet mirror and restore, séance reminders,
khata-mcp over real stdio, and a full judge flow with AI off.

MIT licensed.
