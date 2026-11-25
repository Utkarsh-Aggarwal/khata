# Google Sheets: one per shop (free)

Each shopkeeper keeps their khata in their **own** Google Sheet, connected from Telegram. The sheet is
two-way (edit Udhaar, Entries, Rates and Customers there; Bills and Dues are calculated). On free hosting
the server's disk is wiped on restart, so every shop's ledger is rebuilt from its sheet on boot.
Browser demo accounts are never mirrored.

## For shopkeepers (in Telegram)

| Send | What happens |
|---|---|
| `/start` (first time) | Greets, lists the three steps, and sends the bot's email alone in a message so a long-press copies it. Paste the sheet's link back (no command needed), or tap **Skip** |
| `/sheet` | Shows your sheet's link, or the same steps if none is connected |
| `/sheet <link>` | Connects that sheet. An empty sheet gets your khata right away. A sheet that already has a khata (from an earlier phone or server) is read into an empty chat |
| `/sheet <new link>` | Moves to a new sheet. The old sheet is left as it is |
| `/sheet off` | Stops writing to the sheet. The sheet is left as it is |
| `/reset` | Deletes this chat's khata after a confirm tap, disconnects the sheet and keeps it as a backup. Then `/sheet <new link>` starts fresh |

Steps: open https://sheets.new, **Share** it with the service account email as **Editor**, send `/sheet` and
the link. Only the shop's main phone can change the sheet or reset; phones added with `/invite` can view the
link.

The bot refuses, and changes nothing, when:
- the sheet isn't shared with the service account, or is shared as Viewer only;
- both the sheet and this chat already have records. Importing would mix two khatas and redrawing would
  erase the sheet's. Use an empty sheet, or `/reset` first.

## For the operator (once)

### A. Create a service account (10 minutes)
1. Go to https://console.cloud.google.com and sign in. No billing account is needed.
2. Top bar project picker > **New project**, name it `khata`, create, and select it.
3. Search **Google Sheets API**, open it, click **Enable**.
4. **IAM & Admin > Service accounts > Create service account**. Name it `khata-sheet`. Skip roles, **Done**.
5. The new service account > **Keys > Add key > Create new key > JSON**. Treat the file like a password.

If key creation is blocked by an organisation policy, use a personal Gmail account.

### B. Settings
```
GOOGLE_APPLICATION_CREDENTIALS=sa.json          # locally; on Render paste the JSON into GOOGLE_SERVICE_ACCOUNT_JSON
KHATA_REGISTRY_SHEET_ID=<admin sheet id>         # optional; defaults to KHATA_SHEET_ID
KHATA_SHEET_ID=<sheet id>                        # optional default shop, with:
KHATA_SHEET_OWNER_TELEGRAM_ID=123456789          # the number the bot replies to /id
```
`KHATA_OWNER_SALT` must never change: shop ids are derived from it.

**Which shop uses which sheet** is kept in the database and in a **Shops** tab of the admin sheet
(`KHATA_REGISTRY_SHEET_ID`), because the database doesn't survive a restart on free hosting. The Shops tab
holds hashed shop ids and sheet ids only. The bot never touches any other tab you add yourself.

### C. Check
`/health` shows `"sheet": "on"`, `"sheets": <shops connected>`, `"sheet_paused"`, `"sheet_pending"` and
`"sheet_boot"` (one result per shop, e.g. `{"restored": 12, "skipped": 0}` after a restart). It never shows
shop ids.

## Safety rules in the code (`khata/sheets.py`)
- A shop is restored only if it has no records yet; other shops' records don't count.
- If a restored Ref is already used by another shop, the record gets a new Ref, and the sheet's old Ref is
  mapped to it so the first sync doesn't add the row twice.
- If restore fails, that shop's mirror pauses and never redraws its sheet from an empty database.
- Disconnecting waits for a sync in progress, so `/reset` can never blank the old sheet.
- Deleting more than half of a tab at once is treated as an accident and the rows are put back.
- One thread serves every shop: right after a change it redraws only sheets that are behind; every 15
  seconds it reads all of them for edits. The Sheets API's free quota (300 reads a minute per project) fits
  about 60 active shops at that rate.
