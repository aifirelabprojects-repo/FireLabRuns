import os
import json
import sys
import asyncio
import aiohttp
import hashlib
import time
from typing import Dict, Any, Tuple, List
import re
from ClientModel import client,MODEL_NAME,SEARCH_API_KEY

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


VERIFICATION_CACHE_FILE = "verification_cache.json"


class TTLCache:
    def __init__(self, ttl_seconds: int = 86400):  
        self.cache: Dict[str, tuple[tuple[str, List[str]], float]] = {}
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

# Load persistent cache
def load_persistent_cache() -> Dict[str, Dict[str, Any]]:
    if os.path.exists(VERIFICATION_CACHE_FILE):
        try:
            with open(VERIFICATION_CACHE_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Failed to load cache: {e}")
            return {}
    return {}

def save_persistent_cache(cache: Dict[str, Dict[str, Any]]):
    try:
        with open(VERIFICATION_CACHE_FILE, 'w') as f:
            json.dump(cache, f, indent=2)
    except IOError as e:
        print(f"Warning: Failed to save cache: {e}")

persistent_cache = load_persistent_cache()
ttl_cache = TTLCache()

def get_cache_key(inputs: Dict[str, str]) -> str:
    key_str = ''.join([f"{k}:{v}" for k, v in sorted(inputs.items())])
    return hashlib.md5(key_str.encode()).hexdigest()

# Regex to extract name from email (before @)
def extract_name_from_email(email: str) -> str:
    match = re.match(r'^(.+)@', email.strip().lower())
    if match:
        name = match.group(1).replace('.', ' ').replace('_', ' ').title()
        # Simple cleanup: split and capitalize words
        return ' '.join(word.capitalize() for word in name.split() if word)
    return ''

# Clean expired entries from persistent cache
def clean_persistent_cache():
    current_time = time.time()
    expired_keys = [key for key, data in persistent_cache.items() if current_time - data.get('timestamp', 0) > 86400]  # 24 hours
    for key in expired_keys:
        del persistent_cache[key]
    if expired_keys:
        save_persistent_cache(persistent_cache)

# Async user verification with one search + one LLM call
async def verify_user(company: str, role: str, username: str, email: str) -> Tuple[str, List[str]]:
    inputs = {"company": company.lower().strip(), "role": role.lower().strip(), "username": username.lower().strip(), "email": email.lower().strip()}
    cache_key = get_cache_key(inputs)

    # Clean persistent cache before checking
    clean_persistent_cache()

    # Step 1: Check caches
    ttl_result = ttl_cache.get(cache_key)
    if ttl_result:
        return ttl_result

    persistent_data = persistent_cache.get(cache_key, {})
    if persistent_data:
        result = persistent_data['result']
        sources = persistent_data.get('sources', [])
        ttl_cache.set(cache_key, (result, sources))
        return result, sources

    # Extract name from email
    if username is None or len(username.strip()) < 2:
        username = extract_name_from_email(inputs["email"])
    # Step 2: One search call for evidence and sources
    sources: List[str] = []
    if not SEARCH_API_KEY:
        search_result = "No SearchAPI key available. Cannot verify."
    else:
        search_query = f'"{username}" {role} "{company}" (employee OR "works at" OR profile OR verification) site:linkedin.com OR site:company-website.com "{email}" OR "{username}"'

        search_url = "https://www.searchapi.io/api/v1/search"
        params = {
            "engine": "google",
            "q": search_query,
            "api_key": SEARCH_API_KEY,
            "num": 10,  # Increased to 20 for more results
            "gl": "us",
            "hl": "en"
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(search_url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        try:
                            data = await resp.json()
                            # Aggregate results concisely without links
                            search_parts = []
                            kg = data.get("knowledge_graph", {})
                            if kg and company.lower() in str(kg).lower():
                                search_parts.append(f"Knowledge Graph match: {kg.get('description', '')}")
                            for result in data.get("organic_results", [])[:10]: 
                                title = result.get("title", "")
                                snippet = result.get("snippet", "")
                                link = result.get("link", "")
                                content_lower = (title + snippet).lower()
                                if any(term in content_lower for term in [username.lower(), role, company.lower(), email]):
                                    sources.append(link)
                                    search_parts.append(f"{title}: {snippet[:200]}...")
                            search_result = "\n".join(search_parts) if search_parts else "No matching results found."
                        except (json.JSONDecodeError, KeyError) as e:
                            search_result = f"Search JSON parse error: {str(e)}"
                            sources = []
                    else:
                        search_result = f"Search HTTP error: {resp.status}"
                        sources = []
        except (aiohttp.ClientError, asyncio.TimeoutError, Exception) as e:
            search_result = f"Search error: {str(e)}"
            print("Search Raw ",search_result)
            sources = []

    # Step 3: One LLM call to verify and output strict JSON
    prompt = f"""Verify if the user (name: "{username}", role: "{role}", username: "{username}", email: "{email}") is a legitimate employee in the given role at "{company}".
Use ONLY the search evidence below. Base verification on matches for name/role/company/email in profiles or directories.
Output EXACTLY one strict JSON object—no extra text, markdown, or explanations. Use this schema:
{{
    "verified": true/false,
    "confidence": "high" | "medium" | "low" | "none",
    "details": {{
        "name": "{username}",
        "role": "{role}",
        "username": "{username}",
        "email": "{email}",
        "company": "{company}",
        "evidence": "Brief 1-sentence summary of key evidence (or 'Insufficient evidence found')"
    }}
}}

Search Evidence:
{search_result}
"""

    try:
        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=250,
        )
        llm_output = response.choices[0].message.content.strip()
        # Validate JSON
        parsed = json.loads(llm_output)
        # Ensure required fields
        if not all(key in parsed for key in ["verified", "confidence", "details"]):
            raise ValueError("Missing required JSON fields")
    except (json.JSONDecodeError, ValueError, Exception) as e:
        llm_output = json.dumps({
            "verified": False,
            "confidence": "none",
            "details": {
                "name": username,
                "role": role,
                "username": username,
                "email": email,
                "company": company,
                "evidence": f"Verification failed: {str(e)}"
            }
        })

    # Step 4: Cache and return
    ttl_cache.set(cache_key, (llm_output, sources))
    persistent_cache[cache_key] = {'result': llm_output, 'sources': sources, 'timestamp': time.time()}
    save_persistent_cache(persistent_cache)

    return llm_output, sources


async def VerifyUser(company: str, role: str, username: str, email: str) -> Tuple[str, List[str]]:
    try:
        return await verify_user(company, role, username, email)
    except Exception as e:
        default_json = json.dumps({
            "verified": False,
            "confidence": "none",
            "details": {
                "name": extract_name_from_email(email) or username,
                "role": role,
                "username": username,
                "email": email,
                "company": company,
                "evidence": f"Verification failed due to runtime error: {str(e)}"
            }
        })
        return default_json, []

