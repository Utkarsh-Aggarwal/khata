# Google Sheet mirror (free), tested locally

Your WhatsApp ledger gets copied to a Google Sheet. On free hosting, the app also rebuilds the ledger from this
sheet after a restart. It mirrors **one** owner: the WhatsApp number you set below. Browser demo accounts are
not mirrored.

Do the Telegram guide first, because the sheet mirrors the entries you send from your phone.

## A. Create a service account (10 minutes)
1. Go to https://console.cloud.google.com and sign in. If asked, accept the terms; no billing account is needed.
2. Top bar project picker > **New project**, name it `khata`, create, and select it.
3. Search **Google Sheets API** in the top search bar, open it, click **Enable**.
4. Menu > **IAM & Admin > Service accounts > Create service account**. Name it `khata-sheet`.
   Skip the optional role and user steps, then **Done**.
5. Click the new service account > **Keys > Add key > Create new key > JSON**. A `.json` file downloads.
   Treat it like a password: never commit it.
6. Copy the service account's **email** (looks like `khata-sheet@khata-12345.iam.gserviceaccount.com`).

If key creation is blocked by an organisation policy, you're signed in to a work or school Google account;
use a personal Gmail account instead.

## B. Create the sheet
1. Go to https://sheets.new.
2. Rename the tab at the bottom from `Sheet1` to exactly **`Khata`**.
3. **Share** > paste the service account email > role **Editor** > untick "Notify" > **Share**.
4. Copy the sheet id from the URL: `https://docs.google.com/spreadsheets/d/`**`THIS_PART`**`/edit`.

## C. Put three values in `.env`
Move the key file out of Downloads into the repo folder as `sa.json` (it's covered by `.gitignore`), then:
```
KHATA_SHEET_ID=THIS_PART
GOOGLE_APPLICATION_CREDENTIALS=sa.json
KHATA_SHEET_OWNER_TELEGRAM_ID=123456789  # the number the Telegram bot replies to /id
```
`KHATA_OWNER_SALT` must already be set from the Telegram guide and must never change, because your owner id is derived from it.

Restart the server.

## D. Test
1. `http://localhost:8765/health` should show `"sheet": "on"` and `"sheet_boot": {"reason": "new sheet"}` the
   first time. The sheet's row 1 becomes the header: `entry_id, date, customer, kind, rupees, due, status, source`.
2. From your phone, send `Sita ko 300 udhaar` and tap **Yes**. Within about 15 seconds a row appears.
3. Send `undo`. The row's **status** changes to `cancelled`. The row is not deleted.
4. Restore test: stop the server, rename `khata.db` to `khata.db.bak`, start the server again.
   `/health` should show `"sheet_boot": {"restored": N, "skipped": 0}`, and `balance` on WhatsApp shows the same totals.

## Troubleshooting
- **`"sheet": "off"`**: one of the three variables is missing, or `WA_APP_SECRET` is empty.
- **`"sheet": "paused: restore failed"`** with `PermissionError` or `HTTPError`: the sheet isn't shared with
  the service account email as Editor, or the Sheets API isn't enabled.
- **Rows don't appear**: the tab must be named `Khata`. Also check you're messaging from the number in
  `KHATA_SHEET_OWNER_NUMBER`.
- **Don't edit rows by hand** while the app is running. On restore, rows that fail validation are skipped
  and counted, never guessed at.

For Render later: instead of a file, paste the whole JSON into `GOOGLE_SERVICE_ACCOUNT_JSON`.
