# Devpost draft

Check every field against the official Kiroween rules page before submitting. Deadline: **December 4, 2:00 pm PT**
(about 3:30 am IST, December 5). Aim for the evening of December 3 IST.

**Category:** Frankenstein. Also claim **Best Student Project: Frankenstein** (needs LinkedIn, student email, year and
degree, school; proof of enrollment if asked).

**Name:** Khata

**Tagline:** A paper khata stitched to voice, vision, chat and a Google Sheet, with ghosts that collect the dues.

## Inspiration
Small shops across India run on udhaar (credit) written in a paper khata. The book gets lost, the totals drift, bills are
written by hand, and asking customers to pay is awkward. Most "khata apps" ask shopkeepers to change how they work. We
wanted to keep the habit (speak, write, photograph) and do the bookkeeping behind it.

## What it does
A Telegram bot, @Ledgy637Bot, with three modes chosen by button so it never guesses:
- **🧾 Bill:** list items by voice or text ("atta 2 kg 45, sabun 2 x 35"), edit them in chat, and get a PDF bill.
  Item rates are remembered. The bill is recorded as a cash sale or added to the customer's udhaar.
- **📒 Entry:** money in or out with nobody owing: sales, expenses, supplier bills.
- **🤝 Udhaar:** credit given or taken and money returned, with the balance shown after every entry. `/dues` lists everyone.

It understands Hindi, Punjabi, Hinglish and English, typed or as voice notes. It reads printed and **kacha
(handwritten) bills**, line by line, and checks the arithmetic: it caught a planted 3 × ₹45 = ₹145 mistake.
Nothing is saved without a confirm tap, and you can correct it in plain words before or after saving:
"nahi, Suresh", "nahi, maine diye", or "uska naam Ramesh hai, Mujhko Ramesh nahi". When the correction is unclear, it asks.

Everything lands in the shopkeeper's **own two-way Google Sheet**, connected from the chat: `/start` lists three steps, and
pasting the sheet's link connects it. The shopkeeper can edit, delete or add rows there and the bot picks it up. `/reset`
starts a fresh khata and keeps the old sheet as a backup.
Any table exports as PDF or Excel. Customers who owe money become ghosts, and the séance drafts polite WhatsApp
reminders in the customer's language.

## The Frankenstein parts
A rule-based multilingual text brain, Groq Whisper ears, Groq vision eyes, a Telegram voice box, a SQLite heart, a Google
Sheet spine that the shopkeeper edits and that rebuilds the database after the free host wipes its disk, a PDF and Excel
printing press, and a read-only MCP server. The Lab panel on the web page shows each part and whether it's alive.

## How we built it
Python, FastAPI and SQLite, built in Kiro: two specs (one run through requirements → design → 54 tasks), always-on
steering, hooks, a custom eval-guard agent, an MCP server and Hypothesis property-based tests. Everything runs on free
tiers: Groq, Telegram, Google Sheets and Render. See `docs/KIRO.md`.

## Challenges
- **Who paid whom.** "Maine Ramesh ko 500 lautaye" (I returned 500 to Ramesh) and "Ramesh ne 500 lautaye" (Ramesh returned
  500) move money in opposite directions. When unsure, the bot asks instead of guessing.
- **Whisper writes Hindi in Urdu script** for some voices. The bot detects that and re-asks in Hindi.
- **Old buttons.** Telegram keeps old "Yes" buttons tappable. A property-based test proved an old tap can never confirm a
  different entry.
- **Corrections are where rules break.** "maine nahi diye" (I did *not* give), "not credit", "usne usko diye": every
  wording we handled exposed another. Our custom Kiro agent, khata-eval-guard, rejected the correction feature four times
  with concrete failing phrases before accepting it. The rule we kept: if a correction is unclear, ask.
- **The sheet is both a report and an input.** Edits are compared with what the bot last wrote, and bad rows stay visible
  with a reason, so nothing typed is lost.

## Accomplishments
- Rule-based extraction: 16/20 on a blind set never tuned on, with no wrong entry sent straight to confirmation.
- 430+ tests, including 18 Hypothesis property-based tests covering all 16 correctness properties in the spec, and 140
  wordings of corrections in Hindi, Punjabi, Hinglish and English.
- Voice: tested by hand on real Hindi and Hinglish voice notes. We have not run a scored voice eval yet.

## What's next
An LLM conversation layer: we tested `gpt-oss-120b` on Groq with tools that only *propose* entries, and it handled the
correction sentences our rules struggled with. The rules would stay as the safety net and the offline fallback. Also: a
collections agent that messages customers directly with a UPI link and negotiates payment plans; reading whole
handwritten khata pages; today's sales and profit.

## Links
- Repo: https://github.com/Utkarsh-Aggarwal/khata
- Live app: https://khata-aql4.onrender.com
- Telegram: https://t.me/Ledgy637Bot
- Video: [YouTube URL]

## Judge notes (private field)
- Telegram: message @Ledgy637Bot. Each judge gets a private khata. Try `Ramesh ko 500 udhaar`, a Hindi voice note, a bill
  photo, `atta 2 kg 45` → 🧾 Bill, then `/export`.
- Web: open the live URL. A demo account with sample data is created automatically. With AI off, typing, ghosts, the
  sample bill, the kacha sample bill, undo and export all still work.
- Sheet: [view-only link to a demo Google Sheet].
- If the free AI quota runs out, voice and photos ask you to type instead.
- The app stays up until January 29, 2027.
