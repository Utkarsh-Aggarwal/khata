# Khata 👻📒

A paper khata stitched to voice, vision and chat, with ghosts that collect the dues.
Kiroween 2026 entry, Frankenstein category.

Shopkeepers type (soon: speak or photograph) entries in Hindi, Punjabi, Hinglish or English:

- `Ramesh ko 500 udhaar` saves credit (after you confirm)
- `रमेश ने 200 दिए` saves a payment, matched to the same customer across scripts
- `Ramesh ka kitna baaki`, `list`, `undo`, `export`

Each customer who owes money appears as a ghost. Tap a ghost to open a drafted WhatsApp reminder.

## Run

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn khata.app:app --port 8765
```

Open http://localhost:8765. A demo login with sample data is created automatically.
`/health` reports `demo` mode while paid APIs are off.

## Design

- `khata/ledger.py`: SQLite, integer paise, per-owner isolation, undo marks entries cancelled.
- `khata/names.py`: cross-script name keys (Ramesh = रमेश = ਰਮੇਸ਼).
- `khata/extract.py`: rule-based extractor with Indian number words (dedh, dhai, hazaar, lakh), and "kal" read as yesterday or tomorrow based on tense.
- `khata/flow.py`: confirm, edit and cancel conversation in three languages.

## Tests

`python -m pytest -q tests` runs the hypothesis property tests, golden extraction cases and an end-to-end API test.

MIT licensed.
