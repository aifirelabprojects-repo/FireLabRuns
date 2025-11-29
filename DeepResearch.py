
import json
import asyncio
from fastapi import Depends, HTTPException
import httpx
from sqlalchemy.future import select
from loguru import logger
from ClientModel import PERPLEXITY_API_KEY, PERPLEXITY_BASE_URL
from Schemas import ResearchPayload
from database import CompanyDetails, Session as SessionModel, get_db
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
import logging

logger = logging.getLogger(__name__)


http_client = httpx.AsyncClient(
    base_url=PERPLEXITY_BASE_URL,
    headers={
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json",
    },
    timeout=httpx.Timeout(30.0, connect=10.0),  
    limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
)


import json
import asyncio
import logging
from fastapi import HTTPException
import httpx

logger = logging.getLogger(__name__)

# Your desired JSON schema (exact same as you provided)
RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "company_enrichment",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "A comprehensive, detailed summary of the company (500-800 words). Include: founding story, mission, core products/services, key milestones, funding (if any), market position, competitors, recent news, and future outlook. Write in engaging, narrative style."
                },
                "details": {
                    "type": "object",
                    "properties": {
                        "founded": {"type": ["string", "null"]},
                        "employees": {"type": ["string", "null"]},
                        "founders": {"type": ["string", "null"]},
                        "location": {"type": ["string", "null"]},
                        "revenue": {"type": ["string", "null"]},
                        "industry": {"type": ["string", "null"]},
                        "confidence": {"type": "integer", "minimum": 0, "maximum": 100}
                    },
                    "required": [],
                    "additionalProperties": False
                }
            },
            "required": ["summary", "details"],
            "additionalProperties": False
        }
    }
}

async def _call_research_async(prompt: str, max_tokens: int = 800, temperature: float = 0.1):
    payload = {
        "model": "sonar",
        "messages": [
            {
                "role": "system",
                "content": (
                "You are a senior business intelligence analyst. Your job is to research the company and return ONLY valid JSON. "
                "The 'summary' must be a rich, detailed 500-800 word company profile with company founders, founded etc. "
                "For 'confidence' you MUST provide an honest integer assessment from 0 to 100 of how reliable and complete your data is. "
                "Examples: "
                "• Well-known public company with recent Crunchbase + official site + news → 95-100 "
                "• Private startup with only LinkedIn + one article → 70-85 "
                "• Almost no data → 30-50 "
                "Never default to 0. 0 is only for completely unverifiable or fake companies. "
                "Use common sense: if you found real sources and wrote a long summary, confidence is at least 75+."
            )
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": RESPONSE_FORMAT,  # This forces strict JSON output on supported models
    }

    for attempt in range(3):
        try:
            response = await http_client.post("/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()

            # Extract raw message content (should be pure JSON string)
            raw_content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

            # Parse the JSON (will raise if invalid)
            try:
                parsed_json = json.loads(raw_content)
            except json.JSONDecodeError as parse_err:
                logger.error(f"Failed to parse model JSON response: {raw_content}")
                raise HTTPException(
                    status_code=502,
                    detail=f"Invalid JSON from research model: {parse_err}"
                )

            # Extract citations (Perplexity-style)
            citations = data.get("citations", []) or []

            return parsed_json, citations

        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            logger.warning(f"Research API attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
            else:
                raise HTTPException(status_code=502, detail=f"Perplexity API error: {e}")


def _build_research_prompt(payload: ResearchPayload) -> str:
    prompt_lines = ["Research inputs:",f"Name: {payload.name}",f"Email: {payload.email}",f"Company: {payload.company}",f"Email domain: {payload.email_domain}",f"Additional info: {payload.additional_info or ''}",
        "",
    ]
    return "\n".join(prompt_lines)




def init(app):
    @app.post("/api/deep-research")
    async def deep_research(
        payload: ResearchPayload,
        db: AsyncSession = Depends(get_db)
    ):
        try:
            # Step 1: Call external research provider
            prompt = _build_research_prompt(payload)
            sessionID = payload.id
            logger.info(f"Starting deep research for session {sessionID}")

            message_content, citations = await _call_research_async(prompt)

            stmt = (
                select(SessionModel)
                .options(selectinload(SessionModel.company_details))
                .where(SessionModel.id == sessionID)
            )
            result = await db.execute(stmt)
            session = result.scalar_one_or_none()

            if not session:
                raise HTTPException(status_code=404, detail="Session not found")

            # Step 3: Prepare new data (always fresh)
            summary = message_content["summary"]
            details = json.dumps(message_content["details"], ensure_ascii=False)
            citations_json = json.dumps(citations, ensure_ascii=False)

            print(citations_json)  # For debugging

            # Step 4: ALWAYS OVERWRITE CompanyDetails with fresh research data
            if session.company_details is None:
                # First time: create new
                session.company_details = CompanyDetails(
                    session_id=session.id,
                    c_info=summary,
                    c_data=details,
                    c_sources=citations_json,
                    c_images=None
                )
                db.add(session.company_details)
            else:
                # EXISTING: FULLY OVERWRITE with new data
                cd = session.company_details
                cd.c_info = summary
                cd.c_data = details
                cd.c_sources = citations_json
                # Optionally reset images if you want a clean slate:
                # cd.c_images = None  
                # Or keep existing images: leave as-is

            # Step 5: Commit
            await db.commit()
            await db.refresh(session)

            logger.info(f"Deep research completed and OVERWRITTEN for session {sessionID}")

            # Step 6: Return response
            return {
                "status": "success",
                "message": "Deep research completed and saved",
                "sessionID":sessionID,
                "result": summary,
                "citations": citations,
                "session_id": session.id,
                "details":details
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Deep research failed for session {sessionID}: {str(e)}", exc_info=True)
            await db.rollback()
            raise HTTPException(
                status_code=500,
                detail="Internal server error during deep research"
            )