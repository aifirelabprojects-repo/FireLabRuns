import os
import json
import time
import asyncio
from openai import AsyncOpenAI
from database import AsyncSessionLocal, CompanyDetails, Session as SessionModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from dotenv import load_dotenv

load_dotenv()

perplexity_client = AsyncOpenAI(
    api_key=os.getenv("PERPLEXITY_API_KEY"),
    base_url="https://api.perplexity.ai"
)

class SimpleRateLimiter:
    def __init__(self, max_concurrent=5, rpm=60):
        self.sem = asyncio.Semaphore(max_concurrent)
        self.rpm = rpm
        self.tokens = rpm
        self.last = time.time()
        self.lock = asyncio.Lock()

    async def wait(self):
        async with self.sem:
            async with self.lock:
                now = time.time()
                elapsed = now - self.last
                self.tokens = min(self.rpm, self.tokens + elapsed * (self.rpm / 60))
                self.last = now
                if self.tokens < 1:
                    await asyncio.sleep((1 - self.tokens) * (60 / self.rpm))
                    self.tokens = 1
                self.tokens -= 1

limiter = SimpleRateLimiter(
    max_concurrent=int(os.getenv("MAX_CONCURRENT", "5")),
    rpm=int(os.getenv("RPM_LIMIT", "60"))
)

async def enrich_company(question: str) -> dict:
    ques = question.strip()
    if not ques:
        return {"summary": "", "details": {}, "sources": []}

    if not os.getenv("PERPLEXITY_API_KEY"):
        return {
            "summary": "Perplexity API key missing.",
            "details": {},
            "sources": []
        }

    await limiter.wait()

    system_prompt = (
        "Return ONLY valid JSON with this exact structure. No explanations.\n"
        "{\n"
        '  "summary": "5-8 sentence detailed summary of the company",\n'
        '  "details": {\n'
        '    "founded": "year or null",\n'
        '    "employees": "number/range or null",\n'
        '    "founders": "names or null",\n'
        '    "location": "HQ city/country or null",\n'
        '    "revenue": "latest known revenue or null",\n'
        '    "industry": "primary industry or null",\n'
        '    "confidence": 0-100\n'
        "  }\n"
        "}\n"
        "Use null if info not found. Base only on real search results."
    )

    try:
        response = await perplexity_client.chat.completions.create(
            model="sonar",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"{ques}"}
            ],
            temperature=0.1,
            max_tokens=800,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "company_enrichment",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "summary": {"type": "string"},
                            "details": {
                                "type": "object",
                                "properties": {
                                    "founded": {"type": ["string", "null"]},
                                    "employees": {"type": ["string", "null"]},
                                    "founders": {"type": ["string", "null"]},
                                    "location": {"type": ["string", "null"]},
                                    "revenue": {"type": ["string", "null"]},
                                    "industry": {"type": ["string", "null"]},
                                    "confidence": {"type": "number", "minimum": 0, "maximum": 100}
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
        )

        content = response.choices[0].message.content.strip()
        data = json.loads(content)

        # Extract sources if available
        sources = []
        if hasattr(response, "citations") and response.citations:
            sources = [str(c).strip() for c in response.citations[:5]]

        result = {
            "summary": data.get("summary", "").strip() or f"Information gathering failed",
            "details": data.get("details", {}),
            "sources": sources
        }

        return result

    except Exception as e:
        return {
            "summary": f"Failed to enrich '{ques}': {str(e)}",
            "details": {},
            "sources": []
        }

async def FindTheComp(
    question: str,
    session_id: str
) -> dict:
    result = await enrich_company(question)

    if not result["summary"] or "failed" in result["summary"].lower() or "error" in result["summary"].lower():
        return result

    c_data = json.dumps(result["details"], ensure_ascii=False)
    c_sources = json.dumps(result["sources"], ensure_ascii=False)

    async with AsyncSessionLocal() as db:
        try:
            stmt = select(SessionModel).options(selectinload(SessionModel.company_details)).where(SessionModel.id == session_id)
            session_obj = (await db.execute(stmt)).scalar_one_or_none()

            if not session_obj:
                return result

            # Update or create CompanyDetails
            if hasattr(session_obj, "company_details") and session_obj.company_details:
                cd = session_obj.company_details
                if not cd.c_data or cd.c_data == "{}":
                    cd.c_info = result["summary"]
                    cd.c_data = c_data
                    cd.c_sources = c_sources
                    await db.commit()
            else:
                new_cd = CompanyDetails(
                    session_id=session_obj.id,
                    c_info=result["summary"],
                    c_data=c_data,
                    c_sources=c_sources,
                    c_images=None
                )
                session_obj.company_details = new_cd
                db.add(new_cd)
                await db.commit()

        except Exception as e:
            await db.rollback()
            print(f"DB save failed: {e}")