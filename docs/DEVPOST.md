# Devpost draft

Check every field against the official Kiroween rules page before submitting. Deadline: **December 4, 2:00 pm PT** (about 3:30 am IST, December 5). Aim for the evening of December 3 IST.

**Category:** Frankenstein. Also claim **Best Student Project** (needs student details and proof of enrollment).

**Name:** Khata

**Tagline:** A paper khata stitched to voice, vision and chat, with ghosts that collect the dues.

## Inspiration
Small shops across India run on udhaar (credit) written in a paper khata. The notebook gets lost, the totals drift, and asking customers to pay is awkward.

## What it does
Shopkeepers record credit and payments by typing, sending a WhatsApp voice note, or photographing a bill, in Hindi, Punjabi, Hinglish or English. Every entry is confirmed before it is saved. Customers who owe money appear as ghosts, most overdue first, and a séance drafts a polite WhatsApp reminder in the customer's language. The shopkeeper decides whether to send it.

## The Frankenstein parts
A rule-based multilingual text brain, Groq Whisper ears, Groq vision eyes, a WhatsApp voice box, a SQLite heart, a Google Sheet spine that also restores the ledger after the free host wipes its disk, and a read-only MCP server. The Lab panel in the app shows each part and whether it's alive.

## How we built it
Python, FastAPI and SQLite, with Kiro specs, steering, hooks and an MCP server (see `docs/KIRO.md`). Everything runs on free tiers: Groq, Render, Google Sheets, and Meta's WhatsApp test number.

## Challenges
- One person, three scripts. Ramesh, रमेश and ਰਮੇਸ਼ must match, but Mohan and Mohini must not.
- Money safety: bill text is treated as data, an unrelated message discards an unsaved entry, and undo never deletes.
- Free hosting wipes the disk, so the ledger rebuilds itself from the Google Sheet on boot.

## Accomplishments
- 15/20 on a blind set of phrases the extractor was never tuned on, with 0 wrong entries sent straight to confirmation.
- [Fill in after step 6 of docs/SETUP.md: voice results on N real recordings.]

## What's next
Handwritten khata pages, scheduled reminders, and more languages.

## Links
- Live app: [Render URL]
- Repo: [GitHub URL]
- Video: [YouTube URL]

## Judge access
Open the live URL. A demo account with sample data is created automatically, and no sign-up is needed. With AI off, text chat, ghosts, séance, the sample bill, undo and export all still work. The app stays up until January 29, 2027.
