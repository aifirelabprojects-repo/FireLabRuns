import os
import redis


def get_cache(session_id: str = "default"):


    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    try:
        # Try connecting to Redis
        r = redis.Redis.from_url(redis_url, decode_responses=True)
        r.ping()  # check connection
        print("✅ Connected to Redis at", redis_url)



    except redis.exceptions.ConnectionError:
        print("⚠️ Redis not available — using in-memory cache")

memory = get_cache(session_id="user_001")