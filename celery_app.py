# celery_app.py
from celery import Celery
import os

try:
    # Try to use real Redis
    CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/1")
    CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/2")

    # Quick check if Redis is alive
    import redis
    r = redis.Redis(host="localhost", port=6379, db=1)
    r.ping()
    print("✅ Connected to real Redis")

except Exception:
    print("⚠️ Redis not found, using in-memory FakeRedis for Celery")
    import fakeredis

    # Create a fake Redis instance
    fake_redis_server = fakeredis.FakeServer()

    # Celery doesn’t directly use fakeredis, so we simulate an in-memory broker
    CELERY_BROKER_URL = "memory://"
    CELERY_RESULT_BACKEND = "cache+memory://"

# --- Initialize Celery ---
celery_app = Celery("astavyuha_tasks", broker=CELERY_BROKER_URL, backend=CELERY_RESULT_BACKEND)
celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Kolkata",
    enable_utc=False
)
