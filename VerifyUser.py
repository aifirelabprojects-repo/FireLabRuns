# VerifyUser.py
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

load_dotenv()

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Simple async rate limiter using Semaphore for concurrency control
# Configurable via env vars: RATE_LIMIT_CONCURRENT (default: 5), RATE_LIMIT_RPM (requests per minute, default: 60)
class AsyncRateLimiter:
    def __init__(self, concurrent_limit: int = 5, rpm: int = 60):
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
            # Refill tokens periodically (simple token bucket approximation)
            async with self.lock:
                now = time.time()
                elapsed = now - self.last_refill
                self.tokens = min(self.rpm, self.tokens + (elapsed / 60) * self.rpm)
                self.last_refill = now
                while self.tokens < 1:
                    await asyncio.sleep(60 / self.rpm)  # Wait for next token
                    now = time.time()
                    elapsed = now - self.last_refill
                    self.tokens = min(self.rpm, self.tokens + (elapsed / 60) * self.rpm)
                    self.last_refill = now
                self.tokens -= 1

# Global rate limiter instance (lazy init)
_rate_limiter = None

def get_rate_limiter() -> AsyncRateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        concurrent = int(os.getenv("RATE_LIMIT_CONCURRENT", "5"))
        rpm = int(os.getenv("RATE_LIMIT_RPM", "60"))
        _rate_limiter = AsyncRateLimiter(concurrent, rpm)
    return _rate_limiter

class TTLCache:
    def __init__(self, ttl_seconds: int = 86400):
        self.cache: Dict[str, tuple[str, List[str]]] = {}
        self.ttl = ttl_seconds

    def get(self, key: str) -> Tuple[str, List[str]] | None:
        if key in self.cache:
            value, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl:
                return value
            else:
                del self.cache[key]
        return None

    def set(self, key: str, value: Tuple[str, List[str]]):
        self.cache[key] = (value, time.time())

ttl_cache = TTLCache()

perplexity_client = AsyncOpenAI(
    api_key=os.getenv("PERPLEXITY_API_KEY"),
    base_url="https://api.perplexity.ai"
)

def get_cache_key(inputs: Dict[str, str]) -> str:
    key_str = ''.join([f"{k}:{v}" for k, v in sorted(inputs.items())])
    return hashlib.md5(key_str.encode()).hexdigest()  # Note: import hashlib if not already

# Regex to extract name from email (before @)
def extract_name_from_email(email: str) -> str:
    match = re.match(r'^(.+)@', email.strip().lower())
    if match:
        name = match.group(1).replace('.', ' ').replace('_', ' ').title()
        # Simple cleanup: split and capitalize words
        return ' '.join(word.capitalize() for word in name.split() if word)
    return ''

async def retry_on_failure(coro, max_retries: int = 3, base_delay: float = 1.0, max_delay: float = 10.0):

    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            return await coro()  # Call the function to get the coroutine, then await it
        except Exception as e:
            last_exception = e
            if attempt == max_retries:
                raise  # Re-raise on final failure
            # Exponential backoff with jitter
            delay = min(base_delay * (2 ** attempt) + (time.time() % 1.0), max_delay)
            await asyncio.sleep(delay)
    raise last_exception  # Fallback (shouldn't reach here)

# Async user verification with Perplexity API (search + LLM in one call) and TTL caching
async def verify_user(company: str, role: str, username: str, email: str) -> Tuple[str, List[str]]:
    inputs = {
        "company": company.lower().strip(),
        "role": role.lower().strip(),
        "username": username.lower().strip() if username else "",
        "email": email.lower().strip()
    }
    cache_key = get_cache_key(inputs)
    # Step 1: Check TTL cache
    ttl_result = ttl_cache.get(cache_key)
    if ttl_result:
        return ttl_result
    # Extract name from email if username is insufficient
    display_username = username
    if not username or len(username.strip()) < 2:
        display_username = extract_name_from_email(inputs["email"])
    # Step 2: Perplexity API call (combines search + verification)
    user_prompt = f"""Verify if the user (name: "{display_username}", role: "{role}", email: "{email}")
    Output EXACTLY one strict JSON object—no extra text, markdown, or explanations. Use this schema:
    {{
        "verified": true/false,
        "confidence": "high" | "medium" | "low" | "none",
        "details": {{
            "name": "{display_username}",
            "role": "{role}",
            "username": "{username}",
            "email": "{email}",
            "company": "{company}",
            "evidence": "Brief 1-sentence summary of key evidence (or 'Insufficient evidence found')"
        }}
    }}"""
    try:
        # Apply rate limiting before API call
        limiter = get_rate_limiter()
        await limiter.acquire()

        # Wrap API call with retries
        async def api_coro():
            return await asyncio.wait_for(
                perplexity_client.chat.completions.create(
                    model="sonar",
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a helpful assistant that always responds with valid, complete JSON matching the exact schema in the user message. Include search-based evidence in the 'evidence' field. Never add extra text."
                        },
                        {
                            "role": "user",
                            "content": user_prompt
                        }
                    ],
                    temperature=0.1,  # From ref: Lower for factual/cited responses
                    max_tokens=500,   # Increased from ref for fuller JSON
                ),
                timeout=30.0
            )

        response = await retry_on_failure(api_coro, max_retries=3, base_delay=1.0, max_delay=10.0)
        llm_output = response.choices[0].message.content.strip()
        
        # Log raw output for debugging (remove in prod)
        print(f"Raw LLM Output: {repr(llm_output[:200])}...")  # Or use logging
        
        if not llm_output:
            raise ValueError("Empty response content from API")
        
        # Extract citations (match ref: direct access, str() for each)
        citations = response.citations or []
        sources: List[str] = [str(cit).strip() for cit in citations if str(cit).strip()]
        
        # Validate and parse JSON
        parsed = json.loads(llm_output)
        if not all(key in parsed for key in ["verified", "confidence", "details"]):
            raise ValueError("Missing required JSON fields")
    except asyncio.TimeoutError:
        raise Exception("API call timed out after retries")
    except (json.JSONDecodeError, ValueError, Exception) as e:
        print(f"Verification error: {str(e)}")  # Debug log
        llm_output = json.dumps({
            "verified": False,
            "confidence": "none",
            "details": {
                "name": display_username,
                "role": role,
                "username": username,
                "email": email,
                "company": company,
                "evidence": f"Verification failed: {str(e)}"
            }
        })
        sources = []
    # Step 3: Cache in TTL
    ttl_cache.set(cache_key, (llm_output, sources))
    return llm_output, sources

async def VerifyUser(company: str, role: str, username: str, email: str) -> Tuple[str, List[str]]:
    try:
        return await verify_user(company, role, username, email)
    except Exception as e:
        display_username = extract_name_from_email(email) if not username or len(username.strip()) < 2 else username
        default_json = json.dumps({
            "verified": False,
            "confidence": "none",
            "details": {
                "name": display_username,
                "role": role,
                "username": username,
                "email": email,
                "company": company,
                "evidence": f"Verification failed due to runtime error: {str(e)}"
            }
        })
        return default_json, []
# VerifyUser.py
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

load_dotenv()

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Simple async rate limiter using Semaphore for concurrency control
# Configurable via env vars: RATE_LIMIT_CONCURRENT (default: 5), RATE_LIMIT_RPM (requests per minute, default: 60)
class AsyncRateLimiter:
    def __init__(self, concurrent_limit: int = 5, rpm: int = 60):
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
            # Refill tokens periodically (simple token bucket approximation)
            async with self.lock:
                now = time.time()
                elapsed = now - self.last_refill
                self.tokens = min(self.rpm, self.tokens + (elapsed / 60) * self.rpm)
                self.last_refill = now
                while self.tokens < 1:
                    await asyncio.sleep(60 / self.rpm)  # Wait for next token
                    now = time.time()
                    elapsed = now - self.last_refill
                    self.tokens = min(self.rpm, self.tokens + (elapsed / 60) * self.rpm)
                    self.last_refill = now
                self.tokens -= 1

# Global rate limiter instance (lazy init)
_rate_limiter = None

def get_rate_limiter() -> AsyncRateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        concurrent = int(os.getenv("RATE_LIMIT_CONCURRENT", "5"))
        rpm = int(os.getenv("RATE_LIMIT_RPM", "60"))
        _rate_limiter = AsyncRateLimiter(concurrent, rpm)
    return _rate_limiter

class TTLCache:
    def __init__(self, ttl_seconds: int = 86400):
        self.cache: Dict[str, tuple[str, List[str], List[str]]] = {}
        self.ttl = ttl_seconds

    def get(self, key: str) -> Tuple[str, List[str], List[str]] | None:
        if key in self.cache:
            value, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl:
                return value
            else:
                del self.cache[key]
        return None

    def set(self, key: str, value: Tuple[str, List[str], List[str]]):
        self.cache[key] = (value, time.time())

ttl_cache = TTLCache()

perplexity_client = AsyncOpenAI(
    api_key=os.getenv("PERPLEXITY_API_KEY"),
    base_url="https://api.perplexity.ai"
)

def get_cache_key(inputs: Dict[str, str]) -> str:
    key_str = ''.join([f"{k}:{v}" for k, v in sorted(inputs.items())])
    return hashlib.md5(key_str.encode()).hexdigest()  # Note: import hashlib if not already

# Regex to extract name from email (before @)
def extract_name_from_email(email: str) -> str:
    match = re.match(r'^(.+)@', email.strip().lower())
    if match:
        name = match.group(1).replace('.', ' ').replace('_', ' ').title()
        # Simple cleanup: split and capitalize words
        return ' '.join(word.capitalize() for word in name.split() if word)
    return ''

async def retry_on_failure(coro, max_retries: int = 3, base_delay: float = 1.0, max_delay: float = 10.0):

    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            return await coro()  # Call the function to get the coroutine, then await it
        except Exception as e:
            last_exception = e
            if attempt == max_retries:
                raise  # Re-raise on final failure
            # Exponential backoff with jitter
            delay = min(base_delay * (2 ** attempt) + (time.time() % 1.0), max_delay)
            await asyncio.sleep(delay)
    raise last_exception  # Fallback (shouldn't reach here)

async def fetch_images(company_low: str, display_username: str) -> List[str]:
    if not display_username:
        return []
    query = f"{company_low} linkedin {display_username.lower()}".strip()
    api_key = os.getenv("SEARCH_API_KEY4")
    if not api_key:
        print("No SEARCH_API_KEY4")
        return []
    url = "https://www.searchapi.io/api/v1/search"
    params = {
        "engine": "google_images",
        "q": query,
        "api_key": api_key,
        "num": 3,
    }
    limiter = get_rate_limiter()
    await limiter.acquire()
    async def api_coro():
        connector = aiohttp.TCPConnector(limit=10)  # Scalability: limit connections
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
                if 'images' not in data:
                    return []
                images_list = data['images'][:3]
                urls = []
                for img in images_list:
                    if isinstance(img, dict) and 'original' in img and isinstance(img['original'], dict) and 'link' in img['original']:
                        urls.append(img['original']['link'])
                return urls
    try:
        return await retry_on_failure(api_coro, max_retries=3)
    except Exception as e:
        print(f"Image fetch error: {str(e)}")
        return []

# Async user verification with Perplexity API (search + LLM in one call) and TTL caching
async def verify_user(company: str, role: str, username: str, email: str) -> Tuple[str, List[str], List[str]]:
    inputs = {
        "company": company.lower().strip(),
        "role": role.lower().strip(),
        "username": username.lower().strip() if username else "",
        "email": email.lower().strip()
    }
    cache_key = get_cache_key(inputs)
    # Step 1: Check TTL cache
    ttl_result = ttl_cache.get(cache_key)
    if ttl_result:
        return ttl_result
    company_low = inputs["company"]
    role_low = inputs["role"]
    username_low = inputs["username"]
    email_low = inputs["email"]
    # Extract name from email if username is insufficient
    display_username = username
    if not username or len(username.strip()) < 2:
        display_username = extract_name_from_email(inputs["email"])

    async def get_verification() -> Tuple[str, List[str]]:
        user_prompt = f"""Verify if the user (name: "{display_username}", role: "{role}" is a employee in the given role at "{company}".
            Search reliable sources like LinkedIn profiles, company websites, directories, or official pages for evidence matching the name, role, company.
            Output EXACTLY one strict JSON object—no extra text, markdown, or explanations. Use this schema:
            {{
                "verified": true/false,
                "confidence": "high" | "medium" | "low" | "none",
                "details": {{
                    "name": "{display_username}",
                    "role": "{role}",
                    "username": "{username}",
                    "email": "{email}",
                    "company": "{company}",
                    "evidence": "Brief 1-sentence summary of key evidence (or 'Insufficient evidence found')"
                }}
            }}"""
        try:
            # Apply rate limiting before API call
            limiter = get_rate_limiter()
            await limiter.acquire()

            # Wrap API call with retries
            async def api_coro():
                return await asyncio.wait_for(
                    perplexity_client.chat.completions.create(
                        model="sonar",
                        messages=[
                            {
                                "role": "system",
                                "content": "You are a helpful assistant that always responds with valid, complete JSON matching the exact schema in the user message. Include search-based evidence in the 'evidence' field. Never add extra text."
                            },
                            {
                                "role": "user",
                                "content": user_prompt
                            }
                        ],
                        temperature=0.1,  # From ref: Lower for factual/cited responses
                        max_tokens=500,   # Increased from ref for fuller JSON
                    ),
                    timeout=30.0
                )

            response = await retry_on_failure(api_coro, max_retries=3, base_delay=1.0, max_delay=10.0)
            llm_output = response.choices[0].message.content.strip()
            
            # Log raw output for debugging (remove in prod)
            print(f"Raw LLM Output: {repr(llm_output[:200])}...")  # Or use logging
            
            if not llm_output:
                raise ValueError("Empty response content from API")
            
            # Extract citations (match ref: direct access, str() for each)
            citations = response.citations or []
            sources: List[str] = [str(cit).strip() for cit in citations if str(cit).strip()]
            
            # Validate and parse JSON
            parsed = json.loads(llm_output)
            if not all(key in parsed for key in ["verified", "confidence", "details"]):
                raise ValueError("Missing required JSON fields")
        except asyncio.TimeoutError:
            raise Exception("API call timed out after retries")
        except (json.JSONDecodeError, ValueError, Exception) as e:
            print(f"Verification error: {str(e)}")  # Debug log
            llm_output = json.dumps({
                "verified": False,
                "confidence": "none",
                "details": {
                    "name": display_username,
                    "role": role_low,
                    "username": username_low,
                    "email": email_low,
                    "company": company_low,
                    "evidence": f"Verification failed: {str(e)}"
                }
            })
            sources = []
        return llm_output, sources

    # Run verification and image fetch in parallel
    verif_task = asyncio.create_task(get_verification())
    imgs_task = asyncio.create_task(fetch_images(company_low, display_username))
    llm_output, sources = await verif_task
    images = await imgs_task

    # Step 3: Cache in TTL
    ttl_cache.set(cache_key, (llm_output, sources, images))
    return llm_output, sources, images

