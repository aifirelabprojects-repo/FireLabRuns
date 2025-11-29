
import asyncio
import time
from typing import Optional
from fastapi import Query, HTTPException
import httpx
import logging

from Config import CACHE_TTL, MAX_OUTBOUND_CONCURRENCY, RATE_LIMIT_PER_SECOND, TOKEN_BUCKET_CAPACITY

logger = logging.getLogger("verify")
logger.setLevel(logging.DEBUG)

UPSTREAM = "https://rapid-email-verifier.fly.dev/api/validate"


outbound_semaphore = asyncio.Semaphore(MAX_OUTBOUND_CONCURRENCY)

_cache_lock = asyncio.Lock()
_cache: dict[str, tuple[dict, float]] = {}  

_token_lock = asyncio.Lock()
_tokens = TOKEN_BUCKET_CAPACITY
_last_refill = time.monotonic()

httpx_client: Optional[httpx.AsyncClient] = None



async def get_cached(email: str) -> Optional[dict]:
    async with _cache_lock:
        row = _cache.get(email)
        if not row:
            return None
        data, expires_at = row
        if time.time() > expires_at:
            # expired
            del _cache[email]
            return None
        return data

async def set_cache(email: str, data: dict, ttl: int = CACHE_TTL):
    async with _cache_lock:
        _cache[email] = (data, time.time() + ttl)

async def allow_request() -> bool:
    global _tokens, _last_refill
    async with _token_lock:
        now = time.monotonic()
        elapsed = now - _last_refill
        if elapsed > 0:
            # refill
            refill_amount = elapsed * RATE_LIMIT_PER_SECOND
            _tokens = min(TOKEN_BUCKET_CAPACITY, _tokens + refill_amount)
            _last_refill = now
        if _tokens >= 1:
            _tokens -= 1
            return True
        return False




async def fetch_upstream(email: str):
    max_retries = 2
    backoff = 0.25
    for attempt in range(1, max_retries + 1):
        try:
            resp = await httpx_client.get(UPSTREAM, params={"email": email})
        except Exception as exc:
            logger.exception("HTTP request to upstream failed on attempt %s: %s", attempt, exc)
            if attempt == max_retries:
                raise HTTPException(status_code=502, detail=f"Failed to contact upstream: {exc}")
            await asyncio.sleep(backoff * (2 ** (attempt - 1)))
            continue

        try:
            body_text = resp.text
        except Exception:
            body_text = "<could not read body>"
        logger.debug("Upstream resp status=%s url=%s content_len=%s body_snippet=%s",
                     resp.status_code, resp.url, len(body_text), (body_text[:500] if body_text else ""))
        if resp.status_code < 200 or resp.status_code >= 300:
            raise HTTPException(status_code=502, detail=f"Upstream {resp.status_code}: {body_text[:1000]}")

        # parse JSON safely
        try:
            return resp.json()
        except ValueError as e:
            logger.exception("Failed to parse JSON from upstream: %s", e)
            raise HTTPException(status_code=502, detail="Invalid JSON from upstream")
    raise HTTPException(status_code=502, detail="Upstream request failed after retries")

def init(app):
    @app.get("/verify/email")
    async def verify(email: str = Query(..., min_length=3)):
        email = email.strip().lower()
        # 1) check in-memory cache
        cached = await get_cached(email)
        if cached is not None:
            return cached
        if not await allow_request():
            raise HTTPException(status_code=429, detail="Rate limit reached. Try again later.")

        async with outbound_semaphore:
            try:
                data = await fetch_upstream(email)
            except httpx.HTTPStatusError as e:
                raise HTTPException(status_code=502, detail=f"Upstream error: {str(e)}")
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"Upstream request failed: {str(e)}")

        await set_cache(email, data)
        return data
