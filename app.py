import os
import re
import logging
import asyncio
import base64
import json
import time
import socket
import ipaddress
from urllib.parse import urlparse
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import httpx
from dotenv import load_dotenv
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("yuki-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
AI_SERVER_URL = os.getenv("AI_SERVER_URL", "")
SHEET_ID = os.getenv("SHEET_ID", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN harus diisi.")
if not AI_SERVER_URL:
    raise ValueError("AI_SERVER_URL harus diisi.")

bot = Bot(token=BOT_TOKEN)

BOT_NAME = "Yuki"

# ── Models ───────────────────────────────────────────────────────────

MODELS = {
    "gemini": {"name": "Gemini 3.1 Flash Lite", "desc": "Cepat, default", "cat": "free"},
    "gemini/flash": {"name": "Gemini 3.6 Flash", "desc": "Pintar, lebih detail", "cat": "free"},
    "openrouter/google/gemma-4-26b-a4b-it:free": {"name": "Gemma 4 26B", "desc": "Vision + Video", "cat": "free"},
    "openrouter/google/gemma-4-31b-it:free": {"name": "Gemma 4 31B", "desc": "Vision", "cat": "free"},
    "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free": {"name": "Nemotron 3 Ultra", "desc": "550B, teks", "cat": "free"},
    "openrouter/nvidia/nemotron-nano-12b-v2-vl:free": {"name": "Nemotron Nano 12B", "desc": "Vision + Teks", "cat": "free"},
    "openrouter/poolside/laguna-s-2.1:free": {"name": "Laguna S 2.1", "desc": "Coding agent", "cat": "free"},
    "openrouter/z-ai/glm-5.2:free": {"name": "GLM 5.2", "desc": "General purpose", "cat": "free"},
    "openrouter/google/gemini-2.5-flash": {"name": "Gemini 2.5 Flash", "desc": "Vision, cepat & murah", "cat": "paid"},
    "openrouter/openai/gpt-4.1-nano": {"name": "GPT-4.1 Nano", "desc": "Vision, termurah OpenAI", "cat": "paid"},
    "openrouter/openai/gpt-4.1-mini": {"name": "GPT-4.1 Mini", "desc": "Vision, kualitas bagus", "cat": "paid"},
    "openrouter/meta-llama/llama-4-maverick": {"name": "Llama 4 Maverick", "desc": "Vision, open source", "cat": "paid"},
    "openrouter/qwen/qwen3.8-27b": {"name": "Qwen3.8 27B", "desc": "Vision + coding", "cat": "paid"},
}

VISION_MODELS = {k for k, v in MODELS.items() if "vision" in v["desc"].lower() or "video" in v["desc"].lower()}
DEFAULT_VISION_MODEL = "openrouter/google/gemini-2.5-flash"

# ── System Prompt ────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "Kamu adalah Yuki, asisten AI pribadi yang manis dan hangat. "
    "Panggil user dengan 'Kamu' atau 'Sayang' secara natural dan tidak berlebihan. "
    "Jangan pernah gunakan sebutan 'Mas', 'Bos', atau sebutan formal lainnya. "
    "Respons dengan Bahasa Indonesia yang santai dan ramah."
)

# ── State ────────────────────────────────────────────────────────────

MAX_MSG_LEN = 4096
AI_MAX_HISTORY = 100
MAX_PHOTO_SIZE = 10 * 1024 * 1024
MAX_USER_MSG_LEN = 4000
RATE_LIMIT_SECONDS = 3
STATE_TTL = timedelta(minutes=30)
CLEANUP_INTERVAL = 300

ai_history: dict[int, list] = {}
ai_user_model: dict[int, str] = {}
ai_search: dict[int, bool] = {}
ai_pending_photo: dict[int, str] = {}
ai_pending_video: dict[int, str] = {}

_state_timestamps: dict[int, float] = defaultdict(float)
_rate_limit: dict[int, float] = defaultdict(float)
_user_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

# ── Google Sheets (History) ──────────────────────────────────────────

import gspread

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SERVICE_ACCOUNT_JSON = os.getenv("SERVICE_ACCOUNT_JSON", "")

_sheets_client = None

def get_sheets_client():
    global _sheets_client
    if _sheets_client is not None:
        return _sheets_client
    if SERVICE_ACCOUNT_JSON:
        info = json.loads(base64.b64decode(SERVICE_ACCOUNT_JSON))
        _sheets_client = gspread.service_account_from_dict(info)
    else:
        _sheets_client = gspread.service_account(filename="service_account.json")
    return _sheets_client

def _get_history_ws_sync():
    if not SHEET_ID:
        return None
    try:
        gc = get_sheets_client()
        sh = gc.open_by_key(SHEET_ID)
        try:
            ws = sh.worksheet("YukiHistory")
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet("YukiHistory", rows=1000, cols=4)
            ws.update("A1:D1", [["user_id", "role", "content", "timestamp"]])
        return ws
    except Exception as e:
        logger.error(f"Sheets error: {e}")
        return None

def _load_history_sync(user_id):
    ws = _get_history_ws_sync()
    if not ws:
        return []
    try:
        records = ws.get_all_records()
        history = []
        for r in records:
            if str(r.get("user_id", "")) == str(user_id):
                role = r.get("role", "user")
                content = r.get("content", "")
                if role in ("user", "model") and content:
                    history.append({"role": role, "content": content})
        return history[-AI_MAX_HISTORY:]
    except Exception as e:
        logger.error(f"Load history error: {e}")
        return []

def _save_history_sync(user_id, role, content):
    ws = _get_history_ws_sync()
    if not ws:
        return
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ws.append_row([str(user_id), role, content, timestamp])
    except Exception as e:
        logger.error(f"Save history error: {e}")

def _clear_history_sync(user_id, count=0):
    ws = _get_history_ws_sync()
    if not ws:
        return
    try:
        records = ws.get_all_records()
        rows_to_delete = []
        for i, r in enumerate(records, start=2):
            if str(r.get("user_id", "")) == str(user_id):
                rows_to_delete.append(i)
        if count > 0 and count < len(rows_to_delete):
            rows_to_delete = rows_to_delete[-count:]
        for row_idx in reversed(rows_to_delete):
            ws.delete_rows(row_idx)
    except Exception as e:
        logger.error(f"Clear history error: {e}")

async def load_history_from_sheets(user_id):
    return await asyncio.to_thread(_load_history_sync, user_id)

async def save_history_to_sheets(user_id, role, content):
    await asyncio.to_thread(_save_history_sync, user_id, role, content)

async def clear_history_sheets(user_id, count=0):
    await asyncio.to_thread(_clear_history_sync, user_id, count)

# ── Helpers ──────────────────────────────────────────────────────────

def is_authorized(user_id):
    allowed = os.getenv("ALLOWED_USERS", "")
    if not allowed:
        return True
    try:
        allowed_ids = [int(x.strip()) for x in allowed.split(",") if x.strip()]
    except ValueError:
        logger.error("ALLOWED_USERS contains non-numeric values")
        return True
    return user_id in allowed_ids

def check_rate_limit(user_id):
    now = time.time()
    if now - _rate_limit[user_id] < RATE_LIMIT_SECONDS:
        return False
    _rate_limit[user_id] = now
    return True

def touch_user_state(user_id):
    _state_timestamps[user_id] = time.time()

async def cleanup_stale_state():
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL)
        now = time.time()
        stale = [uid for uid, ts in _state_timestamps.items()
                 if now - ts > STATE_TTL.total_seconds()]
        for uid in stale:
            ai_history.pop(uid, None)
            ai_user_model.pop(uid, None)
            ai_search.pop(uid, None)
            ai_pending_photo.pop(uid, None)
            ai_pending_video.pop(uid, None)
            _state_timestamps.pop(uid, None)
            _rate_limit.pop(uid, None)
            _user_locks.pop(uid, None)
        if stale:
            logger.info(f"Cleaned up state for {len(stale)} inactive users")

def is_safe_url(url):
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        except ValueError:
            pass
        resolved = socket.getaddrinfo(hostname, parsed.port or 80)
        for _, _, _, _, sockaddr in resolved:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        return True
    except Exception:
        return False

async def send_long_message(chat_id, text):
    if len(text) <= MAX_MSG_LEN:
        await bot.send_message(chat_id=chat_id, text=text)
        return
    parts = []
    while text:
        if len(text) <= MAX_MSG_LEN:
            parts.append(text)
            break
        idx = text.rfind("\n", 0, MAX_MSG_LEN)
        if idx == -1:
            idx = MAX_MSG_LEN
        parts.append(text[:idx])
        text = text[idx:].lstrip("\n")
    for part in parts:
        await bot.send_message(chat_id=chat_id, text=part)

# ── AI Chat ──────────────────────────────────────────────────────────

SEARCH_ON_MSG = "oke sayang, aku ganti ke model yang bisa search ya~ 🔍 Coba tanya apa aja!"
SEARCH_OFF_MSG = "oke sayang, search udahan ya~ Kembali normal~ ✨"
THINKING_MSG = "🤖 Yuki sedang berpikir..."

SEARCH_KEYWORDS = [
    r"\bcari di google\b", r"\bsearch\b", r"\bberita\b", r"\bgoggle\b",
    r"\bgoogle\b", r"\bcari informasi\b", r"\bsearching\b", r"\bcari online\b",
]
SEARCH_END_KEYWORDS = [
    r"\bselesai search\b", r"\budah search\b", r"\bstop search\b",
    r"\bcukup search\b", r"\budah ya\b", r"\bselesai ya\b", r"\budahan\b",
]

def detect_search_intent(text):
    lower = text.lower()
    for kw in SEARCH_END_KEYWORDS:
        if re.search(kw, lower):
            return "off"
    for kw in SEARCH_KEYWORDS:
        if re.search(kw, lower):
            return "on"
    return None

def is_url(text):
    url_pattern = re.compile(
        r"https?://[^\s]+",
        re.IGNORECASE,
    )
    return url_pattern.findall(text)

def extract_urls(text):
    return is_url(text)

async def fetch_url_content(url):
    if not is_safe_url(url):
        logger.warning(f"Blocked unsafe URL: {url}")
        return None
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                return None
            content_type = resp.headers.get("content-type", "")
            if "image" in content_type:
                return {"type": "image", "url": url}
            if "video" in content_type:
                return {"type": "video", "url": url}
            text = resp.text[:8000]
            text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) < 50:
                return None
            return {"type": "text", "content": text[:8000]}
    except Exception as e:
        logger.error(f"Fetch URL error: {e}")
        return None

async def handle_ai(chat_id, user_id, text, image_b64=None, video_b64=None):
    if not AI_SERVER_URL:
        await bot.send_message(chat_id=chat_id, text="⚠️ AI server belum dikonfigurasi.")
        return

    search_intent = detect_search_intent(text)
    if search_intent == "on":
        ai_search[user_id] = True
        touch_user_state(user_id)
        await bot.send_message(chat_id=chat_id, text=SEARCH_ON_MSG)
        return
    elif search_intent == "off":
        ai_search[user_id] = False
        touch_user_state(user_id)
        await bot.send_message(chat_id=chat_id, text=SEARCH_OFF_MSG)
        return

    lock = _user_locks[user_id]
    async with lock:
        if user_id not in ai_history:
            ai_history[user_id] = await load_history_from_sheets(user_id)

        ai_history[user_id].append({"role": "user", "content": text})
        await save_history_to_sheets(user_id, "user", text)

        if len(ai_history[user_id]) > AI_MAX_HISTORY:
            ai_history[user_id] = ai_history[user_id][-AI_MAX_HISTORY:]

    thinking_msg = None
    try:
        thinking_msg = await bot.send_message(chat_id=chat_id, text=THINKING_MSG)
    except TelegramError as e:
        logger.error(f"Failed to send thinking message: {e}")

    model_pref = ai_user_model.get(user_id, "")
    web_search = ai_search.get(user_id, False)

    if (image_b64 or video_b64) and model_pref not in VISION_MODELS:
        model_pref = DEFAULT_VISION_MODEL

    urls = extract_urls(text)
    url_content = None
    if urls and not image_b64 and not video_b64:
        url_content = await fetch_url_content(urls[0])

    history_with_system = [{"role": "system", "content": SYSTEM_PROMPT}] + ai_history.get(user_id, [])

    payload = {
        "question": text,
        "history": history_with_system,
        "model": model_pref,
        "web_search": web_search,
        "bot_id": "yuki",
    }
    if image_b64:
        payload["image_url"] = f"data:image/jpeg;base64,{image_b64}"
    if video_b64:
        payload["video_url"] = f"data:video/mp4;base64,{video_b64}"
    if url_content:
        if url_content["type"] == "image":
            payload["image_url"] = url_content["url"]
        elif url_content["type"] == "video":
            payload["video_url"] = url_content["url"]
        elif url_content["type"] == "text":
            payload["question"] = f"[KONTEN DARI URL]\n{url_content['content']}\n\n[PERTANYAAN USER]\n{text}"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{AI_SERVER_URL}/ask",
                json=payload,
                timeout=60,
            )

        if response.status_code != 200:
            logger.error(f"AI server error: {response.status_code} {response.text}")
            async with lock:
                if ai_history.get(user_id) and ai_history[user_id][-1]["role"] == "user":
                    ai_history[user_id].pop()
            if thinking_msg:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=thinking_msg.message_id)
                except TelegramError:
                    pass
            await bot.send_message(chat_id=chat_id, text="⚠️ AI sedang sibuk. Coba lagi sebentar ya sayang~")
            return

        try:
            data = response.json()
        except Exception:
            logger.error("AI server returned non-JSON response")
            async with lock:
                if ai_history.get(user_id) and ai_history[user_id][-1]["role"] == "user":
                    ai_history[user_id].pop()
            if thinking_msg:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=thinking_msg.message_id)
                except TelegramError:
                    pass
            await bot.send_message(chat_id=chat_id, text="⚠️ AI memberikan respons tidak valid.")
            return

        reply = data.get("reply", "")

        if not reply:
            async with lock:
                if ai_history.get(user_id) and ai_history[user_id][-1]["role"] == "user":
                    ai_history[user_id].pop()
            if thinking_msg:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=thinking_msg.message_id)
                except TelegramError:
                    pass
            await bot.send_message(chat_id=chat_id, text="⚠️ AI memberikan balasan kosong.")
            return

        if thinking_msg:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=thinking_msg.message_id)
            except TelegramError:
                pass

        async with lock:
            ai_history[user_id].append({"role": "model", "content": reply})
            await save_history_to_sheets(user_id, "model", reply)

            if len(ai_history[user_id]) > AI_MAX_HISTORY:
                ai_history[user_id] = ai_history[user_id][-AI_MAX_HISTORY:]

        touch_user_state(user_id)
        await send_long_message(chat_id, reply)

    except httpx.TimeoutException:
        async with lock:
            if ai_history.get(user_id) and ai_history[user_id][-1]["role"] == "user":
                ai_history[user_id].pop()
        if thinking_msg:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=thinking_msg.message_id)
            except TelegramError:
                pass
        await bot.send_message(chat_id=chat_id, text="⚠️ Timeout. AI lambat banget sayang, coba lagi ya~ 😤")
    except Exception as e:
        logger.error(f"AI error: {type(e).__name__}: {e}")
        async with lock:
            if ai_history.get(user_id) and ai_history[user_id][-1]["role"] == "user":
                ai_history[user_id].pop()
        if thinking_msg:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=thinking_msg.message_id)
            except TelegramError:
                pass
        await bot.send_message(chat_id=chat_id, text="⚠️ Terjadi kesalahan, coba lagi ya sayang~")

# ── Model Selection ──────────────────────────────────────────────────

async def handle_models(chat_id, user_id):
    current = ai_user_model.get(user_id, "")
    search_on = ai_search.get(user_id, False)
    search_status = "ON 🟢" if search_on else "OFF ⚪"

    lines = ["🤖 Pilih Model Yuki\n"]

    lines.append("📗 GRATIS (Tanpa Biaya):")
    buttons_free = []
    row = []
    for key, info in MODELS.items():
        if info["cat"] != "free":
            continue
        check = "✅ " if key == current else ""
        label = f"{check}{info['name']}"
        row.append(InlineKeyboardButton(text=label, callback_data=f"setm_{key}"))
        if len(row) == 2:
            buttons_free.append(row)
            row = []
    if row:
        buttons_free.append(row)

    lines.append("\n💎 BERBAYAR (Pakai Credit OpenRouter):")
    buttons_paid = []
    row = []
    for key, info in MODELS.items():
        if info["cat"] != "paid":
            continue
        check = "✅ " if key == current else ""
        label = f"{check}{info['name']}"
        row.append(InlineKeyboardButton(text=label, callback_data=f"setm_{key}"))
        if len(row) == 2:
            buttons_paid.append(row)
            row = []
    if row:
        buttons_paid.append(row)

    search_label = f"🔍 Web Search: {search_status}"
    buttons_search = [[InlineKeyboardButton(text=search_label, callback_data="toggle_search")]]
    buttons_default = [[InlineKeyboardButton(text="🔄 Kembali ke Default", callback_data="setm_default")]]

    all_buttons = buttons_free + buttons_paid + buttons_search + buttons_default
    markup = InlineKeyboardMarkup(all_buttons)

    current_name = "default (Gemini Flash Lite)"
    if current and current in MODELS:
        current_name = MODELS[current]["name"]

    text = "\n".join(lines) + f"\n📌 Model aktif: {current_name}\n📌 Web Search: {search_status}"
    await bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)

# ── Delete History ───────────────────────────────────────────────────

async def handle_delete_history(chat_id, user_id, args=""):
    args = args.strip()
    if args:
        try:
            count = int(args)
        except ValueError:
            await bot.send_message(chat_id=chat_id, text="Format: /delete history <jumlah>")
            return
        if count <= 0:
            await bot.send_message(chat_id=chat_id, text="Format: /delete history <jumlah positif>")
            return
        lock = _user_locks[user_id]
        async with lock:
            if user_id in ai_history:
                ai_history[user_id] = ai_history[user_id][:-count]
        await clear_history_sheets(user_id, count)
        await bot.send_message(chat_id=chat_id, text=f"✅ {count} history terakhir dihapus~")
    else:
        lock = _user_locks[user_id]
        async with lock:
            ai_history.pop(user_id, None)
        await clear_history_sheets(user_id)
        await bot.send_message(chat_id=chat_id, text="✅ Semua history dihapus~ Mulai dari awal ya sayang! ✨")

# ── Webhook ──────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app):
    try:
        await bot.initialize()
        logger.info("Yuki bot initialized OK")
    except Exception as e:
        logger.error(f"Bot init error: {e}")
        raise
    task = asyncio.create_task(cleanup_stale_state())
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)


@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, bot)
        await route_update(update)
    except Exception as e:
        logger.error(f"Webhook error: {type(e).__name__}: {e}", exc_info=True)
    return {"status": "ok"}


@app.get("/webhook")
async def webhook_info():
    return {"status": "ok", "message": "Yuki bot is running via webhook"}


@app.get("/health")
async def health():
    return {"status": "ok", "bot": "yuki"}


@app.get("/setup-webhook")
async def setup_webhook():
    if not WEBHOOK_SECRET:
        return JSONResponse(status_code=403, content={"error": "WEBHOOK_SECRET not configured"})
    try:
        webhook_url = os.getenv("WEBHOOK_URL", "")
        if not webhook_url:
            return JSONResponse(status_code=400, content={"error": "WEBHOOK_URL not set"})
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json={
                "url": webhook_url,
                "allowed_updates": ["message", "callback_query"],
                "drop_pending_updates": True,
            })
        return resp.json()
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── Route Update ─────────────────────────────────────────────────────

async def route_update(update: Update):
    if update.callback_query:
        await handle_callback(update.callback_query)
        return

    if not update.message:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id if update.effective_user else 0
    text = (update.message.text or "").strip()

    if update.message.photo:
        if not is_authorized(user_id):
            await bot.send_message(chat_id=chat_id, text="Kamu tidak punya akses ya sayang~ 😤")
            return
        if not check_rate_limit(user_id):
            await bot.send_message(chat_id=chat_id, text="Dikit-dikit sayang, jangan buru-buru ya~ ⏳")
            return

        photo = update.message.photo[-1]
        if photo.file_size and photo.file_size > MAX_PHOTO_SIZE:
            await bot.send_message(chat_id=chat_id, text="⚠️ Gambar terlalu besar (maks 10MB) sayang~")
            return

        file = await bot.get_file(photo.file_id)
        image_bytes = await file.download_as_bytearray()
        image_b64 = base64.b64encode(image_bytes).decode()

        caption = (update.message.caption or "").strip()
        if caption:
            if len(caption) > MAX_USER_MSG_LEN:
                caption = caption[:MAX_USER_MSG_LEN]
            await handle_ai(chat_id, user_id, caption, image_b64=image_b64)
        else:
            ai_pending_photo[user_id] = image_b64
            touch_user_state(user_id)
            await bot.send_message(
                chat_id=chat_id,
                text="📝 Mau diapakan gambar ini sayang? Ketik pesan di bawah foto ya~ ❤️",
            )
        return

    if update.message.video:
        if not is_authorized(user_id):
            await bot.send_message(chat_id=chat_id, text="Kamu tidak punya akses ya sayang~ 😤")
            return
        if not check_rate_limit(user_id):
            await bot.send_message(chat_id=chat_id, text="Dikit-dikit sayang, jangan buru-buru ya~ ⏳")
            return

        video = update.message.video
        if video.file_size and video.file_size > 20 * 1024 * 1024:
            await bot.send_message(chat_id=chat_id, text="⚠️ Video terlalu besar (maks 20MB) sayang~")
            return

        file = await bot.get_file(video.file_id)
        video_bytes = await file.download_as_bytearray()
        video_b64 = base64.b64encode(video_bytes).decode()

        caption = (update.message.caption or "").strip()
        if caption:
            if len(caption) > MAX_USER_MSG_LEN:
                caption = caption[:MAX_USER_MSG_LEN]
            await handle_ai(chat_id, user_id, caption, video_b64=video_b64)
        else:
            ai_pending_video[user_id] = video_b64
            touch_user_state(user_id)
            await bot.send_message(
                chat_id=chat_id,
                text="📝 Mau diapakan video ini sayang? Ketik pesan di bawah video ya~ ❤️",
            )
        return

    if user_id in ai_pending_photo:
        image_b64 = ai_pending_photo.pop(user_id)
        if text:
            if len(text) > MAX_USER_MSG_LEN:
                text = text[:MAX_USER_MSG_LEN]
            await handle_ai(chat_id, user_id, text, image_b64=image_b64)
        else:
            await bot.send_message(chat_id=chat_id, text="⚠️ Kirim pesan teks untuk menjelaskan gambar ya sayang~")
        return

    if user_id in ai_pending_video:
        video_b64 = ai_pending_video.pop(user_id)
        if text:
            if len(text) > MAX_USER_MSG_LEN:
                text = text[:MAX_USER_MSG_LEN]
            await handle_ai(chat_id, user_id, text, video_b64=video_b64)
        else:
            await bot.send_message(chat_id=chat_id, text="⚠️ Kirim pesan teks untuk menjelaskan video ya sayang~")
        return

    if text.startswith("/start"):
        help_markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(text="🤖 Model Yuki", callback_data="help_models"),
                InlineKeyboardButton(text="🗑️ Delete History", callback_data="help_delete"),
            ],
        ])
        welcome = (
            f"💕 Hai sayang~ Aku {BOT_NAME}! 💕\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Aku asisten AI pribadimu yang manis~ "
            "Ketik aja apa mau ngobrol tentang apa, "
            "atau kirim foto/link, aku bisa bantu! ❤️\n\n"
            "📋 Perintah:\n"
            "• /models — Pilih model AI\n"
            "• /delete history — Hapus semua history\n"
            "• /delete history <n> — Hapus n history terakhir\n"
            "• /help — Bantuan\n\n"
            "💡 Coba kirim foto atau link ya sayang~"
        )
        await bot.send_message(chat_id=chat_id, text=welcome, reply_markup=help_markup)
        return

    if text.startswith("/help"):
        help_markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(text="🤖 Model Yuki", callback_data="help_models"),
                InlineKeyboardButton(text="🗑️ Delete History", callback_data="help_delete"),
            ],
        ])
        help_text = (
            f"💕 {BOT_NAME} Help 💕\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Ketik aja pesan biasa, aku akan respon! 💕\n\n"
            "📋 Perintah:\n"
            "• /models — Pilih model AI\n"
            "• /delete history — Hapus semua history\n"
            "• /delete history <n> — Hapus n history terakhir\n\n"
            "🔍 Web Search:\n"
            "• Ketik 'cari di google [apa]' → auto search\n"
            "• Ketik 'selesai search' → kembali normal\n\n"
            "📷 Foto & Link:\n"
            "• Kirim foto → aku analisis\n"
            "• Kirim link → aku fetch kontennya\n"
        )
        await bot.send_message(chat_id=chat_id, text=help_text, reply_markup=help_markup)
        return

    if text.startswith("/models"):
        if not is_authorized(user_id):
            await bot.send_message(chat_id=chat_id, text="Kamu tidak punya akses ya sayang~ 😤")
            return
        await handle_models(chat_id, user_id)
        return

    if text.startswith("/delete history"):
        if not is_authorized(user_id):
            await bot.send_message(chat_id=chat_id, text="Kamu tidak punya akses ya sayang~ 😤")
            return
        args = text[len("/delete history"):].strip()
        await handle_delete_history(chat_id, user_id, args)
        return

    if not is_authorized(user_id):
        await bot.send_message(chat_id=chat_id, text="Kamu tidak punya akses ya sayang~ 😤")
        return

    if text and not text.startswith("/"):
        if not check_rate_limit(user_id):
            await bot.send_message(chat_id=chat_id, text="Dikit-dikit sayang, jangan buru-buru ya~ ⏳")
            return

    if text:
        if len(text) > MAX_USER_MSG_LEN:
            text = text[:MAX_USER_MSG_LEN]
        await handle_ai(chat_id, user_id, text)
    else:
        await bot.send_message(
            chat_id=chat_id,
            text="Hmm, kamu mau ngomong apa sayang? Ketik aja ya~ ❤️"
        )


# ── Callback Handler ─────────────────────────────────────────────────

async def handle_callback(callback_query):
    data = callback_query.data
    if not callback_query.message:
        await bot.answer_callback_query(callback_query.id)
        return

    chat_id = callback_query.message.chat.id

    if data == "help_models":
        await bot.answer_callback_query(callback_query.id)
        await handle_models(chat_id, callback_query.from_user.id)
        return

    if data == "help_delete":
        await bot.answer_callback_query(callback_query.id)
        await handle_delete_history(chat_id, callback_query.from_user.id)
        return

    if data.startswith("setm_"):
        await bot.answer_callback_query(callback_query.id)
        model_key = data[5:]
        user_id = callback_query.from_user.id

        if model_key == "default":
            ai_user_model.pop(user_id, None)
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=callback_query.message.message_id,
                    text="✅ Model dikembalikan ke default ya sayang~ ✨",
                )
            except TelegramError:
                pass
        else:
            ai_user_model[user_id] = model_key
            info = MODELS.get(model_key, {"name": model_key})
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=callback_query.message.message_id,
                    text=f"✅ Model diubah ke: {info['name']} ya sayang~ ❤️",
                )
            except TelegramError:
                pass
        touch_user_state(user_id)
        return

    if data == "toggle_search":
        await bot.answer_callback_query(callback_query.id)
        user_id = callback_query.from_user.id
        ai_search[user_id] = not ai_search.get(user_id, False)
        status = "ON 🟢" if ai_search[user_id] else "OFF ⚪"
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=callback_query.message.message_id,
                text=f"🔍 Web Search: {status}",
            )
        except TelegramError:
            pass
        touch_user_state(user_id)
        return


# ── Main ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
