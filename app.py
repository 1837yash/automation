"""
WhatsApp customer automation bot (Meta WhatsApp Cloud API + Flask)

Flow:
Customer -> WhatsApp -> Meta webhook -> this server -> bot logic
         -> auto reply  OR  human handoff -> reply via Graph API -> log in SQLite
"""
import hashlib
import hmac
import os
import sqlite3
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request

load_dotenv()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "my_verify_token")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
APP_SECRET = os.getenv("APP_SECRET", "")  # optional but recommended
ADMIN_KEY = os.getenv("ADMIN_KEY", "change-me")
GRAPH_VERSION = os.getenv("GRAPH_VERSION", "v23.0")
DB_PATH = os.getenv("DB_PATH", "chats.db")

GRAPH_URL = f"https://graph.facebook.com/{GRAPH_VERSION}/{PHONE_NUMBER_ID}/messages"

app = Flask(__name__)

# Demo data: replace with a call to your real order system / database
ORDERS = {
    "1001": "Packed and will ship today.",
    "1002": "Out for delivery. Expected by 6 PM.",
    "1003": "Delivered yesterday.",
}


# ----------------------------------------------------------------- database
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wamid TEXT UNIQUE,
                wa_id TEXT,
                direction TEXT,
                body TEXT,
                ts TEXT)"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                wa_id TEXT PRIMARY KEY,
                state TEXT DEFAULT 'idle',
                handoff INTEGER DEFAULT 0)"""
        )


def log_message(wa_id, direction, body, wamid=None):
    """Returns False if this wamid was already processed (Meta retries webhooks)."""
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO messages (wamid, wa_id, direction, body, ts) VALUES (?,?,?,?,?)",
                (wamid, wa_id, direction, body, datetime.now(timezone.utc).isoformat()),
            )
        return True
    except sqlite3.IntegrityError:
        return False
    


def get_session(wa_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE wa_id=?", (wa_id,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO sessions (wa_id) VALUES (?)", (wa_id,))
            return {"state": "idle", "handoff": 0}
        return {"state": row["state"], "handoff": row["handoff"]}


def set_session(wa_id, state=None, handoff=None):
    get_session(wa_id)
    with db() as conn:
        if state is not None:
            conn.execute("UPDATE sessions SET state=? WHERE wa_id=?", (state, wa_id))
        if handoff is not None:
            conn.execute("UPDATE sessions SET handoff=? WHERE wa_id=?", (handoff, wa_id))


# ------------------------------------------------------------------ sending
def _post(payload):
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(GRAPH_URL, json=payload, headers=headers, timeout=15)
        if r.status_code >= 400:
            print("Send error:", r.status_code, r.text)
        return r
    except requests.RequestException as exc:
        print("Send exception:", exc)


def send_text(to, text):
    _post({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    })
    log_message(to, "out", text)


def send_menu(to, intro="Hi! How can I help you today?"):
    _post({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": intro},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": "order_status", "title": "Order status"}},
                    {"type": "reply", "reply": {"id": "hours", "title": "Business hours"}},
                    {"type": "reply", "reply": {"id": "human", "title": "Talk to a person"}},
                ]
            },
        },
    })
    log_message(to, "out", f"[menu] {intro}")


# ---------------------------------------------------------------- bot logic
def handle_message(wa_id, text):
    session = get_session(wa_id)

    # A human agent owns this chat: the bot stays silent.
    if session["handoff"]:
        return

    key = text.strip().lower()

    if session["state"] == "awaiting_order":
        status = ORDERS.get(key.replace("#", ""))
        set_session(wa_id, state="idle")
        if status:
            send_text(wa_id, f"Order #{key.replace('#', '')}: {status}")
        else:
            send_text(wa_id, "Sorry, I couldn't find that order number.")
        send_menu(wa_id, "Anything else I can help with?")
        return

    if key in ("hi", "hello", "hey", "menu", "start"):
        send_menu(wa_id)
    elif key in ("order_status", "order status"):
        set_session(wa_id, state="awaiting_order")
        send_text(wa_id, "Please send your order number (try 1001).")
    elif key in ("hours", "business hours"):
        send_text(wa_id, "We are open Monday to Saturday, 10 AM to 7 PM.")
        send_menu(wa_id, "Anything else I can help with?")
    elif key in ("human", "talk to a person", "agent"):
        set_session(wa_id, handoff=1)
        send_text(wa_id, "Connecting you to a team member. They will reply here shortly.")
        print(f"HANDOFF requested by {wa_id}")  # TODO: notify your team (email/Slack)
    else:
        send_menu(wa_id, "Sorry, I didn't understand that. Please choose an option:")


# ----------------------------------------------------------------- webhooks
def valid_signature(raw_body, header):
    if not APP_SECRET:
        return True  # skipped if APP_SECRET is not configured
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


@app.get("/webhook")
def verify():
    """Meta calls this once when you register the webhook URL."""
    if (
        request.args.get("hub.mode") == "subscribe"
        and request.args.get("hub.verify_token") == VERIFY_TOKEN
    ):
        return request.args.get("hub.challenge", ""), 200
    return "Verification failed", 403


@app.post("/webhook")
def receive():
    if not valid_signature(request.get_data(), request.headers.get("X-Hub-Signature-256")):
        return "Invalid signature", 403

    data = request.get_json(silent=True) or {}
    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                for msg in change.get("value", {}).get("messages", []):
                    wa_id = msg["from"]
                    kind = msg.get("type")

                    if kind == "text":
                        text = msg["text"]["body"]
                    elif kind == "interactive":
                        reply = msg["interactive"]
                        text = (reply.get("button_reply") or reply.get("list_reply"))["id"]
                    else:
                        text = ""
                        send_text(wa_id, "Sorry, I can only read text messages for now.")

                    if not log_message(wa_id, "in", text or f"[{kind}]", msg.get("id")):
                        continue  # duplicate delivery
                    if text:
                        handle_message(wa_id, text)
    except Exception as exc:  # never fail the webhook, Meta would keep retrying
        print("Webhook error:", exc)

    return jsonify(status="ok"), 200


# -------------------------------------------------------------- admin tools
def _admin_ok():
    return request.args.get("key") == ADMIN_KEY


@app.get("/chats")
def chats():
    """View recent messages: /chats?key=YOUR_ADMIN_KEY"""
    if not _admin_ok():
        return "Forbidden", 403
    with db() as conn:
        rows = conn.execute(
            "SELECT wa_id, direction, body, ts FROM messages ORDER BY id DESC LIMIT 100"
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/resume/<wa_id>")
def resume(wa_id):
    """After an agent finishes, give the chat back to the bot."""
    if not _admin_ok():
        return "Forbidden", 403
    set_session(wa_id, state="idle", handoff=0)
    return jsonify(status="bot resumed", wa_id=wa_id)


@app.get("/")
def health():
    return "WhatsApp bot is running", 200


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
