# Khata 👻📒

A paper khata stitched to voice, vision and chat, with ghosts that collect the dues.
Kiroween 2026 entry, Frankenstein category.

Shopkeepers record udhaar (credit) by typing, sending a voice note, or photographing a bill,
in Hindi, Punjabi, Hinglish or English. Every entry is confirmed before it is saved.

- `Ramesh ko 500 udhaar` proposes a credit entry and asks you to confirm
- `रमेश ने 200 दिए` proposes a payment, matched to the same Ramesh across scripts
- A bill photo reads the total, then asks who the customer is and whether it's credit or payment
- `Ramesh ka kitna baaki`, `list`, `undo`, `export`

Each customer who owes money appears as a ghost. Tapping a ghost opens a drafted WhatsApp reminder.

## The stitched parts

| Part | File | Swappable via |
|---|---|---|
| Ledger (SQLite, integer paise, per-owner isolation, undo) | `khata/ledger.py` | n/a |
| Cross-script names (Ramesh = रमेश = ਰਮੇਸ਼) | `khata/names.py` | n/a |
| Text understanding, rule-based | `khata/extract.py` | n/a |
| Confirm / edit / cancel flow in en, hi, pa | `khata/flow.py` | n/a |
| Voice notes (ffmpeg, then Whisper-compatible STT) | `khata/voice.py` | `KHATA_STT_URL`, `KHATA_STT_MODEL` |
| Bill photos (vision model returns 3 validated fields) | `khata/bills.py` | `KHATA_VISION_URL`, `KHATA_VISION_MODEL` |
| WhatsApp Cloud API webhook | `khata/whatsapp.py` | `WA_*` |
| Google Sheet mirror (retrying, idempotent) | `khata/sheets.py` | `KHATA_SHEET_ID` |
| Paid-API budget and kill switch | `khata/guard.py` | `KHATA_BUDGET_CALLS`, `KHATA_KILL` |

## Demo mode

With no paid key, a spent budget, or `KHATA_KILL=1`, the app keeps working. Text chat,
the ghost page, export and a pre-read sample bill all work. Voice notes and real bill photos
reply asking the user to type instead. `/health` reports `"mode": "demo"`.

## Safety

- Nothing is saved without a confirm step. Voice, bills and WhatsApp all go through the same flow.
- Text inside bill images is treated as data. The customer and credit/payment are never taken from a bill.
- WhatsApp webhooks are rejected unless the `X-Hub-Signature-256` HMAC matches. Retried messages are de-duplicated.
- Audio is deleted right after transcription. WhatsApp owners are stored as salted hashes of the phone number.
- Rate limits on chat and demo login. Demo accounts are purged after 7 days of inactivity.

## Extraction accuracy (rule-based, text input)

From `python -m evals.run` (full report in `evals/REPORT.md`):

| set | result | note |
|---|---|---|
| golden | 32/32 | developed against, overfit by design |
| dev2 | 15/15 | scored 6/15 when first written blind, then tuned on |
| blind | 15/20 (75%) | never tuned on |

None of the 67 cases produced a wrong entry that went straight to the confirm step. Failures
make the bot ask a follow-up question. These are hand-written text cases. Real voice
transcripts have not been measured yet.

## Run locally

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn khata.app:app --port 8765
scripts/smoke.sh            # in another terminal
```

Open http://localhost:8765. A demo login with sample data is created automatically.
Configuration is listed in `.env.example`.

## Deploy (Fly.io)

```
fly launch --no-deploy --copy-config
fly volumes create khata_data --size 1
fly secrets set KHATA_PAID_KEY=... WA_VERIFY_TOKEN=... WA_APP_SECRET=... WA_TOKEN=... WA_PHONE_ID=...
fly deploy
scripts/smoke.sh https://khata-kiroween.fly.dev
```

For WhatsApp, set the webhook URL to `https://<host>/webhook/whatsapp` in the Meta app
dashboard, with the same verify token.

## Tests

`python -m pytest -q tests` covers hypothesis property tests for the ledger, eval regression,
voice, WhatsApp, bills, the sheet mirror, and a full judge flow with paid APIs off.

MIT licensed.
