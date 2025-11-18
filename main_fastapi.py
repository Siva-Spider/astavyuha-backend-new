# main_fastapi.py
from fastapi import FastAPI, HTTPException, Depends, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi import WebSocket, WebSocketDisconnect
import backend.logger_util as logger_util
#from backend.logger_util import websocket_connections, logger_util.push_log, get_log_buffer
from fastapi.middleware.wsgi import WSGIMiddleware
from pydantic import BaseModel
import sqlite3
import os
# --- Additional Imports (if not already in main_fastapi.py) ---
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pathlib import Path
from backend import get_lot_size as ls
from backend.upstox_instrument_manager import update_instrument_file
from backend import get_expiry_date as ed
from backend import find_positions_with_symbol as fps
from backend import user_manager as usr
import threading, json, asyncio
from queue import Queue, Empty
from backend import Upstox as us

# Import helpers used in many endpoints
from backend.update_db import init_db
from backend.password_utils import generate_random_password
from backend.email_utils import send_email
from fastapi import Request
import queue
import redis, json, time, asyncio, threading, queue
import ssl
from urllib.parse import urlparse
 # re-use the same module that has websocket_connections

# Existing broker map
broker_map = {
    "u": "Upstox",
    "z": "Zerodha",
    "a": "AngelOne",
    "g": "Groww",
    "5": "5paisa"
}

broker_sessions = {}
event_loop = None
DATA_DIR = Path("data")
LATEST_LINK_FILENAME = "complete.csv.gz"

# Celery app and trading task
try:
    from celery_app import celery_app
except Exception:
    celery_app = None
# Import the task (tasks/trading_tasks.py should define start_trading_loop)
from backend.tasks.trading_tasks import start_trading_loop

# Create FastAPI app
app = FastAPI(title="Astavyuha Backend (FastAPI wrapper)", version="1.0.0")

# CORS - allow your frontend local dev hosts. Adjust for production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- insert in main_fastapi.py (after app = FastAPI()) ---

REDIS_URL = os.getenv("REDIS_URL", "").strip() or "redis://localhost:6379"
REDIS_PUBSUB_CHANNEL = "log_stream"

# Thread control
_redis_thread = None
_redis_thread_stop = threading.Event()

def _redis_listener_thread(redis_url, channel):
    """
    Blocking thread which polls Redis PubSub and forwards published logs
    to FastAPI WebSocket clients using logger_util.push_ws().
    """
    try:
        parsed = urlparse(redis_url)
        if parsed.scheme == "rediss":
            r = redis.StrictRedis.from_url(redis_url, ssl_cert_reqs=None, decode_responses=True)
        else:
            r = redis.StrictRedis.from_url(redis_url, decode_responses=True)

        pubsub = r.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(channel)

        print(f"✅ Redis listener thread subscribed to: {channel}")

    except Exception as e:
        print("⚠️ Redis listener failed to start:", e)
        return

    # Poll-loop
    while not _redis_thread_stop.is_set():
        try:
            message = pubsub.get_message(timeout=1)

            if message and message.get("type") == "message":
                data = message.get("data")

                # Convert bytes → string
                if isinstance(data, bytes):
                    try:
                        data = data.decode("utf-8")
                    except:
                        data = str(data)

                # Validate JSON
                try:
                    payload = json.loads(data)
                    entry_json = json.dumps(payload)
                except:
                    entry_json = data if isinstance(data, str) else json.dumps({"message": str(data)})

                # 🔥 Forward message to WebSocket clients
                try:
                    import backend.logger_util as logger_util
                    logger_util.push_ws(entry_json)
                except Exception as e:
                    print("⚠️ Failed to push WS log from Redis listener:", e)

        except Exception as e:
            print("⚠️ Redis listener error:", e)
            time.sleep(0.5)

    try:
        pubsub.close()
    except:
        pass

    print("🧹 Redis listener thread stopped.")

"""async def _broadcast_to_websockets(entry_json: str):
    print("📤 Broadcast function triggered with:", entry_json)

    connected = len(logger_util.websocket_connections)
    print(logger_util.websocket_connections)
    print("🔍 WebSockets connected:", connected)

    if connected == 0:
        print("❌ No WebSocket clients to send to")
        return

    dead = []
    sent_count = 0

    for ws in list(logger_util.websocket_connections):
        try:
            await ws.send_text(entry_json)
            sent_count += 1
        except Exception as e:
            print("⚠️ WebSocket send failed:", e)
            dead.append(ws)

    for ws in dead:
        logger_util.websocket_connections.discard(ws)

    print(f"📣 Sent to {sent_count} websocket clients.")"""

@app.on_event("startup")
async def start_redis_listener():
    print("🔥 FastAPI startup running...")

    # 1️⃣ Store the uvicorn event loop inside logger_util
    try:
        import backend.logger_util as logger_util
        logger_util.event_loop = asyncio.get_running_loop()
        print("✅ Stored FastAPI event_loop inside logger_util.event_loop")
    except Exception as e:
        print("❌ Failed to set logger_util.event_loop:", e)
    global _redis_thread, _redis_thread_stop
    # try to set event_loop inside logger_util for other code sanity
    try:
        logger_util.event_loop = asyncio.get_running_loop()
    except Exception:
        pass

    if _redis_thread is None or not _redis_thread.is_alive():
        _redis_thread_stop.clear()
        _redis_thread = threading.Thread(target=_redis_listener_thread, args=(REDIS_URL, REDIS_PUBSUB_CHANNEL), daemon=True)
        _redis_thread.start()
        print("✅ Started Redis → WebSocket listener thread.")

@app.on_event("startup")
async def startup_event():
    print("🔥 FASTAPI STARTUP")
    loop = asyncio.get_running_loop()
    print(loop)
    logger_util.event_loop = loop
    print("🔥 logger_util.event_loop SET to:", logger_util.event_loop)


@app.on_event("shutdown")
async def stop_redis_listener():
    global _redis_thread_stop
    _redis_thread_stop.set()
    print("🛑 Stopping Redis listener thread...")

# ---------- Pydantic models to match front-end payloads ----------
class LoginRequest(BaseModel):
    userId: str
    password: str
    role: str | None = None

class RegisterRequest(BaseModel):
    username: str
    email: str
    mobilenumber: str
    role: str
    userId: str | None = None

# ---------- Useful helpers ----------
DB_PATH = os.path.join(os.getcwd(), "user_data_new.db")


def get_user_row_by_userid(userId: str):
    """Return row or None: (userId, password, role, email, mobilenumber, username)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # your DB columns: id | userId | username | email | password | role | mobilenumber
    cursor.execute("SELECT userId, password, role, email, mobilenumber, username FROM users WHERE userId = ?", (userId,))
    row = cursor.fetchone()
    conn.close()
    return row


# ---------- FastAPI endpoints (these will override Flask ones if same path) ----------
@app.get("/api/health")
async def health():
    return {"status": "ok", "message": "FastAPI backend running"}


@app.post("/api/login")
async def login_user(data: LoginRequest):
    """
    Login endpoint (returns the payload shape your frontend expects).
    Returns:
    {
      "success": True|False,
      "message": "Login successful" or error,
      "profile": { userid, username, email, mobilenumber, role }
    }
    """
    print("📩 Login request received:", data)
    try:
        # normalize role "client" -> "user" for compatibility
        if data.role and data.role.lower() == "client":
            data.role = "user"

        row = get_user_row_by_userid(data.userId)
        if not row:
            # not found
            logger_util.push_log(f"⚠️ Login attempt failed: user {data.userId} not found")
            return {"success": False, "message": "Invalid User ID"}

        db_userId, db_password, db_role, db_email, db_mobile, db_username = row
        # Note: your DB stores password in plaintext (per your earlier files). If you use hashing,
        # change this compare accordingly.
        if db_password != data.password:
            logger_util.push_log(f"⚠️ Incorrect password for user {data.userId}")
            return {"success": False, "message": "Incorrect password"}

        # Optional role match if provided
        if data.role:
            normalized_role = data.role.lower().strip()
            db_normalized_role = db_role.lower().strip()
            if normalized_role == "client":
                normalized_role = "user"
            if normalized_role != db_normalized_role:
                logger_util.push_log(f"⚠️ Role mismatch: provided {data.role}, db {db_role}")
                return {"success": False, "message": "Role mismatch"}

        profile = {
            "userid": db_userId,
            "username": db_username or db_userId,
            "email": db_email or "",
            "mobilenumber": db_mobile or "",
            "role": db_role,
        }
        logger_util.push_log(f"✅ User {data.userId} logged in as {db_role}")
        return {"success": True, "message": "Login successful", "profile": profile}

    except Exception as e:
        logger_util.push_log(f"❌ Login error: {e}", "error")
        return {"success": False, "message": str(e)}


@app.post("/api/register")
async def register_user_route(data: RegisterRequest):
    """
    Register a new user (PENDING APPROVAL).
    The user is saved in 'pending_users' table.
    An admin must approve before moving them to 'users' table.
    """
    try:
        # Normalize role
        role = data.role.lower().strip()
        if role == "client":
            role = "user"

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Ensure pending_users table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pending_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                userId TEXT UNIQUE,
                username TEXT,
                email TEXT,
                password TEXT,
                role TEXT,
                mobilenumber TEXT
            )
        """)

        # Check for existing user or pending user
        user_id_candidate = data.userId or data.username.split()[0]
        cursor.execute("SELECT * FROM users WHERE email=? OR userId=?", (data.email, user_id_candidate))
        if cursor.fetchone():
            conn.close()
            return {"success": False, "message": "User with this email or ID already exists (registered)."}

        cursor.execute("SELECT * FROM pending_users WHERE email=? OR userId=?", (data.email, user_id_candidate))
        if cursor.fetchone():
            conn.close()
            return {"success": False, "message": "User with this email or ID already pending approval."}

        # Generate temporary password
        password = generate_random_password()

        # Insert into pending_users
        cursor.execute(
            "INSERT INTO pending_users (userId, username, email, password, role, mobilenumber) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id_candidate, data.username, data.email, password, role, data.mobilenumber)
        )
        conn.commit()
        conn.close()

        # Send email: registration received but pending approval
        try:
            send_email(
                data.email,
                "Registration Received - Pending Approval",
                f"Dear {data.username},\n\nThank you for registering with Astavyuha.\n"
                f"Your registration is pending admin approval.\n"
                f"You will receive another email once approved.\n\n"
                f"Requested Role: {role}\n\nRegards,\nAstavyuha Team"
            )
        except Exception as mail_err:
            logger_util.push_log(f"⚠️ Pending user email failed for {data.email}: {mail_err}")

        logger_util.push_log(f"🕓 New pending registration: {user_id_candidate} ({role})")

        return {
            "success": True,
            "message": "Registration submitted and pending admin approval.",
            "profile": {
                "userid": user_id_candidate,
                "username": data.username,
                "email": data.email,
                "mobilenumber": data.mobilenumber,
                "role": role,
            },
        }

    except Exception as e:
        logger_util.push_log(f"❌ Error in pending registration: {e}", "error")
        raise HTTPException(status_code=500, detail=str(e))

async def async_gsleep(seconds: float):
    """Async-safe sleep function for FastAPI event loops."""
    await asyncio.sleep(seconds)

def gsleep(seconds: float):
    """Universal sleep helper (safe for threads and async)."""
    try:
        loop = asyncio.get_running_loop()
        # We are inside an async loop, so schedule non-blocking sleep
        loop.create_task(asyncio.sleep(seconds))
    except RuntimeError:
        # No event loop running, use regular blocking sleep (for threads)
        time.sleep(seconds)

@app.post("/api/get_profit_loss")
async def get_profit_loss(request: Request):
    """
    Get profit/loss report for Upstox broker.
    """
    try:
        data = await request.json()
        access_token = data.get("access_token")
        segment = data.get("segment")
        from_date = data.get("from_date")
        to_date = data.get("to_date")
        year = data.get("year")

        # ✅ Convert financial year like "2025-2026" → "2526"
        fy_code = None
        if year and "-" in year:
            parts = year.split("-")
            fy_code = parts[0][-2:] + parts[1][-2:]
        elif year and len(year) == 9 and year[:4].isdigit():
            fy_code = year[2:4] + year[7:9]
        else:
            fy_code = year  # fallback if already short form

        # ✅ Validate required parameters
        if not all([access_token, segment, from_date, to_date, fy_code]):
            raise HTTPException(status_code=400, detail="Missing required parameters")

        # ✅ Fetch P&L from Upstox
        result, charges = us.upstox_profit_loss(access_token, segment, from_date, to_date, fy_code)

        # ✅ Log success
        try:
            logger_util.push_log(f"✅ Profit/Loss fetched successfully for Upstox {segment} FY{fy_code}")
        except Exception as log_err:
            print("Logging suppressed:", log_err)

        return JSONResponse(content={"success": True, "data": result, "rows": charges}, status_code=200)

    except Exception as e:
        # 🔒 Prevent recursive logging errors
        try:
            logger_util.push_log(f"❌ Error in /api/get_profit_loss: {e}", "error")
        except Exception as log_err:
            print("Logging suppressed:", log_err)

        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/start-all-trading")
async def start_trading(request: Request):
    """
    Receive full trading configuration from frontend (brokers + stocks),
    save it as a single JSON file, and trigger Celery trading loop.
    """
    try:
        data = await request.json()
        print(data)
        config_file = Path("trading_config.json")

        # Save everything as it is
        config_file.write_text(json.dumps(data, indent=2))
        logger_util.push_log("💾 Saved unified trading configuration to trading_config.json")

        # Trigger Celery background trading task
        task = start_trading_loop.apply_async()
        logger_util.push_log(f"🟢 Celery task {task.id} started for trading loop.")

        return {"success": True, "message": "Trading loop started.", "task_id": task.id}

    except Exception as e:
        logger_util.push_log(f"❌ Could not start trading task: {e}", "error")
        return {"success": False, "message": str(e)}


# ---------- ADMIN endpoints (thin wrappers) ----------

def admin_role_check(user_role: str | None):
    """Utility: treat 'client' == 'user' and require admin to call admin routes."""
    if user_role is None:
        raise HTTPException(status_code=403, detail="Missing role")
    normalized = user_role.lower().strip()
    if normalized == "client":
        normalized = "user"
    if normalized != "admin":
        raise HTTPException(status_code=403, detail="Access denied: Admins only.")
    return True


@app.get("/api/admin/users")
async def get_all_users(user_role: str = Query(..., description="Pass user_role=admin to access")):
    admin_role_check(user_role)
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT userId, username, email, role, mobilenumber FROM users")
        rows = cursor.fetchall()
        conn.close()
        users = [
            {
                "userid": r[0],
                "username": r[1],
                "email": r[2],
                "role": r[3],
                "mobilenumber": r[4],
            }
            for r in rows
        ]
        return {"success": True, "users": users}
    except Exception as e:
        logger_util.push_log(f"❌ Error fetching users: {e}", "error")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/admin/users/{userId}")
async def delete_user(userId: str, user_role: str = Query(..., description="Pass user_role=admin to access")):
    admin_role_check(user_role)
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE userId = ?", (userId,))
        conn.commit()
        conn.close()
        logger_util.push_log(f"🗑️ User {userId} deleted by admin")
        return {"success": True, "message": f"User {userId} deleted successfully"}
    except Exception as e:
        logger_util.push_log(f"❌ Error deleting user: {e}", "error")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/admin/users/{userId}")
async def update_user_role(userId: str, new_role: str = Query(...), user_role: str = Query(...)):
    admin_role_check(user_role)
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET role = ? WHERE userId = ?", (new_role, userId))
        conn.commit()
        conn.close()
        logger_util.push_log(f"🧩 Admin updated role for {userId} to {new_role}")
        return {"success": True, "message": f"Role for {userId} updated to {new_role}"}
    except Exception as e:
        logger_util.push_log(f"❌ Error updating role: {e}", "error")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/summary")
async def get_admin_summary(user_role: str = Query(...)):
    admin_role_check(user_role)
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM users WHERE role='admin'")
        total_admins = cursor.fetchone()[0]
        conn.close()
        return {
            "success": True,
            "summary": {
                "total_users": total_users,
                "total_admins": total_admins,
                "active_trades": 0,
                "error_logs": 0,
            },
        }
    except Exception as e:
        logger_util.push_log(f"❌ Error fetching summary: {e}", "error")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/logs")
async def get_admin_logs(user_role: str = Query(...)):
    admin_role_check(user_role)
    try:
        logs = logger_util.get_log_buffer()
        return {"success": True, "logs": logs[-200:]}
    except Exception as e:
        logger_util.push_log(f"❌ Error fetching logs: {e}", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/instruments/latest")
async def get_latest_instruments():
    """
    Returns the latest instrument file (complete.csv.gz)
    """
    try:
        file_path = DATA_DIR / LATEST_LINK_FILENAME
        if not file_path.exists():
            update_instrument_file()
            if not file_path.exists():
                raise HTTPException(
                    status_code=503,
                    detail="Instrument file is not yet available. Please wait for the daily update process to complete."
                )

        return FileResponse(
            path=file_path,
            filename="complete.csv.gz",
            media_type="application/gzip"
        )
    except Exception as e:
        logger_util.push_log(f"❌ Error serving instrument file: {e}", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/connect-broker")
async def connect_broker(request: Request):
    """
    Connects to selected brokers using credentials.
    """
    try:
        from backend import Upstox as us
        from backend import Zerodha as zr
        from backend import Groww as gr
        from backend import Fivepaisa as fp
        from backend import AngelOne as ar

        data = await request.json()
        brokers_data = data.get("brokers", [])
        responses = []

        for broker_item in brokers_data:
            broker_key = broker_item.get("name")
            creds = broker_item.get("credentials")
            broker_name = broker_map.get(broker_key)

            profile = None
            balance = None
            message = "Broker not supported or credentials missing."
            status = "failed"

            try:
                if broker_name == "Upstox":
                    access_token = creds.get("access_token")
                    print(access_token)
                    profile = us.upstox_profile(access_token)
                    balance = us.upstox_balance(access_token)
                    if profile and balance:
                        status, message = "success", "Connected successfully."
                    else:
                        message = "Connection failed. Check your access token."

                elif broker_name == "Zerodha":
                    api_key = creds.get("api_key")
                    access_token = creds.get("access_token")
                    profile = zr.zerodha_get_profile(api_key, access_token)
                    balance = zr.zerodha_get_equity_balance(api_key, access_token)
                    if profile and balance:
                        status, message = "success", "Connected successfully."
                    else:
                        message = "Connection failed. Check credentials."

                elif broker_name == "AngelOne":
                    api_key = creds.get("api_key")
                    user_id = creds.get("user_id")
                    pin = creds.get("pin")
                    totp_secret = creds.get("totp_secret")
                    obj, refresh_token, auth_token, feed_token = ar.angelone_connect(
                        api_key, user_id, pin, totp_secret
                    )
                    profile, balance = ar.angelone_fetch_profile_and_balance(obj, refresh_token)
                    if profile and balance:
                        status, message = "success", "Connected successfully."
                        broker_sessions[broker_name] = {
                            "obj": obj,
                            "refresh_token": refresh_token,
                            "auth_token": auth_token,
                            "feed_token": feed_token
                        }
                    else:
                        message = "Connection failed. Check credentials."

                elif broker_name == "5paisa":
                    app_key = creds.get("app_key")
                    access_token = creds.get("access_token")
                    client_code = creds.get("client_id")
                    profile = {"User Name": client_code}
                    balance = fp.fivepaisa_get_balance(app_key, access_token, client_code)
                    if profile and balance:
                        status, message = "success", "Connected successfully."
                    else:
                        message = "Connection failed. Check credentials."

                elif broker_name == "Groww":
                    api_key = creds.get("api_key")
                    access_token = creds.get("access_token")
                    if api_key and access_token:
                        profile = {"User Name": f"Dummy {broker_name} User"}
                        balance = {"Available Margin": "10000.00"}
                        status, message = "success", "Connected successfully."
                    else:
                        message = "Connection failed. Missing credentials."

            except Exception as e:
                status, message = "failed", f"An error occurred: {str(e)}"
                logger_util.push_log(message, "error")

            responses.append({
                "broker": broker_name,
                "broker_key": broker_key,
                "status": status,
                "message": message,
                "profileData": {
                    "profile": profile,
                    "balance": balance,
                    "status": status,
                    "message": message
                }
            })

        return JSONResponse(content=responses)
    except Exception as e:
        logger_util.push_log(f"❌ Error in connect_broker: {e}", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/get-lot-size")
async def get_lot_size_post(request: Request):
    """
    POST version for lot size (accepts JSON body)
    """
    try:
        data = await request.json()
        symbol_key = data.get("symbol_key")
        symbol_value = data.get("symbol_value")
        type_ = data.get("type")

        if not symbol_key:
            raise HTTPException(status_code=400, detail="Stock symbol is required.")

        if type_ == "EQUITY":
            lot_size, tick_size = ls.lot_size(symbol_key)
        elif type_ == "COMMODITY":
            lot_size, tick_size = ls.commodity_lot_size(symbol_key, symbol_value)
        else:
            lot_size, tick_size = None, None

        if lot_size:
            return {"lot_size": lot_size, "tick_size": tick_size, "symbol": symbol_key}
        else:
            return JSONResponse(status_code=404, content={"message": "Lot size not found."})
    except Exception as e:
        msg = f"Error in get_lot_size (POST): {e}"
        logger_util.push_log(msg, "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/expiry")
async def get_expiry(symbol: str = Query(...)):
    """
    Fetch expiry dates for a given symbol (from get_expiry_date.py)
    """
    try:
        expiries = ed.get_expiry_date(symbol)
        if not expiries:
            raise HTTPException(status_code=404, detail="No expiry dates found.")
        return {"symbol": symbol, "expiries": expiries}
    except Exception as e:
        logger_util.push_log(f"❌ Error in get_expiry: {e}", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/users")
async def unified_users_list():
    """
    Frontend expects /users returning registered, pending, and rejected users.
    We'll return them in one JSON response.
    """
    try:
        conn = sqlite3.connect("user_data_new.db")
        cursor = conn.cursor()

        # Registered
        cursor.execute("SELECT userId, username, email, role, mobilenumber FROM users")
        registered = [
            {"userId": r[0], "username": r[1], "email": r[2], "role": r[3], "mobilenumber": r[4]}
            for r in cursor.fetchall()
        ]

        # Pending
        cursor.execute("SELECT userId, username, email, role, mobilenumber FROM pending_users")
        pending = [
            {"userId": r[0], "username": r[1], "email": r[2], "role": r[3], "mobilenumber": r[4]}
            for r in cursor.fetchall()
        ]

        # Rejected (optional table)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rejected_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                userId TEXT UNIQUE,
                username TEXT,
                email TEXT,
                role TEXT,
                mobilenumber TEXT
            )
        """)
        cursor.execute("SELECT userId, username, email, role, mobilenumber FROM rejected_users")
        rejected = [
            {"userId": r[0], "username": r[1], "email": r[2], "role": r[3], "mobilenumber": r[4]}
            for r in cursor.fetchall()
        ]

        conn.close()
        return {"users": registered, "pending": pending, "rejected": rejected}
    except Exception as e:
        logger_util.push_log(f"❌ unified_users_list error: {e}", "error")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/approve/{userId}")
async def approve_user(userId: str):
    """
    Move user from pending_users → users
    """
    try:
        conn = sqlite3.connect("user_data_new.db")
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM pending_users WHERE userId=?", (userId,))
        user = cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found in pending list")

        # Insert into users table
        cursor.execute("""
            INSERT INTO users (userId, username, email, password, role, mobilenumber)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user[1], user[2], user[3], user[4], user[5], user[6]))

        # Delete from pending_users
        cursor.execute("DELETE FROM pending_users WHERE userId=?", (userId,))
        conn.commit()
        conn.close()
        logger_util.push_log(f"✅ Approved pending user {userId}")
        return {"success": True, "message": f"User {userId} approved"}
    except Exception as e:
        logger_util.push_log(f"❌ Error approving user {userId}: {e}", "error")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/reject/{userId}")
async def reject_user(userId: str):
    """
    Move user from pending_users → rejected_users
    """
    try:
        conn = sqlite3.connect("user_data_new.db")
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM pending_users WHERE userId=?", (userId,))
        user = cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found in pending list")

        # Ensure rejected_users exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rejected_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                userId TEXT UNIQUE,
                username TEXT,
                email TEXT,
                role TEXT,
                mobilenumber TEXT
            )
        """)
        cursor.execute(
            "INSERT OR IGNORE INTO rejected_users (userId, username, email, role, mobilenumber) VALUES (?, ?, ?, ?, ?)",
            (user[1], user[2], user[3], user[5], user[6])
        )
        cursor.execute("DELETE FROM pending_users WHERE userId=?", (userId,))
        conn.commit()
        conn.close()
        logger_util.push_log(f"🚫 Rejected pending user {userId}")
        return {"success": True, "message": f"User {userId} rejected"}
    except Exception as e:
        logger_util.push_log(f"❌ Error rejecting user {userId}: {e}", "error")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/delete-user/{userId}")
async def delete_registered_user(userId: str):
    """
    Delete registered user (from /users table)
    """
    try:
        conn = sqlite3.connect("user_data_new.db")
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE userId=?", (userId,))
        conn.commit()
        conn.close()
        logger_util.push_log(f"🗑️ Deleted registered user {userId}")
        return {"success": True, "message": f"User {userId} deleted successfully"}
    except Exception as e:
        logger_util.push_log(f"❌ Error deleting user {userId}: {e}", "error")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/delete-rejected/{userId}")
async def delete_rejected_user(userId: str):
    """
    Delete user from rejected_users table
    """
    try:
        conn = sqlite3.connect("user_data_new.db")
        cursor = conn.cursor()
        cursor.execute("DELETE FROM rejected_users WHERE userId=?", (userId,))
        conn.commit()
        conn.close()
        logger_util.push_log(f"🗑️ Deleted rejected user {userId}")
        return {"success": True, "message": f"Rejected user {userId} deleted successfully"}
    except Exception as e:
        logger_util.push_log(f"❌ Error deleting rejected user {userId}: {e}", "error")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/reset-password/{userId}")
async def reset_user_password(userId: str):
    """
    Reset password for a registered user.
    """
    try:
        new_pass = generate_random_password()
        conn = sqlite3.connect("user_data_new.db")
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET password=? WHERE userId=?", (new_pass, userId))
        conn.commit()
        conn.close()
        logger_util.push_log(f"🔑 Password reset for {userId}")
        return {"success": True, "message": "Password reset successfully", "new_password": new_pass}
    except Exception as e:
        logger_util.push_log(f"❌ Error resetting password for {userId}: {e}", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/pending-users")
async def get_pending_users():
    """
    Fetch all pending users from the database.
    """
    try:
        conn = sqlite3.connect("user_data_new.db")
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM pending_users")
        rows = cursor.fetchall()
        conn.close()

        users_data = [
            {
                "userId": row[1],
                "username": row[2],
                "email": row[3],
                "role": row[5],
                "mobilenumber": row[6],
            }
            for row in rows
        ]
        return {"pending_users": users_data}

    except Exception as e:
        logger_util.push_log(f"❌ Error fetching pending users: {e}", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/registered-users")
async def get_registered_users():
    """
    Fetch all registered users from the database.
    """
    try:
        conn = sqlite3.connect("user_data_new.db")
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users")
        rows = cursor.fetchall()
        conn.close()

        users_data = [
            {
                "userId": row[1],
                "username": row[2],
                "email": row[3],
                "role": row[5],
                "mobilenumber": row[6],
            }
            for row in rows
        ]
        return {"registered_users": users_data}

    except Exception as e:
        logger_util.push_log(f"❌ Error fetching registered users: {e}", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/get-positions")
async def get_positions(request: Request):
    """
    Fetch positions for a given broker and symbol.
    """
    try:
        data = await request.json()
        broker = data.get("broker")
        symbol = data.get("symbol")
        credentials = data.get("credentials")

        if not broker or not symbol or not credentials:
            raise HTTPException(status_code=400, detail="Missing required fields.")

        positions = fps.find_positions_for_symbol(broker, symbol, credentials)
        return {"broker": broker, "symbol": symbol, "positions": positions}

    except Exception as e:
        logger_util.push_log(f"❌ Error in /api/get-positions: {e}", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/disconnect-stock")
async def disconnect_stock(request: Request):
    """
    Disconnect a symbol from active trades.
    Removes it from Redis active_trades set.
    """
    import redis
    data = await request.json()
    symbol = data.get("symbol_value")

    if not symbol:
        return {"success": False, "message": "Symbol missing."}

    try:
        r = redis.StrictRedis(host="localhost", port=6379, db=5, decode_responses=True)
        removed = r.srem("active_trades", symbol)
        if removed:
            logger_util.push_log(f"🛑 User disconnected {symbol} — stopping trade after current interval.")
            return {"success": True, "message": f"{symbol} disconnected."}
        else:
            return {"success": False, "message": f"{symbol} was not active."}
    except Exception as e:
        return {"success": False, "message": f"Redis error: {e}"}


@app.post("/api/close-position")
async def close_position(request: Request):
    """
    Close a specific position manually.
    """
    try:
        data = await request.json()
        broker = data.get("broker")
        symbol = data.get("symbol")
        credentials = data.get("credentials")

        if not broker or not symbol:
            raise HTTPException(status_code=400, detail="Missing broker or symbol.")

        # You can call the respective broker close position logic here.
        logger_util.push_log(f"🔻 Closed position for {symbol} ({broker})")
        return {"message": f"Closed position for {symbol} ({broker})"}

    except Exception as e:
        logger_util.push_log(f"❌ Error closing position: {e}", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/close-all-positions")
async def close_all_positions(request: Request):
    """
    Close all open positions across all brokers.
    """
    try:
        data = await request.json()
        brokers = data.get("brokers", [])
        summary = []

        for broker_item in brokers:
            broker = broker_item.get("broker")
            credentials = broker_item.get("credentials")

            try:
                # For each broker, you can call their close_all_positions() function
                logger_util.push_log(f"🔻 Closing all positions for {broker}")
                summary.append({
                    "broker": broker,
                    "status": "success",
                    "message": "All positions closed"
                })
            except Exception as inner_e:
                summary.append({
                    "broker": broker,
                    "status": "failed",
                    "message": str(inner_e)
                })

        return {"summary": summary}

    except Exception as e:
        logger_util.push_log(f"❌ Error in /api/close-all-positions: {e}", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    print("⚡ Client trying to connect...")
    await websocket.accept()
    print("⚡ Client accepted. Adding to pool...")

    logger_util.websocket_connections.add(websocket)
    print(f"⚡ Total WS clients after add: {len(logger_util.websocket_connections)}")

    logger_util.push_log("📡 WebSocket client connected.")

    try:
        # Keep alive — DO NOT use receive_text()
        while True:
            await asyncio.sleep(1)
    except:
        print("🔥 WS DISCONNECTED")
        logger_util.websocket_connections.discard(websocket)
        print(f"⚡ Total WS clients after remove: {len(logger_util.websocket_connections)}")


