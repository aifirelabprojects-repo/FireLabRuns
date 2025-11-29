import os
import sys
import asyncio
from openai import AsyncOpenAI
from dotenv import load_dotenv


load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SEARCH_API_KEY = os.getenv("SEARCH_API_KEY")
VERIFICATION_CACHE_FILE = "verification_cache.json"

PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
PERPLEXITY_BASE_URL = "https://api.perplexity.ai"

client = AsyncOpenAI(api_key=OPENAI_API_KEY)
MODEL_NAME = "gpt-4o-mini"