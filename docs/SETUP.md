# Setup checklist (all free, no card)

The app works after step 1 alone, in demo mode. Steps 2 to 4 are tested locally first; Render comes last.

## 1. Run locally
```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # then fill in values; .env is gitignored
.venv/bin/uvicorn khata.app:app --env-file .env --port 8765
```
Open http://localhost:8765. `scripts/smoke.sh` in another terminal should print `SMOKE OK`.

## 2. Groq key (real voice notes and bill photos)
1. Sign up at https://console.groq.com (free tier, no card). **API Keys > Create**.
2. Put it in `.env` as `GROQ_API_KEY=gsk_...` and restart. The page header should say **live**.
3. When the free-tier limit is hit, the app tells the user to type instead.

## 3. Telegram bot
See **[TELEGRAM.md](TELEGRAM.md)**. Locally the app polls Telegram, so no tunnel is needed.
(WhatsApp is optional: [WHATSAPP.md](WHATSAPP.md). New Meta accounts may not get access.)

## 4. Google Sheet mirror
See **[SHEETS.md](SHEETS.md)**.

## 5. Voice eval
Record about 15 short voice notes (mixed Hindi, Punjabi, Hinglish, English) and send them through the web chat
or WhatsApp. Note the "Heard:" text and whether the confirm step was right. Report the count, not a guess.

## 6. Render (public URL for judges, last)
1. Sign up at https://render.com with GitHub. **New > Blueprint**, pick the repo; it reads `render.yaml`.
2. Paste the same values from `.env` into Render's environment. Set `KHATA_PUBLIC_URL` to the Render URL.
3. `scripts/smoke.sh https://<name>.onrender.com` should print `SMOKE OK`.
4. Keep it awake: in GitHub **Settings > Secrets and variables > Actions > Variables**, add `KHATA_URL`. The
   `keepalive` workflow pings every 10 minutes. Add a cron-job.org ping as backup.
5. Set `TELEGRAM_MODE=webhook` on Render and stop the local server; the app registers its own Telegram webhook.
6. Keep it running until **January 29, 2027**.

## 7. Kiro MCP (optional)
Copy `docs/kiro-mcp.example.json` into `.kiro/settings/mcp.json`, set `KHATA_MCP_OWNER`, and set `disabled` to `false`.
