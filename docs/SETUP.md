# Setup checklist (all free, no card)

Do these in order. Each step says what it unlocks. The app works after step 1 alone, in demo mode.

## 1. Push to GitHub (unlocks CI and Render)
1. Create a **public** repo on github.com (MIT licence is already in the repo).
2. `git remote add origin https://github.com/<you>/khata.git`
3. `git push -u origin main`
4. Check the **Actions** tab: `ci` should go green. It also runs the ffmpeg test that skips on your Mac.

## 2. Groq key (unlocks real voice notes and bill photos)
1. Sign up at https://console.groq.com (free tier, no card).
2. **API Keys > Create**. Copy the key (starts with `gsk_`).
3. Test locally: `GROQ_API_KEY=gsk_... .venv/bin/uvicorn khata.app:app --port 8765`. The header should say **live**.
4. Free-tier limits apply. When hit, the app tells the user to type instead.

## 3. Render (unlocks the public URL judges use)
1. Sign up at https://render.com with GitHub (free, no card).
2. **New > Blueprint**, pick the repo. Render reads `render.yaml`.
3. Fill `GROQ_API_KEY` and `KHATA_PUBLIC_URL` (your `https://<name>.onrender.com`). Leave the rest blank for now.
4. After deploy: `scripts/smoke.sh https://<name>.onrender.com` should print `SMOKE OK`.
5. **Keep it awake** (free services sleep after 15 idle minutes, and the first visit then takes about a minute):
   - GitHub repo **Settings > Secrets and variables > Actions > Variables**: add `KHATA_URL` = your Render URL. The `keepalive` workflow pings every 10 minutes.
   - Backup: a free job at https://cron-job.org hitting `<url>/health` every 10 minutes.
6. Render's free plan includes 750 hours a month, enough for one always-on service. Keep it running until **January 29, 2027**.

## 4. WhatsApp test number (unlocks the demo video's phone scenes)
1. https://developers.facebook.com > **My Apps > Create App > Business**, add the **WhatsApp** product.
2. **API Setup** gives a test number, a temporary token and a phone number id. Add your own phone as a recipient.
3. In Render, set `WA_TOKEN`, `WA_PHONE_ID`, `WA_APP_SECRET` (App settings > Basic) and any `WA_VERIFY_TOKEN` you choose.
4. **Configuration > Webhook**: URL `https://<name>.onrender.com/webhook/whatsapp`, the same verify token. Subscribe to **messages**.
5. The temporary token expires after about 24 hours. Generate a fresh one right before recording.

## 5. Google Sheet (unlocks the sheet mirror and restore)
1. https://console.cloud.google.com: create a project, enable **Google Sheets API**.
2. **IAM > Service accounts > Create**, then **Keys > Add key > JSON**. Download it.
3. Create a Google Sheet with a tab named **Khata**. Share it with the service account's email as **Editor**.
4. In Render: `KHATA_SHEET_ID` (from the sheet URL), `GOOGLE_SERVICE_ACCOUNT_JSON` (paste the whole file), `KHATA_SHEET_OWNER_NUMBER` (your WhatsApp number, digits only).
5. Send an entry on WhatsApp and confirm it: a row should appear within about 15 seconds. `/health` shows `sheet_pending`.

## 6. Voice eval (unlocks honest voice accuracy numbers)
Record about 15 short voice notes (mixed Hindi, Punjabi, Hinglish, English) and send them through the web chat or WhatsApp with the Groq key on. Note the "Heard:" text and whether the confirm step was right. Report the count, not a guess.

## 7. Kiro MCP (optional)
Copy `docs/kiro-mcp.example.json` into `.kiro/settings/mcp.json`, set `KHATA_MCP_OWNER`, and set `disabled` to `false`.
