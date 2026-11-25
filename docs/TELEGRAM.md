# Telegram bot (free), tested locally

Telegram is the shopkeeper's chat channel. Locally the app **polls** Telegram, so it needs no public URL or tunnel.

## Setup
1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token.
2. In `.env`:
   ```
   TELEGRAM_BOT_TOKEN=<token>
   TELEGRAM_MODE=polling
   KHATA_OWNER_SALT=<run: openssl rand -hex 16>
   ```
3. Start: `.venv/bin/uvicorn khata.app:app --env-file .env --port 8765`. `/health` shows `"telegram": "polling"`.
4. Open your bot, press **Start**, and send `/sheet`. It tells you which email to share a new Google Sheet with;
   then send `/sheet <link>`. Every shopkeeper connects their own sheet this way (docs/SHEETS.md).

## Test from your phone
| Send | Expect |
|---|---|
| `/start` | Help text |
| `Ramesh ko 500 udhaar` | Confirm message with **Yes / No** buttons; tap **Yes** to save |
| A Hindi voice note: "रमेश को पाँच सौ रुपये उधार" | `🎙️ Heard: …`, then Yes / No |
| A photo of a shop bill | `🧾 Read from the bill: ₹…`, then "Who is the customer?" |
| `/remind` | Drafted WhatsApp reminder links for your customers |
| `/export` | `khata.xlsx` arrives as a file |
| `undo` | The last entry is cancelled (and marked cancelled in the Sheet) |

## Safety
- Only the newest keyboard works. Tapping an old **Yes** does nothing and removes those buttons, so an old tap can't
  confirm a different entry or become a customer name.
- Private chats only. Optional allow-list: `TELEGRAM_ALLOWED_CHAT_IDS=123,456`.
- Each chat has its own ledger. The owner id is a salted hash, not the chat id.
- The token never appears in logs or error messages.

## Troubleshooting
- **No reply:** check `/health`. `"telegram": "off"` means the token is missing. `startup failed` means the token is wrong.
- **409 Conflict in the log:** another process is polling the same bot, or a webhook is set. Run only one server.
- **Voice or bills say "demo mode":** `GROQ_API_KEY` is missing.

## On Render
Set `TELEGRAM_MODE=webhook`, `KHATA_PUBLIC_URL`, and the same `TELEGRAM_BOT_TOKEN`, `KHATA_OWNER_SALT` and
`KHATA_SHEET_OWNER_TELEGRAM_ID` (render.yaml lists them). On startup the app registers
`<KHATA_PUBLIC_URL>/webhook/telegram` with a secret header. Stop the local server first, or the two will fight.
