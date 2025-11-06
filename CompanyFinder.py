import os
import json
import sys
import aiohttp
import re
import hashlib
from typing import Dict, Any, List, Optional, Tuple
import time
import urllib.parse
from ClientModel import client, MODEL_NAME, SEARCH_API_KEY
from database import  Session as SessionModel
import asyncio
from sqlalchemy.orm import object_session
from sqlalchemy import or_, update
from sqlalchemy.exc import SQLAlchemyError

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

CACHE_FILE = "enrichment_cache.json"


class TTLCache:
    def __init__(self, ttl_seconds: int = 3600):
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




async def fetch_company_images(session: aiohttp.ClientSession, company: str, n: int = 2, website_domain: str | None = None) -> List[str]:
    if not SEARCH_API_KEY:
        return []

    def _pick_images(payload, limit):
        imgs = []
        arr = payload.get("images", [])
        for item in arr:
            link = item.get("original", {}).get("link", "") or item.get("thumbnail", "")
            if link:
                imgs.append(link)
            if len(imgs) >= limit:
                break
        return imgs

    base = "https://www.searchapi.io/api/v1/search"
    links: List[str] = []

    try:
        params1 = {"engine": "google_images", "q": company, "api_key": SEARCH_API_KEY, "num": max(n * 2, 5), "gl": "us", "hl": "en"}
        async with session.get(base, params=params1, timeout=aiohttp.ClientTimeout(total=10)) as r1:
            if r1.status == 200:
                data1 = await r1.json()
                links.extend(_pick_images(data1, n))
    except Exception as e:
        print(f"Debug: General images error: {e}")

    if len(links) < n:
        try:
            params2 = {"engine": "google_images", "q": f'{company} logo', "api_key": SEARCH_API_KEY, "num": max(n * 2, 5), "gl": "us", "hl": "en"}
            async with session.get(base, params=params2, timeout=aiohttp.ClientTimeout(total=10)) as r2:
                if r2.status == 200:
                    data2 = await r2.json()
                    for x in _pick_images(data2, n - len(links)):
                        if x not in links:
                            links.append(x)
        except Exception as e:
            print(f"Debug: Logo images error: {e}")


    if len(links) < n and website_domain:
        cb = f"https://logo.clearbit.com/{website_domain}"
        if cb not in links:
            links.append(cb)

    return links[:n]


async def simulate_company_enrichment(company: str) -> Dict[str, Any]:
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

    if not SEARCH_API_KEY:
        payload = {"summary": f"No SearchAPI.io key found. Cannot enrich '{company}'.", "details": {}, "sources": [], "images": []}
        ttl_cache.set(cache_key, payload)
        return payload

    search_url = "https://www.searchapi.io/api/v1/search"
    website_domain = None
    connector = aiohttp.TCPConnector(limit=10)
    timeout = aiohttp.ClientTimeout(total=15)

    # --- IMPORTANT FIX: initialize these before using them ---
    full_results: List[Dict[str, Any]] = []
    sources: List[Dict[str, str]] = []

    try:
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            # Build a conservative search query and limit to 5 results
            search_query = f'{company} founder site:linkedin.com/company OR site:linkedin.com/in'
            params = {"engine": "google", "q": search_query, "api_key": SEARCH_API_KEY, "num": 5, "gl": "us", "hl": "en"}

            data = None
            try:
                async with session.get(search_url, params=params, timeout=timeout) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                    else:
                        print(f"Debug: search api status {resp.status}")
            except Exception as e:
                print(f"Debug: Broader search error: {e}")

            if data:
                kg = data.get("knowledge_graph", {}) or {}
                site = kg.get("website") or ""
                if site:
                    try:
                        domain = urllib.parse.urlparse(site).netloc or site
                        website_domain = domain.replace("www.", "")
                        print(f"Debug: Found website domain: {website_domain}")
                    except Exception:
                        website_domain = None

                organic = data.get("organic_results", [])[:5]  # explicit limit
                for r in organic:
                    link = r.get("link", "")
                    title = r.get("title") or link
                    snippet = r.get("snippet", "") or ""
                    broader_result = {"title": title, "link": link, "snippet": snippet}
                    full_results.append(broader_result)
                    sources.append({"title": title, "link": link})
                    if len(sources) >= 5:
                        break

                # Keep sizes small for downstream prompt
                full_results = full_results[:5]
                sources = sources[:5]
                print(f"Debug: Collected {len(full_results)} results and {len(sources)} sources")

            # Fetch images (use website_domain if available; function will fallback)
            images = await fetch_company_images(session, company, n=2, website_domain=website_domain)

            # Prompt model with raw search results for JSON output with details
            summary_prompt = {
                "role": "system",
                "content": (
                    "You are a helpful assistant that extracts company information from search results.\n"
                    "Return JSON ONLY with this exact schema:\n"
                    "{\n"
                    '  "summary": "A detailed 5-8 sentence summary of the company .",\n'
                    '  "details": {\n'
                    '    "founded": "Year founded or null if not found",\n'
                    '    "employees": "Number of employees or range or null",\n'
                    '    "founders": "Names of founders or null",\n'
                    '    "location": "Headquarters location or null",\n'
                    '    "revenue": "Annual revenue or null",\n'
                    '    "industry": "Industry or sector or null"\n'
                    "  }\n"
                    "}\n"
                    "Base everything strictly on the provided search results. If information is not clearly stated in the snippets, use null. Do not speculate or infer beyond the text."
                )
            }
            user_payload = {"company": company, "search_results": full_results}
            user_content = json.dumps(user_payload)

            # lower max tokens to speed the model and keep deterministic (you can reduce further)
            summary_resp = await client.chat.completions.create(
                model=MODEL_NAME,
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=300,
                messages=[summary_prompt, {"role": "user", "content": user_content}],
            )
            summary_json = json.loads(summary_resp.choices[0].message.content)
            summary_text = (summary_json.get("summary") or "").strip()
            details = summary_json.get("details", {})

            final_payload = {
                "summary": summary_text or f"Limited public information found for '{company}' (emerging company?).",
                "details": details,
                "sources": sources,
                "images": images
            }

            # Cache and persist
            ttl_cache.set(cache_key, final_payload)
            persistent_cache[cache_key] = {'result': final_payload, 'timestamp': time.time()}
            save_persistent_cache(persistent_cache)
            return final_payload

    except Exception as e:
        print(f"Debug: Overall enrichment error for '{company}': {e}")
        payload = {"summary": f"Error fetching results for '{company}': {str(e)}", "details": {}, "sources": [], "images": []}
        ttl_cache.set(cache_key, payload)
        return payload

functions = [
    {
        "name": "simulate_company_enrichment",
        "description": "Enrich company details & return JSON: summary, details, sources, images.",
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
    """
    Returns a STRICT JSON string:
    {
      "summary": str,
      "details": {},
      "sources": [{"title": str, "link": str}, ...],
      "images": [str, str]
    }
    """
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

    # If the model called the function, run enrichment
    if getattr(message, "function_call", None):
        args = json.loads(message.function_call.arguments or "{}")
        company = args.get("company")
        if company:
            payload = await simulate_company_enrichment(company)
            # Return strict JSON string
            return json.dumps(payload, ensure_ascii=False)

    # Fallback: no company detected, return empty shell
    return json.dumps({
        "summary": "No specific company name was detected in the question.",
        "details": {},
        "sources": [],
        "images": []
    }, ensure_ascii=False)




def _run_coro(coro):
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(coro)
        finally:
            loop.close()

def _normalize_output(raw: Any) -> Tuple[Optional[str], Dict[str, Any], List[Dict[str, str]], List[str]]:

    # If it's a JSON string of the whole object, parse it.
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            raw = parsed
        except Exception:
            # Treat as bare summary string
            return raw, {}, [], []

    if not isinstance(raw, dict):
        raw = {}

    summary = raw.get("summary") or None

    details = raw.get("details") or {}
    if not isinstance(details, dict):
        details = {"_raw_details": details}

    sources_in = raw.get("sources") or []
    sources: List[str] = []

    for s in sources_in:
        if isinstance(s, dict):
            link = s.get("link") or ""
            if isinstance(link, str) and link.strip():
                sources.append(link.strip())
        elif isinstance(s, str) and s.strip():
            # If API already returned a string link
            sources.append(s.strip())


    images_in = raw.get("images") or []
    images: List[str] = []
    for u in images_in:
        if isinstance(u, (bytes, bytearray)):
            images.append(u.decode())
        elif isinstance(u, str):
            images.append(u)

    return summary, details, sources, images

from sqlalchemy.types import JSON as SAJSON

def _as_db_value(col, value):
    if isinstance(col.type, SAJSON):
        return value
    return json.dumps(value)

def _empty_db_value(col):

    return _as_db_value(col, {})

def FindTheComp(question: str, session_obj: "SessionModel") -> None:

    sess = object_session(session_obj)
    if sess is None:
        raise RuntimeError("SessionModel instance is not attached to a SQLAlchemy session.")

    # Quick local check (works for JSON or TEXT-as-JSON)
    if session_obj.c_data:
        return

    raw_out = _run_coro(find_the_comp(question))
    summary, details, sources, images = _normalize_output(raw_out)

    # Ensure types
    details  = details or {}
    sources  = list(sources or [])
    images   = list(images or [])

    Model = type(session_obj)

    # Convert values according to the column storage
    c_data_val    = _as_db_value(Model.c_data, details)
    c_sources_val = _as_db_value(Model.c_sources, sources)
    c_images_val  = _as_db_value(Model.c_images, images)
    empty_json    = _empty_db_value(Model.c_data)

    # IMPORTANT: compare using a serialized empty value, not a bare {}
    stmt = (
        update(Model)
        .where(Model.id == session_obj.id)
        .where(or_(Model.c_data == None, Model.c_data == empty_json))  # noqa: E711
        .values(
            c_info=summary,                 # summary is a string or None
            c_data=c_data_val,              # dict or JSON-string depending on column type
            c_sources=c_sources_val,        # list or JSON-string depending on column type
            c_images=c_images_val           # list or JSON-string depending on column type
        )
    ).execution_options(synchronize_session=False)

    try:
        res = sess.execute(stmt)
        if res.rowcount:
            sess.commit()
        else:
            sess.rollback()
    except Exception:
        sess.rollback()
        raise