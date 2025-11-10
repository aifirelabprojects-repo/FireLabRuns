from io import StringIO
import math
import mimetypes
import os
from pathlib import Path
import uuid
import json
import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Set,AsyncGenerator, Tuple,Union
from fastapi import Depends, FastAPI, File, Request, Response, UploadFile, WebSocket, WebSocketDisconnect, Query, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from openai import OpenAI, APIError, AuthenticationError, RateLimitError
from dotenv import load_dotenv
from sqlalchemy import func, or_, select,case,and_
from sqlalchemy.ext.asyncio import AsyncSession
from BotGraph import invoke_chat_async,reload_system_prompt
from VerifyUser import verify_user
from database import AsyncSessionLocal, Session as SessionModel, Message as MessageModel, get_db, init_db 
from collections import Counter, defaultdict
from CompanyFinder import FindTheComp
from FindUser import find_existing_customer
from pydantic import BaseModel
import csv
from fastapi.responses import FileResponse, StreamingResponse
from functools import lru_cache
from cachetools import TTLCache  
from dateutil.relativedelta import relativedelta
from functools import partial
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy.sql import Select
from KnowledgeBase import cfg
from dataclasses import dataclass


load_dotenv()

_MAIN_CATEGORIES = cfg.get("main_categories", "")
_SUB_SERVICES = cfg.get("sub_services", "")
_TIMELINE_OPTIONS = cfg.get("timeline_options", "")
_BUDGET_OPTIONS = cfg.get("budget_options", "")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL_NAME = "gpt-4o-mini"
SITE_NAME = "Business Chatbot"
INACTIVITY_THRESHOLD = timedelta(minutes=5)  

session_cache = TTLCache(maxsize=1000, ttl=300)



os.makedirs("data", exist_ok=True)
os.makedirs("vectorstore", exist_ok=True)

app = FastAPI(title="Business Chatbot API")

@app.api_route("/health", methods=["GET", "HEAD"])
async def health_check():
    return {"status": "ok"}

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.on_event("startup")
async def startup_event():
    await init_db()

@app.on_event("shutdown")
def shutdown_event():
    cfg.stop()


client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, session_id: str):
        await websocket.accept()
        if session_id not in self.active_connections:
            self.active_connections[session_id] = set()
        self.active_connections[session_id].add(websocket)

    def disconnect(self, websocket: WebSocket, session_id: str):
        if session_id in self.active_connections:
            self.active_connections[session_id].discard(websocket)
            if not self.active_connections[session_id]:
                del self.active_connections[session_id]

    async def broadcast(self, message: str, session_id: str):
        if session_id not in self.active_connections:
            return
        disconnected = set()
        for connection in list(self.active_connections[session_id]):
            try:
                await connection.send_text(message)
            except:
                disconnected.add(connection)
        for conn in disconnected:
            self.active_connections[session_id].discard(conn)

    async def send_history(self, session_id: str, websocket: WebSocket):
        async def fetch_history():
            async with AsyncSessionLocal() as db:  # Proper async context
                try:
                    from sqlalchemy import select
                    stmt = select(MessageModel).filter(MessageModel.session_id == session_id).order_by(MessageModel.timestamp.asc())
                    result = await db.execute(stmt)
                    messages = result.scalars().all()
                    # Convert to dicts (handle None timestamps)
                    message_list = [
                        {
                            "role": msg.role,
                            "content": msg.content,
                            "timestamp": msg.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ") if msg.timestamp else "2025-01-01T00:00:00Z",
                            "interest": msg.interest,
                            "mood": msg.mood,
                        }
                        for msg in messages
                    ]
                    return json.dumps({"type": "history", "messages": message_list})
                except Exception as e:
                    print(f"[DB ERROR] {e}")
                    return json.dumps({"type": "history", "messages": []})

        # Run in threadpool if needed, but since it's async, just await
        history_json = await fetch_history()
        await websocket.send_text(history_json)

manager = ConnectionManager()

  

MAIN_CATEGORIES = []  
SUB_SERVICES = {}  
TIMELINE_OPTIONS = []  
BUDGET_RANGES = ""  


async def get_bot_response_async(question: str, session_obj: SessionModel, session_id: str = "transient") -> Dict[str, Any]:
    phase = session_obj.phase if session_obj.phase else "initial"
    haveRole = session_obj.q2_role if session_obj.q2_role else None
    routing = session_obj.routing
    customer_enrichment = None
    fetch_trigger_phases = ["existing_fetch", "snip_q0"]
    if phase in fetch_trigger_phases and session_obj.q1_company is None:
        customer_enrichment, kind = await find_existing_customer(question)  # Now await instead of asyncio.run
        print(customer_enrichment)
        if customer_enrichment:
            session_obj.q1_email = customer_enrichment["email"]
            session_obj.q1_company = customer_enrichment["company"]
            session_obj.q2_role = customer_enrichment["role"]
            session_obj.q3_categories = customer_enrichment["categories"]
            session_obj.q4_services = customer_enrichment["services"]
            session_obj.q5_activity = customer_enrichment["activity"]
            session_obj.q6_timeline = customer_enrichment["timeline"]
            session_obj.q7_budget = customer_enrichment["budget"]
            session_obj.username = customer_enrichment["username"]
            session_obj.mobile = customer_enrichment["mobile"]
            session_obj.routing = "cre"
            session_obj.phase = "routing"
            routing = "cre"
        else:
            if phase == "initial":
                phase = "existing_fetch"
                session_obj.phase = "existing_fetch"

    enrichment = None
    company_det_used = False
    company_det = session_obj.c_info
    if company_det is None and phase in ("snip_q1", "snip_q2", "snip_q2a"):
        asyncio.create_task(FindTheComp(question, session_obj.id))
    lead_data = json.dumps({
        "name": session_obj.username,
        "phone": session_obj.mobile,
        "phase": session_obj.phase,
        "routing": session_obj.routing,
        "lead_company": session_obj.q1_company,
        "lead_email": session_obj.q1_email,
        "lead_email_domain": session_obj.q1_email_domain,
        "lead_role": session_obj.q2_role,
        "lead_categories": session_obj.q3_categories,
        "lead_services": session_obj.q4_services,
        "lead_activity": session_obj.q5_activity,
        "lead_timeline": session_obj.q6_timeline,
        "lead_budget": session_obj.q7_budget
    })

    options = []
    context_parts = []
    
    print(f"q3_categories: {session_obj.q3_categories}, type: {type(session_obj.q3_categories)}")
    print(f"Condition result: {(phase == 'snip_q3' or phase == 'snip_q2') and not session_obj.q3_categories}")

    try:
        if phase == "snip_q2a" and not session_obj.q2_role:
            cat_context = "Customer Business role is not provided yet, ask it and set phase to snip_q3"
            context_parts.append(cat_context)
        elif (phase == "snip_q2" or phase == "snip_q2a") and (session_obj.q3_categories is None or session_obj.q3_categories == []):
            print(f"Listing Main: {session_obj.q3_categories}")
            cat_context = f"Use This as Options for Main Categories: {str(_MAIN_CATEGORIES)}"
            session_obj.phase = "snip_q4"
            context_parts.append(cat_context)
        elif (phase == "snip_q3") and not session_obj.q4_services:
            print(f"Listing Sub Serv: {session_obj.q3_categories}")
            main_cats = f"Use This as Options for SUB SERVICES based on the Main category user selected: {str(_SUB_SERVICES)}"
            context_parts.append(main_cats)
        elif session_obj.q3_categories and (phase == "snip_q5" or phase == "snip_q6"):
            print("Listing Time and Budget")
            context_parts.append(f"Timeline info: str(_TIMELINE_OPTIONS)")
            context_parts.append(f"Budget info: {_BUDGET_OPTIONS}")

    except Exception:
        pass
    context = "\n".join(context_parts)
    print("\n\nphase:", phase, "\n")
    EnrData = f"Existing Customer Data (if applicable):Welcome the user with thier data {json.dumps(lead_data)} if the user is exisitng set phase='routing' and routing='cre' " if routing == "cre" else " "
    input_text = f"""Current state from session: Phase='{phase}'
                    Context (from vectorstore—use for services, benefits, company data): {context}
                    {EnrData}
                    User Question/Input: {question}
                """.strip()

    if company_det is not None and phase in ("snip_q1", "snip_q2", "snip_q2a") and not company_det_used:
        print("triggered\n")
        print(company_det)
        input_text += f"\nUSE THE FOLLOWING COMPANY DETAILS IN YOUR RESPONSE. Refer to them whenever relevant:\n{company_det}\n"
        company_det_used = True

    try:
        full_response = ""
        async for chunk in invoke_chat_async(input_text, session_id):
            full_response += chunk
        raw_output = full_response
        try:
            parsed = json.loads(raw_output)
            print("\n\nphase model detected:", parsed, "\n")
            if phase == "snip_q4":
                options = []
            if "options" not in parsed or not parsed["options"]:
                parsed["options"] = options
            return parsed
        except json.JSONDecodeError:
            json_part = re.search(r'\{.*\}', raw_output, re.DOTALL)
            if json_part:
                parsed = json.loads(json_part.group(0))
                if "options" not in parsed or not parsed["options"]:
                    parsed["options"] = options
                return parsed
            # Fallback structure if parse fails

            print("\n\nphase model detected:", phase, "\n")
            return {
                "answer": raw_output,
                "options": options,
                "phase": phase,
                "lead_data": lead_data,
                "routing": "none",
                "analysis": {
                    "interest": "unknown",
                    "mood": "unknown",

                }
            }
    except Exception as lc_err:
        print(f"[LangChain invoke failed — falling back to direct client] {lc_err}")
        # Fallback to direct client (wrap sync call in executor for async compatibility)
        loop = asyncio.get_event_loop()
        try:
            completion = await loop.run_in_executor(
                None,
                lambda: client.chat.completions.create(
                    model="qwen/qwen-2.5-coder-32b-instruct:free",
                    messages=[
                        {"role": "system", "content": "You are a JSON-only business assistant following the SNIP flow. Respond EXCLUSIVELY with valid JSON in the EXACT format specified. No other text."},
                        {"role": "user", "content": input_text}
                    ],
                )
            )
            raw_output = completion.choices[0].message.content
            try:
                parsed = json.loads(raw_output)
                if "options" not in parsed or not parsed["options"]:
                    parsed["options"] = options
                return parsed
            except json.JSONDecodeError:
                json_part = re.search(r'\{.*\}', raw_output, re.DOTALL)
                if json_part:
                    parsed = json.loads(json_part.group(0))
                    if "options" not in parsed or not parsed["options"]:
                        parsed["options"] = options
                    return parsed
                else:
                    return {
                        "answer": raw_output,
                        "options": options,
                        "phase": phase,
                        "lead_data": lead_data,
                        "routing": "none",
                        "analysis": {
                            "interest": "unknown",
                            "mood": "unknown",

                        }
                    }
        except AuthenticationError as e:
            print(f"Authentication Error: {e}")
            return {
                "answer": "Authentication failed — check your API key.",
                "options": [],
                "phase": phase,
                "lead_data": lead_data,
                "routing": "none",
                "analysis": {"interest": "unknown", "mood": "frustrated", "details": {}}
            }
        except RateLimitError as e:
            print(f"Rate Limit Error: {e}")
            return {
                "answer": "Too many requests. Please try again later.",
                "options": [],
                "phase": phase,
                "lead_data": lead_data,
                "routing": "none",
                "analysis": {"interest": "high", "mood": "impatient", "details": {}}
            }
        except APIError as e:
            print(f"API Error: {e}")
            return {
                "answer": "Something went wrong on my end. Let me try that again or feel free to rephrase!",
                "options": [],
                "phase": phase,
                "lead_data": lead_data,
                "routing": "none",
                "analysis": {"interest": "medium", "mood": "confused", "details": {}}
            }
        except Exception as e:
            print(f"Error: {e}")
            return {
                "answer": "Something went wrong on my end. Let me try that again or feel free to rephrase!",
                "options": [],
                "phase": phase,
                "lead_data": lead_data,
                "routing": "none",
                "analysis": {"interest": "unknown", "mood": "confused", "details": {}}
            }
            
            
async def insert_user_message_async(session_id: str, content: str) -> Tuple[str, str]:
    async with AsyncSessionLocal() as db:  
        try:
            ts = datetime.utcnow()
            from sqlalchemy import select
            result = await db.execute(select(SessionModel).filter(SessionModel.id == session_id))
            session_obj = result.scalar_one_or_none()

            if not session_obj:
                session_obj = SessionModel(id=session_id, status="active")
                db.add(session_obj)
                await db.flush()  # Flush to get ID if needed

            message = MessageModel(
                session_id=session_id,
                role="user",
                content=content,
                timestamp=ts,
                interest=None,
                mood=None
            )
            db.add(message)

            if session_obj.status != "admin":
                session_obj.status = "active"
            session_obj.updated_at = datetime.utcnow()
            await db.commit()

            return ts.isoformat(), session_obj.status

        except Exception as e:
            await db.rollback()
            raise e

async def handle_bot_response_async(session_id: str, question: str) -> Dict[str, Any]:
    async with AsyncSessionLocal() as db:  # Context manager
        try:
            session_obj = await db.get(SessionModel, session_id)
            if not session_obj:
                raise ValueError(f"Session {session_id} not found")

            current_details = {
                "phase": "initial",
                "lead_data": {},
                "details": {}
            }

            # Use async bot response
            response_data = await get_bot_response_async(question, session_obj, session_id)

            # Extract main response components
            answer = response_data.get("answer", "")
            options = response_data.get("options", [])
            next_phase = response_data.get("phase", session_obj.phase)
            lead_data = response_data.get("lead_data", {}) or {}
            routing = response_data.get("routing", session_obj.routing)

            # Extract analysis safely
            analysis = response_data.get("analysis") or {}
            interest = analysis.get("interest", "medium")
            mood = analysis.get("mood", "neutral")

            # Create a bot message
            bot_message = MessageModel(
                session_id=session_id,
                role="bot",
                content=answer,
                timestamp=datetime.utcnow(),
                interest=interest,
                mood=mood
            )
            db.add(bot_message)

            # --- Clean and save lead_data safely ---
            lead_fields = [
                "q1_company",
                "q1_email",
                "q1_email_domain",
                "q2_role",
                "q3_categories",
                "q4_services",
                "q5_activity",
                "q6_timeline",
                "q7_budget",
                "username",
                "mobile"
            ]

            for field in lead_fields:
                if field in lead_data:
                    val = lead_data.get(field)

                    # Convert list → comma-separated string
                    if isinstance(val, list):
                        val = ", ".join(str(v).strip() for v in val if str(v).strip())

                    # Skip None or empty/whitespace values
                    if val is None or (isinstance(val, str) and val.strip() == ""):
                        continue

                    # Set cleaned value if the model has this attribute
                    if hasattr(session_obj, field):
                        try:
                            setattr(session_obj, field, str(val).strip())
                        except Exception:
                            pass  

            session_obj.interest = interest
            session_obj.mood = mood
            session_obj.phase = next_phase
            if routing != None :
                session_obj.routing = routing
            session_obj.updated_at = datetime.utcnow()
            session_obj.status = "active"

            db.add(session_obj)
            await db.commit()  

            # Response payload
            bot_ts = bot_message.timestamp.isoformat() if getattr(bot_message, "timestamp", None) else datetime.utcnow().isoformat()

            return {
                "answer": answer,
                "options": options,
                "phase": next_phase,
                "lead_data": lead_data,
                "routing": routing,
                "analysis": analysis,
                "bot_ts": bot_ts
            }

        except Exception:
            await db.rollback()  
            raise
        finally:
            await db.close()  




async def update_inactive_sessions():  # Make async
    async with AsyncSessionLocal() as db:
        try:
            threshold_time = datetime.utcnow() - INACTIVITY_THRESHOLD
            from sqlalchemy import select, update
            stmt = update(SessionModel).where(
                SessionModel.status == "active",
                SessionModel.updated_at < threshold_time
            ).values(status="inactive")
            await db.execute(stmt)
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        
# main

@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/api/sessions/")
async def create_session(db: AsyncSession = Depends(get_db)):  
    session_id = str(uuid.uuid4())
    new_session = SessionModel(
        id=session_id,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    db.add(new_session)
    await db.commit()  
    await db.refresh(new_session)
    return {"session_id": session_id}


interest_score = {"low": 0.0, "medium": 1.0, "high": 2.0}
score_to_interest = lambda s: "low" if s < 0.5 else ("medium" if s < 1.5 else "high")

@app.get("/api/sessions/")
async def get_sessions(
    active: bool = Query(False),
    page: int = Query(1, ge=1),
    per_page: int = Query(5, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    try:
        await update_inactive_sessions()
        
        # Build base query for sessions
        base_query = select(SessionModel)
        if active:
            base_query = base_query.filter(SessionModel.status == "active")
        
        # Get total count for pagination
        total_stmt = select(func.count(SessionModel.id))
        if active:
            total_stmt = total_stmt.filter(SessionModel.status == "active")
        total_result = await db.execute(total_stmt)
        total = total_result.scalar() or 0

        if total == 0:
            return {
                "sessions": [],
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": 0,
                    "pages": 0
                }
            }

        # Paginated query
        offset = (page - 1) * per_page
        session_stmt = base_query.order_by(SessionModel.created_at.desc()).offset(offset).limit(per_page)
        session_result = await db.execute(session_stmt)
        sessions = session_result.scalars().all()

        # Batch messages (as before, but ensure async)
        session_ids = [s.id for s in sessions]
        msg_stmt = (
            select(MessageModel)
            .where(MessageModel.session_id.in_(session_ids))
            .order_by(MessageModel.session_id, MessageModel.timestamp.asc())
        )
        msg_result = await db.execute(msg_stmt)
        msg_rows_all = msg_result.scalars().all()

        # Group messages by session_id
        messages_by_session: Dict[int, List[MessageModel]] = defaultdict(list)
        for msg in msg_rows_all:
            messages_by_session[msg.session_id].append(msg)

        # Precompute half-life constants (shared across sessions)
        half_life_seconds = 3 * 24 * 3600
        ln2 = math.log(2)

        sessions_list = []
        for sess in sessions:
            # Parse details once
            try:
                details_json = json.loads(sess.details) if sess.details else {}
            except Exception:
                details_json = {}

            # Get messages for this session from grouped data
            msg_rows = messages_by_session.get(sess.id, [])

            # Compute last message from the grouped messages (no extra query)
            last_msg_obj = msg_rows[-1] if msg_rows else None
            last_msg = (
                (last_msg_obj.content[:50] + "...")
                if last_msg_obj and len(last_msg_obj.content) > 50
                else (last_msg_obj.content if last_msg_obj else "")
            )

            # Compute interest and mood
            overall_interest_label = sess.interest
            overall_mood_label = sess.mood

            if msg_rows:
                parsed = []
                for msg in msg_rows:
                    ts_dt = msg.timestamp or datetime.utcnow()
                    parsed.append(
                        (
                            msg.role.lower() if msg.role else "",
                            (msg.interest or "").lower(),
                            (msg.mood or "").lower(),
                            ts_dt,
                        )
                    )

                latest_ts = max((ts for _, _, _, ts in parsed if ts), default=datetime.utcnow())

                weighted_sum = 0.0
                weight_total = 0.0
                mood_weights = {}

                # Track user interests for dominance logic
                user_interest_counts = {"low": 0, "medium": 0, "high": 0}
                user_msg_count = 0
                last_user_interest = None
                last_user_ts = None

                for role, interest_val, mood_val, ts_dt in parsed:
                    delta = (latest_ts - ts_dt).total_seconds() if ts_dt else 0.0
                    weight = math.exp(-ln2 * (delta / half_life_seconds))

                    if interest_val in interest_score:
                        s = interest_score[interest_val]
                        weighted_sum += s * weight
                        weight_total += weight

                    if role == "bot":
                        # Count user interest distribution
                        if interest_val in user_interest_counts:
                            user_interest_counts[interest_val] += 1
                        user_msg_count += 1

                        # Track last (most recent) user message
                        if not last_user_ts or ts_dt > last_user_ts:
                            last_user_ts = ts_dt
                            last_user_interest = interest_val

                        # Mood weighting from user only
                        if mood_val:
                            mood_weights[mood_val] = mood_weights.get(mood_val, 0.0) + weight

                # Rule-based interest override
                forced_label = None
                if user_msg_count > 0:
                    low_prop = user_interest_counts.get("low", 0) / user_msg_count
                    high_prop = user_interest_counts.get("high", 0) / user_msg_count

                    LOW_DOMINANCE_THRESHOLD = 0.5   # 50% of user msgs low → low
                    HIGH_DOMINANCE_THRESHOLD = 0.66 # 66% of user msgs high → high

                    # 👇 Strong rule: if user's last message has low interest → overall low
                    if last_user_interest == "low":
                        forced_label = "low"
                    elif low_prop >= LOW_DOMINANCE_THRESHOLD:
                        forced_label = "low"
                    elif high_prop >= HIGH_DOMINANCE_THRESHOLD:
                        forced_label = "high"

                if forced_label:
                    overall_interest_label = forced_label
                elif weight_total > 0:
                    avg_score = weighted_sum / weight_total
                    overall_interest_label = score_to_interest(avg_score)

                if mood_weights:
                    overall_mood_label = max(mood_weights.items(), key=lambda kv: kv[1])[0]

            sessions_list.append({
                "id": sess.id,
                "created_at": sess.created_at,
                "status": sess.status,
                "verified": sess.verified,
                "confidence": sess.confidence,
                "evidence": sess.evidence,
                "sources": sess.v_sources,
                "research_data":sess.research_data,
                "interest": overall_interest_label,
                "mood": overall_mood_label,
                "name": sess.username,
                "usr_phone": sess.mobile,
                "phase": sess.phase,
                "routing": sess.routing,
                "last_message": last_msg,
                "lead_company": sess.q1_company,
                "lead_email": sess.q1_email,
                "lead_email_domain": sess.q1_email_domain,
                "lead_role": sess.q2_role,
                "lead_categories": sess.q3_categories,
                "lead_services": sess.q4_services,
                "lead_activity": sess.q5_activity,
                "lead_timeline": sess.q6_timeline,
                "lead_budget": sess.q7_budget,
                "c_sources": sess.c_sources,
                "c_info": sess.c_info,
                "c_data": sess.c_data,
                "c_images": sess.c_images,
                "approved": sess.approved,
            })

        pages = math.ceil(total / per_page)

        return {
            "sessions": sessions_list,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": pages
            }
        }

    except Exception as e:
        print("Error in get_sessions:", e)
        return {
            "sessions": [],
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": 0,
                "pages": 0
            }
        }





# Module-level executor so we don't create a new executor per request:
_MAX_WORKERS = min(32, (os.cpu_count() or 4) * 4)
_THREAD_POOL = ThreadPoolExecutor(max_workers=_MAX_WORKERS)

# --- Helper: safe count of a Select statement ---
async def _safe_count(db, base_select: Select) -> int:

    # Remove ordering (ORDER BY can slow COUNT or break counting semantics)
    stmt_no_order = base_select.order_by(None)
    count_stmt = select(func.count()).select_from(stmt_no_order.subquery())
    result = await db.execute(count_stmt)
    return int(result.scalar() or 0)

# --- Async wrapper for CPU-bound compute, using module-level thread pool ---
async def _compute_session_data_async(sess: SessionModel, msg_rows: List[MessageModel],
                                      half_life_seconds: float, ln2: float) -> Dict[str, Any]:
    loop = asyncio.get_running_loop()
    # schedule on shared thread pool to limit concurrency
    return await loop.run_in_executor(
        _THREAD_POOL,
        partial(_compute_session_data, sess, msg_rows, half_life_seconds, ln2),
    )

# (your existing _compute_session_data function unchanged)
def _compute_session_data(sess: SessionModel, msg_rows: List[MessageModel],
                          half_life_seconds: float, ln2: float) -> Dict[str, Any]:
    try:
        details_json = json.loads(sess.details) if sess.details else {}
    except Exception:
        details_json = {}
    last_msg_obj = msg_rows[-1] if msg_rows else None
    last_msg = (
        (last_msg_obj.content[:50] + "...")
        if last_msg_obj and len(last_msg_obj.content) > 50
        else (last_msg_obj.content if last_msg_obj else "")
    )
    overall_interest_label = sess.interest or "medium"
    overall_mood_label = sess.mood or ""
    if msg_rows:
        parsed = []
        for msg in msg_rows:
            ts_dt = msg.timestamp or datetime.utcnow()
            role_lower = msg.role.lower() if msg.role else ""
            mood_val = (msg.mood or "").lower()
            parsed.append((role_lower, mood_val, ts_dt))
        latest_ts = max((ts for _, _, ts in parsed), default=datetime.utcnow())
        mood_weights = {}
        for role, mood_val, ts_dt in parsed:
            if role == "user" and mood_val:
                delta = (latest_ts - ts_dt).total_seconds() if ts_dt else 0.0
                weight = math.exp(-ln2 * (delta / half_life_seconds))
                mood_weights[mood_val] = mood_weights.get(mood_val, 0.0) + weight
        if mood_weights:
            overall_mood_label = max(mood_weights.items(), key=lambda kv: kv[1])[0]
    date_str = sess.created_at.strftime("%b %d, %Y") if sess.created_at else ""
    return {
        "id": sess.id,
        "created_at": sess.created_at,
        "status": sess.status,
        "verified": sess.verified,
        "confidence": sess.confidence,
        "evidence": sess.evidence,
        "sources": sess.v_sources,
        "interest": overall_interest_label,
        "mood": overall_mood_label,
        "name": sess.username,
        "usr_phone": sess.mobile,
        "phase": sess.phase,
        "routing": sess.routing,
        "last_message": last_msg,
        "lead_company": sess.q1_company or "-",
        "lead_email": sess.q1_email,
        "lead_email_domain": sess.q1_email_domain,
        "lead_role": sess.q2_role,
        "lead_categories": sess.q3_categories,
        "lead_services": sess.q4_services or "-",
        "lead_activity": sess.q5_activity,
        "lead_timeline": sess.q6_timeline,
        "lead_budget": sess.q7_budget,
        "c_sources": sess.c_sources,
        "c_info": sess.c_info,
        "c_data": sess.c_data,
        "c_images": sess.c_images,
        "approved": sess.approved,
        "date_str": date_str,
    }

# --- Internal fetch + compute (keeps the same high-level behavior) ---
async def _fetch_and_compute_sessions(db: AsyncSession, base_query: Select, page: int, per_page: int) -> Tuple[List[Dict[str, Any]], int, int]:
    half_life_seconds = 3 * 24 * 3600
    ln2 = math.log(2)

    # Get total count robustly
    total = await _safe_count(db, base_query)
    if total == 0:
        return [], 0, 0

    # Pagination
    offset = (page - 1) * per_page
    session_stmt = base_query.order_by(SessionModel.created_at.desc()).offset(offset).limit(per_page)
    session_result = await db.execute(session_stmt)
    sessions = session_result.scalars().all()

    # If no sessions, return quickly
    if not sessions:
        pages = math.ceil(total / per_page) if total else 0
        return [], total, pages

    # Batch fetch messages for those sessions (single extra query)
    session_ids = [s.id for s in sessions]
    msg_stmt = (
        select(MessageModel)
        .where(MessageModel.session_id.in_(session_ids))
        .order_by(MessageModel.session_id, MessageModel.timestamp.asc())
    )
    msg_result = await db.execute(msg_stmt)
    msg_rows_all = msg_result.scalars().all()

    # Group messages by session_id
    messages_by_session: Dict[Any, List[MessageModel]] = defaultdict(list)
    for msg in msg_rows_all:
        messages_by_session[msg.session_id].append(msg)

    # Compute per-session using bounded thread pool via _compute_session_data_async
    compute_tasks = [
        _compute_session_data_async(sess, messages_by_session.get(sess.id, []), half_life_seconds, ln2)
        for sess in sessions
    ]
    sessions_list = await asyncio.gather(*compute_tasks)

    pages = math.ceil(total / per_page) if total else 0
    return sessions_list, total, pages

# --- Streaming CSV generator (lightweight fields-only query for big exports) ---
async def _generate_csv_stream_minimal(db: AsyncSession, query: Select) -> AsyncGenerator[str, None]:
    """
    For export_all: query should select just the minimal columns needed.
    This avoids loading messages / computing mood for every row.
    """
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Name", "Email", "Company", "Service", "Score", "Date"])
    yield output.getvalue()
    output.seek(0)
    output.truncate(0)

    # Stream rows (execute the minimal select and iterate)
    result = await db.stream(query)
    async for row in result:
        # Row fields depend on selected columns; adapt as needed
        username = row[0]
        email = row[1]
        company = row[2] or "-"
        service = row[3] or "-"
        score = (row[4] or "medium").capitalize()
        date_str = row[5] or ""
        writer.writerow([username, email, company, service, score, date_str])
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)

# --- Main endpoint (optimized) ---
@app.get("/api/leads/", response_model=None)
async def get_leads(
    q: str = Query(None),
    interest: str = Query(None),
    approved: bool = Query(True),
    active: bool = Query(False),
    format: str = Query(None),
    export_all: bool = Query(False),
    page: int = Query(1, ge=1),
    per_page: int = Query(5, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
) -> Union[Dict[str, Any], Response]:
    try:
        if interest in [None, "", "all","neutral"]:
            interest = None

        # Simple cache key
        cache_key = f"{q}_{interest}_{approved}_{active}_{page}_{per_page}"
        if 'session_cache' in globals() and cache_key in session_cache:
            cached = session_cache[cache_key]
            if format == "csv":
                pass
            return cached

        await update_inactive_sessions()

        # Build base query
        base_query = select(SessionModel)
        if active:
            base_query = base_query.where(SessionModel.status == "active")
        if approved:
            base_query = base_query.where(SessionModel.approved.is_(True))

        if q:
            search_term = f"%{q}%"
            base_query = base_query.where(
                or_(
                    SessionModel.username.ilike(search_term),
                    SessionModel.q1_email.ilike(search_term),
                    SessionModel.q1_company.ilike(search_term)
                )
            )

        if interest:
            base_query = base_query.where(SessionModel.interest == interest.lower())

        # Full export: stream minimal columns directly from DB (no heavy compute)
        if export_all and format == "csv":
            # cap export size for safety
            export_stmt = select(
                SessionModel.username,
                SessionModel.q1_email,
                SessionModel.q1_company,
                SessionModel.q4_services,
                SessionModel.interest,
                SessionModel.created_at
            ).order_by(SessionModel.created_at.desc()).limit(10000)
            # apply same filters by joining with base_query's where clause if any
            # (we rebuilt base_query above so filters already applied)
            return StreamingResponse(
                _generate_csv_stream_minimal(db, export_stmt),
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=leads.csv"}
            )

        # Paginated compute & response
        sessions_list, total, pages = await _fetch_and_compute_sessions(db, base_query, page, per_page)
        response = {
            "sessions": sessions_list,
            "pagination": {"page": page, "per_page": per_page, "total": total, "pages": pages}
        }

        if 'session_cache' in globals():
            session_cache[cache_key] = response

        # Paginated CSV (small) - build CSV from already computed sessions_list
        if format == "csv" and not export_all:
            output = StringIO()
            writer = csv.DictWriter(output, fieldnames=["Name", "Email", "Company", "Service", "Score", "Date"])
            writer.writeheader()
            for s in sessions_list:
                writer.writerow({
                    "Name": s["name"],
                    "Email": s["lead_email"],
                    "Company": s["lead_company"],
                    "Service": s["lead_services"],
                    "Score": s["interest"].capitalize(),
                    "Date": s.get("date_str", "")
                })
            return Response(
                content=output.getvalue(),
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename=leads_page_{page}.csv"}
            )

        return response

    except Exception as e:
        # keep existing fallback behavior
        if format == "csv":
            return Response(content="data:text/csv;charset=utf-8,", media_type="text/csv")
        return {
            "sessions": [],
            "pagination": {"page": page, "per_page": per_page, "total": 0, "pages": 0}
        }


templates = Jinja2Templates(directory="templates")


@app.get("/admin/")
async def admin_home(request: Request, db = Depends(get_db)):
    context = {
        "request": request,
    }

    return templates.TemplateResponse("admin.html", context)


@app.websocket("/ws/chat/{session_id}")
async def websocket_chat(websocket: WebSocket, session_id: str):
    await manager.connect(websocket, session_id)
    await manager.send_history(session_id, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                parsed_data = json.loads(data)
            except json.JSONDecodeError:
                continue
            if parsed_data.get("type") == "message":
                content = parsed_data["content"]
                try:
                    ts, current_status = await insert_user_message_async(session_id, content)
                except Exception:
                    error_msg = {"type": "error", "content": "Sorry, an error occurred while processing your message."}
                    await manager.broadcast(json.dumps(error_msg), session_id)
                    continue
                
                user_msg = {
                    "type": "message",
                    "role": "user",
                    "content": content,
                    "timestamp": ts,
                    "interest": None,
                    "mood": None
                }
                await manager.broadcast(json.dumps(user_msg), session_id)
                
                if current_status == "active":
                    try:
                        bot_data = await handle_bot_response_async(session_id, content)
                        answer = bot_data.get("answer", "")
                        options = bot_data.get("options", [])
                        analysis = bot_data.get("analysis") or {}

                        # Safe defaults
                        interest = analysis.get("interest", "medium")
                        mood = analysis.get("mood", "neutral")

                        bot_ts = bot_data.get("bot_ts")
                        bot_msg = {
                            "type": "message",
                            "role": "bot",
                            "content": answer,
                            "options": options,
                            "timestamp": bot_ts,
                            "interest": interest,
                            "mood": mood
                        }
                        await manager.broadcast(json.dumps(bot_msg), session_id)
                    except Exception as e:
                        print(e)
                        bot_error = {"type": "message","role": "error", "content": f"Bot is temporarily unavailable. Please try again.{e}"}
                        await manager.broadcast(json.dumps(bot_error), session_id)
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket, session_id)
        
        
@app.websocket("/ws/view/{session_id}")
async def websocket_view(websocket: WebSocket, session_id: str):
    await manager.connect(websocket, session_id)
    await manager.send_history(session_id, websocket)
    try:
        while True:
            await asyncio.sleep(60)
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket, session_id)



from sqlalchemy.orm.exc import NoResultFound


@app.websocket("/ws/control/{session_id}")
async def websocket_control(websocket: WebSocket, session_id: str):
    async def set_admin_status(session_id: str):
        async with AsyncSessionLocal() as db:
            try:
                sess = await db.get(SessionModel, session_id)
                if sess:
                    sess.status = "admin"
                    sess.updated_at = datetime.now(timezone.utc)
                    await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def do_handover(session_id: str):
        async with AsyncSessionLocal() as db:
            try:
                sess = await db.get(SessionModel, session_id)
                if sess:
                    sess.status = "active"
                    sess.updated_at = datetime.now(timezone.utc)
                    await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def insert_admin_message(session_id: str, content: str) -> str:
        async with AsyncSessionLocal() as db:
            try:
                ts = datetime.now(timezone.utc)
                msg = MessageModel(
                    session_id=session_id,
                    role="admin",
                    content=content,
                    timestamp=ts,
                    interest=None,
                    mood=None
                )
                db.add(msg)

                # update session's updated_at if session exists
                sess = await db.get(SessionModel, session_id)
                if sess:
                    sess.updated_at = datetime.now(timezone.utc)

                await db.commit()
                # refresh to ensure id populated if needed
                await db.refresh(msg)
                return ts.isoformat()
            except Exception:
                await db.rollback()
                raise

    async def set_active_status(session_id: str):
        async with AsyncSessionLocal() as db:
            try:
                sess = await db.get(SessionModel, session_id)
                if sess:
                    sess.status = "active"
                    sess.updated_at = datetime.now(timezone.utc)
                    await db.commit()
            except Exception:
                await db.rollback()
                # swallow errors similar to original finally block behavior

    try:
        # Set session status = 'admin' (async DB op)
        try:
            await set_admin_status(session_id)
        except Exception:
            # If DB update fails, close socket (similar to original)
            await websocket.close(code=1011)
            return

        # notify clients that status changed to admin
        await manager.broadcast(json.dumps({"type": "status", "status": "admin"}), session_id)

        # connect this websocket to manager and send history
        await manager.connect(websocket, session_id)
        await manager.send_history(session_id, websocket)

        # handle incoming websocket messages
        while True:
            data = await websocket.receive_text()
            try:
                parsed_data = json.loads(data)
            except json.JSONDecodeError:
                # ignore malformed JSON (same as original)
                continue

            msg_type = parsed_data.get("type")

            if msg_type == "handover":
                # make session active again
                try:
                    await do_handover(session_id)
                    handover_msg = {"type": "handover", "content": "Handed over to bot."}
                    await manager.broadcast(json.dumps(handover_msg), session_id)
                except Exception:
                    # swallow exceptions (same behavior as original)
                    pass

            elif msg_type == "message":
                content = parsed_data.get("content", "")
                try:
                    ts = await insert_admin_message(session_id, content)
                    admin_msg = {
                        "type": "message",
                        "role": "admin",
                        "content": content,
                        "timestamp": ts,
                        "interest": None,
                        "mood": None
                    }
                    await manager.broadcast(json.dumps(admin_msg), session_id)
                except Exception:
                    # swallow DB/broadcast errors 
                    pass
    except WebSocketDisconnect:
        pass
    finally:
        # restore session status -> active 
        await set_active_status(session_id)
        await manager.broadcast(json.dumps({"type": "status", "status": "active"}), session_id)
        manager.disconnect(websocket, session_id)

class VerifyPayload(BaseModel):
    id: str
    name: str = ""
    email: str = ""
    lead_role: str = ""
    company: str = ""

@app.post("/api/verify/")
async def main_verify_user(payload: VerifyPayload, db: AsyncSession = Depends(get_db)):
    company = payload.company
    role = payload.lead_role
    username = payload.name
    email = payload.email

    result_str, sources, images = await verify_user(company, role, username, email)

    try:
        result = json.loads(result_str)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail=f"Invalid JSON returned : {result_str}")

    stmt = select(SessionModel).where(SessionModel.id == payload.id)
    db_result = await db.execute(stmt)
    db_session = db_result.scalar_one_or_none()
    if not db_session:
        raise HTTPException(status_code=404, detail="Session not found")

    db_session.verified = "true" if result.get("verified") else "false"
    if result.get("verified") and not db_session.username:
        db_session.username = result.get("details", {}).get("name", "")
    db_session.confidence = result.get("confidence")
    db_session.evidence = result.get("details", {}).get("evidence", "")
    db_session.v_sources = json.dumps(sources)
    db_session.c_images= json.dumps(images)

    await db.commit()
    await db.refresh(db_session)

    return {
        "status": "success",
        "message": "User verification details updated in session",
        "updated_data": {
            "verified": db_session.verified,
            "confidence": db_session.confidence,
            "evidence": db_session.evidence,
            "sources": db_session.v_sources
        }
    }
    
def invalidate_leads_cache():
    session_cache.clear()  
    

class SessionResponse(BaseModel):
    message: str
    id: str
    approved: bool

    class Config:
        from_attributes = True  

@app.post("/api/approve/", response_model=SessionResponse)
async def approve_session(session_id: str, db: AsyncSession = Depends(get_db)):
    stmt = select(SessionModel).filter(SessionModel.id == session_id)
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session.approved = True
    await db.commit()
    await db.refresh(session)
    invalidate_leads_cache()
    return {"message": "Session approved successfully", "id": session.id, "approved": session.approved}

@app.post("/api/leads/refresh")
async def force_refresh_cache() -> Dict[str, str]:
    session_cache.clear()
    return {"message": "Cache refreshed successfully. Next leads request will fetch fresh data."}




@app.get("/api/dashboard", response_model=dict)
async def get_dashboard(db: AsyncSession = Depends(get_db)):
    now = datetime.utcnow()
    week_start = now - timedelta(days=7)
    last_week_start = week_start - timedelta(days=7)
    last_week_end = week_start

    # Total Leads: sessions created this week
    stmt_total = select(func.count(SessionModel.id)).where(SessionModel.created_at >= week_start)
    total_leads = (await db.execute(stmt_total)).scalar()

    # High Engagement: sessions with interest == "high" this week
    stmt_high = select(func.count(SessionModel.id)).where(and_(SessionModel.created_at >= week_start, SessionModel.interest == "high"))
    high_engagement = (await db.execute(stmt_high)).scalar()

    # Active Chats: currently active sessions (no time filter)
    stmt_active = select(func.count(SessionModel.id)).where(SessionModel.status == "active")
    active_chats = (await db.execute(stmt_active)).scalar()

    # Requested Services: sessions with q4_services not null this week + % change
    stmt_req = select(func.count(SessionModel.id)).where(and_(SessionModel.created_at >= week_start, SessionModel.q4_services.isnot(None)))
    requested_services = (await db.execute(stmt_req)).scalar()
    stmt_last_req = select(func.count(SessionModel.id)).where(and_(SessionModel.created_at >= last_week_start, SessionModel.created_at < last_week_end, SessionModel.q4_services.isnot(None)))
    last_requested = (await db.execute(stmt_last_req)).scalar()
    req_change_pct = ((requested_services - last_requested) / last_requested * 100) if last_requested > 0 else 100
    requested_change = f"{math.floor(req_change_pct)}%" if req_change_pct >= 0 else f"{math.floor(req_change_pct)}%"

    # Avg Bot Response Time: average time from user message to assistant reply this week
    stmt_msgs = select(MessageModel).join(SessionModel).where(SessionModel.created_at >= week_start).order_by(MessageModel.session_id, MessageModel.timestamp)
    result_msgs = await db.execute(stmt_msgs)
    all_messages = result_msgs.scalars().all()
    session_messages = defaultdict(list)
    for msg in all_messages:
        session_messages[msg.session_id].append(msg)
    response_times = []
    for messages in session_messages.values():
        if len(messages) < 2:
            continue
        for i in range(1, len(messages)):
            if messages[i-1].role == "user" and messages[i].role == "bot":
                diff_seconds = (messages[i].timestamp - messages[i-1].timestamp).total_seconds()
                response_times.append(diff_seconds)
    avg_response_seconds = sum(response_times) / len(response_times) if response_times else 0
    if avg_response_seconds < 60:
        avg_response = f"{int(avg_response_seconds)}s"
    else:
        mins = int(avg_response_seconds // 60)
        secs = int(avg_response_seconds % 60)
        avg_response = f"{mins}m {secs}s"

    # Service Demand Pulse: top 6 services this week + % changes
    stmt_services = select(SessionModel.q4_services).where(and_(SessionModel.created_at >= week_start, SessionModel.q4_services.isnot(None)))
    this_week_services = [(row[0] or "") for row in (await db.execute(stmt_services)).fetchall()]
    service_counts = defaultdict(int)
    for serv_str in this_week_services:
        services = [s.strip() for s in serv_str.split(",") if s.strip()]
        for s in services:
            service_counts[s] += 1
    sorted_services = sorted(service_counts.items(), key=lambda x: x[1], reverse=True)[:6]

    stmt_last_services = select(SessionModel.q4_services).where(and_(SessionModel.created_at >= last_week_start, SessionModel.created_at < last_week_end, SessionModel.q4_services.isnot(None)))
    last_week_services = [(row[0] or "") for row in (await db.execute(stmt_last_services)).fetchall()]
    last_service_counts = defaultdict(int)
    for serv_str in last_week_services:
        services = [s.strip() for s in serv_str.split(",") if s.strip()]
        for s in services:
            last_service_counts[s] += 1

    service_demand = []
    for name, count in sorted_services:
        last_count = last_service_counts[name]
        change_pct = ((count - last_count) / last_count * 100) if last_count > 0 else 100
        change_str = f"+{math.floor(change_pct)}%" if change_pct >= 0 else f"{math.floor(change_pct)}%"
        service_demand.append({"name": name, "count": count, "change": change_str})
    top_service = service_demand[0] if service_demand else {"name": "N/A", "count": 0, "change": "0%"}

    # Deepest Conversations: top 7 longest sessions this week + per-user change vs last week
    subq_sessions = select(SessionModel.id).where(SessionModel.created_at >= week_start)
    stmt_durations = (
        select(
            MessageModel.session_id,
            func.max(MessageModel.timestamp).label("max_ts"),
            func.min(MessageModel.timestamp).label("min_ts")
        )
        .where(MessageModel.session_id.in_(subq_sessions))
        .group_by(MessageModel.session_id)
        .having(func.count(MessageModel.id) > 0)
    )
    duration_result = await db.execute(stmt_durations)
    duration_rows = duration_result.fetchall()
    duration_list = [(row[0], row[1], row[2]) for row in duration_rows]
    top_durations = sorted(
        [(sid, max_ts - min_ts) for sid, max_ts, min_ts in duration_list if max_ts and min_ts],
        key=lambda x: x[1],
        reverse=True
    )[:7]

    deepest_conversations = []
    for session_id, duration_td in top_durations:
        # Fetch session details
        stmt_session = select(SessionModel).where(SessionModel.id == session_id)
        session_result = await db.execute(stmt_session)
        session = session_result.scalar_one()

        # Format duration
        total_seconds = int(duration_td.total_seconds()) if duration_td else 0
        mins = total_seconds // 60
        secs = total_seconds % 60
        duration_str = f"{mins}m {secs:02d}s"

        # Per-user change vs last week
        username = session.username
        change_icon = "arrow_upward"
        change_color = "green-600"
        change_str = "0m 00s"
        if username:
            stmt_last_user_sessions = select(SessionModel.id).where(
                and_(SessionModel.username == username, SessionModel.created_at >= last_week_start, SessionModel.created_at < last_week_end)
            )
            last_session_ids_result = await db.execute(stmt_last_user_sessions)
            last_session_ids = [row[0] for row in last_session_ids_result.fetchall()]

            last_durations_seconds = []
            for last_id in last_session_ids:
                stmt_last_dur = (
                    select(
                        func.max(MessageModel.timestamp),
                        func.min(MessageModel.timestamp)
                    ).where(MessageModel.session_id == last_id)
                )
                last_dur_result = await db.execute(stmt_last_dur)
                last_dur_row = last_dur_result.fetchone()
                if last_dur_row and last_dur_row[0] and last_dur_row[1]:
                    last_dur = last_dur_row[0] - last_dur_row[1]
                    last_durations_seconds.append(last_dur.total_seconds())

            if last_durations_seconds:
                avg_last_seconds = sum(last_durations_seconds) / len(last_durations_seconds)
                change_seconds = total_seconds - avg_last_seconds
                abs_change = abs(change_seconds)
                change_mins = int(abs_change // 60)
                change_secs = int(abs_change % 60)
                change_str = f"{change_mins}m {change_secs:02d}s"
                if change_seconds < 0:
                    change_icon = "arrow_downward"
                    change_color = "red-600"

        deepest_conversations.append({
            "name": username or "Anonymous",
            "company": session.q1_company or "N/A",
            "duration": duration_str,
            "change": change_str,
            "change_icon": change_icon,
            "change_color": change_color
        })

    # Avg Conversation Time: average duration this week
    if duration_list:
        avg_duration_seconds = sum((max_ts - min_ts).total_seconds() for _, max_ts, min_ts in duration_list) / len(duration_list)
        avg_mins = int(avg_duration_seconds // 60)
        avg_secs = int(avg_duration_seconds % 60)
        avg_conversation_time = f"{avg_mins:02d}m {avg_secs:02d}s"
    else:
        avg_conversation_time = "00m 00s"

    # Hot Leads Radar: top 7 approved sessions, ordered by updated_at desc
    stmt_hot = select(SessionModel).where(SessionModel.approved == True).order_by(SessionModel.updated_at.desc()).limit(7)
    hot_results = await db.execute(stmt_hot)
    hot_sessions = hot_results.scalars().all()
    hot_leads_count = len(hot_sessions)
    hot_leads = []
    for ses in hot_sessions:
        priority = "High" if ses.interest == "high" else "Medium"
        delta = now - ses.updated_at
        if delta.days >= 2:
            time_ago = f"{delta.days} days ago"
        elif delta.days == 1:
            time_ago = "Yesterday"
        elif delta.seconds >= 3600:
            hours = delta.seconds // 3600
            time_ago = f"{hours}h ago"
        else:
            minutes = delta.seconds // 60
            time_ago = f"{minutes}m ago" if minutes > 0 else "Just now"
        service = (ses.q4_services.split(",")[0].strip() if ses.q4_services else "N/A")
        name = ses.username or ses.q1_email or "Unknown"
        hot_leads.append({
            "name": name,
            "priority": priority,
            "time": time_ago,
            "company": ses.q1_company or "N/A",
            "service": service
        })

    return {
        "total_leads": total_leads,
        "high_engagement": high_engagement,
        "active_chats": active_chats,
        "avg_response": avg_response,
        "requested_services": requested_services,
        "requested_change": requested_change,
        "service_demand": service_demand,
        "top_service": top_service,
        "deepest_conversations": deepest_conversations,
        "avg_conversation_time": avg_conversation_time,
        "hot_leads": hot_leads,
        "hot_leads_count": hot_leads_count
    }
    

@app.get("/analytics", response_model=Dict[str, Any])
async def get_analytics_optimized(
    period: str = Query("week", regex="^(week|month|year|all)$"),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    now = datetime.utcnow()

    # determine start / prev ranges
    if period == "week":
        delta = timedelta(days=7)
    elif period == "month":
        delta = relativedelta(months=1)
    elif period == "year":
        delta = relativedelta(years=1)
    else:  # all
        start_date = datetime(1970, 1, 1)
        prev_start_date = prev_end_date = None

    if period != "all":
        start_date = now - delta
        prev_delta = delta
        prev_start_date = start_date - prev_delta
        prev_end_date = start_date

    # --- Build message aggregation subquery: one row per session_id with msg_count, min_ts, max_ts ---
    msg_agg = (
        select(
            MessageModel.session_id.label("sid"),
            func.count(MessageModel.id).label("msg_count"),
            func.min(MessageModel.timestamp).label("min_ts"),
            func.max(MessageModel.timestamp).label("max_ts"),
        )
        .group_by(MessageModel.session_id)
        .subquery()
    )

    # helper to detect dialect for timestamp diff approach
    bind = db.get_bind()  # AsyncSession.get_bind() returns the engine/connection
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "") or ""

    # avg duration expression differs per dialect (Postgres vs SQLite)
    if "postgres" in dialect_name or "psycopg" in dialect_name:
        # avg seconds from interval: AVG(EXTRACT(EPOCH FROM (max_ts - min_ts)))
        duration_expr = func.avg(func.extract("epoch", msg_agg.c.max_ts - msg_agg.c.min_ts)).label("avg_sec")
    else:
        # SQLite: use julianday difference * 86400 (seconds)
        duration_expr = func.avg(
            (func.julianday(msg_agg.c.max_ts) - func.julianday(msg_agg.c.min_ts)) * 86400
        ).label("avg_sec")

    coalesce_msg_count = func.coalesce(msg_agg.c.msg_count, 0)

    # --- single aggregated query for the main period: many metrics computed by conditional SUM/CASE ---
    agg_stmt = (
        select(
            # totals
            func.count(SessionModel.id).label("total_sessions"),

            # flags / totals
            func.sum(case((SessionModel.approved == True, 1), else_=0)).label("hot_leads"),
            func.sum(case((SessionModel.c_info != None, 1), else_=0)).label("enriched_leads"),
            func.sum(case((and_(SessionModel.q1_email != None, SessionModel.mobile != None), 1), else_=0)).label("key_contacts"),

            # we approximate "company_insights" by presence of c_data (for Postgres you can test jsonb keys instead)
            func.sum(case((SessionModel.c_data != None, 1), else_=0)).label("company_insights"),

            # engagement buckets
            func.sum(case((coalesce_msg_count >= 10, 1), else_=0)).label("highly_engaged"),
            func.sum(case((and_(coalesce_msg_count >= 5, coalesce_msg_count < 10), 1), else_=0)).label("engaged"),
            func.sum(case((and_(coalesce_msg_count >= 2, coalesce_msg_count < 5), 1), else_=0)).label("neutral"),
            func.sum(case((coalesce_msg_count < 2, 1), else_=0)).label("disengaged"),

            # moods (conditional counts)
            func.sum(case((SessionModel.mood == "excited", 1), else_=0)).label("m_excited"),
            func.sum(case((SessionModel.mood == "positive", 1), else_=0)).label("m_positive"),
            func.sum(case((SessionModel.mood == "neutral", 1), else_=0)).label("m_neutral"),
            func.sum(case((SessionModel.mood == "friendly", 1), else_=0)).label("m_friendly"),
            func.sum(case((SessionModel.mood == "confused", 1), else_=0)).label("m_confused"),

            # interest
            func.sum(case((SessionModel.interest == "high", 1), else_=0)).label("interest_high"),
            func.sum(case((SessionModel.interest == "medium", 1), else_=0)).label("interest_medium"),

            # buying signals: either interest high or approved
            func.sum(case((or_(SessionModel.interest == "high", SessionModel.approved == True), 1), else_=0)).label(
                "buying_signals"
            ),

            # avg duration seconds (from message min/max)
            duration_expr,
        )
        .select_from(SessionModel)
        .outerjoin(msg_agg, SessionModel.id == msg_agg.c.sid)
        .where(SessionModel.created_at >= start_date)
    )

    main_row = (await db.execute(agg_stmt)).one_or_none()
    if main_row is None:
        # return defaults if no data
        return {
            "period": period,
            "summary": {
                "highly_engaged_users": 0,
                "highly_engaged_pct": 0,
                "ai_enriched_leads": 0,
                "buying_signals": 0,
                "avg_chat_duration": "0m 0s",
                "avg_change_pct": 0,
            },
            "engagement_quality": {},
            "sentiment_analysis": {},
            "genuine_interest_detection": {},
            "ai_research_insights": {},
        }

    # main_row is a Row object; convert to dict-like for clarity
    r = dict(main_row._mapping)

    total_sessions = int(r.get("total_sessions", 0)) or 0

    # compute percentages and bars (simple Python logic; cheap compared to DB work)
    eng_counts = {
        "highly_engaged": int(r.get("highly_engaged", 0) or 0),
        "engaged": int(r.get("engaged", 0) or 0),
        "neutral": int(r.get("neutral", 0) or 0),
        "disengaged": int(r.get("disengaged", 0) or 0),
    }
    max_eng = max(eng_counts.values()) or 1
    eng_bars = {k: int(v / max_eng * 100) for k, v in eng_counts.items()}
    highly_engaged_pct = round((eng_counts["highly_engaged"] / total_sessions * 100) if total_sessions else 0, 0)

    # moods
    mood_counts = {
        "excited": int(r.get("m_excited", 0) or 0),
        "positive": int(r.get("m_positive", 0) or 0),
        "neutral": int(r.get("m_neutral", 0) or 0),
        "friendly": int(r.get("m_friendly", 0) or 0),
        "confused": int(r.get("m_confused", 0) or 0),
    }
    total_mood = sum(mood_counts.values()) or 1
    max_sent = max(mood_counts.values()) or 1
    sent_bars = {k: int(v / max_sent * 100) for k, v in mood_counts.items()}
    sent_pcts = {k: round(v / total_mood * 100, 0) for k, v in mood_counts.items()}
    # fix rounding to sum 100
    total_pct = sum(sent_pcts.values())
    if total_pct != 100:
        max_key = max(mood_counts, key=mood_counts.get)
        sent_pcts[max_key] += 100 - total_pct

    positive_pct = round(
        (mood_counts["excited"] + mood_counts["positive"] + mood_counts["friendly"]) / total_mood * 100, 0
    )

    # interest
    high_intent = int(r.get("interest_high", 0) or 0)
    medium_intent = int(r.get("interest_medium", 0) or 0)
    max_gen = max(high_intent, medium_intent) or 1
    gen_bars = {"high_intent": int(high_intent / max_gen * 100), "medium_intent": int(medium_intent / max_gen * 100)}

    # avg duration
    avg_sec = float(r.get("avg_sec") or 0)
    mins = int(avg_sec // 60)
    secs = int(avg_sec % 60)
    avg_str = f"{mins}m {secs}s"

    # --- previous period comparison (if requested) ---
    pct_change = 0
    if period != "all" and prev_start_date and prev_end_date:
        last_stmt = agg_stmt.where(
            and_(SessionModel.created_at >= prev_start_date, SessionModel.created_at < prev_end_date)
        )
        last_row = (await db.execute(last_stmt)).one_or_none()
        last_avg_sec = float(last_row._mapping.get("avg_sec") or 0) if last_row else 0
        pct_change = round(((avg_sec - last_avg_sec) / last_avg_sec * 100) if last_avg_sec > 0 else 0, 0)

    return {
        "period": period,
        "summary": {
            "highly_engaged_users": eng_counts["highly_engaged"],
            "highly_engaged_pct": highly_engaged_pct,
            "ai_enriched_leads": int(r.get("enriched_leads", 0) or 0),
            "buying_signals": int(r.get("buying_signals", 0) or 0),
            "avg_chat_duration": avg_str,
            "avg_change_pct": pct_change,
        },
        "engagement_quality": {
            "highly_engaged": {"count": eng_counts["highly_engaged"], "bar_pct": eng_bars["highly_engaged"]},
            "engaged": {"count": eng_counts["engaged"], "bar_pct": eng_bars["engaged"]},
            "neutral": {"count": eng_counts["neutral"], "bar_pct": eng_bars["neutral"]},
            "disengaged": {"count": eng_counts["disengaged"], "bar_pct": eng_bars["disengaged"]},
        },
        "sentiment_analysis": {
            "excited": {"count": mood_counts["excited"], "pct": sent_pcts["excited"], "bar_pct": sent_bars["excited"]},
            "positive": {"count": mood_counts["positive"], "pct": sent_pcts["positive"], "bar_pct": sent_bars["positive"]},
            "neutral": {"count": mood_counts["neutral"], "pct": sent_pcts["neutral"], "bar_pct": sent_bars["neutral"]},
            "friendly": {"count": mood_counts["friendly"], "pct": sent_pcts["friendly"], "bar_pct": sent_bars["friendly"]},
            "confused": {"count": mood_counts["confused"], "pct": sent_pcts["confused"], "bar_pct": sent_bars["confused"]},
            "positive_pct": positive_pct,
        },
        "genuine_interest_detection": {
            "high_intent": {"count": high_intent, "bar_pct": gen_bars["high_intent"]},
            "medium_intent": {"count": medium_intent, "bar_pct": gen_bars["medium_intent"]},
        },
        "ai_research_insights": {
            "total_enriched": int(r.get("enriched_leads", 0) or 0),
            "company_insights": int(r.get("company_insights", 0) or 0),
            "decision_makers": int(r.get("key_contacts", 0) or 0),
        },
    }

def format_last_synced(last_synced_str):
    if not last_synced_str:
        return "Never"
    try:
        # Assume ISO format, adjust if needed
        dt = datetime.fromisoformat(last_synced_str.replace('Z', '+00:00'))
        now = datetime.utcnow()  # Use UTC for consistency
        delta = now - dt
        if delta.total_seconds() < 60:
            return "Just now"
        elif delta.total_seconds() < 3600:
            mins = int(delta.total_seconds() / 60)
            return f"{mins}m ago"
        elif delta.days < 1:
            hours = int(delta.total_seconds() / 3600)
            return f"{hours}h ago"
        elif delta.days < 7:
            return f"{delta.days}d ago"
        else:
            return dt.strftime("%b %d, %Y")
    except ValueError:
        return last_synced_str 

DEFAULT_DATA_FOLDER = Path("./data")
DEFAULT_DATA_FOLDER.mkdir(exist_ok=True)

@dataclass
class FileInfo:
    name: str
    type: str  # Extension
    size: str  # Formatted
    size_bytes: int
    status: str = "Indexed"

def calculate_sources_and_storage(folder: Path) -> Dict:
    if not folder.exists() or not folder.is_dir():
        raise ValueError(f"{folder} is not a valid folder path")
    
    def sizeof_fmt(num: int, suffix="B") -> str:
        for unit in ["", "K", "M", "G", "T"]:
            if abs(num) < 1024.0:
                return f"{num:.1f}{unit}{suffix}"
            num /= 1024.0
        return f"{num:.1f}P{suffix}"
    
    total_size = 0
    file_count = 0
    files: List[FileInfo] = []
    
    for file_path in folder.rglob('*'):
        if file_path.is_file():
            file_count += 1
            size_bytes = file_path.stat().st_size
            total_size += size_bytes
            
            ext = file_path.suffix[1:].upper() if file_path.suffix else "Unknown"  # e.g., "PDF"
            formatted_size = sizeof_fmt(size_bytes)
            
            files.append(FileInfo(
                name=file_path.name,
                type=ext,
                size=formatted_size,
                size_bytes=size_bytes
            ))
    
    return {
        "file_count": file_count,
        "total_size_bytes": total_size,
        "total_size_readable": sizeof_fmt(total_size),
        "files": [f.__dict__ for f in sorted(files, key=lambda f: f.name)]  # Sort by name
    }

# Existing format_last_synced remains unchanged

class InsightUpdate(BaseModel):
    # Existing fields unchanged
    guidelines: str = ""
    tones: str = ""
    name: str = ""
    banned: str = ""
    company_profile: str = ""
    main_categories: str = ""
    sub_services: str = ""
    timeline_options: str = ""
    budget_options: str = ""

@app.get("/insight")
def get_insight():
    stats = calculate_sources_and_storage(DEFAULT_DATA_FOLDER)
    last_synced = cfg.get("last_synced", "")
    return {
        "guidelines": cfg.get("guidelines", ""),
        "tones": cfg.get("tones", ""),
        "name": cfg.get("name", ""),
        "banned": cfg.get("banned", ""),
        "company_profile": cfg.get("company_profile", ""),
        "main_categories": cfg.get("main_categories", ""),
        "sub_services": cfg.get("sub_services", ""),
        "timeline_options": cfg.get("timeline_options", ""),
        "budget_options": cfg.get("budget_options", ""),
        "last_synced": last_synced,
        "num_sources": stats['file_count'],
        "storage_used": stats['total_size_readable'],
        "last_sync_display": format_last_synced(last_synced),
        "files": stats['files']  # New: list of file details
    }

@app.post("/insight")
def update_insight(update: InsightUpdate):
    # Existing unchanged
    try:
        cfg.update(
            guidelines=update.guidelines,
            tones=update.tones,
            name=update.name,
            banned=update.banned,
            company_profile=update.company_profile,
            main_categories=update.main_categories,
            sub_services=update.sub_services,
            timeline_options=update.timeline_options,
            budget_options=update.budget_options,
            last_synced=datetime.utcnow().isoformat() + 'Z'
        )
        reload_system_prompt()
        return {"status": "updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Update failed: {e}")

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    if not file.content_type or not file.content_type.startswith(('application/pdf', 'text/', 'text/csv')):
        raise HTTPException(status_code=400, detail="Unsupported file type. Only PDFs, Markdown, CSV, or plain text allowed.")
    
    # Sanitize filename
    safe_filename = "".join(c for c in file.filename if c.isalnum() or c in ('.', '_', '-')).rstrip('.')
    if not safe_filename:
        raise HTTPException(status_code=400, detail="Invalid filename.")
    
    filepath = DEFAULT_DATA_FOLDER / safe_filename
    if filepath.exists():
        raise HTTPException(status_code=409, detail="File already exists.")
    
    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)
    
    # Update last_synced
    cfg.update(last_synced=datetime.utcnow().isoformat() + 'Z')
    
    return {"status": "uploaded successfully", "filename": safe_filename}

@app.delete("/files/{filename}")
def delete_file(filename: str):
    filepath = DEFAULT_DATA_FOLDER / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    
    filepath.unlink()
    
    # Optionally update last_synced; skipping for delete to avoid unnecessary sync triggers
    return {"status": "deleted successfully", "filename": filename}

@app.get("/files/{filename}")
async def download_file(filename: str):
    filepath = DEFAULT_DATA_FOLDER / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    
    # Set content-type
    mime_type, _ = mimetypes.guess_type(filepath)
    return FileResponse(
        path=filepath,
        media_type=mime_type or "application/octet-stream",
        filename=filename
    )