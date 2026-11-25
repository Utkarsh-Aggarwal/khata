# Demo video script (target 2:45, hard limit 3:00)

Record at 1080p: phone screen (Telegram) plus the Google Sheet on a laptop. Speak English; on-screen chat is in Hindi,
Punjabi and Hinglish. Upload `docs/subtitles.en.srt`.

Before recording: the bot running, a fresh chat with the bot, the sheet open, and a real kacha bill to photograph.

| Time | Screen | Voice-over |
|---|---|---|
| 0:00–0:12 | A paper khata page, then the Telegram chat | Millions of Indian shops run on a paper khata. Khata keeps the habit and does the bookkeeping. |
| 0:12–0:35 | Hindi **voice note**: "Ramesh ko paanch sau udhaar". "Heard: …", then 🧾/📒/🤝, tap 🤝, confirm. Balance after shown | Just speak. Pick what it is, Bill, Entry or Udhaar, and confirm. It shows what Ramesh owes now. |
| 0:35–1:00 | Type `atta 2 kg 45, sabun 2 x 35, cheeni 3 kg` → 🧾 Bill. Draft, `atta 50`, ✅ Done, 🤝 Udhaar, Ramesh. The **PDF** arrives | Make a bill in chat. It remembers rates and lets you edit, then sends a PDF and adds it to Ramesh's account. |
| 1:00–1:25 | Photograph a **kacha bill**. Line items and "⚠️ Please check: 3 × ₹45 = ₹135, but the bill says ₹145" | Photograph a handwritten bill. It reads every line and catches the mistake before anything is saved. |
| 1:25–1:50 | Laptop: the **Google Sheet**. Udhaar row appears. Change an amount in the sheet, then `/dues` in the bot shows the new number | The shopkeeper may only ever open the sheet, so it works both ways. Edit here, and the bot knows. |
| 1:50–2:05 | `/export` → Dues → PDF arrives | Any table, as PDF or Excel. |
| 2:05–2:20 | Web page: ghosts, Hold a séance, WhatsApp opens with a drafted reminder | Customers who owe money become ghosts. The séance drafts a polite reminder. You choose whether to send. |
| 2:20–2:45 | Lab panel, then `.kiro/specs`, the property tests passing | Stitched from Telegram, Whisper, vision, Sheets and SQLite on free tiers, and built with Kiro specs, steering, hooks, a custom agent and property-based tests. |

If something fails while recording, show the web demo's sample bill and kacha sample. Say so on screen rather than faking it.
