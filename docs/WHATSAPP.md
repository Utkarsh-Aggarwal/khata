# WhatsApp test number (optional)

> Telegram is the main channel (see TELEGRAM.md). New Meta accounts may not get WhatsApp API access; this guide is kept for those who do.

Meta gives every developer app a free test number that can message up to 5 verified phones.
Meta's dashboard changes its labels often; if a button name differs, look for the nearest match.

## A. Create the app (10 minutes)
1. Go to https://developers.facebook.com and log in with your Facebook account. Accept the developer terms
   and verify your account if asked.
2. **My Apps > Create App**. Choose the use case **Connect with customers through WhatsApp**
   (or type **Business**), name it `Khata`, and create it. Skip linking a business portfolio if it lets you;
   otherwise create a free one.
3. In the left menu open **WhatsApp > API Setup**. You'll see:
   - a **test number** (the "From" number),
   - a **Phone number ID** under it,
   - a **Temporary access token** button.
4. Under **To**, click **Manage phone number list**, add **your own WhatsApp number**, and enter the code
   WhatsApp sends you.
5. Click **Send message** on that page once. A "hello_world" message should arrive on your phone. This opens
   the conversation; reply anything from your phone so the 24-hour reply window opens.

## B. Copy four values into `.env`
| `.env` variable | Where to find it |
|---|---|
| `WA_TOKEN` | **API Setup > Generate access token** (expires in about 24 hours) |
| `WA_PHONE_ID` | **API Setup**, "Phone number ID" (digits, not the phone number) |
| `WA_APP_SECRET` | **App settings > Basic > App secret > Show** |
| `WA_VERIFY_TOKEN` | Any string you invent, e.g. `khata-ghost-2026` |

Also set `KHATA_PUBLIC_URL=` to the tunnel URL from step C once you have it. Restart the server after editing `.env`.

## C. Open a free tunnel so Meta can reach your Mac
```
brew install cloudflared
cloudflared tunnel --url http://localhost:8765
```
It prints a URL like `https://random-words.trycloudflare.com`. Keep that terminal open. The URL changes every
time you restart cloudflared, so update the webhook (step D) if you restart it.

Check it works: open `https://random-words.trycloudflare.com/health` in a browser.

## D. Connect the webhook
1. In the Meta app: **WhatsApp > Configuration > Webhook > Edit**.
2. **Callback URL**: `https://random-words.trycloudflare.com/webhook/whatsapp`
3. **Verify token**: the same `WA_VERIFY_TOKEN` as in `.env`. Click **Verify and save**.
   If it fails: the server must be running with the new `.env`, and the tunnel must be open.
4. Under **Webhook fields**, click **Manage** and subscribe to **messages**.

## E. Test from your phone (send these to the test number)
| Send | Expect |
|---|---|
| `hi` | Help text |
| `Ramesh ko 500 udhaar` | Confirm message with **Yes / No** buttons; tap **Yes** to save |
| A Hindi voice note: "रमेश को पाँच सौ रुपये उधार" | "🎙️ Heard: …" then Yes / No buttons |
| A photo of any shop bill | "🧾 Read from the bill: ₹…", then "Who is the customer?" |
| `remind` | Drafted reminder links |

## Troubleshooting
- **No reply at all**: check the server terminal for `POST /webhook/whatsapp`. If there's no request, the
  webhook field **messages** isn't subscribed, or the tunnel URL is old.
- **`401` in the server log**: `WA_APP_SECRET` is wrong (it's the *App secret*, not the token).
- **`503`**: `WA_TOKEN` or `WA_APP_SECRET` is missing from `.env`, or the server wasn't restarted.
- **Replies stopped the next day**: the temporary token expired. Generate a new one and restart.
- **"Re-engagement" error from Meta**: more than 24 hours since your phone last messaged the test number;
  send any message from your phone first.
- Voice notes and bills need `GROQ_API_KEY` in `.env`.

Your WhatsApp ledger is separate from the browser demo account: it's keyed to a salted hash of your number.
