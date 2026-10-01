# WhatsApp customer automation bot

Flask server using Meta's WhatsApp Cloud API. Features: button menu, order-status lookup,
business hours, human handoff, SQLite chat log, duplicate-message protection.

## 1. Run locally in VS Code

```bash
# open the folder in VS Code, then open the terminal (Ctrl + `)
python -m venv venv

# activate it
venv\Scripts\activate          # Windows
source venv/bin/activate       # Mac / Linux

pip install -r requirements.txt
copy .env.example .env         # Windows   (Mac/Linux: cp .env.example .env)
```

Edit `.env` (fill in values from step 2), then:

```bash
python app.py
```

Open http://localhost:5000, you should see "WhatsApp bot is running".

## 2. Meta setup (one time)

1. Go to https://developers.facebook.com, create an app, type "Business", add the WhatsApp product.
2. WhatsApp > API Setup: copy the temporary access token and the Phone number ID into `.env`.
   Add your own phone number as a test recipient.
3. Meta cannot reach localhost, so expose it with a tunnel (new terminal):
   ```bash
   ngrok http 5000
   ```
   Copy the https URL it shows.
4. WhatsApp > Configuration > Webhook: set Callback URL to `https://YOUR-NGROK-URL/webhook`
   and Verify token to the same value as `VERIFY_TOKEN` in `.env`. Click Verify and save.
5. Subscribe to the `messages` webhook field.
6. From your phone, message the test number: send "hi".

## 3. Useful URLs

- `/chats?key=ADMIN_KEY` shows the last 100 messages
- `/resume/<phone>?key=ADMIN_KEY` gives a handed-off chat back to the bot

## 4. Going to production

- The temporary token expires in about 24 hours. Create a System User permanent token in Meta Business Settings.
- Deploy to a host with a fixed https URL (Render, Railway, a VPS) and run with `gunicorn app:app`.
- Add a real business phone number and get it verified.
- To message customers first (offers, reminders) you need Meta-approved message templates.
- Replace the `ORDERS` dictionary in `app.py` with your real database or API.
- Check Meta's current rate card for per-message costs.

## 5. Ideas to extend

- Plug an AI model into the fallback branch of `handle_message` for free-text questions.
- Send a Slack or email alert where the code says `TODO: notify your team`.
- Replace SQLite with PostgreSQL when you have real volume.
