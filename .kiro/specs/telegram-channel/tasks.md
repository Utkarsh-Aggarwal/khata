# Implementation Plan: Telegram Channel

## Overview

Telegram becomes Khata's primary shopkeeper chat channel through a new module `khata/telegram.py`
(`TelegramAPI`, `TelegramAdapter`, `Poller`, `make_router`), plus `khata/export.py`, a
trilingual `export_doc` message in `khata/flow.py`, lifespan wiring in `khata/app.py`, and
Telegram-based owners for the sheet mirror and khata-mcp. Language: Python (FastAPI, httpx,
openpyxl). Tests: pytest + Hypothesis in `tests/test_telegram.py` and `tests/test_export.py`,
with a `FakeAPI` and `FakeHTTP` and no network.

Each top-level task leaves `python -m pytest -q` green. The adapter never writes to the
ledger: every save goes through `Bot.handle` or `Bot.start_from_extraction` and the confirm
step in `khata/flow.py`. Test sub-tasks are required, because the money-safety rules depend on them.

## Tasks

- [x] 1. Factor the xlsx builder into `khata/export.py`
  - [x] 1.1 Create `khata/export.py` with `HEADER` and `build_xlsx(ledger, owner) -> bytes`
    - Move the workbook code out of the `/api/export.xlsx` route in `khata/app.py`: sheet title
      `Khata`, header `id, customer, kind, rupees, date, due, status`, one row per
      `ledger.entries(owner, include_cancelled=True)`, `rupees = e.paise / 100` (display only;
      the ledger stays integer paise)
    - Change `/api/export.xlsx` to `StreamingResponse(io.BytesIO(build_xlsx(ledger, o)), ...)`
      with the same media type and `Content-Disposition: attachment; filename=khata.xlsx`
    - _Requirements: 9.1, 9.2_
  - [x] 1.2 Write property test for the export round trip in `tests/test_export.py`
    - **Property 11: Export round trip**
    - Hypothesis entries for one owner plus a second owner: customers in Latin, Devanagari and
      Gurmukhi, both kinds, paise 1..`MAX_ENTRY_PAISE`, dates, due dates, some undone; reload
      with openpyxl; check header, ids, customers, kinds, dates, due, status,
      `round(rupees * 100) == paise`, no rows from the other owner, bytes start with `PK`
    - `@settings(max_examples=100, deadline=None)`, tagged
      `# Feature: telegram-channel, Property 11: Export round trip`
    - **Validates: Requirements 9.1, 9.5, 10.8**
  - [x] 1.3 Write unit test that `/api/export.xlsx` still returns the same workbook for a demo login
    - _Requirements: 9.2_

- [x] 2. Telegram API client and test isolation
  - [x] 2.1 Add an autouse fixture in `tests/conftest.py` that removes `TELEGRAM_BOT_TOKEN`,
    `TELEGRAM_MODE`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_ALLOWED_CHAT_IDS`, `KHATA_OWNER_SALT`
    and `KHATA_SHEET_OWNER_TELEGRAM_ID` from the environment for every test
    - Keeps a developer's `.env` from starting a real poller during tests
    - _Requirements: 2.7_
  - [x] 2.2 Create `khata/telegram.py` with the constants, `TelegramError` and `TelegramAPI`
    - Constants from the design: `API_BASE`, `FILE_MAX = 10 MB`, `ALLOWED_UPDATES`,
      `BUTTON_TITLES`, `log = logging.getLogger("khata.telegram")`
    - `TelegramAPI(token, http=None)` with a private `_token`, `httpx.Client(timeout=35)` by default,
      and `_call(method, ...)` that raises `TelegramError(f"{method}: {type(ex).__name__}") from None`
      on any exception, logs only the type name, and scrubs the token from Telegram's `description`
    - Methods: `get_updates(offset, timeout=25)`, `send_message(chat_id, text, buttons=None) -> int`
      (plain text, no `parse_mode`, `text[:4096]`, one inline-keyboard row, `callback_data` `b[:64]`),
      `answer_callback`, `clear_buttons` (`editMessageReplyMarkup` with `{"inline_keyboard": []}`),
      `download(file_id) -> (bytes, file_path)` with both 10 MB checks, `send_document`
      (multipart, `caption[:1024]`), `set_webhook(url, secret_token)`, `delete_webhook()`
    - No `__repr__` that shows the token
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11_
  - [x] 2.3 Write request-shape unit tests with `FakeHTTP` in `tests/test_telegram.py`
    - Base URL and file URL (1.1); `getUpdates` offset, timeout 25 and `allowed_updates` (1.2);
      inline keyboard row with `BUTTON_TITLES` (1.4); `clear_buttons` body (1.5); `download`
      returns bytes and path (1.6); `getFile` size over 10 MB and downloaded content over 10 MB
      both raise `TelegramError` (1.7); `sendDocument` multipart fields (1.8);
      `setWebhook`/`deleteWebhook` bodies (1.9); `ok: false` raises with the description (1.10)
    - _Requirements: 1.1, 1.2, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10_
  - [x] 2.4 Write property test for the outgoing text cap
    - **Property 1: Outgoing and incoming text is capped** (outgoing half: for any string `t`,
      `sendMessage` carries `text == t[:4096]` and no `parse_mode`; the incoming half is added in 3.6)
    - Tag `# Feature: telegram-channel, Property 1: Outgoing and incoming text is capped`, 100+ examples
    - **Validates: Requirements 1.3**
  - [x] 2.5 Write property test that the token never leaks
    - **Property 2: The bot token never appears in errors or logs**
    - Random tokens; `FakeHTTP` raises an exception whose message contains the full URL, or
      returns `ok: false` with the token in `description`; assert the token is absent from
      `str(err)`, from `traceback.format_exception(err)`, and from `caplog.text`
    - **Validates: Requirements 1.11**

- [x] 3. Telegram adapter: gates, commands, text, voice and photos
  - [x] 3.1 Add the pure helpers to `khata/telegram.py`
    - `owner_for_chat(chat_id, salt)` (`"tg-" + sha256(f"{salt}:tg:{int(chat_id)}")[:24]`,
      `ValueError` on a non-integer id), `parse_allowed(raw)` (`ValueError` on a bad entry),
      `command_of(text)` (lowercase, strips `@BotName` and arguments), `pick_photo(sizes, limit)`
    - Constants `ERROR_REPLY`, `PRIVATE_ONLY`, `UNSUPPORTED`, `TOO_LARGE`, `RATE_PER_MIN = 30`, `COMMANDS`
    - _Requirements: 4.2, 4.5, 8.1, 8.3, 10.1, 10.2, 10.5_
  - [x] 3.2 Write property test for the owner id
    - **Property 12: Owner id format and privacy**
    - **Validates: Requirements 10.1, 10.2**
  - [x] 3.3 Write property test for command normalisation
    - **Property 6: Command suffix normalization**
    - **Validates: Requirements 4.2, 4.5**
  - [x] 3.4 Write property test for photo size selection
    - **Property 10: Photo size selection**
    - **Validates: Requirements 8.1, 8.3**
  - [x] 3.5 Implement `TelegramAdapter` message handling
    - Constructor per the design (`bot, voice, api, on_image, export, salt, allowed, clock,
      image_limit=bills.MAX_IMAGE`), `threading.Lock`, `Seen` from `khata.whatsapp`, `keyboards`, `hits`
    - `handle_update`: under the lock, drop updates without `update_id` or already seen, then route
      to `_on_message` or `_on_callback`
    - `_admit(chat)`: allow-list (silent ignore), then private-only (`PRIVATE_ONLY`, Bot not called),
      then the 30-per-60-seconds rate limit (silent drop), in that order
    - `_on_message`: `/id` replies with the chat id; `COMMANDS` go to `bot.handle(owner, word,
      source="telegram")`; other text goes as `text[:1000]`; `voice`/`audio` download, suffix from the
      file path (default `.oga`), `voice.transcribe`, `VoiceError` text on failure, else
      `🎙️ Heard: “…”` then `bot.handle(owner, text, source="voice")`; `photo` uses `pick_photo` and
      `on_image(owner, data, "image/jpeg")` or `TOO_LARGE`; image `document` checks `file_size`
      then `on_image(owner, data, mime_type)`; anything else gets `UNSUPPORTED`
    - Catch-all logs `type(ex).__name__` and sends `ERROR_REPLY`; a failure of that send is logged and swallowed
    - A minimal `_send(chat_id, owner, r)` that sends `r["reply"]` with `r.get("buttons")`
      (the keyboard bookkeeping comes in task 4)
    - The adapter calls only `Bot.handle` and `on_image` (which calls `Bot.start_from_extraction`);
      it never touches the `Ledger`
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 5.1, 5.3, 7.1, 7.2, 7.3, 7.4, 8.1, 8.2, 8.3, 8.4, 8.5, 10.4, 10.5, 10.6, 10.7_
  - [x] 3.6 Write property test for the incoming text cap
    - **Property 1: Outgoing and incoming text is capped** (incoming half: a private non-command
      text `t` reaches `Bot.handle` as `t[:1000]`); extend the 2.4 test into one test covering both halves
    - **Validates: Requirements 1.3, 4.1**
  - [x] 3.7 Write property test for the allow-list gate
    - **Property 13: Allow-list gate**
    - **Validates: Requirements 10.5**
  - [x] 3.8 Write property test for the rate limit with an injected list-backed clock
    - **Property 14: Rate limit**
    - **Validates: Requirements 10.6**
  - [x] 3.9 Write property test for duplicate updates
    - **Property 15: Duplicate updates are idempotent**
    - **Validates: Requirements 10.7**
  - [x] 3.10 Write unit tests for messages and commands
    - `/start` and `/help` reply with the Bot help text (4.3); `/id` replies with the chat id (4.4);
      `/List@KhataBot` reaches the Bot as `list` (4.2, 4.5); an exception inside `Bot.handle`
      gives `ERROR_REPLY` and a log line with only the type name (4.6); group chat gets
      `PRIVATE_ONLY` and the Bot is not called (10.4)
    - Voice with `FakeSTT`: `voice/file_1.oga` gives suffix `.oga`, `Heard` then the confirm buttons,
      nothing saved (7.1, 7.2); demo-mode `PaidGuard` gives the `VoiceError` demo message (7.3, 7.4)
    - Photo routed to `on_image` with the largest allowed size and `image/jpeg` (8.1); image document
      with its MIME type (8.2); every size too large gives `TOO_LARGE` and no `on_image` call (8.3);
      bill result asks for the customer and credit/payment (8.4); a sticker gets `UNSUPPORTED` (8.5)
    - _Requirements: 4.2, 4.3, 4.4, 4.5, 4.6, 7.1, 7.2, 7.3, 7.4, 8.1, 8.2, 8.3, 8.4, 8.5, 10.4_

- [x] 4. Stale-button safety and the confirm step
  - [x] 4.1 Implement the keyboard rule in `TelegramAdapter`
    - `_send`: pop the chat's Latest_Keyboard and `_clear` it, send, record the new message id
      only when the reply has buttons; route every plain notice (`Heard`, `PRIVATE_ONLY`, `/id`,
      `ERROR_REPLY`, `TOO_LARGE`, `UNSUPPORTED`) through `_send`
    - `_clear(chat_id, mid)`: call `api.clear_buttons`, log and swallow any exception
    - `_on_callback`: best-effort `answer_callback`; `_admit`; if the message id differs from
      `keyboards.get(chat_id)`, clear that message and return without calling the Bot; otherwise
      clear, delete the record, and send `bot.handle(owner, data[:64], source="telegram")`
    - _Requirements: 5.2, 5.4, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_
  - [x] 4.2 Write property test: nothing is saved without an accepted confirmation
    - **Property 7: Nothing is saved without an accepted confirmation**
    - Random sequences of entry texts, unrelated texts, `yes`/`no`/`credit`/`payment` callbacks with
      arbitrary message ids, and duplicates, against a real `Bot` on an in-memory `Ledger`
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.4**
  - [x] 4.3 Write property test: at most one live keyboard per chat
    - **Property 8: At most one live keyboard per chat**
    - **Validates: Requirements 6.1, 6.2, 6.3**
  - [x] 4.4 Write property test: stale callbacks have no effect
    - **Property 9: Stale callbacks have no effect**
    - **Validates: Requirements 6.4, 5.4**
  - [x] 4.5 Write property test: chats are isolated
    - **Property 16: Chats are isolated**
    - **Validates: Requirements 10.8**
  - [x] 4.6 Write scenario unit tests for the stale-button rule
    - Text gives buttons and saves nothing; tapping Yes on the latest keyboard saves exactly once;
      a second tap of the same Yes leaves the ledger unchanged (5.1, 5.2, 5.4)
    - Old Yes cannot become a customer name: bare amount or bill → "Who is the customer?" →
      tap Yes from an earlier keyboard → no customer named "Yes", the pending entry still waits
      for a customer, and the old keyboard was cleared (6.4)
    - Old Yes cannot confirm a different entry: propose entry A, type entry B, tap A's Yes →
      nothing saved, B still pending; tap B's Yes → only B saved (6.1, 6.4)
    - `clear_buttons` raising still lets the update finish (6.5); two threads calling
      `handle_update` never overlap inside `Bot.handle` (6.6)
    - _Requirements: 5.1, 5.2, 5.4, 6.1, 6.4, 6.5, 6.6_

- [x] 5. Poller and webhook router
  - [x] 5.1 Implement `next_delay`, `webhook_token` and `Poller` in `khata/telegram.py`
    - `Poller(adapter, api, timeout=25, stop=None)`: `run_once` sorts by `update_id`, advances the
      offset before handling each update, logs and continues on adapter errors; `run` calls
      `delete_webhook()` once, then loops with backoff 1 → 60 s via `stop_event.wait(delay)`,
      resetting to 1 after a success; `start()` runs a daemon thread `telegram-poller`;
      `stop(join_timeout=2.0)` sets the event and joins
    - `webhook_token(secret) = sha256("khata-tg:" + secret).hexdigest()`
    - _Requirements: 2.2, 2.3, 2.4, 2.5, 2.6, 3.8_
  - [x] 5.2 Implement `make_router(adapter, secret)` with `POST /webhook/telegram`
    - 503 when the adapter or secret is missing; 401 unless `hmac.compare_digest` on the header
      bytes matches `webhook_token(secret)`; 400 on bad JSON or a non-object body; otherwise
      `bg.add_task(adapter.handle_update, update)` and `{"ok": True}`
    - _Requirements: 3.3, 3.4, 3.5, 3.6, 3.7_
  - [x] 5.3 Write property test for poller offset and order
    - **Property 3: Poller offset and order**
    - **Validates: Requirements 2.3, 2.5**
  - [x] 5.4 Write property test for poller backoff with a fake stop event that records waits
    - **Property 4: Poller backoff**
    - **Validates: Requirements 2.4**
  - [x] 5.5 Write property test for the webhook secret
    - **Property 5: Webhook secret** (token charset and determinism for secrets with `+`, `/`, `=`
      and non-ASCII; any other header value gives 401 and the adapter receives nothing)
    - **Validates: Requirements 3.4, 3.8**
  - [x] 5.6 Write unit tests for the poller and router
    - `delete_webhook` called once before the first `getUpdates`; the loop survives one
      `get_updates` error; `stop()` ends the thread (2.2, 2.5, 2.6)
    - Router: 503 without adapter or secret, missing header 401, bad JSON 400, valid request 200
      with the update handled (3.3, 3.4, 3.6, 3.7)
    - _Requirements: 2.2, 2.5, 2.6, 3.3, 3.4, 3.6, 3.7_

- [x] 6. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. App wiring, `/health` and the Lab panel
  - [x] 7.1 Wire Telegram into `create_app` in `khata/app.py`
    - Add the `telegram_api=None` parameter; read `TELEGRAM_BOT_TOKEN`, `KHATA_OWNER_SALT`,
      `TELEGRAM_MODE` (`webhook` or else `polling`), `TELEGRAM_WEBHOOK_SECRET`,
      `TELEGRAM_ALLOWED_CHAT_IDS`; build `TelegramAdapter` only when an API client and a salt exist;
      a bad allow-list turns the channel off with `telegram_error`
    - Define an `asynccontextmanager` lifespan and pass it to `FastAPI(..., lifespan=lifespan)`
      (create the app after the lifespan is defined, or set `app.router.lifespan_context`):
      polling starts a `Poller` and stops it with `asyncio.to_thread(poller.stop)` on exit;
      webhook calls `asyncio.to_thread(tg_api.set_webhook, KHATA_PUBLIC_URL + "/webhook/telegram",
      webhook_token(secret))`, or records a missing URL/secret or the `TelegramError` text in
      `app.state.telegram_error` and logs it without the token
    - `app.include_router(telegram.make_router(tg, tg_secret))` next to the WhatsApp router
      (import the WhatsApp `make_router` under its own name to avoid the clash)
    - `/health` adds `"telegram"` (`polling`, `webhook`, `off`) and `telegram_error` when set
    - Lab "Voice box": Telegram (`f"Telegram bot ({mode})"`, alive unless `telegram_error`,
      swap `TELEGRAM_BOT_TOKEN, TELEGRAM_MODE`), else the current WhatsApp entry, else
      `"off: web chat only"`
    - _Requirements: 2.1, 2.6, 2.7, 3.1, 3.2, 10.3, 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 14.1_
  - [x] 7.2 Write unit tests for app wiring with `create_app(":memory:", telegram_api=FakeAPI())`
    and `monkeypatch` env
    - Building the app makes no `FakeAPI` calls until `with TestClient(app)` enters the lifespan (2.7)
    - Polling mode starts the poller (`delete_webhook` seen) and stops it on exit (2.1, 2.6)
    - Webhook mode calls `set_webhook` with `KHATA_PUBLIC_URL + "/webhook/telegram"` and
      `webhook_token(secret)` (3.1); a missing `KHATA_PUBLIC_URL` and a failing `set_webhook`
      show `telegram_error` in `/health` (3.2)
    - Empty salt gives `"telegram": "off"` (10.3); `/health` values for each mode (12.3)
    - Lab Voice box for Telegram, WhatsApp-only, and neither (12.4, 12.5, 12.6)
    - Existing tests in `tests/test_whatsapp.py` still pass unchanged (14.1)
    - _Requirements: 2.1, 2.6, 2.7, 3.1, 3.2, 10.3, 12.1, 12.3, 12.4, 12.5, 12.6, 14.1_

- [x] 8. Telegram `/export` sends the Excel file
  - [x] 8.1 Add `export_doc` to `M` in `khata/flow.py` and the Telegram branch in `Bot._fresh`
    - Texts in en, hi and pa from the design; in the export branch, before the WhatsApp check:
      `if source == "telegram": return self._r(self.say("export_doc", lang), export=True)`
    - _Requirements: 9.3_
  - [x] 8.2 Send the document from `TelegramAdapter._send`
    - When `r.get("export")` and `self.export`: `api.send_document(chat_id, "khata.xlsx",
      self.export(owner), caption="khata.xlsx")`; `create_app` passes
      `export=lambda o: build_xlsx(ledger, o)`
    - _Requirements: 9.4_
  - [x] 8.3 Write unit tests for export through Telegram
    - `export_doc` exists in en, hi and pa; `Bot.handle(o, "export", source="telegram")` sets
      `export=True`; web and WhatsApp export replies are unchanged (9.3)
    - `/export` in a Telegram chat sends one document named `khata.xlsx` whose bytes start with `PK`
      and contain only that chat's entries (9.4, 10.8)
    - _Requirements: 9.3, 9.4, 10.8_

- [x] 9. Sheet mirror and khata-mcp owner from a Telegram chat id
  - [x] 9.1 Resolve the sheet owner in `create_app`
    - `KHATA_SHEET_OWNER_TELEGRAM_ID` with a salt → `owner_for_chat` (a `ValueError` is logged and
      leaves the mirror off); else `KHATA_SHEET_OWNER_NUMBER` → `owner_for`; else no mirror
    - _Requirements: 11.1, 11.2_
  - [x] 9.2 Update `resolve_owner` in `khata/mcp_server.py`
    - Order: `KHATA_MCP_OWNER` → `KHATA_SHEET_OWNER_TELEGRAM_ID` + `KHATA_OWNER_SALT` →
      `KHATA_SHEET_OWNER_NUMBER` + `WA_APP_SECRET` → `SystemExit` naming all three options;
      update the module docstring; add no tools
    - _Requirements: 11.3, 11.4_
  - [x] 9.3 Write unit tests for owner precedence
    - Sheet owner from a Telegram id, Telegram winning over a WhatsApp number, and a bad id
      giving `"sheet": "off"` (stub `sheet_from_env` via `monkeypatch`) (11.1, 11.2)
    - `resolve_owner` for each branch and the `SystemExit` message (11.3); the existing MCP test
      still lists only read-only tools (11.4)
    - _Requirements: 11.1, 11.2, 11.3, 11.4_

- [x] 10. Config, docs and steering
  - [x] 10.1 Update `.env.example` and `render.yaml`
    - `.env.example`: Telegram block with blank `TELEGRAM_BOT_TOKEN`, `TELEGRAM_MODE`,
      `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_ALLOWED_CHAT_IDS`, `KHATA_OWNER_SALT`, and
      `KHATA_SHEET_OWNER_TELEGRAM_ID` next to `KHATA_SHEET_OWNER_NUMBER`; label the WhatsApp block optional
    - `render.yaml`: `TELEGRAM_BOT_TOKEN` (`sync: false`), `TELEGRAM_MODE` (`value: webhook`),
      `TELEGRAM_WEBHOOK_SECRET` and `KHATA_OWNER_SALT` (`generateValue: true`),
      `KHATA_SHEET_OWNER_TELEGRAM_ID` (`sync: false`); keep the WhatsApp keys
    - _Requirements: 13.1, 13.2, 13.3_
  - [x] 10.2 Write smoke tests for the config files in `tests/test_deploy.py`
    - `render.yaml` declares the Telegram keys, has no plain `value` on secrets, and uses
      `generateValue` for the webhook secret and salt; `.env.example` lists every variable with a blank value
    - _Requirements: 13.2, 13.3_
  - [x] 10.3 Write `docs/TELEGRAM.md`
    - Creating the bot with @BotFather, filling `.env`, restarting with `--env-file .env`,
      `/id` for the chat id and `KHATA_SHEET_OWNER_TELEGRAM_ID`, polling vs webhook, a test
      table (text, Yes tap, old Yes tap, voice, photo, `/export`), 409 Conflict troubleshooting,
      and the note that Render ignores new `sync: false` keys on existing Blueprints
    - _Requirements: 13.4_
  - [x] 10.4 Update `docs/SETUP.md`, `docs/SHEETS.md`, `README.md`, `docs/VIDEO.md`,
    `docs/DEVPOST.md` and `docs/KIRO.md` to name Telegram as the primary chat channel
    - _Requirements: 13.4, 14.2_
  - [x] 10.5 Mark WhatsApp optional and update steering
    - Top of `docs/WHATSAPP.md`: WhatsApp is optional because new Meta accounts may not get access
    - `.kiro/steering/khata.md`: the adapter rule names voice, bill, WhatsApp and Telegram; add the
      stale-button rule (only the latest keyboard per chat is honoured; older keyboards are cleared
      and their taps change nothing)
    - _Requirements: 14.3, 14.4_

- [x] 11. Final checkpoint - Ensure all tests pass
  - Run `python -m pytest -q` (73 passed, 1 skipped before this feature, plus the new tests)
  - Run `python -m evals.run`: golden 32/32 and dev2 15/15 (unchanged, `khata/extract.py` is not
    touched); report the blind score as is and never tune on `evals/blind.json`
  - Run `scripts/smoke.sh` against a running server, because `/health` and a new endpoint changed;
    `scripts/contrast.py` is not needed (no CSS change)
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- No sub-task is optional: the property and unit tests guard the confirm step, owner isolation
  and the stale-button rule.
- Each property test uses Hypothesis with `@settings(max_examples=100, deadline=None)` and a tag
  comment `# Feature: telegram-channel, Property N: <title>`; each property has exactly one test.
- Not tested automatically: constant-time comparison (3.5, code review of `hmac.compare_digest`),
  the free-services policy (13.1) and docs wording (13.4, 14.3, 14.4).
- Séance reminders keep their `wa.me` links (14.2); no change to `khata/reminders.py`.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1"] },
    { "id": 1, "tasks": ["1.2", "2.2"] },
    { "id": 2, "tasks": ["1.3", "2.3"] },
    { "id": 3, "tasks": ["2.4"] },
    { "id": 4, "tasks": ["2.5"] },
    { "id": 5, "tasks": ["3.1"] },
    { "id": 6, "tasks": ["3.2"] },
    { "id": 7, "tasks": ["3.3"] },
    { "id": 8, "tasks": ["3.4"] },
    { "id": 9, "tasks": ["3.5"] },
    { "id": 10, "tasks": ["3.6"] },
    { "id": 11, "tasks": ["3.7"] },
    { "id": 12, "tasks": ["3.8"] },
    { "id": 13, "tasks": ["3.9"] },
    { "id": 14, "tasks": ["3.10"] },
    { "id": 15, "tasks": ["4.1"] },
    { "id": 16, "tasks": ["4.2"] },
    { "id": 17, "tasks": ["4.3"] },
    { "id": 18, "tasks": ["4.4"] },
    { "id": 19, "tasks": ["4.5"] },
    { "id": 20, "tasks": ["4.6"] },
    { "id": 21, "tasks": ["5.1"] },
    { "id": 22, "tasks": ["5.2"] },
    { "id": 23, "tasks": ["5.3"] },
    { "id": 24, "tasks": ["5.4"] },
    { "id": 25, "tasks": ["5.5"] },
    { "id": 26, "tasks": ["5.6"] },
    { "id": 27, "tasks": ["6"] },
    { "id": 28, "tasks": ["7.1", "8.1"] },
    { "id": 29, "tasks": ["7.2", "8.2"] },
    { "id": 30, "tasks": ["8.3", "9.1", "9.2"] },
    { "id": 31, "tasks": ["9.3", "10.1", "10.3", "10.4", "10.5"] },
    { "id": 32, "tasks": ["10.2"] },
    { "id": 33, "tasks": ["11"] }
  ]
}
```
