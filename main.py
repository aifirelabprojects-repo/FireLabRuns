from io import StringIO
import math
import mimetypes
import os
from pathlib import Path
import shutil
import uuid
import json
import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set,AsyncGenerator, Tuple,Union
from fastapi import Depends, FastAPI, File, Request, Response, UploadFile, WebSocket, WebSocketDisconnect, Query, HTTPException,status, Form
from sqlalchemy.orm import selectinload,joinedload
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from openai import OpenAI, APIError, AuthenticationError, RateLimitError
from dotenv import load_dotenv
from sqlalchemy import delete, func, or_, select,case,and_,outerjoin
from sqlalchemy.ext.asyncio import AsyncSession
from BotGraph import invoke_chat_async,reload_system_prompt
from DeepResearch import  _call_research_async
from SessionUtils import get_field, set_field
from VerifyUser import verify_user
from database import AsyncSessionLocal, CompanyDetails, Consultant, Consultation, Project, ProjectTask, ResearchDetails, ServiceTemplate, Session as SessionModel, Message as MessageModel, SessionPhase, TaskFile, TemplateTask, VerificationDetails, get_db, init_db 
from collections import Counter, defaultdict
from CompanyFinder import FindTheComp
from FindUser import find_existing_customer
from pydantic import BaseModel, Field
import csv
from fastapi.responses import FileResponse, StreamingResponse
from functools import lru_cache,partial
from cachetools import TTLCache  
from dateutil.relativedelta import relativedelta
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy.sql import Select
from KnowledgeBase import cfg
from dataclasses import dataclass
from ClientModel import client


load_dotenv()

_MAIN_CATEGORIES = cfg.get("main_categories", "")
_SUB_SERVICES = cfg.get("sub_services", "")
_TIMELINE_OPTIONS = cfg.get("timeline_options", "")
_BUDGET_OPTIONS = cfg.get("budget_options", "")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")

MODEL_NAME = "gpt-4o-mini"
SITE_NAME = "Business Chatbot"
INACTIVITY_THRESHOLD = timedelta(minutes=5)  

session_cache = TTLCache(maxsize=1000, ttl=300)



os.makedirs("data", exist_ok=True)
app = FastAPI(title="Business Chatbot API")

@app.api_route("/health", methods=["GET", "HEAD"])
async def health_check():
    return {"status": "ok"}

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")



@app.on_event("startup")
async def startup_event():
    await init_db()

@app.on_event("shutdown")
def shutdown_event():
    cfg.stop()


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


async def get_bot_response_async(question: str, session_obj, session_id: str = "transient") -> Dict[str, Any]:
    
    phase = get_field(session_obj, "phase") or "initial"
    haveRole = get_field(session_obj, "q2_role") or None
    routing = get_field(session_obj, "routing") or None

    customer_enrichment = None
    fetch_trigger_phases = ["existing_fetch", "snip_q0"]

    if phase in fetch_trigger_phases and get_field(session_obj, "q1_company") is None:
        customer_enrichment, kind = await find_existing_customer(question)  # await instead of asyncio.run
        print(customer_enrichment)
        if customer_enrichment:
            # set enriched fields (will set on child relation if available, else on session_obj)
            set_field(session_obj, "q1_email", customer_enrichment.get("email"))
            set_field(session_obj, "q1_company", customer_enrichment.get("company"))
            set_field(session_obj, "q2_role", customer_enrichment.get("role"))
            set_field(session_obj, "q3_categories", customer_enrichment.get("categories"))
            set_field(session_obj, "q4_services", customer_enrichment.get("services"))
            set_field(session_obj, "q5_activity", customer_enrichment.get("activity"))
            set_field(session_obj, "q6_timeline", customer_enrichment.get("timeline"))
            set_field(session_obj, "q7_budget", customer_enrichment.get("budget"))
            set_field(session_obj, "username", customer_enrichment.get("username"))
            set_field(session_obj, "mobile", customer_enrichment.get("mobile"))
            set_field(session_obj, "routing", "cre")
            set_field(session_obj, "phase", "routing")
            routing = "cre"
        else:
            if phase == "initial":
                phase = "existing_fetch"
                set_field(session_obj, "phase", "existing_fetch")

    enrichment = None
    company_det_used = False
    company_det = get_field(session_obj, "c_info")
    if company_det is None and phase in ("snip_q1", "snip_q2", "snip_q2a"):
        # keep creating the background task as before
        asyncio.create_task(FindTheComp(question, get_field(session_obj, "id") or getattr(session_obj, "id", None)))

    lead_data = json.dumps({
        "name": get_field(session_obj, "username"),
        "phone": get_field(session_obj, "mobile"),
        "phase": get_field(session_obj, "phase"),
        "routing": get_field(session_obj, "routing"),
        "lead_company": get_field(session_obj, "q1_company"),
        "lead_email": get_field(session_obj, "q1_email"),
        "lead_email_domain": get_field(session_obj, "q1_email_domain"),
        "lead_role": get_field(session_obj, "q2_role"),
        "lead_categories": get_field(session_obj, "q3_categories"),
        "lead_services": get_field(session_obj, "q4_services"),
        "lead_activity": get_field(session_obj, "q5_activity"),
        "lead_timeline": get_field(session_obj, "q6_timeline"),
        "lead_budget": get_field(session_obj, "q7_budget")
    })

    options = []
    context_parts = []

    print(f"q3_categories: {get_field(session_obj, 'q3_categories')}, type: {type(get_field(session_obj, 'q3_categories'))}")
    print(f"Condition result: {(phase == 'snip_q3' or phase == 'snip_q2') and not get_field(session_obj, 'q3_categories')}")

    try:
        if phase == "snip_q2a" and not get_field(session_obj, "q2_role"):
            cat_context = "Customer Business role is not provided yet, ask it and set phase to snip_q3"
            context_parts.append(cat_context)
        elif (phase == "snip_q2" or phase == "snip_q2a") and (get_field(session_obj, "q3_categories") is None or get_field(session_obj, "q3_categories") == []):
            print(f"Listing Main: {get_field(session_obj, 'q3_categories')}")
            cat_context = f"Use This as Options for Main Categories: {str(_MAIN_CATEGORIES)}"
            set_field(session_obj, "phase", "snip_q4")
            context_parts.append(cat_context)
        elif (phase == "snip_q3") and not get_field(session_obj, "q4_services"):
            print(f"Listing Sub Serv: {get_field(session_obj, 'q3_categories')}")
            main_cats = f"Use This as Options for SUB SERVICES based on the Main category user selected: {str(_SUB_SERVICES)}"
            context_parts.append(main_cats)
        elif get_field(session_obj, "q3_categories") and (phase == "snip_q5" or phase == "snip_q6"):
            print("Listing Time and Budget")
            context_parts.append(f"Timeline info: str(_TIMELINE_OPTIONS)")
            context_parts.append(f"Budget info: {_BUDGET_OPTIONS}")

    except Exception:
        pass

    context = "\n".join(context_parts)
    print("\n\nphase:", phase, "\n")
    EnrData = (
        f"Existing Customer Data (if applicable):Welcome the user with thier data {json.dumps(lead_data)} "
        f"if the user is exisitng set phase='routing' and routing='cre' "
    ) if routing == "cre" else " "
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
            # Eager-load ALL relationships to block lazy loads
            session_obj = await db.get(
                SessionModel,
                session_id,
                options=[
                    selectinload(SessionModel.phase_info),
                    selectinload(SessionModel.company_details),
                    selectinload(SessionModel.verification_details),
                    selectinload(SessionModel.research_details),
                ]
            )
            if not session_obj:
                raise ValueError(f"Session {session_id} not found")
            
            if session_obj.phase_info is None:
                session_obj.phase_info = SessionPhase(session_id=session_id)
            
            if session_obj.company_details is None:
                session_obj.company_details = CompanyDetails(session_id=session_id)

            if session_obj.verification_details is None:
                session_obj.verification_details = VerificationDetails(session_id=session_id)
                
            if session_obj.research_details is None:
                session_obj.research_details = ResearchDetails(session_id=session_id)
                

            # Use async bot response
            response_data = await get_bot_response_async(question, session_obj, session_id)
            # Extract main response components
            answer = response_data.get("answer", "")
            options = response_data.get("options", [])
            next_phase = response_data.get("phase", get_field(session_obj, "phase"))
            lead_data = response_data.get("lead_data", {}) or {}
            routing = response_data.get("routing", get_field(session_obj, "routing"))
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
                    # convert list -> comma-separated string
                    if isinstance(val, list):
                        val = ", ".join(str(v).strip() for v in val if str(v).strip())
                    # skip None or empty/whitespace values
                    if val is None or (isinstance(val, str) and val.strip() == ""):
                        continue
                    try:
                        set_field(session_obj, field, str(val).strip())
                    except Exception:
                        pass

            session_obj.interest = interest
            session_obj.mood = mood
            set_field(session_obj, "phase", next_phase)
            if routing is not None:
                set_field(session_obj, "routing", routing)
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
async def create_session():
    async with AsyncSessionLocal() as db: 
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

        # Base select with eager loads to prevent lazy async IO
        base_query = select(SessionModel).options(
            selectinload(SessionModel.phase_info),
            selectinload(SessionModel.company_details),
            selectinload(SessionModel.verification_details),
            selectinload(SessionModel.research_details),
            # DO NOT selectinload messages here because you load them in a batch below
        )

        if active:
            base_query = base_query.filter(SessionModel.status == "active")

        # Count total
        total_stmt = select(func.count(SessionModel.id))
        if active:
            total_stmt = total_stmt.filter(SessionModel.status == "active")
        total_result = await db.execute(total_stmt)
        total = total_result.scalar() or 0

        if total == 0:
            return {
                "sessions": [],
                "pagination": {"page": page, "per_page": per_page, "total": 0, "pages": 0}
            }

        offset = (page - 1) * per_page
        session_stmt = base_query.order_by(SessionModel.created_at.desc()).offset(offset).limit(per_page)
        session_result = await db.execute(session_stmt)
        sessions = session_result.scalars().all()

        # If there are no sessions, early return
        if not sessions:
            pages = math.ceil(total / per_page)
            return {"sessions": [], "pagination": {"page": page, "per_page": per_page, "total": total, "pages": pages}}

        # Batch-load messages for those sessions (already async)
        session_ids = [s.id for s in sessions]
        msg_stmt = (
            select(MessageModel)
            .where(MessageModel.session_id.in_(session_ids))
            .order_by(MessageModel.session_id, MessageModel.timestamp.asc())
        )
        msg_result = await db.execute(msg_stmt)
        msg_rows_all = msg_result.scalars().all()

        # Group messages by session_id (no further DB I/O)
        messages_by_session: Dict[str, List[MessageModel]] = defaultdict(list)
        for m in msg_rows_all:
            messages_by_session[m.session_id].append(m)

        # Precompute half-life constants
        half_life_seconds = 3 * 24 * 3600
        ln2 = math.log(2)

        sessions_list = []
        for sess in sessions:
            # All related attributes (phase_info, verification_details, etc.) are eager-loaded,
            # so accessing them will not trigger DB IO.
            msg_rows = messages_by_session.get(sess.id, [])

            last_msg_obj = msg_rows[-1] if msg_rows else None
            last_msg = ""
            if last_msg_obj:
                content = last_msg_obj.content or ""
                last_msg = (content[:50] + "...") if len(content) > 50 else content

            overall_interest_label = sess.interest or "low"
            overall_mood_label = sess.mood or "neutral"

            if msg_rows:
                parsed = []
                for msg in msg_rows:
                    ts_dt = msg.timestamp or datetime.utcnow()
                    parsed.append(
                        (
                            (msg.role or "").lower(),
                            (msg.interest or "").lower(),
                            (msg.mood or "").lower(),
                            ts_dt,
                        )
                    )

                latest_ts = max((ts for _, _, _, ts in parsed if ts), default=datetime.utcnow())

                weighted_sum = 0.0
                weight_total = 0.0
                mood_weights = {}

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

                    # you had role == "bot" in original; likely you want user messages counted,
                    # fix if needed — keep same behavior:
                    if role == "bot":
                        if interest_val in user_interest_counts:
                            user_interest_counts[interest_val] += 1
                        user_msg_count += 1

                        if not last_user_ts or ts_dt > last_user_ts:
                            last_user_ts = ts_dt
                            last_user_interest = interest_val

                        if mood_val:
                            mood_weights[mood_val] = mood_weights.get(mood_val, 0.0) + weight

                forced_label = None
                if user_msg_count > 0:
                    low_prop = user_interest_counts.get("low", 0) / user_msg_count
                    high_prop = user_interest_counts.get("high", 0) / user_msg_count

                    LOW_DOMINANCE_THRESHOLD = 0.5
                    HIGH_DOMINANCE_THRESHOLD = 0.66

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

            # Helper to safely pull values from eager-loaded related objects
            def rel_get(obj, attr_name, fallback=None):
                try:
                    return getattr(obj, attr_name) if obj is not None else fallback
                except Exception:
                    return fallback

            sessions_list.append({
                "id": sess.id,
                "created_at": sess.created_at,
                "status": sess.status,
                "verified": rel_get(sess.verification_details, "verified", None),
                "confidence": rel_get(sess.verification_details, "confidence", None),
                "evidence": rel_get(sess.verification_details, "evidence", None),
                "sources": rel_get(sess.verification_details, "v_sources", None),
                "research_data": rel_get(sess.research_details, "research_data", None),
                "interest": overall_interest_label,
                "mood": overall_mood_label,
                "name": sess.username,
                "usr_phone": sess.mobile,
                "phase": rel_get(sess.phase_info, "phase", None),
                "routing": rel_get(sess.phase_info, "routing", None),
                "last_message": last_msg,
                "lead_company": rel_get(sess.phase_info, "q1_company", None),
                "lead_email": rel_get(sess.phase_info, "q1_email", None),
                "lead_email_domain": rel_get(sess.phase_info, "q1_email_domain", None),
                "lead_role": rel_get(sess.phase_info, "q2_role", None),
                "lead_categories": rel_get(sess.phase_info, "q3_categories", None),
                "lead_services": rel_get(sess.phase_info, "q4_services", None),
                "lead_activity": rel_get(sess.phase_info, "q5_activity", None),
                "lead_timeline": rel_get(sess.phase_info, "q6_timeline", None),
                "lead_budget": rel_get(sess.phase_info, "q7_budget", None),
                "c_sources": rel_get(sess.company_details, "c_sources", None),
                "c_info": rel_get(sess.company_details, "c_info", None),
                "c_data": rel_get(sess.company_details, "c_data", None),
                "c_images": rel_get(sess.company_details, "c_images", None),
                "approved": sess.approved,
            })

        pages = math.ceil(total / per_page)

        return {
            "sessions": sessions_list,
            "pagination": {"page": page, "per_page": per_page, "total": total, "pages": pages}
        }

    except Exception as e:
        print("Error in get_sessions:", e)
        return {
            "sessions": [],
            "pagination": {"page": page, "per_page": per_page, "total": 0, "pages": 0}
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

        "verified": get_field(sess, "verified"),
        "confidence": get_field(sess, "confidence"),
        "evidence": get_field(sess, "evidence"),
        "sources": get_field(sess, "v_sources"),

        "interest": overall_interest_label,
        "mood": overall_mood_label,

        "name": get_field(sess, "username"),
        "usr_phone": get_field(sess, "mobile"),
        "phase": get_field(sess, "phase"),
        "routing": get_field(sess, "routing"),

        "last_message": last_msg,
        "lead_company": get_field(sess, "q1_company") or "-",
        "lead_email": get_field(sess, "q1_email"),
        "lead_email_domain": get_field(sess, "q1_email_domain"),
        "lead_role": get_field(sess, "q2_role"),
        "lead_categories": get_field(sess, "q3_categories"),
        "lead_services": get_field(sess, "q4_services") or "-",
        "lead_activity": get_field(sess, "q5_activity"),
        "lead_timeline": get_field(sess, "q6_timeline"),
        "lead_budget": get_field(sess, "q7_budget"),
        "c_sources": get_field(sess, "c_sources"),
        "c_info": get_field(sess, "c_info"),
        "c_data": get_field(sess, "c_data"),
        "c_images": get_field(sess, "c_images"),

        "approved": sess.approved,
        "date_str": date_str,
        "research_data": get_field(sess, "research_data"),
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
        if interest in [None, "", "all", "neutral"]:
            interest = None

        # Simple cache key
        cache_key = f"{q}_{interest}_{approved}_{active}_{page}_{per_page}"
        if 'session_cache' in globals() and cache_key in session_cache:
            cached = session_cache[cache_key]
            if format == "csv":
                pass
            return cached

        await update_inactive_sessions()

        base_stmt = (
            select(SessionModel)
            .options(
                selectinload(SessionModel.phase_info),
                selectinload(SessionModel.company_details),
                selectinload(SessionModel.verification_details),
                selectinload(SessionModel.research_details),
            )
        )

        if active:
            base_stmt = base_stmt.where(SessionModel.status == "active")
        if approved:
            base_stmt = base_stmt.where(SessionModel.approved.is_(True))

        if q:
            search_term = f"%{q}%"
            phase_join = outerjoin(SessionModel, SessionPhase, SessionPhase.session_id == SessionModel.id)
            base_stmt = base_stmt.select_from(phase_join).where(
                or_(
                    SessionModel.username.ilike(search_term),
                    SessionPhase.q1_email.ilike(search_term),
                    SessionPhase.q1_company.ilike(search_term),
                )
            )

        if interest:
            base_stmt = base_stmt.where(SessionModel.interest == interest.lower())

        # stream minimal columns directly from DB (no heavy compute)
        if export_all and format == "csv":
            # select minimal columns including phase fields (q1_email, q1_company, q4_services)
            export_stmt = (
                select(
                    SessionModel.username,
                    SessionPhase.q1_email,
                    SessionPhase.q1_company,
                    SessionPhase.q4_services,
                    SessionModel.interest,
                    SessionModel.created_at
                )
                .select_from(outerjoin(SessionModel, SessionPhase, SessionPhase.session_id == SessionModel.id))
                .order_by(SessionModel.created_at.desc())
                .limit(10000)
            )
            return StreamingResponse(
                _generate_csv_stream_minimal(db, export_stmt),
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=leads.csv"}
            )

        # Paginated compute & response
        # _fetch_and_compute_sessions should accept a select() statement and handle pagination.
        sessions_list, total, pages = await _fetch_and_compute_sessions(db, base_stmt, page, per_page)
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
                    "Score": s["interest"].capitalize() if s.get("interest") else "",
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
    stmt = (
        select(SessionModel)
        .options(
            selectinload(SessionModel.phase_info),
            selectinload(SessionModel.company_details),
            selectinload(SessionModel.verification_details),
            selectinload(SessionModel.research_details),
        )
        .where(SessionModel.id == payload.id)
    )
    db_result = await db.execute(stmt)
    db_session = db_result.scalar_one_or_none()
    if not db_session:
        raise HTTPException(status_code=404, detail="Session not found")

    if getattr(db_session, "verification_details", None) is None:
        vd = VerificationDetails(session_id=db_session.id)
        db_session.verification_details = vd

    if getattr(db_session, "company_details", None) is None:
        cd = CompanyDetails(session_id=db_session.id)
        db_session.company_details = cd

    set_field(db_session, "verified", "true" if result.get("verified") else "false")

    existing_username = get_field(db_session, "username")
    if result.get("verified") and not existing_username:
        set_field(db_session, "username", result.get("details", {}).get("name", ""))

    set_field(db_session, "confidence", result.get("confidence"))
    set_field(db_session, "evidence", result.get("details", {}).get("evidence", ""))

    set_field(db_session, "v_sources", json.dumps(sources))

    set_field(db_session, "c_images", json.dumps(images))

    # persist
    await db.commit()
    await db.refresh(db_session)

    # read values back using get_field (works for old or new layout)
    updated_verified = get_field(db_session, "verified")
    updated_confidence = get_field(db_session, "confidence")
    updated_evidence = get_field(db_session, "evidence")
    updated_sources = get_field(db_session, "v_sources")

    return {
        "status": "success",
        "message": "User verification details updated in session",
        "updated_data": {
            "verified": updated_verified,
            "confidence": updated_confidence,
            "evidence": updated_evidence,
            "sources": updated_sources,
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

    # total Leads: sessions created this week
    stmt_total = select(func.count(SessionModel.id)).where(SessionModel.created_at >= week_start)
    total_leads = (await db.execute(stmt_total)).scalar() or 0

    # high Engagement: sessions with interest == "high" this week
    stmt_high = select(func.count(SessionModel.id)).where(
        and_(SessionModel.created_at >= week_start, SessionModel.interest == "high")
    )
    high_engagement = (await db.execute(stmt_high)).scalar() or 0

    stmt_active = select(func.count(SessionModel.id)).where(SessionModel.status == "active")
    active_chats = (await db.execute(stmt_active)).scalar() or 0

    # requested Services: sessions with q4_services not null this week + % change

    stmt_req = (
        select(func.count(SessionModel.id))
        .join(SessionPhase, SessionPhase.session_id == SessionModel.id)
        .where(and_(SessionModel.created_at >= week_start, SessionPhase.q4_services.isnot(None)))
    )
    requested_services = (await db.execute(stmt_req)).scalar() or 0

    stmt_last_req = (
        select(func.count(SessionModel.id))
        .join(SessionPhase, SessionPhase.session_id == SessionModel.id)
        .where(
            and_(
                SessionModel.created_at >= last_week_start,
                SessionModel.created_at < last_week_end,
                SessionPhase.q4_services.isnot(None),
            )
        )
    )
    last_requested = (await db.execute(stmt_last_req)).scalar() or 0

    req_change_pct = ((requested_services - last_requested) / last_requested * 100) if last_requested > 0 else 100
    requested_change = f"{math.floor(req_change_pct)}%" if req_change_pct >= 0 else f"{math.floor(req_change_pct)}%"

    # avg Bot Response Time: average time from user message to assistant reply this week
    stmt_msgs = (
        select(MessageModel)
        .join(SessionModel, SessionModel.id == MessageModel.session_id)
        .where(SessionModel.created_at >= week_start)
        .order_by(MessageModel.session_id, MessageModel.timestamp)
    )
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
            if messages[i - 1].role == "user" and messages[i].role == "bot":
                diff_seconds = (messages[i].timestamp - messages[i - 1].timestamp).total_seconds()
                response_times.append(diff_seconds)

    avg_response_seconds = sum(response_times) / len(response_times) if response_times else 0
    if avg_response_seconds < 60:
        avg_response = f"{int(avg_response_seconds)}s"
    else:
        mins = int(avg_response_seconds // 60)
        secs = int(avg_response_seconds % 60)
        avg_response = f"{mins}m {secs}s"

    # top 6 services this week + % changes
    stmt_services = (
        select(SessionPhase.q4_services)
        .join(SessionModel, SessionModel.id == SessionPhase.session_id)
        .where(and_(SessionModel.created_at >= week_start, SessionPhase.q4_services.isnot(None)))
    )
    this_week_services = [(row[0] or "") for row in (await db.execute(stmt_services)).fetchall()]
    service_counts = defaultdict(int)
    for serv_str in this_week_services:
        services = [s.strip() for s in serv_str.split(",") if s.strip()]
        for s in services:
            service_counts[s] += 1
    sorted_services = sorted(service_counts.items(), key=lambda x: x[1], reverse=True)[:6]

    stmt_last_services = (
        select(SessionPhase.q4_services)
        .join(SessionModel, SessionModel.id == SessionPhase.session_id)
        .where(
            and_(
                SessionModel.created_at >= last_week_start,
                SessionModel.created_at < last_week_end,
                SessionPhase.q4_services.isnot(None),
            )
        )
    )
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


    subq_sessions = select(SessionModel.id).where(SessionModel.created_at >= week_start)
    stmt_durations = (
        select(
            MessageModel.session_id,
            SessionModel.username,
            SessionPhase.q1_company,
            func.max(MessageModel.timestamp).label("max_ts"),
            func.min(MessageModel.timestamp).label("min_ts"),
        )
        .join(SessionModel, SessionModel.id == MessageModel.session_id)
        .join(SessionPhase, SessionPhase.session_id == SessionModel.id, isouter=True)
        .where(MessageModel.session_id.in_(subq_sessions))
        .group_by(MessageModel.session_id, SessionModel.username, SessionPhase.q1_company)
        .having(func.count(MessageModel.id) > 0)
    )
    duration_result = await db.execute(stmt_durations)
    duration_rows = duration_result.fetchall()
    duration_list = [(row[0], row[1], row[2], row[3], row[4]) for row in duration_rows]

    # Compute this week's overall average duration
    if duration_list:
        this_week_durations_seconds = [
            (max_ts - min_ts).total_seconds() for _, _, _, max_ts, min_ts in duration_list if max_ts and min_ts
        ]
        avg_this_week_seconds = sum(this_week_durations_seconds) / len(this_week_durations_seconds)
        avg_mins = int(avg_this_week_seconds // 60)
        avg_secs = int(avg_this_week_seconds % 60)
        avg_conversation_time = f"{avg_mins:02d}m {avg_secs:02d}s"
    else:
        avg_this_week_seconds = 0
        avg_conversation_time = "00m 00s"

    # Prepare last-week user averages only if needed
    compare_to_last_week = False
    user_avg_seconds = {}
    if compare_to_last_week:
        subq_last_sessions = select(SessionModel.id).where(
            and_(SessionModel.created_at >= last_week_start, SessionModel.created_at < last_week_end)
        )
        stmt_last_durations = (
            select(
                SessionModel.username,
                MessageModel.session_id,
                func.max(MessageModel.timestamp).label("max_ts"),
                func.min(MessageModel.timestamp).label("min_ts"),
            )
            .join(MessageModel, MessageModel.session_id == SessionModel.id)
            .where(SessionModel.id.in_(subq_last_sessions))
            .group_by(MessageModel.session_id, SessionModel.username)
            .having(func.count(MessageModel.id) > 0)
        )
        last_duration_result = await db.execute(stmt_last_durations)
        last_duration_rows = last_duration_result.fetchall()

        user_last_durations = defaultdict(list)
        for username, sid, max_ts, min_ts in last_duration_rows:
            if username and max_ts and min_ts:
                td = max_ts - min_ts
                user_last_durations[username].append(td.total_seconds())

        user_avg_seconds = {user: sum(durs) / len(durs) for user, durs in user_last_durations.items()}

    # top 7 durations
    top_durations = sorted(
        [
            (sid, username, company, (max_ts - min_ts))
            for sid, username, company, max_ts, min_ts in duration_list
            if max_ts and min_ts
        ],
        key=lambda x: x[3],
        reverse=True,
    )[:7]

    deepest_conversations = []
    for session_id, username, company, duration_td in top_durations:
        total_seconds = int(duration_td.total_seconds()) if duration_td else 0
        mins = total_seconds // 60
        secs = total_seconds % 60
        duration_str = f"{mins}m {secs:02d}s"

        # Determine comparison average
        if compare_to_last_week:
            avg_seconds = user_avg_seconds.get(username, 0)
        else:
            avg_seconds = avg_this_week_seconds

        change_seconds = total_seconds - avg_seconds
        abs_change = abs(change_seconds)
        change_mins = int(abs_change // 60)
        change_secs = int(abs_change % 60)
        change_str = f"{change_mins}m {change_secs:02d}s"

        change_icon = "arrow_upward"
        change_color = "text-green-600"
        if change_seconds < 0:
            change_icon = "arrow_downward"
            change_color = "text-red-600"

        deepest_conversations.append(
            {
                "name": username or "Anonymous",
                "company": company or "N/A",
                "duration": duration_str,
                "change": change_str,
                "change_icon": change_icon,
                "change_color": change_color,
            }
        )

    stmt_hot = (
        select(SessionModel)
        .where(SessionModel.approved == True)
        .options(
            selectinload(SessionModel.phase_info),
            selectinload(SessionModel.company_details),
            selectinload(SessionModel.verification_details),
            selectinload(SessionModel.research_details),
        )
        .order_by(SessionModel.updated_at.desc())
        .limit(7)
    )
    hot_results = await db.execute(stmt_hot)
    hot_sessions = hot_results.scalars().all()
    hot_leads_count = len(hot_sessions)
    hot_leads = []
    for ses in hot_sessions:
        priority = "High" if (get_field(ses, "interest") == "high") else "Medium"
        delta = now - get_field(ses, "updated_at") if get_field(ses, "updated_at") else timedelta(0)
        if isinstance(delta, timedelta):
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
        else:
            time_ago = "Unknown"

        q4 = get_field(ses, "q4_services")
        service = (q4.split(",")[0].strip() if q4 else "N/A")
        name = get_field(ses, "username") or get_field(ses, "q1_email") or "Unknown"
        hot_leads.append(
            {
                "name": name,
                "priority": priority,
                "time": time_ago,
                "company": get_field(ses, "q1_company") or "N/A",
                "service": service,
            }
        )

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
        "hot_leads_count": hot_leads_count,
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
    bind = db.get_bind()  
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "") or ""

    if "postgres" in dialect_name or "psycopg" in dialect_name:
        duration_expr = func.avg(func.extract("epoch", msg_agg.c.max_ts - msg_agg.c.min_ts)).label("avg_sec")
    else:
        duration_expr = func.avg(
            (func.julianday(msg_agg.c.max_ts) - func.julianday(msg_agg.c.min_ts)) * 86400
        ).label("avg_sec")

    coalesce_msg_count = func.coalesce(msg_agg.c.msg_count, 0)

    # --- single aggregated query for the main period: many metrics computed by conditional SUM/CASE ---
    # --- single aggregated query for the main period ---
    agg_stmt = (
        select(
            # totals
            func.count(SessionModel.id).label("total_sessions"),
            func.sum(case((SessionModel.approved == True, 1), else_=0)).label("hot_leads"),
            func.sum(case((CompanyDetails.c_info != None, 1), else_=0)).label("enriched_leads"),

            func.sum(case((and_(SessionPhase.q1_email != None, SessionModel.mobile != None), 1), else_=0)).label("key_contacts"),

            func.sum(case((CompanyDetails.c_data != None, 1), else_=0)).label("company_insights"),

            # engagement buckets (uses msg_agg subquery)
            func.sum(case((coalesce_msg_count >= 10, 1), else_=0)).label("highly_engaged"),
            func.sum(case((and_(coalesce_msg_count >= 5, coalesce_msg_count < 10), 1), else_=0)).label("engaged"),
            func.sum(case((and_(coalesce_msg_count >= 2, coalesce_msg_count < 5), 1), else_=0)).label("neutral"),
            func.sum(case((coalesce_msg_count < 2, 1), else_=0)).label("disengaged"),

            # moods (conditional counts on SessionModel)
            func.sum(case((SessionModel.mood == "excited", 1), else_=0)).label("m_excited"),
            func.sum(case((SessionModel.mood == "positive", 1), else_=0)).label("m_positive"),
            func.sum(case((SessionModel.mood == "neutral", 1), else_=0)).label("m_neutral"),
            func.sum(case((SessionModel.mood == "friendly", 1), else_=0)).label("m_friendly"),
            func.sum(case((SessionModel.mood == "confused", 1), else_=0)).label("m_confused"),

            # interest
            func.sum(case((SessionModel.interest == "high", 1), else_=0)).label("interest_high"),
            func.sum(case((SessionModel.interest == "medium", 1), else_=0)).label("interest_medium"),

            # buying signals
            func.sum(case((or_(SessionModel.interest == "high", SessionModel.approved == True), 1), else_=0)).label(
                "buying_signals"
            ),

            # avg duration seconds
            duration_expr,
        )
        .select_from(SessionModel)
        .outerjoin(msg_agg, SessionModel.id == msg_agg.c.sid)
        .outerjoin(CompanyDetails, SessionModel.id == CompanyDetails.session_id)
        .outerjoin(SessionPhase, SessionModel.id == SessionPhase.session_id)
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

    mime_type, _ = mimetypes.guess_type(filepath)
    return FileResponse(
        path=filepath,
        media_type=mime_type or "application/octet-stream",
        filename=filename
    )
    
class ResearchPayload(BaseModel):
    id: str
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

@app.post("/api/deep-research")
async def deep_research(payload: ResearchPayload, db: AsyncSession = Depends(get_db)):
    prompt = _build_research_prompt(payload)

    try:
        # Assuming this is the research API call
        message_content, citations = await _call_research_async(prompt) 
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Research provider error: {e}")

    # --- Start of Database Correction ---
    stmt = (
        select(SessionModel)
        .options(joinedload(SessionModel.research_details)) 
        .where(SessionModel.id == payload.id)
    )
    db_result = await db.execute(stmt)
    db_session = db_result.scalar_one_or_none()

    if not db_session:
        raise HTTPException(status_code=404, detail="Session not found")

    rel = getattr(db_session, "research_details", None) 

    if rel is None:
        rel = ResearchDetails(session_id=db_session.id)
        db_session.research_details = rel
        db.add(rel)
    rel.research_data = json.dumps(message_content)
    rel.research_sources = json.dumps(citations)

    await db.commit()
    await db.refresh(db_session)

    return {
        "status": "success",
        "message": "Deep research completed and saved to session",
        "result": json.dumps(message_content),
        "citations": json.dumps(citations),
        "session_id": db_session.id,
    }




async def send_whatsapp_notification(mobile: str, details: dict):
    """Simulates sending a WhatsApp notification."""
    print(f"--- WA Notification Sent to {mobile} ---")
    print(f"Details: {details}")
    return {"status": "success", "platform": "whatsapp"}

class ConsultantResponse(BaseModel):
    id: str
    name: str
    tier: str
    phone: Optional[str] = None # Optional for security/display purposes

    class Config:
        from_attributes = True # Allows Pydantic to read from SQLAlchemy ORM objects


@app.get("/consultants", response_model=List[ConsultantResponse])
async def get_consultants(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Consultant).order_by(Consultant.tier.desc(), Consultant.name)
    )
    consultants = result.scalars().all()
    
    return consultants

class ConsultationScheduleRequest(BaseModel):
    session_id: str 
    schedule_time: datetime 
    consultant_id: str 
    consultant_name_display: str 


@app.post("/schedule_consultant", status_code=status.HTTP_201_CREATED)
async def schedule_consultant(request: ConsultationScheduleRequest, db: AsyncSession = Depends(get_db)):
    session_id = request.session_id
    
    # 🌟 NEW: Get both the ID and the display name from the request
    consultant_id = request.consultant_id 
    consultant_display_name = request.consultant_name_display
    
    try:
        # 1. Fetch Session and Phase Info (required for contact details)
        session_result = await db.execute(
            select(SessionModel)
            .where(SessionModel.id == session_id)
            .options(joinedload(SessionModel.phase_info))
        )
        session = session_result.scalars().first()
        
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session with ID '{session_id}' not found.") 
        
        phase: SessionPhase = session.phase_info
        if not session.mobile or not (phase and phase.q1_email):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Session is missing mandatory contact details (mobile/email) or phase information.")

        # 2. Skip Consultant lookup (relying on frontend data for notifications)
        # Note: You might still want to do a quick validation check if needed,
        # but for a high-traffic flow, this is faster.

    except HTTPException:
        raise
    except Exception as e:
        print(f"Database error during fetch: {e}") 
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error retrieving session data.")

    # 3. Create New Consultation using consultant_id
    new_consultation = Consultation(
        schedule_time=request.schedule_time,
        status="Pending",
        # Store the ID
        consultant_id=consultant_id, 
        session_id=session_id
        # NOTE: If you still need the raw name/tier for archival/search reasons in Consultation,
        # you may add those columns back, but consultant_id is the normalized way.
    )
    
    db.add(new_consultation)
    await db.commit()
    await db.refresh(new_consultation)
    
    # 4. Use the display name directly for notifications
    client_email = phase.q1_email 
    client_mobile = session.mobile
    company_name = phase.q1_company or "N/A"
    services_chosen = phase.q4_services or "Not specified"
    schedule_time_str = request.schedule_time.strftime("%A, %B %d, %Y at %I:%M %p %Z") 
    
    # Use the display name received from the request
    whatsapp_details = {
        "time": schedule_time_str,
        "consultant": consultant_display_name, 
        "company": company_name
    }

    # await send_whatsapp_notification(client_mobile, whatsapp_details)

    email_subject = f"Consultation Confirmed: {company_name} - {schedule_time_str}"
    
    # --- Professional HTML Email Body ---
    email_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <div style="max-width: 600px; margin: 20px auto; padding: 20px; border: 1px solid #ddd; border-radius: 8px;">
            <h2 style="color: #1a73e8;">Consultation Confirmation</h2>
            <p>Dear Client,</p>

            <p>We are pleased to confirm your scheduled consultation with our team. Please review the details below:</p>

            <div style="background-color: #f9f9f9; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                <p><strong>Company:</strong> {company_name}</p>
                <p><strong>Services of Interest:</strong> {services_chosen}</p>
                <p><strong>Date & Time:</strong> <strong style="color: #008000;">{schedule_time_str}</strong></p>
                <p><strong>Consultant:</strong> {consultant_display_name or 'A member of our team'}</p>
            </div>
            
            <p>We look forward to a productive discussion to help you achieve your goals.</p>
            <p>If you have any questions or need to reschedule, please contact us immediately.</p>
            
            <p style="margin-top: 30px;">Best regards,<br>
            <strong>The Team</strong></p>
        </div>
    </body>
    </html>
    """
    await send_email_notification(client_email, email_subject, email_body)
    
    return {
        "message": "Consultation scheduled successfully",
        "consultation_id": new_consultation.id,
        "schedule_time": schedule_time_str,
        "consultant_name": consultant_display_name 
    }
    
@app.get("/session/{session_id}/consultations", status_code=status.HTTP_200_OK)
async def list_consultations_by_session(session_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Consultation)
        .where(Consultation.session_id == session_id)
        .order_by(Consultation.schedule_time.desc()) 
        .options(
            selectinload(Consultation.session).selectinload(SessionModel.phase_info),
            joinedload(Consultation.consultant_info) 
        )
    )
    consultations = result.scalars().all()

    if not consultations:
        return []

    response_list = []
    
    for consultation in consultations:
        consultant_name = consultation.consultant_info.name if consultation.consultant_info else "Unknown"

        entry = {
            "consultation_id": consultation.id,
            "schedule_time": consultation.schedule_time.isoformat(),
            "status": consultation.status,
            "consultant": consultant_name, 
            "created_at": consultation.created_at.isoformat(),
        }
        response_list.append(entry)

    return response_list

class ConsultationStatusUpdate(BaseModel):
    new_status: str 

@app.put("/consultation/{consultation_id}/status", status_code=status.HTTP_200_OK)
async def update_consultation_status(consultation_id: str,update_data: ConsultationStatusUpdate,db: AsyncSession = Depends(get_db)):

    result = await db.execute(
        select(Consultation)
        .where(Consultation.id == consultation_id)
    )
    consultation = result.scalars().first()

    if not consultation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail=f"Consultation with ID '{consultation_id}' not found."
        )

    old_status = consultation.status
    consultation.status = update_data.new_status

    try:
        await db.commit()
        await db.refresh(consultation)
    except Exception as e:
        await db.rollback()
        print(f"Database error during update: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Failed to update consultation status."
        )
    return {
        "message": f"Consultation ID {consultation_id} status updated successfully.",
        "old_status": old_status,
        "new_status": consultation.status,
        "updated_at": consultation.updated_at.isoformat(),
    }
    




async def send_email_notification(to_email: str, subject: str, body: str):
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = EMAIL_USER
        msg["To"] = to_email
        msg["Subject"] = subject
        
        # Create a basic plain text version from the HTML content for compatibility
        # Note: A real parser might be better, but for this simple HTML, a placeholder is often sufficient.
        text_body = f"Your consultation is confirmed: {subject}. Please enable HTML to view full details."
        
        html = body
        
        # Attach both versions
        msg.attach(MIMEText(text_body, "plain")) # Updated text to be relevant
        msg.attach(MIMEText(html, "html"))
        
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASS)
            server.sendmail(EMAIL_USER, to_email, msg.as_string())
            
    except smtplib.SMTPException as e:
        print(f"SMTP error sending to {to_email}: {e}")
    except Exception as e:
        print(f"General error sending to {to_email}: {e}")
        
        
class TemplateTaskSchema(BaseModel):
    id: int
    title: str
    description: Optional[str]
    
class ServiceTemplateSchema(BaseModel):
    id: int
    name: str
    description: Optional[str]
    default_tasks: List[TemplateTaskSchema]

    class Config:
        from_attributes = True


class AITaskSchema(BaseModel):
    title: str
    description: Optional[str] = ""

class ProjectCreateSchema(BaseModel):
    project_name: str
    notes: Optional[str] = ""
    company_name: str
    email: str
    phone: str

    template_id: Optional[int] = None
    selected_task_ids: List[int] = []
    custom_tasks: List[str] = []
    ai_tasks: List[AITaskSchema] = []

@app.get("/api/service-templates", response_model=List[ServiceTemplateSchema])
async def get_service_templates(db: AsyncSession = Depends(get_db)):
    # We MUST use selectinload to fetch the related 'default_tasks'
    result = await db.execute(
        select(ServiceTemplate).options(selectinload(ServiceTemplate.default_tasks))
    )
    templates = result.scalars().all()
    return templates

# --- 3. Updated Create Project Endpoint ---
@app.post("/api/sessions/{session_id}/project")
async def create_project_from_session(
    session_id: str, 
    payload: ProjectCreateSchema, 
    db: AsyncSession = Depends(get_db)
):
    # 1. Fetch Session and Phase Info to update them
    result = await db.execute(
        select(SessionModel)
        .options(selectinload(SessionModel.phase_info), selectinload(SessionModel.project))
        .where(SessionModel.id == session_id)
    )
    session = result.scalar_one_or_none()
    
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.project:
        raise HTTPException(status_code=400, detail="Project already exists for this session")

  
    if payload.phone:
        session.mobile = payload.phone
    
    # Update Company & Email in SessionPhase table
    if session.phase_info:
        session.phase_info.q1_company = payload.company_name
        session.phase_info.q1_email = payload.email
    new_project = Project(
        id=str(uuid.uuid4()),
        name=payload.project_name, # As defined by consultant
        notes=payload.notes,
        status="Active",
        progress_percent=0,
        template_id=payload.template_id,
        session_id=session_id
    )
    db.add(new_project)
    await db.flush() # Flush to generate ID

    # 4. CREATE PROJECT TASKS (One by One)
    task_sequence = 1
    
    # A. Add Selected Template Tasks
    if payload.template_id and payload.selected_task_ids:
        stmt = select(TemplateTask).where(TemplateTask.id.in_(payload.selected_task_ids))
        t_result = await db.execute(stmt)
        selected_template_tasks = t_result.scalars().all()
        
        # Sort by original sequence
        selected_template_tasks.sort(key=lambda x: x.sequence_number)

        for t_task in selected_template_tasks:
            new_p_task = ProjectTask(
                project_id=new_project.id,
                title=t_task.title,
                details=t_task.description,
                status="Pending",
                sequence_number=task_sequence
            )
            db.add(new_p_task)
            task_sequence += 1

    # B. Add Custom Tasks
    for custom_title in payload.custom_tasks:
        if custom_title.strip():
            custom_task = ProjectTask(
                project_id=new_project.id,
                title=custom_title.strip(),
                details="Custom task added by consultant",
                status="Pending",
                sequence_number=task_sequence
            )
            db.add(custom_task)
            task_sequence += 1
            
    ai_tasks = getattr(payload, "ai_tasks", []) or []
    for ai in ai_tasks:
        title = (ai.title or "").strip()
        desc = (ai.description or "").strip()
        if not title:
            continue
        ai_task = ProjectTask(
            project_id=new_project.id,
            title=title,
            details=desc or "AI-suggested task",
            status="Pending",
            sequence_number=task_sequence
        )
        db.add(ai_task)
        task_sequence += 1

    await db.commit()
    return {"message": "Project created and Client Data updated", "project_id": new_project.id}




class TaskFileSchema(BaseModel):
    id: int
    file_name: str
    storage_path: str
    uploaded_at: datetime  

    class Config:
        from_attributes = True

class ProjectTaskSchema(BaseModel):
    id: int
    title: str
    details: Optional[str]
    status: str
    sequence_number: int
    files: List[TaskFileSchema] = []
   
    
    class Config:
        from_attributes = True

class ProjectListSchema(BaseModel):
    id: str
    name: str
    notes: str | None 
    status: str
    progress_percent: int
    total_tasks: int 
    created_at: datetime  
    updated_at: datetime  

    class Config:
        from_attributes = True

class ProjectSchema(BaseModel):
    id: str
    name: str
    status: str
    progress_percent: int
    created_at: datetime  
    updated_at: datetime 

class ProjectDetailSchema(ProjectSchema):
    notes: Optional[str]
    tasks: List[ProjectTaskSchema] = []

class ProjectStatusUpdate(BaseModel):
    status: str

class ConsultantBase(BaseModel):
    name: str
    phone: Optional[str] = None
    tier: str = "junior"

class ConsultantCreate(ConsultantBase):
    pass 

class ConsultantOut(ConsultantBase):
    id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class MessageResponse(BaseModel):
    message: str



@app.get("/projects", response_model=List[ProjectListSchema])
async def list_projects(db: AsyncSession = Depends(get_db)):
    query = (
        select(
            Project,
            func.count(ProjectTask.id).label("total_tasks")
        )
        .outerjoin(Project.tasks) 
        .group_by(Project.id)
        .order_by(Project.updated_at.desc())
    )

    result = await db.execute(query)
    projects_data = []
    for project, total_tasks in result.all():
        projects_data.append(
            ProjectListSchema(
                id=project.id,
                name=project.name,
                notes=project.notes, 
                status=project.status,
                progress_percent=project.progress_percent,
                total_tasks=total_tasks, 
                created_at=project.created_at,
                updated_at=project.updated_at,
            )
        )
    
    return projects_data

@app.get("/projects/{project_id}", response_model=ProjectDetailSchema)
async def get_project_details(project_id: str, db: AsyncSession = Depends(get_db)):
    query = (
        select(Project)
        .where(Project.id == project_id)
        .options(
            selectinload(Project.tasks).selectinload(ProjectTask.files)
        )
    )
    result = await db.execute(query)
    project = result.scalar_one_or_none()
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    return project

@app.patch("/tasks/{task_id}/status")
async def update_task_status(
    task_id: int, 
    status: str = Form(...), 
    db: AsyncSession = Depends(get_db)
):

    result = await db.execute(select(ProjectTask).where(ProjectTask.id == task_id))
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task.status = status
    if status == "Completed":
        task.completed_at = func.now()
    tasks_result = await db.execute(
        select(ProjectTask.status).where(ProjectTask.project_id == task.project_id)
    )
    all_statuses = tasks_result.scalars().all()
    
    total_tasks = len(all_statuses)
    completed_tasks = all_statuses.count("Completed") + (1 if status == "Completed" and "Completed" not in all_statuses else 0)
    
    completed_count = 0
    for s in all_statuses:
        if s == "Completed": 
            completed_count += 1

    count_q = select(func.count()).where(ProjectTask.project_id == task.project_id)
    total_count = await db.scalar(count_q)
    
    completed_q = select(func.count()).where(
        ProjectTask.project_id == task.project_id, 
        ProjectTask.status == "Completed"
    )
    await db.flush() 
    
    real_completed = await db.scalar(completed_q)
    
    new_progress = 0
    if total_count > 0:
        new_progress = int((real_completed / total_count) * 100)
    
    # Update Project
    project_result = await db.execute(select(Project).where(Project.id == task.project_id))
    project = project_result.scalar_one()
    project.progress_percent = new_progress
    
    await db.commit()
    
    return {"status": "updated", "new_progress": new_progress, "task_status": task.status}

@app.post("/tasks/{task_id}/files", response_model=TaskFileSchema)
async def upload_task_file(
    task_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db)
):
    """Uploads a file, saves to disk, and links to the task."""
    # Verify Task Exists
    task = await db.get(ProjectTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Save File
    file_location = os.path.join(UPLOAD_DIR, f"{task_id}_{file.filename}")
    with open(file_location, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    # Create DB Entry
    new_file = TaskFile(
        task_id=task_id,
        file_name=file.filename,
        storage_path=f"/uploads/{task_id}_{file.filename}", # Web accessible path
        mime_type=file.content_type
    )
    
    db.add(new_file)
    await db.commit()
    await db.refresh(new_file)
    
    return new_file

@app.patch("/projects/{project_id}/status")
async def update_project_status(
    project_id: str, 
    update: ProjectStatusUpdate,
    db: AsyncSession = Depends(get_db)
):

    project = await db.get(Project, project_id)
    
    if not project:
        raise HTTPException(status_code=404, detail=f"Project with ID '{project_id}' not found")
    
    # 2. Validate and Update Status
    new_status = update.status
    
    # Optional: Add validation logic here if you only allow specific statuses
    allowed_statuses = ["Active", "On Hold", "Completed", "Canceled"]
    if new_status not in allowed_statuses:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid status '{new_status}'. Must be one of: {', '.join(allowed_statuses)}"
        )
        
    project.status = new_status
    
    # 3. Commit changes (updated_at will be automatically updated by SQLAlchemy's func.now())
    await db.commit()
    await db.refresh(project)
    
    return {
        "id": project.id, 
        "status": project.status, 
        "message": f"Project status successfully updated to '{project.status}'"
    }
    
    
class TemplateTaskBase(BaseModel):
    title: str = Field(..., max_length=255)
    description: Optional[str] = None
    sequence_number: int = Field(1, ge=1)
    is_milestone: bool = False

class TemplateTaskCreate(TemplateTaskBase):
    pass 

class TemplateTaskRead(TemplateTaskBase):
    id: int
    template_id: int

    class Config:
        from_attributes = True
        
class ServiceTemplateCreate(BaseModel):
    name: str = Field(..., max_length=255)
    description: Optional[str] = None
    default_tasks: List[TemplateTaskCreate] = Field([])

class ServiceTemplateRead(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    default_tasks: List[TemplateTaskRead] = Field([]) # Nested tasks

    class Config:
        from_attributes = True

class StatusResponse(BaseModel):
    status: str = "success"
    message: str
    
@app.get("/api/templates/", response_model=List[ServiceTemplateRead])
async def get_all_templates(db: AsyncSession = Depends(get_db)):
    """Fetches all ServiceTemplates with their associated TemplateTasks."""
    
    # FIX: Use .options(selectinload(...)) to fetch tasks immediately
    stmt = select(ServiceTemplate).options(selectinload(ServiceTemplate.default_tasks))
    
    result = await db.execute(stmt)
    templates = result.scalars().all()
    return templates

# 2. POST: Create a new template with tasks
@app.post("/api/templates/", response_model=ServiceTemplateRead, status_code=status.HTTP_201_CREATED)
async def create_template(
    template_data: ServiceTemplateCreate, 
    db: AsyncSession = Depends(get_db)
):
    try:
        # Create the template object
        new_template = ServiceTemplate(
            name=template_data.name, 
            description=template_data.description
        )
        db.add(new_template)
        await db.flush() 

        # Create the tasks
        for task_data in template_data.default_tasks:
            new_task = TemplateTask(
                template_id=new_template.id,
                title=task_data.title,
                description=task_data.description,
                sequence_number=task_data.sequence_number,
                is_milestone=task_data.is_milestone
            )
            db.add(new_task)

        await db.commit()
        
        # FIX: Re-fetch the object with relationships loaded to prevent MissingGreenlet error on response
        stmt = (
            select(ServiceTemplate)
            .options(selectinload(ServiceTemplate.default_tasks))
            .where(ServiceTemplate.id == new_template.id)
        )
        result = await db.execute(stmt)
        return result.scalar_one()

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=400, 
            detail=f"Failed to create template: {e}"
        )

@app.delete("/api/templates/{template_id}", response_model=StatusResponse)
async def delete_template(template_id: int, db: AsyncSession = Depends(get_db)):
    stmt = delete(ServiceTemplate).where(ServiceTemplate.id == template_id)
    result = await db.execute(stmt)
    await db.commit()
    
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail=f"Template with ID {template_id} not found.")

    return {"status": "success", "message": f"Template ID {template_id} deleted successfully."}


@app.delete("/api/tasks/{task_id}", response_model=StatusResponse)
async def delete_task(task_id: int, db: AsyncSession = Depends(get_db)):
    stmt = delete(TemplateTask).where(TemplateTask.id == task_id)
    result = await db.execute(stmt)
    await db.commit()
    
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found.")

    return {"status": "success", "message": f"Task ID {task_id} deleted successfully."}



@app.post("/api/consultant/",response_model=ConsultantOut,status_code=status.HTTP_201_CREATED,summary="Create a new Consultant")
async def create_new_consultant(consultant_data: ConsultantCreate, db: AsyncSession = Depends(get_db)):

    db_consultant = Consultant(
        name=consultant_data.name,
        phone=consultant_data.phone,
        tier=consultant_data.tier,
    )
    db.add(db_consultant)
    await db.commit()
    await db.refresh(db_consultant)
    return db_consultant

@app.get("/api/consultants/",
    response_model=List[ConsultantOut],
    summary="Retrieve all Consultants"
)
async def get_all_consultants(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Consultant).order_by(Consultant.created_at.desc()))
    consultants = result.scalars().all()
    return consultants


@app.delete("/api/consultant/{consultant_id}",
    response_model=MessageResponse,
    summary="Delete a Consultant by ID"
)
async def remove_consultant(consultant_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        delete(Consultant).where(Consultant.id == consultant_id)
    )
    await db.commit()

    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consultant not found")

    return {"message": f"Consultant with ID {consultant_id} deleted successfully"}


class GenerateRequest(BaseModel):
    session_id: Optional[str] = None
    template_id: Optional[int] = None
    project_name: Optional[str] = None
    company: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    notes: Optional[str] = None
    max_tasks: Optional[int] = 8

class GeneratedTask(BaseModel):
    title: str
    description: Optional[str] = ""

def _build_prompt(company_ctx: str, categories: Optional[str], services: Optional[str], max_tasks: int) -> str:
    parts = [
        "You are a task generator for a professional business services firm working in Saudi Arabia.",
        f"Context: company: {company_ctx or 'N/A'}; categories: {categories or 'N/A'}; services: {services or 'N/A'}",
        f"Produce up to {max_tasks} concise suggested tasks (title + one-line description) to move this lead to a project-ready state.",
        "Output MUST be valid JSON: an array of objects with exactly these fields: title (string), description (string).",
        "Return JSON only — no explanation, no backticks, no commentary.",
        "Keep descriptions short (<= 140 characters). Use plain text only."
    ]
    return "\n".join(parts)


async def _call_model_with_retries(prompt: str, max_tokens: int = 700, attempts: int = 3, backoff: float = 0.7) -> str:
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            resp = await client.responses.create(
                model=MODEL_NAME,
                input=prompt,
                temperature=0.0,
            )
            
            # --- SIMPLIFIED EXTRACTION LOGIC ---
            # Most modern SDKs provide a simple way to get the text.
            raw_text = getattr(resp, "output_text", None) or getattr(resp, "text", None)
            
            if raw_text:
                return raw_text
                
            # If the simple property retrieval fails, use the complex logic as a fallback
            # (Keeping it here as provided, but marking it as complex/specific)
            parts = []
            for item in getattr(resp, "output", []) or []:
                # ... existing complex extraction logic ...
                if isinstance(item, dict):
                    for c in item.get("content", []):
                        if isinstance(c, dict) and c.get("type") == "output_text":
                            parts.append(c.get("text", ""))
                        elif isinstance(c, str):
                            parts.append(c)
                elif isinstance(item, str):
                    parts.append(item)
            text = "\n".join(p for p in parts if p)
            if text:
                return text

            # Fallback to string representation (less reliable)
            return str(resp)

        except Exception as e:
            last_exc = e
            if attempt < attempts:
                await asyncio.sleep(backoff * attempt)
            else:
                break

    raise last_exc if last_exc else Exception("Unknown model call error after retries")


def _extract_json_from_text(text: str) -> Optional[List[Dict[str, Any]]]:
    """Robustly extracts a JSON array of objects from the LLM text output."""
    
    # 1. Try direct parse
    try:
        parsed = json.loads(text.strip())
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass
    m = re.search(r"(\[\s*\{.*?\}\s*\])", text, flags=re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group(1))
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass

    matches = re.findall(r"\{[^{}]+\}", text, flags=re.DOTALL)
    if matches:
        items = []
        for m in matches:
            try:
                items.append(json.loads(m))
            except Exception:
                continue
        return items if items else None
        
    return None


@app.post("/api/projects/generate-tasks")
async def generate_tasks(req: GenerateRequest, db: AsyncSession = Depends(get_db)):
    categories = None
    services = None
    company_ctx = (req.company or "")[:1000]
    try:
        if req.session_id:
            q = await db.execute(select(SessionPhase).where(SessionPhase.session_id == req.session_id))
            sp = q.scalar_one_or_none()
            if sp:
                categories = sp.q3_categories
                services = sp.q4_services
                
            q2 = await db.execute(select(CompanyDetails).where(CompanyDetails.session_id == req.session_id))
            cd = q2.scalar_one_or_none()
            if cd and cd.c_info:
                company_ctx = company_ctx or cd.c_info
                
    except Exception as e:
        print(f"Error fetching session context: {e}")


    prompt = _build_prompt(company_ctx, categories, services, req.max_tasks or 8)

    try:
        raw_text = await _call_model_with_retries(prompt, max_tokens=700, attempts=3, backoff=0.7)
        
    except Exception as e:
        print(f"Model call failed permanently: {e}")
        if req.template_id:
            try:
                # SQLAlchemy async execution for template fallback
                qtpl = await db.execute(select(ServiceTemplate).where(ServiceTemplate.id == req.template_id))
                tpl = qtpl.scalar_one_or_none()
                
                if tpl and getattr(tpl, 'default_tasks', None):
                    fallback = []
                    for t in tpl.default_tasks[: (req.max_tasks or 8)]:
                        # Using getattr for safer access to properties
                        fallback.append({
                            "title": getattr(t, 'title', '')[:200], 
                            "description": (getattr(t, 'description', '') or "")[:400]
                        })
                    return {"tasks": fallback}
            except Exception as fe:
                print(f"Fallback template read failed: {fe}")

        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="LLM generation failed; try again later")

    # 4. Extract and Clean Tasks
    tasks_raw = _extract_json_from_text(raw_text)
    
    if not tasks_raw or not isinstance(tasks_raw, list):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Model returned non-JSON output. Try again later.")

    clean_tasks = []
    for t in tasks_raw[: (req.max_tasks or 8)]:
        if isinstance(t, dict):
            title = (t.get("title") or "").strip()
            desc = (t.get("description") or "").strip()
        elif isinstance(t, str):
            # Fallback for LLM returning a list of strings
            title = t.strip()
            desc = ""
        else:
            continue
            
        if not title:
            continue
            
        # Truncation and cleaning
        title = title[:200]
        desc = desc[:400]
        clean_tasks.append({"title": title, "description": desc})

    if not clean_tasks:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Model returned empty/invalid task list.")
        
    return {"tasks": clean_tasks}