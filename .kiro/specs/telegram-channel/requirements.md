# Requirements Document

## Introduction

Khata is a FastAPI + SQLite credit-ledger bot for small shopkeepers in India who speak Hindi,
Punjabi, Hinglish or English. Its shopkeeper chat channel today is the WhatsApp Cloud API
(`khata/whatsapp.py`), but new Meta developer accounts often cannot get WhatsApp access. This
feature makes Telegram the primary shopkeeper chat channel through a new module
`khata/telegram.py`. Telegram works in two modes: long polling for local runs (no public URL or
tunnel needed) and webhook for Render's free plan (an inbound webhook wakes the sleeping service).

Every Telegram message goes through the same `Bot` conversation flow as the web chat and
WhatsApp, so nothing is saved without an explicit confirmation. Telegram inline buttons add one
new risk: an old keyboard can still be tapped later. This feature adds a stale-button rule so an
old "Yes" can never confirm a different entry or become a customer name.

WhatsApp stays in the codebase as an optional channel. The séance reminders stay as WhatsApp
`wa.me` links, because they go to customers, not to the shopkeeper.

## Glossary

- **Khata_Server**: The FastAPI application built by `create_app` in `khata/app.py`.
- **Bot**: The conversation flow in `khata/flow.py` (`Bot.handle`, `Bot.start_from_extraction`).
- **Confirm_Step**: The Bot stage that proposes an entry and saves the entry only after a yes reply.
- **Ledger**: The SQLite ledger in `khata/ledger.py`; every query takes an owner and runs under `ledger.lock`.
- **Telegram_API_Client**: The `TelegramAPI` class in `khata/telegram.py` that calls the Telegram Bot API over HTTPS.
- **Telegram_Adapter**: The `TelegramAdapter` class in `khata/telegram.py` that turns Telegram updates into Bot calls and replies.
- **Poller**: The `Poller` class in `khata/telegram.py` that long-polls `getUpdates` on a daemon thread.
- **Webhook_Route**: The `POST /webhook/telegram` route created by `make_router` in `khata/telegram.py`.
- **Update**: One JSON object from Telegram, identified by an integer `update_id`.
- **Callback**: An Update of type `callback_query`, produced when the shopkeeper taps an inline button.
- **Keyboard_Message**: A message sent by the Telegram_Adapter that carries an inline keyboard.
- **Latest_Keyboard**: The message id of the most recent Keyboard_Message in a chat, or none.
- **Stale_Callback**: A Callback whose message id differs from the Latest_Keyboard of its chat.
- **Owner_Id**: The ledger owner string for a Telegram chat: `"tg-"` followed by the first 24 hex characters of `sha256("{salt}:tg:{chat_id}")`.
- **Owner_Salt**: The secret value of the `KHATA_OWNER_SALT` environment variable.
- **Allow_List**: The optional set of chat ids in `TELEGRAM_ALLOWED_CHAT_IDS` (comma-separated).
- **Bot_Token**: The secret value of `TELEGRAM_BOT_TOKEN`, issued by @BotFather.
- **Webhook_Secret**: The secret value of `TELEGRAM_WEBHOOK_SECRET`.
- **Voice_Pipeline**: The `VoicePipeline` in `khata/voice.py` that transcribes audio behind `PaidGuard`.
- **Bill_Reader**: The bill photo reader in `khata/bills.py`, reached through the `on_image` callback.
- **Export_Builder**: The `build_xlsx(ledger, owner)` function in the new module `khata/export.py`.
- **Sheet_Mirror**: The Google Sheet mirror in `khata/sheets.py`.
- **MCP_Server**: The read-only khata-mcp server in `khata/mcp_server.py`.
- **Demo_Mode**: The state in which `PaidGuard` refuses AI calls (no key, budget spent, or kill switch on).

## Requirements

### Requirement 1: Telegram API client

**User Story:** As the Khata maintainer, I want a small Telegram Bot API client, so that the adapter can send and receive messages without a third-party bot framework.

#### Acceptance Criteria

1. THE Telegram_API_Client SHALL send every request to `https://api.telegram.org/bot<Bot_Token>/<method>` and download files from `https://api.telegram.org/file/bot<Bot_Token>/<file_path>`.
2. WHEN `get_updates(offset, timeout)` is called, THE Telegram_API_Client SHALL request `getUpdates` with the given offset, a timeout of 25 seconds by default, and `allowed_updates` equal to `["message", "callback_query"]`.
3. WHEN `send_message(chat_id, text, buttons)` is called, THE Telegram_API_Client SHALL send the text as plain text without a `parse_mode`, truncated to 4096 characters, and SHALL return the sent message id.
4. WHEN `send_message` receives a list of button ids, THE Telegram_API_Client SHALL attach one inline-keyboard row whose buttons carry the button id as `callback_data` and a title from the `BUTTON_TITLES` map.
5. WHEN `clear_buttons(chat_id, message_id)` is called, THE Telegram_API_Client SHALL call `editMessageReplyMarkup` with an empty inline keyboard for that message.
6. WHEN `download(file_id)` is called, THE Telegram_API_Client SHALL resolve the file with `getFile` and return the file bytes and the file path.
7. IF a file reported by `getFile`, or the downloaded content, exceeds 10 MB, THEN THE Telegram_API_Client SHALL raise a `TelegramError` and return no bytes.
8. WHEN `send_document(chat_id, filename, data, caption)` is called, THE Telegram_API_Client SHALL upload the data as a multipart `sendDocument` request with the given filename and caption.
9. WHEN `set_webhook(url, secret)` or `delete_webhook()` is called, THE Telegram_API_Client SHALL call `setWebhook` with the url, the secret token and the same `allowed_updates`, or call `deleteWebhook`.
10. IF Telegram returns `ok: false`, THEN THE Telegram_API_Client SHALL raise a `TelegramError` whose message is Telegram's `description`.
11. IF any request fails, THEN THE Telegram_API_Client SHALL raise a `TelegramError` whose message and log output contain only the exception type name and the Telegram description, and exclude the Bot_Token.

### Requirement 2: Polling channel

**User Story:** As a shopkeeper testing Khata on a laptop, I want the bot to work without a public URL, so that I don't need a tunnel such as cloudflared.

#### Acceptance Criteria

1. WHERE `TELEGRAM_MODE` is `polling` or unset and a Bot_Token is configured, THE Khata_Server SHALL start the Poller when the application starts.
2. WHEN the Poller starts, THE Poller SHALL call `delete_webhook()` once before the first `getUpdates` call.
3. WHEN the Poller receives a batch of Updates, THE Poller SHALL pass each Update to the Telegram_Adapter in `update_id` order and set the next offset to the largest `update_id` plus 1.
4. IF a `getUpdates` call fails, THEN THE Poller SHALL wait 1 second before retrying, double the wait after each consecutive failure up to 60 seconds, and reset the wait to 1 second after a successful call.
5. IF the Telegram_Adapter raises an exception for one Update, THEN THE Poller SHALL continue with the next Update.
6. WHEN the application shuts down, THE Khata_Server SHALL set the Poller's stop event so that the Poller thread exits.
7. WHILE a Khata_Server instance is built without entering its lifespan, THE Khata_Server SHALL keep the Poller stopped and make no Telegram network calls.

### Requirement 3: Webhook channel

**User Story:** As the Khata maintainer, I want a webhook mode on Render, so that a Telegram message wakes the sleeping free-plan service.

#### Acceptance Criteria

1. WHERE `TELEGRAM_MODE` is `webhook` and a Bot_Token is configured, THE Khata_Server SHALL call `set_webhook(KHATA_PUBLIC_URL + "/webhook/telegram", Webhook_Secret)` when the application starts.
2. IF `KHATA_PUBLIC_URL` or the Webhook_Secret is empty in webhook mode, or `set_webhook` fails, THEN THE Khata_Server SHALL log the failure without the Bot_Token and report the failure in `/health`.
3. IF a request reaches the Webhook_Route while the Telegram_Adapter or the Webhook_Secret is not configured, THEN THE Webhook_Route SHALL respond with HTTP 503.
4. IF the `X-Telegram-Bot-Api-Secret-Token` header is missing or differs from the expected secret token, THEN THE Webhook_Route SHALL respond with HTTP 401 and pass nothing to the Telegram_Adapter.
5. THE Webhook_Route SHALL compare the secret token header in constant time.
6. IF the request body is not valid JSON, THEN THE Webhook_Route SHALL respond with HTTP 400.
7. WHEN a request carries a valid secret token and a valid JSON body, THE Webhook_Route SHALL schedule the Update on a background task and respond with `{"ok": true}`.
8. THE Khata_Server SHALL send Telegram a secret token that contains only the characters A-Z, a-z, 0-9, `_` and `-` and is between 1 and 256 characters long, derived deterministically from the Webhook_Secret.

### Requirement 4: Text messages and commands

**User Story:** As a shopkeeper, I want to type entries and commands in Telegram, so that I can keep my khata from the chat app I already use.

#### Acceptance Criteria

1. WHEN a text message arrives from a private chat, THE Telegram_Adapter SHALL pass the first 1000 characters to `Bot.handle(owner, text, source="telegram")` and send the Bot reply to the same chat.
2. WHEN a command arrives in the form `/cmd@BotName`, THE Telegram_Adapter SHALL treat the command as `/cmd`.
3. WHEN `/start` or `/help` arrives, THE Telegram_Adapter SHALL reply with the Bot help message.
4. WHEN `/id` arrives, THE Telegram_Adapter SHALL reply with the numeric chat id of that chat.
5. WHEN `/export`, `/list`, `/undo` or `/remind` arrives, THE Telegram_Adapter SHALL pass the word without the slash (`export`, `list`, `undo`, `remind`) to the Bot.
6. IF handling a message raises an exception, THEN THE Telegram_Adapter SHALL reply "Something went wrong. Nothing was saved. Please try again." and log only the exception type name.

### Requirement 5: Confirm step through Telegram

**User Story:** As a shopkeeper, I want every entry to wait for my Yes tap, so that a misread message never changes my khata.

#### Acceptance Criteria

1. WHEN the Bot proposes an entry, THE Telegram_Adapter SHALL send the proposal with the Bot's buttons as an inline keyboard and save nothing.
2. WHEN a Callback arrives whose message id equals the Latest_Keyboard of its chat, THE Telegram_Adapter SHALL answer the Callback, clear that keyboard, and pass the button id to `Bot.handle`.
3. THE Telegram_Adapter SHALL write to the Ledger only through `Bot.handle` and `Bot.start_from_extraction`.
4. WHEN the same button is tapped a second time, THE Telegram_Adapter SHALL answer the Callback and leave the Ledger unchanged.

### Requirement 6: Stale-button safety

**User Story:** As a shopkeeper, I want old buttons to stop working, so that tapping an old Yes can't save a customer named "Yes" or confirm a different entry.

#### Acceptance Criteria

1. WHEN the Telegram_Adapter sends any reply to a chat that has a Latest_Keyboard, THE Telegram_Adapter SHALL clear the keyboard of the Latest_Keyboard message before recording the new reply.
2. WHEN the Telegram_Adapter sends a reply with buttons, THE Telegram_Adapter SHALL record the sent message id as the Latest_Keyboard of that chat.
3. WHEN the Telegram_Adapter sends a reply without buttons, THE Telegram_Adapter SHALL set the Latest_Keyboard of that chat to none.
4. IF a Stale_Callback arrives, THEN THE Telegram_Adapter SHALL answer the Callback, clear the keyboard of the tapped message, and leave the Bot, the pending entry and the Ledger unchanged.
5. IF clearing a keyboard fails, THEN THE Telegram_Adapter SHALL continue handling the Update.
6. THE Telegram_Adapter SHALL process Updates one at a time under a lock, so that the Latest_Keyboard record and the Bot see one Update at a time.

### Requirement 7: Voice notes

**User Story:** As a shopkeeper, I want to send a Hindi or Punjabi voice note, so that I can record an entry without typing.

#### Acceptance Criteria

1. WHEN a message with `voice` or `audio` arrives, THE Telegram_Adapter SHALL download the file and call `Voice_Pipeline.transcribe` with the suffix taken from the file path extension.
2. WHEN transcription succeeds, THE Telegram_Adapter SHALL send `🎙️ Heard: “<text>”` and then pass the text to `Bot.handle(owner, text, source="voice")`.
3. IF the Voice_Pipeline raises a `VoiceError`, THEN THE Telegram_Adapter SHALL send the `VoiceError` message and save nothing.
4. WHILE Demo_Mode is active, THE Telegram_Adapter SHALL reply with the Voice_Pipeline's demo-mode message for every voice note.

### Requirement 8: Bill photos

**User Story:** As a shopkeeper, I want to send a photo of a supplier bill, so that Khata reads the total and asks me the rest.

#### Acceptance Criteria

1. WHEN a message with `photo` arrives, THE Telegram_Adapter SHALL download the largest photo size whose file size is within the Bill_Reader image limit and call `on_image(owner, bytes, "image/jpeg")`.
2. WHEN a message with a `document` whose MIME type starts with `image/` arrives, THE Telegram_Adapter SHALL download the document and call `on_image(owner, bytes, mime_type)`.
3. IF every photo size exceeds the Bill_Reader image limit, THEN THE Telegram_Adapter SHALL reply that the photo is too large and call no AI service.
4. THE Telegram_Adapter SHALL treat text read from the image as data and route the result through `Bot.start_from_extraction`, which asks for the customer and for credit or payment.
5. WHEN a message of any other type arrives, THE Telegram_Adapter SHALL reply that Khata reads text, voice notes and bill photos.

### Requirement 9: Export as a document

**User Story:** As a shopkeeper, I want `/export` to send me the Excel file in Telegram, so that I don't need the web app.

#### Acceptance Criteria

1. THE Export_Builder SHALL return the bytes of an .xlsx workbook with one row per entry of the given owner, including cancelled entries, with the columns id, customer, kind, rupees, date, due and status.
2. THE Khata_Server SHALL serve `/api/export.xlsx` with the bytes from the Export_Builder.
3. WHEN the export command arrives with `source="telegram"`, THE Bot SHALL reply with the `export_doc` message in en, hi or pa and mark the reply with `export=True`.
4. WHEN a Bot reply is marked `export=True` in a Telegram chat, THE Telegram_Adapter SHALL send the Export_Builder output for that chat's Owner_Id with `send_document` as `khata.xlsx`.
5. FOR ALL ledgers, reading the Export_Builder output back as a workbook SHALL give the same entry ids, customers, kinds, rupee amounts, dates and statuses as the Ledger (round-trip property).

### Requirement 10: Owner isolation and privacy

**User Story:** As a shopkeeper, I want my khata kept apart from everyone else's, so that no other Telegram user can read or change it.

#### Acceptance Criteria

1. THE Telegram_Adapter SHALL compute the Owner_Id of a chat with `owner_for_chat(chat_id, Owner_Salt)`.
2. THE Telegram_Adapter SHALL produce an Owner_Id that excludes the decimal chat id.
3. IF the Owner_Salt is empty, THEN THE Khata_Server SHALL leave the Telegram channel off and report `"telegram": "off"` in `/health`.
4. IF a message comes from a chat whose type is not `private`, THEN THE Telegram_Adapter SHALL reply "please message me privately" and pass nothing to the Bot.
5. WHERE the Allow_List is set, THE Telegram_Adapter SHALL ignore every Update from a chat id outside the Allow_List.
6. IF a chat sends more than 30 messages within 60 seconds, THEN THE Telegram_Adapter SHALL drop the extra messages without passing them to the Bot.
7. WHEN an Update arrives with an `update_id` already handled, THE Telegram_Adapter SHALL ignore the Update.
8. THE Telegram_Adapter SHALL keep the ledgers of different chats separate, so that an entry confirmed in one chat appears only under that chat's Owner_Id.

### Requirement 11: Sheet and MCP owner from /id

**User Story:** As a shopkeeper, I want to link my Telegram chat to the Google Sheet mirror, so that my ledger survives Render wiping its disk.

#### Acceptance Criteria

1. WHERE `KHATA_SHEET_OWNER_TELEGRAM_ID` is set, THE Khata_Server SHALL mirror the ledger of `owner_for_chat(KHATA_SHEET_OWNER_TELEGRAM_ID, Owner_Salt)` to the Sheet_Mirror.
2. WHERE both `KHATA_SHEET_OWNER_TELEGRAM_ID` and `KHATA_SHEET_OWNER_NUMBER` are set, THE Khata_Server SHALL use the Telegram owner.
3. WHERE `KHATA_MCP_OWNER` is unset and `KHATA_SHEET_OWNER_TELEGRAM_ID` and `KHATA_OWNER_SALT` are set, THE MCP_Server SHALL resolve the owner with `owner_for_chat`.
4. THE MCP_Server SHALL keep exposing read-only tools only.

### Requirement 12: App wiring, health and Lab

**User Story:** As a hackathon judge, I want to see whether the Telegram channel is alive, so that I can tell which parts of Khata are running.

#### Acceptance Criteria

1. THE Khata_Server SHALL accept a `telegram_api` parameter in `create_app` and use the given client instead of building a `TelegramAPI`.
2. THE Khata_Server SHALL start the Poller or set the webhook inside a FastAPI lifespan and run blocking Telegram calls with `asyncio.to_thread`.
3. THE Khata_Server SHALL report `"telegram"` in `/health` as `"polling"`, `"webhook"` or `"off"`.
4. WHERE the Telegram channel is configured, THE Khata_Server SHALL show Telegram as the Lab panel "Voice box" part.
5. WHERE the Telegram channel is off and WhatsApp is configured, THE Khata_Server SHALL show WhatsApp as the Lab panel "Voice box" part.
6. WHERE neither Telegram nor WhatsApp is configured, THE Khata_Server SHALL show "off: web chat only" as the Lab panel "Voice box" part.

### Requirement 13: Free services and configuration

**User Story:** As the Khata maintainer, I want Telegram to run on free services with documented settings, so that anyone can deploy Khata without a card.

#### Acceptance Criteria

1. THE Khata_Server SHALL use only the free Telegram Bot API, Groq's free tier, Render's free plan and Google Sheets.
2. THE `.env.example` file SHALL list `TELEGRAM_BOT_TOKEN`, `TELEGRAM_MODE`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_ALLOWED_CHAT_IDS`, `KHATA_OWNER_SALT` and `KHATA_SHEET_OWNER_TELEGRAM_ID` with blank values.
3. THE `render.yaml` blueprint SHALL declare `TELEGRAM_BOT_TOKEN` with `sync: false`, `TELEGRAM_MODE` with value `webhook`, and `TELEGRAM_WEBHOOK_SECRET` and `KHATA_OWNER_SALT` with `generateValue: true`.
4. THE `docs/TELEGRAM.md` guide SHALL describe creating the bot with @BotFather, setting `.env`, restarting with `--env-file .env`, getting the chat id with `/id`, a test table, and the 409 Conflict troubleshooting case.

### Requirement 14: WhatsApp stays optional

**User Story:** As the Khata maintainer, I want to keep the WhatsApp adapter, so that accounts with Meta access can still use it.

#### Acceptance Criteria

1. THE Khata_Server SHALL keep mounting the WhatsApp webhook routes and keep the existing WhatsApp tests passing.
2. THE séance reminders SHALL keep using WhatsApp `wa.me` links.
3. THE `docs/WHATSAPP.md` guide SHALL state at the top that WhatsApp is optional because new Meta accounts may not get access.
4. THE `.kiro/steering/khata.md` rules SHALL name voice, bill, WhatsApp and Telegram adapters in the adapter rule and SHALL record the stale-button rule.
