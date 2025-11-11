from http.client import HTTPException
import os
import asyncio
import httpx
from sqlalchemy.future import select
from loguru import logger



# --- HTTP client setup for Perplexity ---
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
PERPLEXITY_BASE_URL = "https://api.perplexity.ai"

# Shared async client (connection pooling)
http_client = httpx.AsyncClient(
    base_url=PERPLEXITY_BASE_URL,
    headers={
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json",
    },
    timeout=httpx.Timeout(30.0, connect=10.0),  # fine-tuned timeouts
    limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
)


async def _call_research_async(prompt: str, max_tokens: int = 800, temperature: float = 0.1):
    """Asynchronously call the Perplexity API with retries and structured error handling."""
    payload = {
        "model": "sonar",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    for attempt in range(3):  # Retry mechanism
        try:
            response = await http_client.post("/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()

            message_content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            citations = data.get("citations", []) or []

            return message_content, citations

        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            logger.warning(f"Attempt {attempt+1} failed: {e}")
            if attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))  # exponential backoff
            else:
                raise HTTPException(status_code=502, detail=f"Perplexity API error: {e}")


