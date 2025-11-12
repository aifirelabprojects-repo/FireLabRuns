# CompanyFinder.py
import os
import json
import sys
import hashlib
from typing import Dict, Any, List, Optional, Tuple
import time
from openai import AsyncOpenAI
from database import AsyncSessionLocal, Session as SessionModel, AsyncSession
from sqlalchemy import or_, update
from sqlalchemy.orm import object_session
import asyncio
import re
from dotenv import load_dotenv

load_dotenv()
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

CACHE_FILE = "enrichment_cache.json"

json_schema = {
    "name": "company_enrichment",
    "schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "Detailed 5-8 sentence summary"},
            "details": {
                "type": "object",
                "properties": {
                    "founded": {"type": ["string", "null"]},
                    "employees": {"type": ["string", "null"]},
                    "founders": {"type": ["string", "null"]},
                    "location": {"type": ["string", "null"]},
                    "revenue": {"type": ["string", "null"]},
                    "industry": {"type": ["string", "null"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 100}
                },
                "required": [],  
                "additionalProperties": False
            }
        },
        "required": ["summary", "details"],
        "additionalProperties": False
    }
}

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
    def __init__(self, ttl_seconds: int = 86400):  # 1 day for scale
        self.cache: Dict[str, Tuple[Dict[str, Any], float]] = {}
        self.ttl = ttl_seconds

    def get(self, key: str) -> Dict[str, Any] | None:
        if key in self.cache:
            value, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl:
                return value
            else:
                del self.cache[key]
        return None

    def set(self, key: str, value: Dict[str, Any]):
        self.cache[key] = (value, time.time())

def load_persistent_cache() -> Dict[str, Dict[str, Any]]:
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_persistent_cache(cache: Dict[str, Dict[str, Any]]):
    with open(CACHE_FILE, 'w') as f:
        json.dump(cache, f, indent=2)

persistent_cache = load_persistent_cache()
ttl_cache = TTLCache()

def get_cache_key(company: str) -> str:
    return hashlib.md5(company.lower().strip().encode()).hexdigest()

perplexity_client = AsyncOpenAI(
    api_key=os.getenv("PERPLEXITY_API_KEY"),
    base_url="https://api.perplexity.ai"
)

async def retry_on_failure(coro, max_retries: int = 3, base_delay: float = 1.0, max_delay: float = 10.0):
    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            return await coro()
        except Exception as e:
            last_exception = e
            if attempt == max_retries:
                raise
            delay = min(base_delay * (2 ** attempt) + (time.time() % 1.0), max_delay)
            await asyncio.sleep(delay)
    raise last_exception

async def simulate_company_enrichment(company: str, question: str = "") -> Dict[str, Any]:
    company_lower = company.lower().strip()
    cache_key = get_cache_key(company)

    # Cache hits (TTL then disk)
    ttl_hit = ttl_cache.get(cache_key)
    if ttl_hit:
        return ttl_hit

    disk_hit = persistent_cache.get(cache_key, {}).get('result')
    if disk_hit:
        ttl_cache.set(cache_key, disk_hit)
        return disk_hit

    if not os.getenv("PERPLEXITY_API_KEY"):
        payload = {"summary": f"No Perplexity API key found. Cannot enrich '{company}'.", "details": {}, "sources": []}
        ttl_cache.set(cache_key, payload)
        return payload

    try:
        system_prompt = (
            "You are a helpful assistant that extracts and enriches company information from web search results.\n"
            "Return JSON ONLY with this exact schema—no extra text, markdown, or explanations:\n"
            "{\n"
            '  "summary": "A detailed 5-8 sentence summary of the company based strictly on search results.",\n'
            '  "details": {\n'
            '    "founded": "Year founded or null if not found",\n'
            '    "employees": "Number of employees or range or null",\n'
            '    "founders": "Names of founders or null",\n'
            '    "location": "Headquarters location or null",\n'
            '    "revenue": "Annual revenue or null",\n'
            '    "industry": "Industry or sector or null",\n'  # <-- Added comma here
            '    "confidence": <number between 0 and 100>\n'  # <-- Now valid
            '  },\n'
            "}\n"
            "Base everything strictly on the provided search results. If information is not clearly stated, use null. Do not speculate or infer beyond the text.\n"
        )

        user_content = f"{question}\n\nEnrich the following company with details from web search: {company}"

        # Apply rate limiting
        limiter = get_rate_limiter()
        await limiter.acquire()

        async def api_coro():
            return await asyncio.wait_for(
                perplexity_client.chat.completions.create(
                    model="sonar",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content}
                    ],
                    temperature=0.1,  # Low for consistent JSON
                    max_tokens=800,
                    response_format={
                    "type": "json_schema",
                    "json_schema": json_schema
                },
                ),
                timeout=30.0
            )

        response = await retry_on_failure(api_coro, max_retries=3, base_delay=1.0, max_delay=10.0)

        content = response.choices[0].message.content.strip()


        if not content:
            raise ValueError("Empty response content from API")
        
        # Parse JSON
        summary_json = json.loads(content)
        summary_text = (summary_json.get("summary") or "").strip()
        details = summary_json.get("details", {})

        # Extract sources (top 5; str() for safety)
        citations = getattr(response, 'citations', []) or []
        sources = [str(citation).strip() for citation in citations[:5] if str(citation).strip()]

        final_payload = {
            "summary": summary_text or f"Limited public information found for '{company}' (emerging company?).",
            "details": details,
            "sources": sources,
            
        }

        # Basic validation
        required_keys = ["summary", "details", "sources"]
        if not all(key in final_payload for key in required_keys):
            raise ValueError(f"Missing required fields in parsed JSON: {set(required_keys) - set(final_payload)}")

        # Cache and persist
        ttl_cache.set(cache_key, final_payload)
        persistent_cache[cache_key] = {'result': final_payload, 'timestamp': time.time()}
        save_persistent_cache(persistent_cache)
        return final_payload

    except asyncio.TimeoutError:
        err_msg = "API call timed out after retries"
        # print(f"Debug: {err_msg} for '{company}'")
        raise Exception(err_msg)
    except (json.JSONDecodeError, ValueError) as e:
        # print(f"Debug: Parse error for '{company}': {e}")
        payload = {"summary": f"Parse error for '{company}': {str(e)}", "details": {}, "sources": []}
        ttl_cache.set(cache_key, payload)
        return payload
    except Exception as e:
        # print(f"Debug: Overall enrichment error for '{company}': {e}")
        payload = {"summary": f"Error fetching results for '{company}': {str(e)}", "details": {}, "sources": []}
        ttl_cache.set(cache_key, payload)
        return payload

# Rest of the code remains unchanged (functions, find_the_comp, _normalize_output, FindTheComp)
functions = [
    {
        "name": "simulate_company_enrichment",
        "description": "Enrich company details & return JSON: summary, details, sources.",
        "parameters": {
            "type": "object",
            "properties": {
                "company": {"type": "string", "description": "The company name to enrich."},
                "question": {"type": "string", "description": "The original user question for context."}
            },
            "required": ["company", "question"]
        }
    }
]

async def find_the_comp(question: str) -> str:
    from ClientModel import client, MODEL_NAME  # Assuming OpenAI async client

    response = await client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": "Detect if the user mentions a specific company name. If yes, call the function. Otherwise, summarize the question."},
            {"role": "user", "content": question}
        ],
        functions=functions,
        function_call="auto",
        temperature=0.0,
        max_tokens=300,
    )

    message = response.choices[0].message

    if getattr(message, "function_call", None):
        args = json.loads(message.function_call.arguments or "{}")
        company = args.get("company")
        if company:
            payload = await simulate_company_enrichment(company, question)
            return json.dumps(payload, ensure_ascii=False)

    return json.dumps({
        "summary": "",
        "details": {},
        "sources": [],
    }, ensure_ascii=False)

def _normalize_output(raw: Any) -> Tuple[Optional[str], Dict[str, Any], List[str], List[str]]:  # Simplified sources to List[str]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            raw = parsed
        except Exception:
            return raw, {}, [], []

    if not isinstance(raw, dict):
        raw = {}

    summary = raw.get("summary") or None
    details = raw.get("details") or {}
    if not isinstance(details, dict):
        details = {"_raw_details": details}

    sources_in = raw.get("sources") or []
    sources = [s.strip() for s in sources_in if isinstance(s, str) and s.strip()]

    return summary, details, sources

async def FindTheComp(question: str, session_id: str) -> None:
    raw_out = await find_the_comp(question)
    summary, details, sources = _normalize_output(raw_out)

    details = details or {}
    sources = sources or []


    if not summary or "error" in summary.lower() or "parse" in summary.lower():
        
        return  
    c_data_val = json.dumps(details)
    c_sources_val = json.dumps(sources)
    empty_c_data = '{}'

    stmt = (
        update(SessionModel)
        .where(SessionModel.id == session_id)
        .where(or_(SessionModel.c_data.is_(None), SessionModel.c_data == empty_c_data))
        .values(
            c_info=summary,
            c_data=c_data_val,
            c_sources=c_sources_val,
        )
        .execution_options(synchronize_session=False)
    )

    async with AsyncSessionLocal() as sess:
        try:
            res = await sess.execute(stmt)
            if res.rowcount:
                await sess.commit()
            else:
                await sess.rollback()
        except Exception:
            await sess.rollback()
            raise
        
        
