"""
Unified backend for instant Stop/Adjust actions.
- Telegram sends POST webhooks here the instant a button is tapped.
- Gmail links point here directly (GET for stop / to load the adjust form,
  POST for submitting a new price) — no browser passcode, no stale local token,
  since the GitHub token lives only here as a Vercel environment variable.
"""

import base64
import json
import os
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_OWNER = os.environ.get("GITHUB_OWNER")
GITHUB_REPO = os.environ.get("GITHUB_REPO")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

GITHUB_API = "https://api.github.com"


def github_request(method, path, body=None):
    url = f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_json_file(path):
    data = github_request("GET", f"contents/{path}")
    content = json.loads(base64.b64decode(data["content"]).decode("utf-8"))
    return content, data["sha"]


def put_json_file(path, content_obj, sha, message):
    body = {
        "message": message,
        "content": base64.b64encode(json.dumps(content_obj, indent=2).encode("utf-8")).decode("utf-8"),
    }
    if sha:
        body["sha"] = sha
    github_request("PUT", f"contents/{path}", body)


def telegram_call(method, payload):
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        urllib.request.urlopen(req)
    except Exception:
        pass


def stop_tracking(asin):
    watchlist, sha = get_json_file("watchlist.json")
    updated = [i for i in watchlist if i["asin"].upper() != asin.upper()]
    if len(updated) < len(watchlist):
        put_json_file("watchlist.json", updated, sha, f"Stop tracking {asin} (instant action)")
        return True
    return False


def adjust_price(asin, new_price):
    watchlist, sha = get_json_file("watchlist.json")
    found = False
    for item in watchlist:
        if item["asin"].upper() == asin.upper():
            item["max_price"] = new_price
            found = True
    if found:
        put_json_file("watchlist.json", watchlist, sha, f"Adjust price for {asin} (instant action)")
    return found


def get_pending_adjusts():
    try:
        return get_json_file("pending_adjust.json")
    except Exception:
        return {}, None


def set_pending_adjust(chat_id, asin):
    pending, sha = get_pending_adjusts()
    pending[str(chat_id)] = asin
    put_json_file("pending_adjust.json", pending, sha, "Set pending adjust")


def clear_pending_adjust(chat_id):
    pending, sha = get_pending_adjusts()
    pending.pop(str(chat_id), None)
    put_json_file("pending_adjust.json", pending, sha, "Clear pending adjust")


def handle_telegram_update(update):
    cq = update.get("callback_query")
    if cq:
        data = cq.get("data", "")
        chat_id = cq["message"]["chat"]["id"]
        cq_id = cq["id"]
        if data.startswith("stop:"):
            asin = data.split(":", 1)[1]
            try:
                ok = stop_tracking(asin)
                telegram_call("answerCallbackQuery", {
                    "callback_query_id": cq_id,
                    "text": "Stopped tracking." if ok else "Already removed.",
                })
                if ok:
                    telegram_call("sendMessage", {"chat_id": chat_id, "text": f"Stopped tracking {asin}."})
            except Exception as e:
                telegram_call("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"Error: {e}"})
        elif data.startswith("adjust:"):
            asin = data.split(":", 1)[1]
            try:
                set_pending_adjust(chat_id, asin)
                telegram_call("answerCallbackQuery", {"callback_query_id": cq_id})
                telegram_call("sendMessage", {
                    "chat_id": chat_id,
                    "text": f"Reply with the new alert price for {asin} (just the number, e.g. 49.99).",
                })
            except Exception as e:
                telegram_call("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"Error: {e}"})
        return

    msg = update.get("message")
    if msg and "text" in msg:
        chat_id = msg["chat"]["id"]
        try:
            pending, _ = get_pending_adjusts()
        except Exception:
            pending = {}
        asin = pending.get(str(chat_id))
        if not asin:
            return
        try:
            new_price = float(msg["text"].strip().replace("$", ""))
        except ValueError:
            telegram_call("sendMessage", {
                "chat_id": chat_id,
                "text": "That doesn't look like a number — reply with just the price, e.g. 49.99.",
            })
            return
        try:
            found = adjust_price(asin, new_price)
            clear_pending_adjust(chat_id)
            telegram_call("sendMessage", {
                "chat_id": chat_id,
                "text": (f"Updated {asin} to alert at or below ${new_price:.2f}."
                         if found else f"Couldn't find {asin} anymore."),
            })
        except Exception as e:
            telegram_call("sendMessage", {"chat_id": chat_id, "text": f"Error: {e}"})


PAGE_STYLE = """
<style>
  body { font-family: -apple-system, sans-serif; background:#0f1115; color:#e8e9ec;
         display:flex; align-items:center; justify-content:center; min-height:100vh; margin:0; }
  .card { background:#171a21; border:1px solid #2a2e38; border-radius:12px; padding:24px; max-width:360px; text-align:center; }
  input { width:100%; padding:10px; border-radius:8px; border:1px solid #2a2e38; background:#10131a; color:#e8e9ec; font-size:16px; margin:12px 0; box-sizing:border-box; }
  button { width:100%; padding:12px; border-radius:8px; border:none; background:#4f8cff; color:white; font-weight:600; font-size:15px; cursor:pointer; }
</style>
"""


def html_page(body_html):
    return f"<!DOCTYPE html><html><head><meta charset='utf-8'>{PAGE_STYLE}</head><body><div class='card'>{body_html}</div></body></html>"


class handler(BaseHTTPRequestHandler):
    def _send_html(self, html, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        content_type = self.headers.get("Content-Type", "")

        # Telegram always sends JSON
        if "application/json" in content_type:
            try:
                update = json.loads(raw.decode("utf-8"))
                handle_telegram_update(update)
            except Exception:
                pass
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return

        # Email adjust-price form submits as regular form data
        params = urllib.parse.parse_qs(raw.decode("utf-8"))
        asin = params.get("asin", [""])[0]
        price_raw = params.get("price", [""])[0]
        try:
            new_price = float(price_raw)
            found = adjust_price(asin, new_price)
            msg = f"Updated — alert price for {asin} is now ${new_price:.2f}." if found else "Couldn't find that product anymore."
        except ValueError:
            msg = "That doesn't look like a valid price."
        self._send_html(html_page(f"<p>{msg}</p>"))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        asin = params.get("asin", [""])[0]
        action = params.get("do", [""])[0]

        if not asin or action not in ("stop", "adjust"):
            self._send_html(html_page("<p>Missing or invalid parameters.</p>"), status=400)
            return

        if action == "stop":
            try:
                ok = stop_tracking(asin)
                msg = f"Stopped tracking {asin}." if ok else "Already removed."
                self._send_html(html_page(f"<p>{msg}</p>"))
            except Exception as e:
                self._send_html(html_page(f"<p>Error: {e}</p>"), status=500)
            return

        # action == "adjust" — show a tiny form, no login needed
        form = f"""
        <p>New alert price for {asin}:</p>
        <form method="POST" action="/api/action">
          <input type="hidden" name="asin" value="{asin}">
          <input type="number" step="0.01" name="price" placeholder="49.99" required>
          <button type="submit">Update price</button>
        </form>
        """
        self._send_html(html_page(form))
