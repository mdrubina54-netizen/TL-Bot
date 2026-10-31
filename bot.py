import secrets
import sqlite3
import threading
from contextlib import closing
from functools import wraps
from pathlib import Path

from flask import (Flask, Response, abort, redirect, render_template_string,
                   request, url_for)
from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, InputFile,
                      Update)
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes)

# ---------- settings ----------
BOT_TOKEN = "8989192943:AAFunj7_-Okc213i30DemkBecfVaURdZgPw"
ADMIN_PASSWORD = "rone5225"

BOT_USERNAME = "CYBVERPANELBOT"          # change if BotFather shows another name
BASE_URL = "http://78.154.103.12:16598"
CHANNEL = "@CYBERVPANEL"
CHANNEL_LINK = "https://t.me/CYBERVPANEL"
PORT = 16598

BASE = Path(__file__).parent
UPLOAD_DIR = BASE / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
DB = BASE / "bot.db"

# ---------- database ----------
def q(sql, args=(), commit=False):
    with closing(sqlite3.connect(DB, timeout=10)) as c:
        c.row_factory = sqlite3.Row
        cur = c.execute(sql, args)
        if commit:
            c.commit()
        return cur.fetchall()

def q1(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None

def init_db():
    with closing(sqlite3.connect(DB)) as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS files (
                token TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                path TEXT NOT NULL,
                tg_file_id TEXT,
                created INTEGER DEFAULT (strftime('%s','now'))
            );
            CREATE TABLE IF NOT EXISTS joined (
                user_id INTEGER PRIMARY KEY
            );
        """)
        c.commit()

# ---------- web ----------
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # Telegram bot send limit

def require_admin(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth = request.authorization
        if not auth or auth.password != ADMIN_PASSWORD:
            return Response("Login required", 401,
                            {"WWW-Authenticate": 'Basic realm="admin"'})
        return f(*args, **kwargs)
    return wrapper

PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>File Bot Admin</title>
<style>
body{font-family:system-ui,sans-serif;max-width:720px;margin:2rem auto;padding:0 1rem}
input,button{font-size:1rem;padding:.5rem}
.card{border:1px solid #ccc;border-radius:8px;padding:1rem;margin:.6rem 0}
code{word-break:break-all;font-size:.9rem}
</style></head><body>
<h2>Upload file</h2>
<form method="post" enctype="multipart/form-data" action="{{ url_for('upload') }}">
  <input type="file" name="file" required>
  <button type="submit">Upload</button>
</form>

{% if new_link %}
<div class="card">
  <b>New link:</b><br>
  <code id="new">{{ new_link }}</code><br><br>
  <button onclick="copy('new')">Copy link</button>
</div>
{% endif %}

<h2>Files</h2>
{% for f in files %}
<div class="card">
  <b>{{ f.filename }}</b><br>
  <code id="l{{ f.token }}">{{ f.link }}</code><br><br>
  <button onclick="copy('l{{ f.token }}')">Copy link</button>
</div>
{% else %}
<p>No files yet.</p>
{% endfor %}

<script>
function copy(id){
  navigator.clipboard.writeText(document.getElementById(id).innerText);
  alert("Copied");
}
</script>
</body></html>"""

@app.get("/")
@require_admin
def admin():
    rows = q("SELECT token, filename FROM files ORDER BY created DESC")
    files = [{"token": r["token"], "filename": r["filename"],
              "link": f"{BASE_URL}/f/{r['token']}"} for r in rows]
    return render_template_string(PAGE, files=files,
                                  new_link=request.args.get("new"))

@app.post("/admin/upload")
@require_admin
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        abort(400)
    token = secrets.token_urlsafe(9)          # only letters, digits, - and _
    safe_name = Path(f.filename).name
    path = UPLOAD_DIR / f"{token}_{safe_name}"
    f.save(path)
    q("INSERT INTO files (token, filename, path) VALUES (?, ?, ?)",
      (token, safe_name, str(path)), commit=True)
    return redirect(url_for("admin", new=f"{BASE_URL}/f/{token}"))

@app.get("/f/<token>")
def open_file(token):
    if not q1("SELECT 1 FROM files WHERE token = ?", (token,)):
        abort(404)
    return redirect(f"https://t.me/{BOT_USERNAME}?start={token}")

def run_web():
    app.run(host="0.0.0.0", port=PORT, use_reloader=False)

# ---------- bot ----------
async def is_member(bot, user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(CHANNEL, user_id)
        return m.status in ("member", "administrator", "creator")
    except Exception as e:
        print("membership check failed:", e)  # bot must be admin in the channel
        return False

async def has_access(bot, user_id: int) -> bool:
    if q1("SELECT 1 FROM joined WHERE user_id = ?", (user_id,)):
        return True
    if await is_member(bot, user_id):
        q("INSERT OR IGNORE INTO joined (user_id) VALUES (?)", (user_id,), commit=True)
        return True
    return False

async def send_file(bot, chat_id: int, token: str):
    row = q1("SELECT * FROM files WHERE token = ?", (token,))
    if not row:
        await bot.send_message(chat_id, "This file no longer exists.")
        return
    if row["tg_file_id"]:
        await bot.send_document(chat_id, row["tg_file_id"])
    else:
        with open(row["path"], "rb") as fh:
            msg = await bot.send_document(
                chat_id, InputFile(fh, filename=row["filename"]))
        q("UPDATE files SET tg_file_id = ? WHERE token = ?",
          (msg.document.file_id, token), commit=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    token = context.args[0] if context.args else None
    if not token:
        await update.message.reply_text("Open a file link to get your file.")
        return
    if not q1("SELECT 1 FROM files WHERE token = ?", (token,)):
        await update.message.reply_text("This link is invalid or was removed.")
        return
    if await has_access(context.bot, update.effective_user.id):
        await send_file(context.bot, chat_id, token)
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join channel", url=CHANNEL_LINK)],
        [InlineKeyboardButton("Verify ✅", callback_data=f"check:{token}")],
    ])
    await update.message.reply_text(
        "Join our channel first", reply_markup=kb)

async def on_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    token = query.data.split(":", 1)[1]
    if await has_access(context.bot, query.from_user.id):
        await query.answer()
        await send_file(context.bot, query.message.chat_id, token)
    else:
        await query.answer("You have not joined the channel yet.", show_alert=True)

# ---------- main ----------
def main():
    init_db()
    threading.Thread(target=run_web, daemon=True).start()

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(on_check, pattern=r"^check:"))
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()