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
from sqlalchemy.orm import Session
from sqlalchemy import func  
from database import CustomerBase, get_db  
from ClientModel import client,MODEL_NAME

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


CUSTOMER_CACHE_FILE = "customer_cache.json"

class TTLCache:
    def __init__(self, ttl_seconds: int = 3600):  # 1 hour TTL
        self.cache: Dict[str, tuple[Optional[Dict[str, Any]] | List[Dict[str, Any]], float]] = {}
        self.ttl = ttl_seconds

    def get(self, key: str) -> Optional[Dict[str, Any]] | List[Dict[str, Any]]:
        if key in self.cache:
            value, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl:
                return value
            else:
                del self.cache[key]
        return None

    def set(self, key: str, value: Optional[Dict[str, Any]] | List[Dict[str, Any]]):
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

def save_persistent_cache(cache: Dict[str, Dict[str, Any]]):
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

# Async DB query helper - optimized for production (uses context manager)
def get_db_session() -> Session:
    db = next(get_db())
    return db

async def retrieve_by_groupcode(groupcode: str) -> Optional[Dict[str, Any]]:
    """Retrieve customer details by groupcode. Assumes groupcode is unique."""
    db = get_db_session()
    try:
        # Normalize input
        normalized_groupcode = groupcode.lower().strip()
        print(normalized_groupcode)
        customer = db.query(CustomerBase).filter(
            func.lower(CustomerBase.groupcode) == normalized_groupcode  # Case-insensitive exact match
        ).first()
        
        if customer:
            # Convert to dict for easy serialization/caching
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
    finally:
        db.close()

async def retrieve_by_company(company: str) -> List[Dict[str, Any]]:
    """Retrieve customer details by company name. Returns list as company may not be unique."""
    db = get_db_session()
    try:
        # Normalize input
        normalized_company = company.lower().strip()
        customers = db.query(CustomerBase).filter(
            CustomerBase.company.ilike(f"%{normalized_company}%")  # Fuzzy match for flexibility
        ).all()
        
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
    finally:
        db.close()

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

# Function to execute the tool calls
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

# Main async function: Find existing customer using one model call with function calling
async def find_existing_customer(input_identifier: str) -> Tuple[Optional[Dict[str, Any]] | List[Dict[str, Any]], str]:
    inputs = {"input": input_identifier.lower().strip()}
    cache_key = get_cache_key(inputs)

    # Step 1: Check caches
    ttl_result = ttl_cache.get(cache_key)
    if ttl_result is not None:
        return ttl_result, "cached"

    persistent_data = persistent_cache.get(cache_key, {})
    if persistent_data:
        result = persistent_data['result']
        ttl_cache.set(cache_key, result)
        return result, "cached"

    # Step 2: Single model call with tools for classification + lookup decision
    prompt = f"""You are a customer lookup assistant. Analyze the user input and decide how to retrieve the customer details.

- If the input looks like a GROUP CODE (short alphanumeric code like 'ABC123', 'GRP-456', typically 3-10 characters, no spaces or common words), call the 'lookup_by_groupcode' tool with the groupcode as the parameter.
- If the input looks like a COMPANY NAME (business name like 'Google Inc', 'Microsoft', longer phrases with spaces or descriptive words), call the 'lookup_by_company' tool with the company as the parameter.
- If the input is neither (e.g., random text, email, phone, or unrelated), do NOT call any tool. Just respond with nothing or a minimal message indicating no action.

User input: "{input_identifier}"

Remember: Call exactly one tool if it matches, or none if it doesn't."""

    try:
        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            tools=TOOLS,
            tool_choice="auto",  # Let model decide
            temperature=0.0,
        )
        
        # Step 3: Handle tool calls or no call
        result = None
        classification = "none"  # Default for no tool call
        
        if response.choices[0].message.tool_calls:
            # Execute the tool call (only one expected)
            tool_call = response.choices[0].message.tool_calls[0]
            result = await execute_tool_call(tool_call)
            if tool_call.function.name == "lookup_by_groupcode":
                classification = "groupcode"
            else:
                classification = "company"
        else:
            # No tool called: input is neither, provide no response (return None)
            result = None
            classification = "none"
        
        # Step 4: Cache and return
        ttl_cache.set(cache_key, result)
        persistent_cache[cache_key] = {
            'result': result,
            'classification': classification,
            'timestamp': time.time()
        }
        save_persistent_cache(persistent_cache)

        return result, classification
        
    except Exception as e:
        print(f"Model call error in find_existing_customer: {e}")
        # Fallback to original heuristic if needed, but for now return None
        return None, "error"

# Wrapper for sync usage if needed (production: prefer async)
def FindExistingCustomer(input_identifier: str) -> Tuple[Optional[Dict[str, Any]] | List[Dict[str, Any]], str]:
    """Sync wrapper for async find_existing_customer."""
    try:
        return asyncio.run(find_existing_customer(input_identifier))
    except Exception as e:
        print(f"Runtime error in FindExistingCustomer: {e}")
        return None, "error"
      
