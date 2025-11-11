from openai import OpenAI
import os
import json  
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(
    api_key=os.getenv("PERPLEXITY_API_KEY"),
    base_url="https://api.perplexity.ai"  # Required for Perplexity
)

response = client.chat.completions.create(
    model="sonar",  
    messages=[
        {
            "role": "system",
            "content": "You are a helpful assistant"
        },
        {
            "role": "user",
            "content": "manaal noushad aifirelab"
        }
    ],
    # Optional: These params enhance citation quality
    temperature=0.1,  # Lower for more factual, cited responses
    max_tokens=500
)


# Extract the main message content
message_content = response.choices[0].message.content
print("Message Content:")
print(message_content)

# Extract the citations (list of URLs)
citations = response.citations
print("\nCitations:")
for i, citation in enumerate(citations, 1):
    print(f"[{i}] {citation}")



# backend/research_api.py
import os
import json
import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openai import OpenAI

load_dotenv()


client = OpenAI(
    api_key=os.getenv("PERPLEXITY_API_KEY"),
    base_url="https://api.perplexity.ai"
)


class ResearchPayload(BaseModel):
    id: int 
    name: str
    email: str
    company: str
    email_domain: Optional[str] = None
    additional_info: Optional[str] = None

def _build_research_prompt(payload: ResearchPayload) -> str:
    prompt_lines = [
        "Research inputs:",
        f"Name: {payload.name}",
        f"Email: {payload.email}",
        f"Company: {payload.company}",
        f"Email domain: {payload.email_domain}",
        f"Additional info: {payload.additional_info or ''}",
        "",
    ]
    return "\n".join(prompt_lines)

def _call_perplexity_sync(prompt: str, max_tokens: int = 800, temperature: float = 0.1):

    response = client.chat.completions.create(
        model="sonar",
        messages=[
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": prompt}
        ],
        temperature=temperature,
        max_tokens=max_tokens
    )
    # Based on your snippet
    message_content = None
    citations = []
    try:
        message_content = response.choices[0].message.content
    except Exception:
        # best-effort fallback
        message_content = getattr(response, "text", "") or str(response)
    citations = getattr(response, "citations", []) or []

    return message_content, citations

@app.post("/api/deep-research")
async def deep_research(payload: ResearchPayload, db: AsyncSession = Depends(get_db)):
    # Build prompt
    prompt = _build_research_prompt(payload)

    # Run the synchronous network call in a thread to avoid blocking event loop
    try:
        message_content, citations = await asyncio.to_thread(_call_perplexity_sync, prompt)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Research provider error: {e}")

    stmt = select(SessionModel).where(SessionModel.id == payload.id)
    db_result = await db.execute(stmt)
    db_session = db_result.scalar_one_or_none()
    if not db_session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Save into research-related fields. Adapt field names to your SessionModel schema.
    # We're using research_data (JSON string), research_sources (JSON list), research_status
    db_session.research_data = message_content
    db_session.research_sources = citations

    await db.commit()
    await db.refresh(db_session)

    # Return structured response to frontend so it can show browser notification
    return {
        "status": "success",
        "message": "Deep research completed and saved to session",
        "result": message_content,
        "citations": citations,
        "session_id": db_session.id
    }
