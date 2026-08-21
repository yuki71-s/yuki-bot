import os
import re
import logging
import asyncio
import base64
import json
import time
import random
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
ai_search_engine: dict[int, str] = {}  # "tinyfish" atau "tavily"
ai_pending_photo: dict[int, str] = {}
ai_pending_video: dict[int, str] = {}

_state_timestamps: dict[int, float] = defaultdict(float)
_rate_limit: dict[int, float] = defaultdict(float)
_user_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_user_state: dict[int, dict] = defaultdict(dict)  # Level 2: cache profile, memory, compression

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

# ── Level 2: User Profile & Memory ─────────────────────────────────

def _get_profile_ws_sync():
    """Get or auto-create YukiProfile worksheet."""
    if not SHEET_ID:
        return None
    try:
        gc = get_sheets_client()
        sh = gc.open_by_key(SHEET_ID)
        try:
            return sh.worksheet("YukiProfile")
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet("YukiProfile", rows=1000, cols=4)
            ws.update("A1:D1", [["user_id", "key", "value", "updated_at"]])
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for k, v in [("nama", "Y71"), ("hobi", "oprek-oprek"), ("minuman_suka", "kopi americano"), ("lokasi", "di hati Yuki"), ("skill", "suka coding meskipun ngga bisa")]:
                ws.append_row(["8575279550", k, v, now])
            logger.info("Created YukiProfile worksheet with default Y71 data")
            return ws
    except Exception as e:
        logger.error(f"Profile sheets error: {e}")
        return None

def _get_memory_ws_sync():
    """Get or auto-create YukiMemory worksheet."""
    if not SHEET_ID:
        return None
    try:
        gc = get_sheets_client()
        sh = gc.open_by_key(SHEET_ID)
        try:
            return sh.worksheet("YukiMemory")
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet("YukiMemory", rows=1000, cols=7)
            ws.update("A1:G1", [["id", "user_id", "summary", "topics", "importance", "created_at", "last_recalled"]])
            logger.info("Created YukiMemory worksheet")
            return ws
    except Exception as e:
        logger.error(f"Memory sheets error: {e}")
        return None

def _load_profile_sync(user_id):
    """Load user profile dari Google Sheets."""
    ws = _get_profile_ws_sync()
    if not ws:
        return {}
    try:
        records = ws.get_all_records()
        profile = {}
        for r in records:
            if str(r.get("user_id")) == str(user_id):
                profile[r["key"]] = r["value"]
        return profile
    except Exception as e:
        logger.error(f"Load profile error: {e}")
        return {}

def _save_profile_sync(user_id, key, value):
    """Save/update user profile ke Google Sheets."""
    ws = _get_profile_ws_sync()
    if not ws:
        return
    try:
        records = ws.get_all_records()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for i, r in enumerate(records, start=2):
            if str(r.get("user_id")) == str(user_id) and r.get("key") == key:
                ws.update_cell(i, 4, now)
                ws.update_cell(i, 3, value)
                return
        ws.append_row([str(user_id), key, value, now])
    except Exception as e:
        logger.error(f"Save profile error: {e}")

def _load_memories_sync(user_id, limit=5):
    """Load top memories dari Google Sheets."""
    ws = _get_memory_ws_sync()
    if not ws:
        return ""
    try:
        records = ws.get_all_records()
        user_memories = [r for r in records if str(r.get("user_id")) == str(user_id)]
        user_memories.sort(key=lambda x: int(x.get("importance", 0)), reverse=True)
        user_memories = user_memories[:limit]
        if not user_memories:
            return ""
        lines = ["MEMORY DENGAN USER:"]
        for i, mem in enumerate(user_memories, 1):
            summary = mem.get("summary", "")
            importance = mem.get("importance", 5)
            created = mem.get("created_at", "")[:10]
            lines.append(f"{i}. {summary} ({importance}/10, {created})")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Load memories error: {e}")
        return ""

def _save_memory_sync(user_id, summary, topics, importance):
    """Save memory ke Google Sheets."""
    ws = _get_memory_ws_sync()
    if not ws:
        return
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        memory_id = int(datetime.now().timestamp())
        ws.append_row([str(memory_id), str(user_id), summary, topics, importance, now, now])
    except Exception as e:
        logger.error(f"Save memory error: {e}")

# Async wrappers
async def load_user_profile(user_id):
    return await asyncio.to_thread(_load_profile_sync, user_id)

async def save_user_profile(user_id, key, value):
    await asyncio.to_thread(_save_profile_sync, user_id, key, value)

async def load_memories(user_id, limit=5):
    return await asyncio.to_thread(_load_memories_sync, user_id, limit)

async def save_memory(user_id, summary, topics, importance):
    await asyncio.to_thread(_save_memory_sync, user_id, summary, topics, importance)

def get_user_profile_text(profile):
    """Convert profile dict to prompt text."""
    if not profile:
        return ""
    label_map = {
        "nama": "Nama", "hobi": "Hobi", "minuman_suka": "Minuman favorit",
        "lokasi": "Lokasi", "skill": "Skill/Kemampuan",
        "usia": "Usia", "kota": "Kota", "pekerjaan": "Pekerjaan",
    }
    lines = ["INFO USER:"]
    for key, value in profile.items():
        label = label_map.get(key, key.replace("_", " ").title())
        lines.append(f"- {label}: {value}")
    return "\n".join(lines)

# ── Auto-Extract Facts & Memory (every N chats) ──

def _count_user_messages(history):
    return sum(1 for m in history if m.get("role") == "user")

async def auto_extract_facts(user_id, history):
    """Extract user facts setiap 10 chat."""
    count = _count_user_messages(history)
    if count < 10 or count % 10 != 0:
        return
    try:
        recent = history[-10:]
        conv_text = "\n".join([f"{'User' if m['role']=='user' else 'Yuki'}: {m['content']}" for m in recent])
        prompt = (
            f"[PERCAKAPAN]\n{conv_text}\n\n"
            "Extract fakta tentang user dari percakapan di atas.\n"
            "Return JSON: {{ \"key\": \"value\" }}\n"
            "Hanya extract: nama, hobi, usia, kota, kesukaan, pekerjaan, minuman, makanan.\n"
            "Kalau tidak ada fakta baru, return: {}"
        )
        # Use the server to extract via /ask with special skill
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{AI_SERVER_URL}/ask",
                json={"question": prompt, "history": [], "model": "gemini", "skill": "extract_facts"},
                timeout=30,
            )
        if resp.status_code == 200:
            data = resp.json()
            reply = data.get("reply", "{}").strip()
            if reply and reply != "{}":
                try:
                    facts = json.loads(reply)
                    for k, v in facts.items():
                        await save_user_profile(user_id, k, str(v))
                    logger.info(f"Auto-extracted {len(facts)} facts for user {user_id}")
                except json.JSONDecodeError:
                    pass
    except Exception as e:
        logger.error(f"Auto-extract facts error: {e}")

async def auto_summarize_memory(user_id, history):
    """Summarize conversation setiap 20 chat."""
    count = _count_user_messages(history)
    if count < 20 or count % 20 != 0:
        return
    try:
        recent = history[-20:]
        conv_text = "\n".join([f"{'User' if m['role']=='user' else 'Yuki'}: {m['content']}" for m in recent])
        prompt = (
            f"[PERCAKAPAN]\n{conv_text}\n\n"
            "Ringkas percakapan ini dalam 1-2 kalimat.\n"
            "Return format:\n"
            "Ringkasan: [summary]\n"
            "Topik: [topic1,topic2]\n"
            "Importance: [1-10]"
        )
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{AI_SERVER_URL}/ask",
                json={"question": prompt, "history": [], "model": "gemini", "skill": "summarize_memory"},
                timeout=30,
            )
        if resp.status_code == 200:
            data = resp.json()
            reply = data.get("reply", "").strip()
            if reply:
                summary, topics, importance = "", "", 5
                for line in reply.split("\n"):
                    if line.startswith("Ringkasan:"):
                        summary = line.split(":", 1)[1].strip()
                    elif line.startswith("Topik:"):
                        topics = line.split(":", 1)[1].strip()
                    elif line.startswith("Importance:"):
                        try:
                            importance = int(line.split(":", 1)[1].strip())
                        except ValueError:
                            pass
                if summary:
                    await save_memory(user_id, summary, topics, importance)
                    logger.info(f"Auto-summarized memory for user {user_id}: {summary[:50]}")
    except Exception as e:
        logger.error(f"Auto-summarize memory error: {e}")

async def auto_compress_history(user_id, history):
    """Compress old messages (>80) into summary."""
    if len(history) <= 80:
        return
    cache_key = f"_compressed_{user_id}"
    if cache_key in _user_state and _user_state[cache_key].get("count") == len(history):
        return
    try:
        old = history[:60]
        conv_text = "\n".join([f"{'User' if m['role']=='user' else 'Yuki'}: {m['content']}" for m in old])
        prompt = (
            f"[PERCAKAPAN LAMA]\n{conv_text}\n\n"
            "Ringkas percakapan ini dalam 2-3 kalimat. "
            "Fokus: topik utama, fakta penting, keputusan. "
            "Return plain text, Bahasa Indonesia."
        )
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{AI_SERVER_URL}/ask",
                json={"question": prompt, "history": [], "model": "gemini"},
                timeout=30,
            )
        if resp.status_code == 200:
            summary = resp.json().get("reply", "Percakapan sebelumnya tentang berbagai topik.")
            _user_state[cache_key] = {"summary": summary, "count": len(history)}
            logger.info(f"Compressed history for user {user_id}: {summary[:50]}")
    except Exception as e:
        logger.error(f"Auto-compress history error: {e}")

def get_compressed_history(user_id, history):
    """Return history dengan compression untuk context > 80 messages."""
    if len(history) <= 80:
        return history
    cache_key = f"_compressed_{user_id}"
    cached = _user_state.get(cache_key, {})
    summary = cached.get("summary", "Percakapan sebelumnya tentang berbagai topik.")
    recent = history[80:]
    return [{"role": "system", "content": f"[RINGKASAN PERCAKAPAN SEBELUMNYA]\n{summary}"}] + recent

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
            ai_search_engine.pop(uid, None)
            ai_pending_photo.pop(uid, None)
            ai_pending_video.pop(uid, None)
            ai_tavily_topic.pop(uid, None)
            ai_tavily_depth.pop(uid, None)
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
TAVILY_ON_MSG = "🔍 Tavily Search ON! Pilih setting dulu ya sayang~"
TINY_ON_MSG = "🔍 TinyFish Search ON! (gratis, quick search) ✅"

def get_thinking_msg(context="normal"):
    """Return dynamic thinking message based on context."""
    if context == "search_tinyfish":
        return random.choice([
            "🔍 Yuki sedang cari info dulu ya~",
            "Tunggu sebentar, lagi search~ 🔍",
            "Yuki browsing dulu~",
            "Sebentar ya sayang, lagi cari data~ 🐟",
        ])
    elif context == "search_tavily":
        return random.choice([
            "🌐 Yuki lagi cari berita nih~",
            "Sebentar, lagi riset~ 🌐",
            "Yuki cek dulu ya~",
            "Tunggu sebentar, lagi browsing~ 🔎",
        ])
    elif context == "extract":
        return random.choice([
            "📖 Yuki lagi baca nih~",
            "Sebentar, lagi extract info~",
            "Yuki analisis dulu ya~ 📄",
        ])
    elif context == "crawl":
        return random.choice([
            "🕷️ Yuki lagi crawl nih~",
            "Sebentar ya, lagi baca halaman~",
            "Yuki sedang scraping~ tunggu ya~",
        ])
    elif context == "research":
        return "🔬 Yuki lagi riset nih~ tunggu ya~"
    elif context == "vision":
        return random.choice([
            "👀 Yuki lagi lihat nih~",
            "Sebentar, lagi analisis gambar~",
            "Yuki perhatiin dulu ya~ 👁️",
        ])
    else:
        return random.choice([
            "Yuki sedang mengetik~",
            "Sebentar ya sayang~",
            "Bentar~",
            "Yuki mikir dulu ya~",
            "Tunggu sebentar~ 💭",
            "Yuki jawab dulu ya~",
        ])

# Per-user Tavily settings: topic + depth
ai_tavily_topic: dict[int, str] = {}   # "news" atau "general"
ai_tavily_depth: dict[int, str] = {}   # "advanced", "basic", "fast", "ultra-fast"

SEARCH_KEYWORDS = [
    r"\bcari di google\b", r"\bberita\b", r"\bgoggle\b",
    r"\bgoogle\b", r"\bcari informasi\b", r"\bsearching\b", r"\bcari online\b",
    r"\bcari\s+(?:di|info|tahu|tau|tentang|soal|perihal)\b",
    r"\b(?:bantu|bantuin)\s*(?:cari|carikan)\b",
    r"\bada\s+(?:berita|info|data|fakta)\b",
    r"\bmlink\b", r"\byoutube\b", r"\bvideo\b",
    r"\bwatch\b", r"\blink\b", r"\bsumber\b", r"\breferensi\b",
    r"\bsearch\b", r"\bsearching\b",
]
SEARCH_END_KEYWORDS = [
    r"\bselesai search\b", r"\budah search\b", r"\bstop search\b",
    r"\bcukup search\b", r"\budah ya\b", r"\bselesai ya\b", r"\budahan\b",
]
TINY_ON_KEYWORDS = [  # TinyFish (quick, gratis)
    r"\byuki,?\s*tiny\s*on\b",
    r"\byuki,?\s*tinyfish\s*on\b",
    r"\byuki,?\s*search\s*engine\s*1\b",
]
TAVILY_ON_KEYWORDS = [  # Tavily (realtime, 1-2 credits)
    r"\byuki,?\s*tavily\s*on\b",
    r"\byuki,?\s*search\s*engine\s*2\b",
    r"\byuki,?\s*search\s*deep\b",
]
SEARCH_ENGINE_OFF_KEYWORDS = [
    r"\byuki,?\s*tiny\s*off\b",
    r"\byuki,?\s*tavily\s*off\b",
    r"\byuki,?\s*search\s*engine\s*off\b",
    r"\byuki,?\s*matiin?\s*search\b",
    r"\byuki,?\s*nonaktifin?\s*search\b",
    r"\byuki,?\s*search\s*off\b",
    r"\byuki,?\s*search\s*off\b",
]

# ── Skill Keywords ─────────────────────────────────────────────────
TRANSLATE_KEYWORDS = [
    r"\btranslate\b", r"\bterjemah\b", r"\bterjemahin\b", r"\btranslate\s*ini\b",
    r"\bke\s*(?:bahasa|inggris|jepang|korea|arab|jawa|sunda|mandarin)\b",
    r"\bbahasa\s*(?:inggris|jepang|korea|arab|jawa|sunda|mandarin)\b",
]
SUMMARIZE_KEYWORDS = [
    r"\bsummarize\b", r"\bringkas\b", r"\bringkasin\b", r"\bringkas\s*ini\b",
    r"\bsummary\b", r"\btldr\b", r"\binti\s*nya\b",
]
WRITE_KEYWORDS = [
    r"\btulis\b", r"\btuliskan\b", r"\bwrite\b", r"\bcompose\b",
    r"\bbuat\s*(?:cerita|surat|puisi|essay|artikel)\b",
    r"\bcerita\s*pendek\b", r"\bshort\s*story\b",
]
EXTRACT_KEYWORDS = [
    r"\bextract\b", r"\bambil\s*isi\b", r"\bambil\s*konten\b",
    r"\bdownload\s*isi\b", r"\bsalin\s*isi\b",
]
CRAWL_KEYWORDS = [
    r"\bcrawl\b", r"\bscan\s*website\b", r"\bdownload\s*semua\b",
    r"\ambil\s*semua\s*halaman\b",
]
RESEARCH_KEYWORDS = [
    r"\bresearch\b", r"\briset\b", r"\briset\b",
    r"\bdeep\s*search\b", r"\bpenelitian\b", r"\banalisis\s*deep\b",
]

def detect_search_intent(text):
    lower = text.lower()
    # Cek command khusus Off dulu
    for kw in SEARCH_ENGINE_OFF_KEYWORDS:
        if re.search(kw, lower):
            return "off"
    # Cek Tiny On
    for kw in TINY_ON_KEYWORDS:
        if re.search(kw, lower):
            return "tiny_on"
    # Cek Tavily On
    for kw in TAVILY_ON_KEYWORDS:
        if re.search(kw, lower):
            return "tavily_on"
    # Cek end keywords
    for kw in SEARCH_END_KEYWORDS:
        if re.search(kw, lower):
            return "off"
    # Cek search keywords
    for kw in SEARCH_KEYWORDS:
        if re.search(kw, lower):
            return "on"
    return None

def detect_skill_intent(text):
    """Detect if user wants a specific skill."""
    lower = text.lower()
    # Cek extract (harus ada URL)
    urls = extract_urls(text)
    if urls:
        for kw in EXTRACT_KEYWORDS:
            if re.search(kw, lower):
                return "extract"
    # Cek crawl
    for kw in CRAWL_KEYWORDS:
        if re.search(kw, lower):
            return "crawl"
    # Cek research
    for kw in RESEARCH_KEYWORDS:
        if re.search(kw, lower):
            return "research"
    # Cek translate
    for kw in TRANSLATE_KEYWORDS:
        if re.search(kw, lower):
            return "translate"
    # Cek summarize
    for kw in SUMMARIZE_KEYWORDS:
        if re.search(kw, lower):
            return "summarize"
    # Cek write
    for kw in WRITE_KEYWORDS:
        if re.search(kw, lower):
            return "write"
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
        # Cek apakah ini command khusus (tanpa query)
        is_engine_command = bool(re.search(
            r"yuki,?\s*search\s*engine\s*on|yuki,?\s*nyalain?\s*search|yuki,?\s*aktifin?\s*search|yuki,?\s*search\s*on",
            text.lower()
        ))
        if is_engine_command:
            await bot.send_message(chat_id=chat_id, text=SEARCH_ON_MSG)
            return
        logger.info(f"Search mode ON + search: '{text}'")
    elif search_intent == "off":
        ai_search[user_id] = False
        touch_user_state(user_id)
        await bot.send_message(chat_id=chat_id, text=SEARCH_OFF_MSG)
        return
    elif search_intent == "tiny_on":
        ai_search[user_id] = True
        ai_search_engine[user_id] = "tinyfish"
        touch_user_state(user_id)
        await bot.send_message(chat_id=chat_id, text=TINY_ON_MSG)
        return
    elif search_intent == "tavily_on":
        ai_search[user_id] = True
        ai_search_engine[user_id] = "tavily"
        touch_user_state(user_id)
        # Tampilkan inline keyboard untuk Tavily settings
        keyboard = [
            [InlineKeyboardButton("📰 News", callback_data="tavily_topic_news"),
             InlineKeyboardButton("🌐 General", callback_data="tavily_topic_general")],
            [InlineKeyboardButton("⚡ Fast", callback_data="tavily_depth_fast"),
             InlineKeyboardButton("🚀 Ultra-fast", callback_data="tavily_depth_ultra-fast")],
            [InlineKeyboardButton("📋 Basic (1 cr)", callback_data="tavily_depth_basic"),
             InlineKeyboardButton("🔬 Advanced (2 cr)", callback_data="tavily_depth_advanced")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await bot.send_message(
            chat_id=chat_id,
            text=TAVILY_ON_MSG,
            reply_markup=reply_markup,
        )
        return

    # Detect skill intent
    skill_intent = detect_skill_intent(text)
    skill_urls = extract_urls(text) if skill_intent in ["extract", "crawl"] else []

    lock = _user_locks[user_id]
    async with lock:
        if user_id not in ai_history:
            ai_history[user_id] = await load_history_from_sheets(user_id)

        ai_history[user_id].append({"role": "user", "content": text})
        await save_history_to_sheets(user_id, "user", text)

        if len(ai_history[user_id]) > AI_MAX_HISTORY:
            ai_history[user_id] = ai_history[user_id][-AI_MAX_HISTORY:]

    # Determine thinking context
    if skill_intent in ["extract", "crawl"]:
        thinking_context = skill_intent
    elif skill_intent == "research":
        thinking_context = "research"
    elif image_b64 or video_b64:
        thinking_context = "vision"
    elif web_search and search_engine == "tavily":
        thinking_context = "search_tavily"
    elif web_search:
        thinking_context = "search_tinyfish"
    else:
        thinking_context = "normal"

    thinking_msg = None
    try:
        thinking_msg = await bot.send_message(chat_id=chat_id, text=get_thinking_msg(thinking_context))
    except TelegramError as e:
        logger.error(f"Failed to send thinking message: {e}")

    model_pref = ai_user_model.get(user_id, "")
    web_search = ai_search.get(user_id, False)
    search_engine = ai_search_engine.get(user_id, "tinyfish") if web_search else "tinyfish"

    if (image_b64 or video_b64) and model_pref not in VISION_MODELS:
        model_pref = DEFAULT_VISION_MODEL

    urls = extract_urls(text)
    url_content = None
    if urls and not image_b64 and not video_b64:
        url_content = await fetch_url_content(urls[0])

    # Level 2: Load profile + memory + compress history
    profile = await load_user_profile(user_id)
    profile_text = get_user_profile_text(profile)
    memory_text = await load_memories(user_id)
    history = get_compressed_history(user_id, ai_history.get(user_id, [])[:-1])

    payload = {
        "question": text,
        "history": history,
        "profile": profile_text,
        "memory": memory_text,
        "model": model_pref,
        "web_search": web_search,
        "search_engine": search_engine,
        "tavily_topic": ai_tavily_topic.get(user_id, "general"),
        "tavily_depth": ai_tavily_depth.get(user_id, "advanced"),
        "skill": skill_intent or "",
        "skill_urls": skill_urls,
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

        # Level 2: Auto-extract facts + summarize memory (async, non-blocking)
        try:
            await auto_extract_facts(user_id, ai_history.get(user_id, []))
            await auto_summarize_memory(user_id, ai_history.get(user_id, []))
            await auto_compress_history(user_id, ai_history.get(user_id, []))
        except Exception as e:
            logger.error(f"Level 2 auto-tasks error: {e}")

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
    poll_task = asyncio.create_task(polling_loop())
    yield
    task.cancel()
    poll_task.cancel()


async def polling_loop():
    """Long-polling loop — no webhook/SSL needed."""
    offset = -1
    logger.info("Polling loop started")
    while True:
        try:
            updates = await bot.get_updates(
                offset=offset,
                timeout=30,
                allowed_updates=["message", "callback_query"],
            )
            for update in updates:
                offset = update.update_id + 1
                try:
                    await route_update(update)
                except Exception as e:
                    logger.error(f"Error processing update {update.update_id}: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            await asyncio.sleep(5)

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
        if ai_search[user_id]:
            engine = ai_search_engine.get(user_id, "tinyfish")
            engine_name = "Tavily" if engine == "tavily" else "TinyFish"
            status = f"ON 🟢 [{engine_name}]"
        else:
            status = "OFF ⚪"
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

    # ── Tavily Settings Callbacks ──
    if data.startswith("tavily_topic_"):
        await bot.answer_callback_query(callback_query.id)
        user_id = callback_query.from_user.id
        topic = data.replace("tavily_topic_", "")
        ai_tavily_topic[user_id] = topic
        touch_user_state(user_id)
        # Update pesan dengan setting terbaru
        topic_label = "News 📰" if topic == "news" else "General 🌐"
        depth = ai_tavily_depth.get(user_id, "advanced")
        depth_label = {"advanced": "Advanced (2cr)", "basic": "Basic (1cr)", "fast": "Fast (1cr)", "ultra-fast": "Ultra-fast (1cr)"}.get(depth, depth)
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=callback_query.message.message_id,
                text=f"🔍 Tavily Settings:\n• Topic: {topic_label}\n• Depth: {depth_label}\n\nKetik pesan untuk mulai search!",
            )
        except TelegramError:
            pass
        return

    if data.startswith("tavily_depth_"):
        await bot.answer_callback_query(callback_query.id)
        user_id = callback_query.from_user.id
        depth = data.replace("tavily_depth_", "")
        ai_tavily_depth[user_id] = depth
        touch_user_state(user_id)
        # Update pesan dengan setting terbaru
        topic = ai_tavily_topic.get(user_id, "general")
        topic_label = "News 📰" if topic == "news" else "General 🌐"
        depth_label = {"advanced": "Advanced (2cr)", "basic": "Basic (1cr)", "fast": "Fast (1cr)", "ultra-fast": "Ultra-fast (1cr)"}.get(depth, depth)
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=callback_query.message.message_id,
                text=f"🔍 Tavily Settings:\n• Topic: {topic_label}\n• Depth: {depth_label}\n\nKetik pesan untuk mulai search!",
            )
        except TelegramError:
            pass
        return


# ── Main ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
