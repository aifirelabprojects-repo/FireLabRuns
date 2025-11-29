import hashlib
import os
import json
import sys
import asyncio
import time
from typing import Dict, Any, Tuple, List
import re
from dotenv import load_dotenv
from openai import AsyncOpenAI
import aiohttp
from functools import lru_cache
import json
import asyncio
import re
from typing import Any, Dict, List, Tuple
from fastapi import Depends, HTTPException
from sqlalchemy.orm import selectinload
from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from Schemas import VerifyPayload
from SessionUtils import get_field, set_field
from database import CompanyDetails, Session as SessionModel, VerificationDetails, get_db
from functools import lru_cache
from cachetools import TTLCache  

load_dotenv()

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

DEFAULT_CONCURRENT_LIMIT = 5
DEFAULT_RPM = 60
CACHE_TTL_SECONDS = 86400
API_TIMEOUT = 30.0
MAX_RETRIES = 3
BASE_DELAY = 1.0
MAX_DELAY = 10.0
SEARCH_IMAGE_LIMIT = 3

json_schema = {
    "name": "verification_response",
    "strict": True, 
    "schema": {
        "type": "object",
        "properties": {
            "verified": {
                "type": "boolean",
                "description": "Whether the user is verified in the role at the company."
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 100,
                "description": "Confidence score as an integer between 0 and 100."
            },
            "details": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                    "username": {"type": "string"},
                    "email": {"type": "string"},
                    "company": {"type": "string"},
                    "evidence": {
                        "type": "string",
                        "description": "Brief 1-sentence summary of key evidence (or 'Insufficient evidence found')."
                    }
                },
                "required": ["name", "role", "username", "email", "company", "evidence"],
                "additionalProperties": False
            }
        },
        "required": ["verified", "confidence", "details"],
        "additionalProperties": False
    }
}


class AsyncRateLimiter:
    def __init__(self, concurrent_limit: int = DEFAULT_CONCURRENT_LIMIT, rpm: int = DEFAULT_RPM):
        self.semaphore = asyncio.Semaphore(concurrent_limit)
        self.rpm = rpm
        self.tokens = rpm
        self.last_refill = time.time()
        self.lock = asyncio.Lock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    async def acquire(self):
        async with self.semaphore:
            async with self.lock:
                now = time.time()
                elapsed = now - self.last_refill
                self.tokens = min(self.rpm, self.tokens + (elapsed / 60) * self.rpm)
                self.last_refill = now
                
                while self.tokens < 1:
                    await asyncio.sleep(60 / self.rpm)
                    now = time.time()
                    elapsed = now - self.last_refill
                    self.tokens = min(self.rpm, self.tokens + (elapsed / 60) * self.rpm)
                    self.last_refill = now
                
                self.tokens -= 1


@lru_cache(maxsize=1)
def get_rate_limiter() -> AsyncRateLimiter:
    """Lazy initialization with caching."""
    concurrent = int(os.getenv("RATE_LIMIT_CONCURRENT", DEFAULT_CONCURRENT_LIMIT))
    rpm = int(os.getenv("RATE_LIMIT_RPM", DEFAULT_RPM))
    return AsyncRateLimiter(concurrent, rpm)


class TTLCache:
    def __init__(self, ttl_seconds: int = CACHE_TTL_SECONDS):
        self.cache: Dict[str, Tuple[Any, float]] = {}
        self.ttl = ttl_seconds

    def get(self, key: str) -> Any | None:
        if key not in self.cache:
            return None
        
        value, timestamp = self.cache[key]
        if time.time() - timestamp < self.ttl:
            return value
        
        del self.cache[key]
        return None

    def set(self, key: str, value: Any) -> None:
        self.cache[key] = (value, time.time())


# Global instances
ttl_cache = TTLCache()
perplexity_client = AsyncOpenAI(
    api_key=os.getenv("PERPLEXITY_API_KEY"),
    base_url="https://api.perplexity.ai"
)


def get_cache_key(inputs: Dict[str, str]) -> str:
    """Generate MD5 hash from sorted input dict."""
    key_str = ''.join(f"{k}:{v}" for k, v in sorted(inputs.items()))
    return hashlib.md5(key_str.encode()).hexdigest()


def extract_name_from_email(email: str) -> str:
    """Extract and format name from email address."""
    match = re.match(r'^(.+)@', email.strip().lower())
    if not match:
        return ''
    
    name = match.group(1).replace('.', ' ').replace('_', ' ')
    return ' '.join(word.capitalize() for word in name.split() if word)


def normalize_inputs(company: str, role: str, username: str, email: str) -> Dict[str, str]:
    """Normalize and validate user inputs."""
    return {
        "company": company.lower().strip(),
        "role": role.lower().strip(),
        "username": username.lower().strip() if username else "",
        "email": email.lower().strip()
    }


async def retry_on_failure(coro_func, max_retries: int = MAX_RETRIES, 
                          base_delay: float = BASE_DELAY, max_delay: float = MAX_DELAY):
    """Retry async operation with exponential backoff."""
    last_exception = None
    
    for attempt in range(max_retries + 1):
        try:
            return await coro_func()
        except Exception as e:
            last_exception = e
            if attempt == max_retries:
                raise
            
            delay = min(base_delay * (2 ** attempt) + (time.time() % 1.0), max_delay)
            await asyncio.sleep(delay)
    
    raise last_exception


def extract_image_urls(data: Dict[str, Any]) -> List[str]:
    """Extract image URLs from API response."""
    images = data.get('images', [])
    urls = []
    
    for img in images[:SEARCH_IMAGE_LIMIT]:
        if isinstance(img, dict) and 'original' in img:
            original = img['original']
            if isinstance(original, dict) and 'link' in original:
                urls.append(original['link'])
    
    return urls


async def fetch_images(company: str, username: str) -> List[str]:
    """Fetch LinkedIn profile images from search API."""
    if not username:
        return []
    
    api_key = os.getenv("SEARCH_API_KEY")
    if not api_key:
        print("No SEARCH_API_KEY configured")
        return []
    
    query = f"{company} linkedin {username}".strip().lower()
    params = {
        "engine": "google_images",
        "q": query,
        "api_key": api_key,
        "num": SEARCH_IMAGE_LIMIT,
    }
    
    limiter = get_rate_limiter()
    await limiter.acquire()
    
    async def fetch_coro():
        connector = aiohttp.TCPConnector(limit=10)
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.get("https://www.searchapi.io/api/v1/search", params=params) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
                return extract_image_urls(data)
    
    try:
        return await retry_on_failure(fetch_coro, max_retries=MAX_RETRIES)
    except Exception as e:
        print(f"Image fetch error: {str(e)}")
        return []


async def get_verification(company: str, role: str, username: str, email: str, 
                          display_username: str) -> Tuple[str, List[str]]:
    """Call Perplexity API for user verification."""
    user_prompt = f"""Verify if the user (name: "{display_username}", role: "{role}") is an employee in the given role at "{company}".
Search reliable sources like LinkedIn profiles, company websites, directories, or official pages for evidence matching the name, role, company.
Output EXACTLY one strict JSON object—no extra text, markdown, or explanations."""

    limiter = get_rate_limiter()
    await limiter.acquire()
    
    async def api_coro():
        return await asyncio.wait_for(
            perplexity_client.chat.completions.create(
                model="sonar",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a helpful assistant that responds with valid JSON matching the provided schema. Include search-based evidence in the 'evidence' field."
                    },
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,
                max_tokens=500,
                response_format={"type": "json_schema", "json_schema": json_schema},
            ),
            timeout=API_TIMEOUT
        )
    
    try:
        response = await retry_on_failure(api_coro, max_retries=MAX_RETRIES)
        llm_output = response.choices[0].message.content.strip()
        
        if not llm_output:
            raise ValueError("Empty response from API")
        
        # Validate JSON structure
        json.loads(llm_output)
        
        sources = [str(cit).strip() for cit in (response.citations or []) if str(cit).strip()]
        return llm_output, sources
        
    except (asyncio.TimeoutError, json.JSONDecodeError, ValueError, Exception) as e:
        print(f"Verification error: {str(e)}")
        
        fallback_response = {
            "verified": False,
            "confidence": 0,
            "details": {
                "name": display_username,
                "role": role,
                "username": username,
                "email": email,
                "company": company,
                "evidence": f"Verification failed: {str(e)}"
            }
        }
        return json.dumps(fallback_response), []


async def verify_user(company: str, role: str, username: str, email: str) -> Tuple[str, List[str], List[str]]:
    inputs = normalize_inputs(company, role, username, email)
    cache_key = get_cache_key(inputs)
    
    cached_result = ttl_cache.get(cache_key)
    if cached_result:
        return cached_result
    
    display_username = inputs["username"]
    if not inputs["username"] or len(inputs["username"]) < 2:
        display_username = extract_name_from_email(inputs["email"])
    
    verification_task = asyncio.create_task(
        get_verification(inputs["company"], inputs["role"], inputs["username"], 
                        inputs["email"], display_username)
    )
    images_task = asyncio.create_task(fetch_images(inputs["company"], display_username))
    
    llm_output, sources = await verification_task
    images = await images_task
    
    result = (llm_output, sources, images)
    ttl_cache.set(cache_key, result)
    
    return result


    
    
def init(app):
    @app.post("/api/verify/")
    async def main_verify_user(payload: VerifyPayload, db: AsyncSession = Depends(get_db)):
        company = payload.company
        role = payload.lead_role
        username = payload.name
        email = payload.email

        result_str, sources, images = await verify_user(company, role, username, email)

        try:
            result = json.loads(result_str)
        except json.JSONDecodeError:
            raise HTTPException(status_code=500, detail=f"Invalid JSON returned : {result_str}")
        stmt = (
            select(SessionModel)
            .options(
                selectinload(SessionModel.phase_info),
                selectinload(SessionModel.company_details),
                selectinload(SessionModel.verification_details),
            )
            .where(SessionModel.id == payload.id)
        )
        db_result = await db.execute(stmt)
        db_session = db_result.scalar_one_or_none()
        if not db_session:
            raise HTTPException(status_code=404, detail="Session not found")

        if getattr(db_session, "verification_details", None) is None:
            vd = VerificationDetails(session_id=db_session.id)
            db_session.verification_details = vd

        if getattr(db_session, "company_details", None) is None:
            cd = CompanyDetails(session_id=db_session.id)
            db_session.company_details = cd

        set_field(db_session, "verified", "true" if result.get("verified") else "false")

        existing_username = get_field(db_session, "username")
        if result.get("verified") and not existing_username:
            set_field(db_session, "username", result.get("details", {}).get("name", ""))

        set_field(db_session, "confidence", result.get("confidence"))
        set_field(db_session, "evidence", result.get("details", {}).get("evidence", ""))

        set_field(db_session, "v_sources", json.dumps(sources))

        set_field(db_session, "c_images", json.dumps(images))

        # persist
        await db.commit()
        await db.refresh(db_session)

        # read values back using get_field (works for old or new layout)
        updated_verified = get_field(db_session, "verified")
        updated_confidence = get_field(db_session, "confidence")
        updated_evidence = get_field(db_session, "evidence")
        updated_sources = get_field(db_session, "v_sources")

        return {
            "status": "success",
            "message": "User verification details updated in session",
            "updated_data": {
                "verified": updated_verified,
                "confidence": updated_confidence,
                "evidence": updated_evidence,
                "sources": updated_sources,
            }
        }