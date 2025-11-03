import os
import json
import sys
import asyncio
import aiohttp  
import re  
import hashlib
from typing import Dict, Any
import time  # For TTL
from ClientModel import client,MODEL_NAME,SEARCH_API_KEY

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

CACHE_FILE = "enrichment_cache.json"



# Enhanced caching with TTL (simple in-memory with expiration simulation)
class TTLCache:
    def __init__(self, ttl_seconds: int = 3600):  # 1 hour TTL
        self.cache: Dict[str, tuple[str, float]] = {}  # (value, timestamp)
        self.ttl = ttl_seconds

    def get(self, key: str) -> str | None:
        if key in self.cache:
            value, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl:
                return value
            else:
                del self.cache[key]
        return None

    def set(self, key: str, value: str):
        self.cache[key] = (value, time.time())

# Load persistent cache
def load_persistent_cache() -> Dict[str, str]:
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_persistent_cache(cache: Dict[str, str]):
    with open(CACHE_FILE, 'w') as f:
        json.dump(cache, f, indent=2)

persistent_cache = load_persistent_cache()
ttl_cache = TTLCache()  # In-memory for ultra-fast repeated lookups

def get_cache_key(company: str) -> str:
    return hashlib.md5(company.lower().strip().encode()).hexdigest()

# Regex patterns for extracting details from snippets
PATTERNS = {
    'founded': re.compile(r'(founded|established|started)\s*(in|on)?\s*(\d{4})', re.IGNORECASE),
    'employees': re.compile(r'(\d+(?:,\d+)?)\s*(employees|staff|workers)', re.IGNORECASE),
    'founders': re.compile(r'(founded by|founder[s]?\s*(?:is|are|:\s*))?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)', re.IGNORECASE),
    'location': re.compile(r'(headquarters?|based in|located in)\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*(?:,\s*[A-Z]{2})?)', re.IGNORECASE),
    'revenue': re.compile(r'(\$?\d+(?:,\d+)?(?:\.\d+)?)\s*(billion|million|revenue)', re.IGNORECASE)
}

def extract_with_regex(text: str, pattern_key: str) -> str:
    pattern = PATTERNS.get(pattern_key)
    if pattern:
        match = pattern.search(text)
        if match:
            return match.group(0).strip()
    return ''

# Async company enrichment using single comprehensive query for top 10 sites
async def simulate_company_enrichment(company: str) -> str:
    company_lower = company.lower().strip()
    cache_key = get_cache_key(company)

    # Step 1: Check caches (TTL first for speed, then persistent)
    ttl_result = ttl_cache.get(cache_key)
    if ttl_result:
        return ttl_result

    persistent_result = persistent_cache.get(cache_key, {}).get('result')
    if persistent_result:
        ttl_cache.set(cache_key, persistent_result)  # Promote to TTL
        return persistent_result

    if not SEARCH_API_KEY:
        error_msg = f"No SearchAPI.io key found. Cannot enrich '{company}'."
        ttl_cache.set(cache_key, error_msg)
        return error_msg

    # Step 2: Single comprehensive query targeting top relevant sites for broader coverage
    search_query = f'"{company}" company (founded OR established OR founder OR "founded by" OR employees OR "number of employees" OR headquarters OR location OR "based in" OR revenue OR industry) site:wikipedia.org OR site:crunchbase.com OR site:linkedin.com OR site:glassdoor.com OR site:forbes.com OR site:bloomberg.com OR site:zoominfo.com OR site:owler.com OR site:indeed.com OR site:hoovers.com'

    search_url = "https://www.searchapi.io/api/v1/search"
    params = {
        "engine": "google",
        "q": search_query,
        "api_key": SEARCH_API_KEY,
        "num": 10,  # Top 10 results from best sites
        "gl": "us",
        "hl": "en"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(search_url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status}")
                data = await resp.json()

    except Exception as e:
        error_msg = f"Error fetching search results for '{company}': {str(e)}"
        ttl_cache.set(cache_key, error_msg)
        return error_msg

    enrichment_parts = []

    # Step 3: Extract from knowledge_graph if available
    kg = data.get("knowledge_graph", {})
    if kg:
        title = kg.get('title', company)
        description = kg.get('description', '')
        if description:
            enrichment_parts.append(f"{title}: {description}")

        # Enhanced KG extraction
        entity_type = kg.get('type', '') or kg.get('industry', '')
        if entity_type:
            enrichment_parts.append(f"Type/Industry: {entity_type}.")

        founded = kg.get('founded', '') or kg.get('date_founded', '')
        if founded:
            enrichment_parts.append(f"Founded: {founded}.")

        founders = kg.get('founders', [])
        if isinstance(founders, list) and founders:
            founders_str = ', '.join([f for f in founders if f])
            enrichment_parts.append(f"Founders: {founders_str}.")
        elif isinstance(founders, str) and founders:
            enrichment_parts.append(f"Founders: {founders}.")

        employees = kg.get('number_of_employees') or kg.get('employees', '') or kg.get('employee_count', '')
        if employees:
            enrichment_parts.append(f"Number of Employees: {employees}.")

        location = kg.get('location', '') or kg.get('headquarters', '') or kg.get('hq', '')
        if location:
            enrichment_parts.append(f"Headquarters/Location: {location}.")

        website = kg.get('website', '')
        if website:
            enrichment_parts.append(f"Website: {website}.")

        # Revenue if available
        revenue = kg.get('revenue', '')
        if revenue:
            enrichment_parts.append(f"Revenue: {revenue}.")

    # Step 4: Enhanced parsing of top 10 organic results
    organic_results = data.get("organic_results", [])[:10]  # Limit to top 10

    # Deduplicate by link (rare in single query but good practice)
    seen_links = set()
    unique_organic = []
    for result in organic_results:
        link = result.get("link", "")
        if link not in seen_links:
            seen_links.add(link)
            unique_organic.append(result)

    extracted_details = {'founded': [], 'employees': [], 'founders': [], 'location': [], 'revenue': []}
    for result in unique_organic:
        title = result.get("title", "")
        snippet = result.get("snippet", "")
        link = result.get("link", "")

        if company_lower not in title.lower() and company_lower not in snippet.lower():
            continue

        enrichment_parts.append(f"Source: {title} ({link})")

        # Regex extraction from snippet and title
        text_to_parse = f"{title} {snippet}"
        for detail in extracted_details:
            extract = extract_with_regex(text_to_parse, detail)
            if extract:
                extracted_details[detail].append(extract)

        # Fallback simple parse for additional context
        snippet_lower = snippet.lower()
        if 'founded' in snippet_lower and not extracted_details['founded']:
            for part in snippet.split('.'):
                if any(word in part.lower() for word in ['founded', 'est', 'established', 'started']):
                    enrichment_parts.append(f"Founding info: {part.strip()}")

        if 'employees' in snippet_lower and not extracted_details['employees']:
            for part in snippet.split('.'):
                if any(word in part.lower() for word in ['employees', 'staff', 'team size']):
                    enrichment_parts.append(f"Employees info: {part.strip()}")

        if 'founder' in snippet_lower and not extracted_details['founders']:
            for part in snippet.split('.'):
                if any(word in part.lower() for word in ['founder', 'founded by', 'ceo']):
                    enrichment_parts.append(f"Founders info: {part.strip()}")

        if 'headquarters' in snippet_lower or 'located' in snippet_lower and not extracted_details['location']:
            for part in snippet.split('.'):
                if any(word in part.lower() for word in ['headquarters', 'located', 'based in', 'office']):
                    enrichment_parts.append(f"Location info: {part.strip()}")

        if 'revenue' in snippet_lower and not extracted_details['revenue']:
            for part in snippet.split('.'):
                if 'revenue' in part.lower():
                    enrichment_parts.append(f"Revenue info: {part.strip()}")

    # Aggregate unique extractions
    for detail, values in extracted_details.items():
        if values:
            unique_vals = list(set(values))  # Dedup
            agg = '; '.join(unique_vals)
            enrichment_parts.append(f"{detail.capitalize()}: {agg}")

    if not enrichment_parts:
        enrichment_parts.append(f"Limited information found for '{company}'. It may be a small or emerging company.")

    enrichment_result = " | ".join(enrichment_parts)

    # Step 5: Cache (both TTL and persistent)
    ttl_cache.set(cache_key, enrichment_result)
    persistent_cache[cache_key] = {'result': enrichment_result, 'timestamp': time.time()}
    save_persistent_cache(persistent_cache)

    return enrichment_result

# Updated functions (unchanged but for completeness)
functions = [
    {
        "name": "simulate_company_enrichment",
        "description": "Enrich company details (founded, founders, employees, headquarters, revenue, etc.) using a single optimized search for top 10 relevant sites.",
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
    """Async version for better scalability."""
    response = await client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": "You are a helpful assistant. Detect if the user message mentions a specific company name. If yes, use the simulate_company_enrichment function to gather enhanced details like founded date, founders, employee count, headquarters, revenue, etc., for a more informed response."},
            {"role": "user", "content": question}
        ],
        functions=functions,
        function_call="auto",
        temperature=0.0,
        max_tokens=500,
    )

    message = response.choices[0].message
    if message.function_call is not None:
        func_name = message.function_call.name
        args_json = message.function_call.arguments
        args = json.loads(args_json)
        company = args.get("company")
        question_text = args.get("question")
        
        if company:
            enrichment_result = await simulate_company_enrichment(company)
            
            if enrichment_result and "Error" not in enrichment_result and "No SearchAPI" not in enrichment_result:
                followup = await client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=[
                        {"role": "system", "content": "Incorporate the enriched company details (e.g., founded, founders, employees, location, revenue) concisely and naturally into your response to the user's question. Prioritize relevance; avoid dumping all info."},
                        {"role": "user", "content": question_text},
                        {"role": "function", "name": func_name, "content": enrichment_result}
                    ],
                    temperature=0.0,
                    max_tokens=400,  # Slightly increased for richer response
                )
                return followup.choices[0].message.content
            else:
                return f"Could not enrich details for '{company}'. {message.content or ''}"
    
    return message.content or "No response generated."

def FindTheComp(question: str) -> str:
    return asyncio.run(find_the_comp(question))

