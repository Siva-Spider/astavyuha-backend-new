# logger_util.py
import logging
import os
import datetime
import threading
import json
from collections import deque
from zoneinfo import ZoneInfo
from colorama import Fore, Style, init
import redis

init(autoreset=True)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
r = redis.StrictRedis.from_url(REDIS_URL, decode_responses=True)

# ==========================================================
# 🌐 Global Autotrade Logger Configuration
# ==========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger("autotrade")
if not logger.hasHandlers():
    file_handler = logging.FileHandler("autotrade.log")
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

# ==========================================================
# 🧠 Shared Redis + Local Fallback Logging
# ==========================================================

_LOG_MAX = int(os.environ.get("AUTOTRADE_LOG_BUFFER", 500))
_log_buf = deque(maxlen=_LOG_MAX)
_log_lock = threading.Lock()

# --- Redis connection ---
try:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    redis_client.ping()
    print(f"✅ Connected to Redis at {REDIS_URL}")
except Exception as e:
    print(f"⚠️ Redis not available, using in-memory logging. Error: {e}")
    redis_client = None

# ==========================================================
# 📝 Logging Functions
# ==========================================================
import redis
import json
import datetime
import logging
from threading import Lock

# ---------------------------------------------------------------------
# 🔧 Setup
# ---------------------------------------------------------------------
log_buffer = []
log_lock = Lock()

# Configure global console logger
console_logger = logging.getLogger("TradeLogger")
console_logger.setLevel(logging.INFO)

# Avoid duplicate handlers
if not console_logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    console_logger.addHandler(handler)


# ---------------------------------------------------------------------
# ✅ Push Log (to Redis + Console + Memory)
# ---------------------------------------------------------------------
def push_log(message, level="info"):
    """Push log message to Redis (for UI), in-memory buffer, and Celery/console."""
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = {"ts": ts, "level": level.lower(), "message": str(message), "type": "log"}

    # Save to in-memory buffer
    with log_lock:
        log_buffer.append(entry)
        if len(log_buffer) > 1000:
            log_buffer.pop(0)

    # 🔴 Redis: store and publish for live UI
    try:
        r = redis.StrictRedis(host="localhost", port=6379, db=5, decode_responses=True)
        r.rpush("autotrade_logs", json.dumps(entry))   # for history
        r.publish("live_logs", json.dumps(entry))      # for streaming
    except Exception as e:
        console_logger.warning(f"[push_log] Redis unavailable: {e}")

    # 🟢 Print to Celery/FastAPI console
    level_upper = level.upper()
    log_text = f"[{ts}] {level_upper:7}: {message}"

    if level.lower() == "error":
        console_logger.error(log_text)
    elif level.lower() == "warning":
        console_logger.warning(log_text)
    else:
        console_logger.info(log_text)


def get_log_buffer():
    """Return in-memory log list (for fallback mode)"""
    with log_lock:
        return list(log_buffer)

def push_payload(name, data):
    """Push structured payloads (trade data, metrics, etc.)"""
    ts = datetime.datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S")
    entry = {"type": "payload", "ts": ts, "name": name, "data": data}

    if USE_REDIS:
        try:
            redis_client.lpush("autotrade_logs", json.dumps(entry))
            redis_client.ltrim("autotrade_logs", 0, _LOG_MAX)
        except Exception:
            pass

    with _log_lock:
        _log_buf.append(entry)

__all__ = ["logger", "push_log", "push_payload", "get_log_buffer"]
