# logger_util.py
import os
import redis
import ssl
import json
import datetime
import logging
from threading import Lock
from collections import deque
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

# ==========================================================
# 🌐 Redis Setup
# ==========================================================
REDIS_URL = os.getenv("REDIS_URL", "").strip() or "redis://localhost:6379"
LOG_BUFFER = deque(maxlen=500)
log_lock = Lock()

def connect_redis():
    """Connect to Redis (Upstash or local) safely."""
    try:
        parsed = urlparse(REDIS_URL)
        if parsed.scheme == "rediss":
            print(f"✅ Secure connection to Redis: {parsed.hostname}")
            return redis.StrictRedis.from_url(
                REDIS_URL,
                ssl_cert_reqs=ssl.CERT_NONE,   # ✅ Only this, not ssl=True
                decode_responses=True
            )
        else:
            print(f"✅ Connecting to Redis: {parsed.hostname}")
            return redis.StrictRedis.from_url(REDIS_URL, decode_responses=True)
    except Exception as e:
        print(f"⚠️ Redis connection failed: {e}")
        return None

redis_client = connect_redis()

# ==========================================================
# 🧠 Global Logger Setup
# ==========================================================
console_logger = logging.getLogger("TradeLogger")
console_logger.setLevel(logging.INFO)

if not console_logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s - %(levelname)-7s - %(message)s")
    handler.setFormatter(formatter)
    console_logger.addHandler(handler)

# ==========================================================
# 📝 Push Log
# ==========================================================
def push_log(message, level="info"):
    """Push log message to Redis (for UI), in-memory buffer, and console."""
    ts = datetime.datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S")
    entry = {"ts": ts, "level": level.lower(), "message": str(message), "type": "log"}

    # Add to memory
    with log_lock:
        LOG_BUFFER.append(entry)

    # Publish to Redis (for live updates)
    if redis_client:
        try:
            redis_client.rpush("autotrade_logs", json.dumps(entry))  # history
            redis_client.publish("log_stream", json.dumps(entry))    # live stream
        except Exception as e:
            console_logger.warning(f"[push_log] Redis unavailable: {e}")

    # Print to console
    formatted = f"[{ts}] {level.upper():7}: {message}"
    if level.lower() == "error":
        console_logger.error(formatted)
    elif level.lower() == "warning":
        console_logger.warning(formatted)
    else:
        console_logger.info(formatted)

def get_log_buffer():
    """Return local memory log buffer."""
    with log_lock:
        return list(LOG_BUFFER)

def push_payload(name, data):
    """Push structured payload (trade data, metrics)."""
    ts = datetime.datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S")
    entry = {"type": "payload", "ts": ts, "name": name, "data": data}

    if redis_client:
        try:
            redis_client.rpush("autotrade_logs", json.dumps(entry))
            redis_client.ltrim("autotrade_logs", 0, 499)
        except Exception:
            pass

    with log_lock:
        LOG_BUFFER.append(entry)

__all__ = ["push_log", "get_log_buffer", "push_payload"]
