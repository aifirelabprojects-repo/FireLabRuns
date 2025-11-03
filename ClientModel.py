import os
import sys
import asyncio
from openai import AsyncOpenAI
from dotenv import load_dotenv


load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SEARCH_API_KEY = os.getenv("SEARCH_API_KEY")
VERIFICATION_CACHE_FILE = "verification_cache.json"

client = AsyncOpenAI(api_key=OPENAI_API_KEY)
MODEL_NAME = "gpt-4o-mini"