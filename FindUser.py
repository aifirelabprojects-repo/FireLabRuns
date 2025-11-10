import os
import json
import sys
import asyncio
import aiohttp
from openai import AsyncOpenAI
import hashlib
import time
from typing import Dict, Any, Tuple, Optional, List
import re
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from database import AsyncSessionLocal, CustomerBase
from ClientModel import client, MODEL_NAME

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

CUSTOMER_CACHE_FILE = "customer_cache.json"

class TTLCache:
    def __init__(self, ttl_seconds: int = 3600):  # 1 hour TTL
        self.cache: Dict[str, tuple[Optional[Dict[str, Any]] | List[Dict[str, Any]], float]] = {}
        self.ttl = ttl_seconds
        self._lock = asyncio.Lock()  # Thread-safe for concurrent access

    async def get(self, key: str) -> Optional[Dict[str, Any]] | List[Dict[str, Any]]:
        async with self._lock:
            if key in self.cache:
                value, timestamp = self.cache[key]
                if time.time() - timestamp < self.ttl:
                    return value
                else:
                    del self.cache[key]
            return None

    async def set(self, key: str, value: Optional[Dict[str, Any]] | List[Dict[str, Any]]):
        async with self._lock:
            self.cache[key] = (value, time.time())

# Load persistent cache for customer lookups
def load_persistent_cache() -> Dict[str, Dict[str, Any]]:
    if os.path.exists(CUSTOMER_CACHE_FILE):
        try:
            with open(CUSTOMER_CACHE_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Failed to load cache: {e}")
            return {}
    return {}

async def save_persistent_cache(cache: Dict[str, Dict[str, Any]]):
    # Use a lock to prevent race conditions during concurrent writes
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _sync_save_cache, cache)

def _sync_save_cache(cache: Dict[str, Dict[str, Any]]):
    try:
        with open(CUSTOMER_CACHE_FILE, 'w') as f:
            json.dump(cache, f, indent=2)
    except IOError as e:
        print(f"Warning: Failed to save cache: {e}")

persistent_cache = load_persistent_cache()
ttl_cache = TTLCache()

def get_cache_key(inputs: Dict[str, str]) -> str:
    key_str = ''.join([f"{k}:{v}" for k, v in sorted(inputs.items())])
    return hashlib.md5(key_str.encode()).hexdigest()

async def retrieve_by_groupcode(groupcode: str) -> Optional[Dict[str, Any]]:
    """Retrieve customer details by groupcode. Assumes groupcode is unique."""
    async with AsyncSessionLocal() as db:
        normalized_groupcode = groupcode.lower().strip()
        print(normalized_groupcode)
        stmt = select(CustomerBase).where(
            func.lower(CustomerBase.groupcode) == normalized_groupcode
        )
        result = await db.execute(stmt)
        customer = result.scalar_one_or_none()
        
        if customer:
            customer_dict = {
                "id": customer.id,
                "created_at": customer.created_at.isoformat() if customer.created_at else None,
                "company": customer.company,
                "groupcode": customer.groupcode,
                "email": customer.email,
                "role": customer.role,
                "categories": customer.categories,
                "services": customer.services,
                "activity": customer.activity,
                "timeline": customer.timeline,
                "budget": customer.budget,
                "username": customer.username,
                "mobile": customer.mobile
            }
            return customer_dict
        return None

async def retrieve_by_company(company: str) -> List[Dict[str, Any]]:
    """Retrieve customer details by company name. Returns list as company may not be unique."""
    async with AsyncSessionLocal() as db:
        normalized_company = company.lower().strip()
        # Optimized: Add limit to prevent fetching too many results in high-scale scenarios
        # (e.g., broad searches); adjust limit as needed based on use case
        stmt = select(CustomerBase).where(
            CustomerBase.company.ilike(f"%{normalized_company}%")
        ).limit(5)  # Limit to top 5 matches for performance
        result = await db.execute(stmt)
        customers = result.scalars().all()
        
        customer_list = []
        for customer in customers:
            customer_dict = {
                "id": customer.id,
                "created_at": customer.created_at.isoformat() if customer.created_at else None,
                "company": customer.company,
                "groupcode": customer.groupcode,
                "email": customer.email,
                "role": customer.role,
                "categories": customer.categories,
                "services": customer.services,
                "activity": customer.activity,
                "timeline": customer.timeline,
                "budget": customer.budget,
                "username": customer.username,
                "mobile": customer.mobile
            }
            customer_list.append(customer_dict)
        
        return customer_list

# Tool definitions for OpenAI function calling
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_by_groupcode",
            "description": "Use this tool if the input is a group code (e.g., alphanumeric code like 'ABC123', 'GRP-456'). It retrieves customer details by exact group code match.",
            "parameters": {
                "type": "object",
                "properties": {
                    "groupcode": {
                        "type": "string",
                        "description": "The group code to look up (e.g., 'ABC123')."
                    }
                },
                "required": ["groupcode"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_by_company",
            "description": "Use this tool if the input is a company name (e.g., business name like 'Google Inc', 'Microsoft'). It retrieves customer details by fuzzy company name match.",
            "parameters": {
                "type": "object",
                "properties": {
                    "company": {
                        "type": "string",
                        "description": "The company name to look up (e.g., 'Google')."
                    }
                },
                "required": ["company"]
            }
        }
    }
]

async def execute_tool_call(tool_call):
    function_name = tool_call.function.name
    arguments = json.loads(tool_call.function.arguments)
    
    if function_name == "lookup_by_groupcode":
        groupcode = arguments.get("groupcode")
        if groupcode:
            return await retrieve_by_groupcode(groupcode)
    elif function_name == "lookup_by_company":
        company = arguments.get("company")
        if company:
            return await retrieve_by_company(company)
    
    return None

async def find_existing_customer(input_identifier: str) -> Tuple[Optional[Dict[str, Any]] | List[Dict[str, Any]], str]:
    inputs = {"input": input_identifier.lower().strip()}
    cache_key = get_cache_key(inputs)

    ttl_result = await ttl_cache.get(cache_key)
    if ttl_result is not None:
        return ttl_result, "cached"

    # Check persistent cache (sync read is fine as it's loaded once, but for scale, we could use a shared dict with lock)
    persistent_lock = asyncio.Lock()
    async with persistent_lock:
        persistent_data = persistent_cache.get(cache_key, {})
    if persistent_data:
        result = persistent_data['result']
        await ttl_cache.set(cache_key, result)
        return result, "cached"

    prompt = f"""You are a customer lookup assistant. Analyze the user input and decide how to retrieve the customer details.

- If the input looks like a GROUP CODE (short alphanumeric code like 'ABC123', 'GRP-456', typically 3-10 characters, no spaces or common words), call the 'lookup_by_groupcode' tool with the groupcode as the parameter.
- If the input looks like a COMPANY NAME (business name like 'Google Inc', 'Microsoft', longer phrases with spaces or descriptive words), call the 'lookup_by_company' tool with the company as the parameter.
- If the input is neither (e.g., random text, email, phone, or unrelated), do NOT call any tool. Just respond with nothing or a minimal message indicating no action.

User input: "{input_identifier}"

Remember: Call exactly one tool if it matches, or none if it doesn't."""

    try:
        # Parallelize if needed in future, but single call here; async OpenAI handles concurrency
        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.0,
        )
        
        result = None
        classification = "none"
        
        if response.choices[0].message.tool_calls:
            tool_call = response.choices[0].message.tool_calls[0]
            # Execute tool call asynchronously (already async functions)
            result = await execute_tool_call(tool_call)
            if tool_call.function.name == "lookup_by_groupcode":
                classification = "groupcode"
            else:
                classification = "company"
        else:
            result = None
            classification = "none"
        
        await ttl_cache.set(cache_key, result)
        
        # Update persistent cache with lock for concurrent writes
        async with persistent_lock:
            persistent_cache[cache_key] = {
                'result': result,
                'classification': classification,
                'timestamp': time.time()
            }
        await save_persistent_cache(persistent_cache)

        return result, classification
        
    except Exception as e:
        print(f"Error in find_existing_customer: {e}")
        import traceback
        traceback.print_exc()
        return None, "error"


