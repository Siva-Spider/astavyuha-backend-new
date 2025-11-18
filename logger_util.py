<<<<<<< HEAD
=======
# logger_util.py
>>>>>>> d9757980f8789a522d1ce92544da52526be0c168
import os
import redis
import ssl
import json
import datetime
import logging
<<<<<<< HEAD
from threading import Lock, Thread
from collections import deque
from zoneinfo import ZoneInfo
from urllib.parse import urlparse
import asyncio
from fastapi import FastAPI

event_loop = None   # This is filled from main_fastapi on startup
# ==========================================================
# 🌍 WebSocket Connection Pool
# ==========================================================
websocket_connections = set()
=======
from threading import Lock
from collections import deque
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

>>>>>>> d9757980f8789a522d1ce92544da52526be0c168
# ==========================================================
# 🌐 Redis Setup
# ==========================================================
REDIS_URL = os.getenv("REDIS_URL", "").strip() or "redis://localhost:6379"
LOG_BUFFER = deque(maxlen=500)
log_lock = Lock()

def connect_redis():
<<<<<<< HEAD
=======
    """Connect to Redis (Upstash or local) safely."""
>>>>>>> d9757980f8789a522d1ce92544da52526be0c168
    try:
        parsed = urlparse(REDIS_URL)
        if parsed.scheme == "rediss":
            print(f"✅ Secure connection to Redis: {parsed.hostname}")
            return redis.StrictRedis.from_url(
                REDIS_URL,
<<<<<<< HEAD
                ssl_cert_reqs=ssl.CERT_NONE,
=======
                ssl_cert_reqs=ssl.CERT_NONE,   # ✅ Only this, not ssl=True
>>>>>>> d9757980f8789a522d1ce92544da52526be0c168
                decode_responses=True
            )
        else:
            print(f"✅ Connecting to Redis: {parsed.hostname}")
            return redis.StrictRedis.from_url(REDIS_URL, decode_responses=True)
<<<<<<< HEAD

=======
>>>>>>> d9757980f8789a522d1ce92544da52526be0c168
    except Exception as e:
        print(f"⚠️ Redis connection failed: {e}")
        return None

redis_client = connect_redis()

# ==========================================================
<<<<<<< HEAD
# 🧠 Logger Setup
=======
# 🧠 Global Logger Setup
>>>>>>> d9757980f8789a522d1ce92544da52526be0c168
# ==========================================================
console_logger = logging.getLogger("TradeLogger")
console_logger.setLevel(logging.INFO)

if not console_logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s - %(levelname)-7s - %(message)s")
    handler.setFormatter(formatter)
    console_logger.addHandler(handler)

# ==========================================================
<<<<<<< HEAD
# 🔥 WebSocket Async Sender
# ==========================================================
async def _push_ws_async(entry_json: str):
    print("🔥 _push_ws_async START:", entry_json)  # DEBUG
    dead = []
    for ws in websocket_connections:
        try:
            await ws.send_text(entry_json)
        except:
            dead.append(ws)

    for ws in dead:
        websocket_connections.remove(ws)

def push_ws(entry_json: str):
      # ensure we use same module instance

    loop = event_loop   # <-- THIS IS IMPORTANT
    if not loop:
        print("❌ No FastAPI event loop available")
        return

    try:
        asyncio.run_coroutine_threadsafe(
            _push_ws_async(entry_json),
            loop
        )
    except Exception as e:
        console_logger.warning(f"[WS Error] {e}")


# ==========================================================
# 📡 PUSH LOG
# ==========================================================
def push_log(message: str, level="info"):
    print("🔥 push_log called with:", message)
    ts = datetime.datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S")
    entry = {
        "ts": ts,
        "level": level.lower(),
        "message": str(message),
        "type": "log"
    }

    entry_json = json.dumps(entry)
    print(f"11111 {entry_json}")

    # Store locally
    with log_lock:
        LOG_BUFFER.append(entry)

    # Publish to Redis
    if redis_client:
        try:
            redis_client.rpush("autotrade_logs", entry_json)
            redis_client.publish("log_stream", entry_json)
        except Exception as e:
            console_logger.warning(f"[push_log] Redis unavailable: {e}")

    # Console
    formatted = f"[{ts}] {level.upper():7}: {message}"
    console_logger.info(formatted)

# ==========================================================
# 📤 PAYLOAD SENDER
# ==========================================================
def push_payload(name, data):
    ts = datetime.datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S")

    entry = {
        "type": "payload",
        "ts": ts,
        "name": name,
        "data": data
    }

    entry_json = json.dumps(entry)

    if redis_client:
        try:
            redis_client.rpush("autotrade_logs", entry_json)
            redis_client.publish("log_stream", entry_json)
        except:
=======
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
>>>>>>> d9757980f8789a522d1ce92544da52526be0c168
            pass

    with log_lock:
        LOG_BUFFER.append(entry)

<<<<<<< HEAD
    if websocket_connections:
        push_ws(entry_json)

# ==========================================================
# Getter
# ==========================================================
def get_log_buffer():
    with log_lock:
        return list(LOG_BUFFER)

async def push_log_ws(entry_json: str):
    print("🔥 WS SEND:", entry_json)
    dead_clients = []
    for ws in websocket_connections:
        try:
            await ws.send_text(entry_json)
        except:
            dead_clients.append(ws)

    for ws in dead_clients:
        websocket_connections.remove(ws)

__all__ = ["push_log", "push_payload", "get_log_buffer", "websocket_connections"]
=======
__all__ = ["push_log", "get_log_buffer", "push_payload"]
>>>>>>> d9757980f8789a522d1ce92544da52526be0c168
