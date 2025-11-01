import math
import os
import shutil
import uuid
import json
import asyncio
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Set, Optional
from fastapi import Depends, FastAPI, File, Request, UploadFile, Form, WebSocket, WebSocketDisconnect, Query, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_huggingface.embeddings import HuggingFaceEmbeddings
from langchain_community.docstore.document import Document
from openai import OpenAI, APIError, AuthenticationError, RateLimitError
from PyPDF2 import PdfReader
from starlette.concurrency import run_in_threadpool
from dotenv import load_dotenv
from sqlalchemy import create_engine,func
from sqlalchemy.orm import Session as DBSession
from database import SessionLocal, Session as SessionModel, Message as MessageModel, get_db, init_db 
from prompt import AnalytxPromptTemp
from collections import defaultdict
#langchain imports
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_community.chat_message_histories import SQLChatMessageHistory


load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL_NAME = "gpt-4o-mini"
SITE_NAME = "Business Chatbot"
INACTIVITY_THRESHOLD = timedelta(minutes=5)  

os.makedirs("data", exist_ok=True)
os.makedirs("vectorstore", exist_ok=True)

app = FastAPI(title="Business Chatbot API")

@app.api_route("/health", methods=["GET", "HEAD"])
async def health_check():
    return {"status": "ok"}

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.on_event("startup")
def startup_event():
    init_db()


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
        def fetch_history():
            try:
                db: DBSession = SessionLocal()
                # ORM query to fetch messages for the session
                messages = (
                    db.query(MessageModel)
                    .filter(MessageModel.session_id == session_id)
                    .order_by(MessageModel.timestamp.asc())
                    .all()
                )
                # Convert ORM objects to JSON-serializable dicts
                # Handle potential None timestamps gracefully by using a default ISO string
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
            finally:
                db.close()
        # Run the blocking DB call in a threadpool (important for async)
        history_json = await run_in_threadpool(fetch_history)
        # Send over WebSocket
        await websocket.send_text(history_json)

manager = ConnectionManager()


_MAIN_CATEGORIES="""MAIN_CATEGORIES = [
            "Market Entry & Business Setup",
            "Strategic Growth & Management",
            "Financial Services & Compliance",
            "Industrial & Specialized Operations",
            "Legal & Business Protection"
            ]"""

_SUB_SERVICES = """SUB_SERVICES = {
            "Market Entry & Business Setup": [
            {"text": "Business Setup Saudi Arabia", "value": "setup_sa"},
            {"text": "Business Setup in other GCC (UAE, Qatar, etc.)", "value": "setup_gcc"},
            {"text": "Premium Residency & Investor Visas", "value": "visas"},
            {"text": "Entrepreneur License (IT & Tech Services)", "value": "entrepreneur_license"},
            {"text": "Virtual Office & Business Center", "value": "virtual_office"}
            ],
            "Strategic Growth & Management": [
            {"text": "Management Consultancy (Business Restructuring, Market Strategy, M&A)", "value": "consultancy"},
            {"text": "Vendor Registration & Certification (for NEOM, Aramco, etc.)", "value": "vendor_reg"},
            {"text": "HR & Talent Solutions (Recruitment, EOR, Training)", "value": "hr_solutions"}
            ],
            "Financial Services & Compliance": [
            {"text": "Accounting & Bookkeeping", "value": "accounting"},
            {"text": "Tax Consulting & Audit", "value": "tax_audit"},
            {"text": "Bank Account & Finance Assistance", "value": "finance_assist"}
            ],
            "Industrial & Specialized Operations": [
            {"text": "Industrial License & Factory Setup", "value": "industrial_license"},
            {"text": "ISO & Local Content Certification", "value": "iso_cert"},
            {"text": "Technology & Process Automation", "value": "automation"}
            ],
            "Legal & Business Protection": [
            {"text": "Legal Advisory & Contract Drafting", "value": "legal_advisory"},
            {"text": "Debt Recovery & Dispute Resolution", "value": "debt_recovery"},
            {"text": "Trademark Registration", "value": "trademark"}
            ]
            }"""

_TIMELINE_OPTIONS =""" TIMELINE_OPTIONS = [
            {"text": "Within 1 month", "value": "1_month"},
            {"text": "1-3 months", "value": "1_3_months"},
            {"text": "3-6 months", "value": "3_6_months"},
            {"text": "Just researching", "value": "researching"}
            ] """           

_BUDGET_OPTIONS = [
    {"text": "Under 50,000 SAR", "value": "under_50k"},
    {"text": "50,000 - 75,000 SAR", "value": "50_75k"},
    {"text": "75,000 - 100,000 SAR", "value": "75_100k"},
    {"text": "100,000 - 125,000 SAR", "value": "100_125k"},
    {"text": "125,000 - 150,000 SAR", "value": "125_150k"},
    {"text": "Over 150,000 SAR", "value": "over_150k"}
]
           


memory_engine = create_engine("sqlite:///chat_memory.db", connect_args={"check_same_thread": False})

llm = ChatOpenAI(
    model=MODEL_NAME,
    api_key=OPENAI_API_KEY,
    temperature=0.0,
    max_tokens=800,
    model_kwargs={"response_format": {"type": "json_object"}} 
)

def _get_memory(session_id: str):
    return SQLChatMessageHistory(session_id=session_id, connection=memory_engine)

prompt = ChatPromptTemplate.from_messages([
    ("system", AnalytxPromptTemp),
    MessagesPlaceholder(variable_name="history"),("human", "{input}"),
])

chain = prompt | llm
chat_chain = RunnableWithMessageHistory(
    chain,
    _get_memory,
    input_messages_key="input",
    history_messages_key="history",
)


MAIN_CATEGORIES = []  
SUB_SERVICES = {}  
TIMELINE_OPTIONS = []  
BUDGET_RANGES = ""  
SERVICE_BENEFITS = {
    "setup_sa": "complete full business setup in under 30 days",
    "setup_gcc": "expand seamlessly across GCC with unified compliance",
    "trademark": "protect their brand and avoid costly legal disputes",
    "pro_services": "save 40+ hours/month on government paperwork",
    "visa_relocation": "relocate key talent in 2 weeks with zero delays",
    "virtual_office": "establish a Saudi address instantly at 1/10th the cost",
    "consulting_strategy": "increase revenue by 25% in the first year",
    "marketing_digital": "secure government tenders through targeted campaigns",
    "hr_talent": "meet Saudization goals without compromising quality",
    "tech_implementation": "deploy ERP systems with 99% uptime",
    "factory_setup": "launch industrial operations within 90 days",
    "vendor_reg": "get approved on major vendor lists in 2 weeks",
    "legal_advisory": "avoid fines up to SAR 100k with full compliance",
    "financial_audit": "pass ZATCA audits on first attempt",
    "tax_compliance": "reduce tax liability by 15-20% legally",
    "accelerator": "raise funding 3x faster through Vision 2030 programs",
    "feasibility": "validate market entry with 95% accuracy",
    "funding_assist": "secure up to SAR 5M in non-dilutive grants"
}


# def simulate_company_enrichment(company_name: str, question: str) -> str:
#     context = query_vectorstore(f"Company info for {company_name}: industry, size, location")
#     if "industry" in context.lower() or "size" in context.lower():
#         enrichment = f"{context}" 
#     else:
#         enrichment = "No prior data found—exciting new venture!"
#     return enrichment

def get_bot_response(question: str, current_details: Dict[str, Any], session_id: str = "transient") -> Dict[str, Any]:
    if current_details is None:
        current_details = {}

    # Extract state from current_details
    phase = current_details.get("phase", "initial")
    lead_data = current_details.get("lead_data", {})
    details = current_details.get("details", {})
    

    # Simulate enrichment if company mentioned
    company = lead_data.get("q1_company", details.get("company", "unknown"))
    if company != "unknown" and "company" in question.lower():
        pass
        # enrichment = simulate_company_enrichment(company, question)
    else:
        enrichment = ""
    options=[]
    context_parts = []
    if enrichment:
        context_parts.append(f"Enrichment: {enrichment}")

    try:
        if phase == "snip_q2":
            # cat_context = query_vectorstore("MAIN_CATEGORIES for services")
            cat_context = _MAIN_CATEGORIES
            context_parts.append(cat_context)
        elif phase == "snip_q3":
            print("\n\n\nentered in phase q3")
            # main_cats = query_vectorstore("MAIN_CATEGORIES for services")
            main_cats = _SUB_SERVICES
            context_parts.append(main_cats)
        if phase == "snip_q4" and "q4_services" in lead_data:
                print("\n\n\nentered in phase q4")
        elif phase == "snip_q5":
            context_parts.append(_TIMELINE_OPTIONS)
        elif phase == "snip_q6":
            budget_info = _BUDGET_OPTIONS
            if budget_info:
                context_parts.append(f"Budget info: {budget_info}")

    except Exception:
        pass  
    context = "\n".join(context_parts)
    print("\n\nphase:",phase,"\n")

    input_text=f"""Current state from session: Phase='{phase}', Lead Data={json.dumps(lead_data)}, Known Details={json.dumps(details)}
                    Dynamic Options (ALWAYS include full array in JSON if relevant to phase; weave into answer naturally)
                    You must:
                    1. Advance/follow the SNIP flow based on phase and user input. Update lead_data (e.g., 'q1_company': 'value').
                    2. Personalize: Use enrichment, connect to benefits from vectorstore, build rapport.
                    3. If options relevant, weave into answer (e.g., "Which of these?") and include full "options" array in JSON for frontend clickable handling.
                    4. Extract/merge new details (name, email, etc.)—check for business email upsell.
                    5. Assess interest/mood. If flow complete, set "routing" (high_value|nurturing|cre).
                    6. For existing customer: Simulate Odoo fetch from vectorstore context.
                    7. Query vectorstore for services/categories dynamically.
                    8. Return STRICTLY valid JSON ONLY—no extra text. Use EXACT format provided.
                    9. Use contractions ("you're", "we'll"), occasional emojis (like 😊), and a conversational tone.

                    Context (from vectorstore—use for services, benefits, company data): {context}
                    User Question/Input: {question}
                """.strip()

    # Try LangChain path first (enhanced with robust parsing)
    try:
        result = chat_chain.invoke(
            {"input": input_text},
            config={"configurable": {"session_id": session_id}},
        )
        raw_output = getattr(result, "content", str(result))
        try:
            parsed = json.loads(raw_output)
            print("\n\nphase model detected:",parsed,"\n")
            if phase == "snip_q4":
                options=[]
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
            
            print("\n\nphase model detected:",phase,"\n")
            return {
                "answer": raw_output,
                "options": options,
                "phase": phase,
                "lead_data": lead_data,
                "routing": "none",
                "analysis": {
                    "interest": "unknown",
                    "mood": "unknown",
                    "details": {"name": "unknown", "email": "unknown", "phone": "unknown", "company": "unknown"}
                }
            }
    except Exception as lc_err:
        print(f"[LangChain invoke failed — falling back to direct client] {lc_err}")
        # Fallback to direct client (enhanced with same input_text and robust JSON enforcement)
        try:
            completion = client.chat.completions.create(
                model="qwen/qwen-2.5-coder-32b-instruct:free",
                messages=[
                    {"role": "system", "content": "You are a JSON-only business assistant following the SNIP flow. Respond EXCLUSIVELY with valid JSON in the EXACT format specified. No other text."},
                    {"role": "user", "content": input_text}
                ],
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
                            "details": {"name": "unknown", "email": "unknown", "phone": "unknown", "company": "unknown"}
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
                "answer": f"API Error: {str(e)}",
                "options": [],
                "phase": phase,
                "lead_data": lead_data,
                "routing": "none",
                "analysis": {"interest": "medium", "mood": "confused", "details": {}}
            }
        except Exception as e:
            print(f"Unexpected Error: {e}")
            return {
                "answer": "An unexpected error occurred.",
                "options": [],
                "phase": phase,
                "lead_data": lead_data,
                "routing": "none",
                "analysis": {"interest": "unknown", "mood": "confused", "details": {}}
    }
            
def insert_user_message(session_id: str, content: str):
    db = SessionLocal()
    try:
        ts = datetime.utcnow()

        #  Fetch the existing session
        session_obj = db.query(SessionModel).filter(SessionModel.id == session_id).first()

        # If not found, create a new one
        if not session_obj:
            session_obj = SessionModel(id=session_id, status="active")
            db.add(session_obj)
            db.commit()
            db.refresh(session_obj)

        # Create and add the new user message
        message = MessageModel(
            session_id=session_id,
            role="user",
            content=content,
            timestamp=ts,
            interest=None,
            mood=None
        )
        db.add(message)

        # Update the session’s status and timestamp
        if session_obj.status != "admin":
            session_obj.status = "active"
        session_obj.updated_at = datetime.utcnow()
        db.commit()

        return ts.isoformat(), session_obj.status

    except Exception as e:
        db.rollback()
        raise e

    finally:
        db.close()

def handle_bot_response(session_id: str, question: str) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        # Fetch session
        session_obj = db.get(SessionModel, session_id)
        if not session_obj:
            raise ValueError(f"Session {session_id} not found")

        current_details = json.loads(session_obj.details) if session_obj.details else {
            "phase": "initial",
            "lead_data": {},
            "details": {}
        }

        response_data = get_bot_response(question, current_details, session_id)
        answer = response_data.get("answer", "")
        options = response_data.get("options", [])
        next_phase = response_data.get("phase", current_details.get("phase", "initial"))
        lead_data = response_data.get("lead_data", current_details.get("lead_data", {}))
        routing = response_data.get("routing", current_details.get("routing", "none"))

        # Safe handling of analysis
        analysis = response_data.get("analysis") or {}
        interest = analysis.get("interest", "medium")
        mood = analysis.get("mood", "neutral")
        details_update = analysis.get("details", {})

        # Merge details
        details = {**current_details.get("details", {}), **details_update}
        for k, v in details_update.items():
            if details.get(k) == "unknown" or v != "unknown":
                details[k] = v

        updated_details = {
            **current_details,
            "phase": next_phase,
            "lead_data": lead_data,
            "details": details,
            "routing": routing if routing != "none" else current_details.get("routing", "none")
        }

        # Add bot message
        bot_message = MessageModel(
            session_id=session_id,
            role="bot",
            content=answer,
            timestamp=datetime.utcnow(),
            interest=interest,
            mood=mood
        )
        db.add(bot_message)

        # Update session
        session_obj.details = json.dumps(updated_details)
        session_obj.interest = interest
        session_obj.mood = mood
        session_obj.phase = next_phase
        session_obj.routing = routing
        session_obj.updated_at = datetime.utcnow()
        session_obj.status = "active"

        db.commit()

        return {
            "answer": answer,
            "options": options,
            "phase": next_phase,
            "lead_data": lead_data,
            "routing": routing,
            "analysis": analysis,
            "bot_ts": bot_message.timestamp.isoformat()
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()



def update_inactive_sessions():
    db = SessionLocal()
    try:
        threshold_time = datetime.utcnow() - INACTIVITY_THRESHOLD
        # Bulk update using ORM
        db.query(SessionModel).filter(
            SessionModel.status == "active",
            SessionModel.updated_at < threshold_time
        ).update({"status": "inactive"}, synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        
        
# main

@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/api/sessions/")
def create_session():
    db = SessionLocal()
    try:
        session_id = str(uuid.uuid4())
        new_session = SessionModel(
            id=session_id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(new_session)
        db.commit()
        return {"session_id": session_id}
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create session")
    finally:
        db.close()




interest_score = {"low": 0.0, "medium": 1.0, "high": 2.0}
score_to_interest = lambda s: "low" if s < 0.5 else ("medium" if s < 1.5 else "high")

@app.get("/api/sessions/")
def get_sessions(
    active: bool = Query(False),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    db: DBSession = Depends(get_db)
) -> Dict[str, Any]:
    try:
        update_inactive_sessions()

        # Base query for sessions
        session_query = db.query(SessionModel)
        if active:
            session_query = session_query.filter(SessionModel.status == "active")

        # Get total count for pagination
        total_query = session_query.with_entities(func.count(SessionModel.id))
        total = total_query.scalar()

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
        sessions = (
            session_query
            .order_by(SessionModel.created_at.desc())
            .offset(offset)
            .limit(per_page)
            .all()
        )

        # Collect session IDs for batch querying messages
        session_ids = [sess.id for sess in sessions]
        session_id_to_index = {sess.id: idx for idx, sess in enumerate(sessions)}

        # Batch fetch ALL messages for these sessions in one query
        msg_rows_all = (
            db.query(MessageModel)
            .filter(MessageModel.session_id.in_(session_ids))
            .order_by(MessageModel.session_id, MessageModel.timestamp.asc())
            .all()
        )

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

            inner_details = details_json.get("details", {})
            lead_data = details_json.get("lead_data", {})

            # Backward compatibility (old data may not have nested dict)
            name = inner_details.get("name") or details_json.get("name", "Unknown")
            usr_email = inner_details.get("email") or details_json.get("email", "Unknown")
            usr_phone = inner_details.get("phone") or details_json.get("phone", "Unknown")
            usr_company = inner_details.get("company") or details_json.get("company", "Unknown")
            
            lead_company = lead_data.get("q1_company") or details_json.get("q1_company") or None
            lead_email = lead_data.get("q1_email") or details_json.get("q1_email") or None
            phase = details_json.get("phase", "unknown")
            routing = details_json.get("routing", "none")

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

                for role, interest_val, mood_val, ts_dt in parsed:
                    delta = (latest_ts - ts_dt).total_seconds() if ts_dt else 0.0
                    weight = math.exp(-ln2 * (delta / half_life_seconds))

                    if interest_val in interest_score:
                        s = interest_score[interest_val]
                        weighted_sum += s * weight
                        weight_total += weight

                    if role == "user" and mood_val:
                        mood_weights[mood_val] = mood_weights.get(mood_val, 0.0) + weight

                if weight_total > 0:
                    avg_score = weighted_sum / weight_total
                    overall_interest_label = score_to_interest(avg_score)

                if mood_weights:
                    overall_mood_label = max(mood_weights.items(), key=lambda kv: kv[1])[0]

            sessions_list.append({
                "id": sess.id,
                "created_at": sess.created_at,
                "status": sess.status,
                "interest": overall_interest_label,
                "mood": overall_mood_label,
                "name": name,
                "usr_email": usr_email,
                "usr_phone": usr_phone,
                "usr_company": usr_company,
                "phase": phase,
                "routing": routing,
                "last_message": last_msg,
                
                "lead_company": lead_company,
                "lead_email": lead_email,
                "lead_email_domain": lead_data.get("q1_email_domain") or details_json.get("q1_email_domain") or None,
                "lead_role": lead_data.get("q2_role") or details_json.get("q2_role") or None,
                "lead_categories": lead_data.get("q3_categories") or details_json.get("q3_categories") or None,
                "lead_services": lead_data.get("q4_services") or details_json.get("q4_services") or None,
                "lead_activity": lead_data.get("q5_activity") or details_json.get("q5_activity") or None,
                "lead_timeline": lead_data.get("q6_timeline") or details_json.get("q6_timeline") or None,
                "lead_budget": lead_data.get("q7_budget") or details_json.get("q7_budget") or None,
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


@app.get("/api/sessions/{session_id}/metrics")
def session_metrics(session_id: str, db: DBSession = Depends(get_db)):
    try:
        # Fetch all messages for the session in chronological order
        rows = (
            db.query(MessageModel)
            .filter(MessageModel.session_id == session_id)
            .order_by(MessageModel.timestamp.asc())
            .all()
        )

        timeline = []
        daily = {}
        for msg in rows:
            ts_dt = msg.timestamp or None
            ts_str = (
                msg.timestamp.strftime("%Y-%m-%d %H:%M:%S")
                if msg.timestamp else None
            )

            # Compute numeric interest score
            interest_val = (msg.interest or "").lower()
            iscore = interest_score.get(interest_val) if interest_val in interest_score else None

            mood_val = (msg.mood or "").lower()
            role = msg.role or ""

            timeline.append({
                "timestamp": ts_str,
                "role": role,
                "interest": msg.interest or None,
                "interest_score": iscore,
                "mood": msg.mood or None
            })

            # Daily aggregation
            if ts_dt:
                date_key = ts_dt.strftime("%Y-%m-%d")
                if date_key not in daily:
                    daily[date_key] = {
                        "interest_sum": 0.0,
                        "interest_count": 0,
                        "mood_counts": {}
                    }

                if iscore is not None:
                    daily[date_key]["interest_sum"] += iscore
                    daily[date_key]["interest_count"] += 1

                if role.lower() == "user" and mood_val:
                    mood_key = mood_val
                    daily[date_key]["mood_counts"][mood_key] = (
                        daily[date_key]["mood_counts"].get(mood_key, 0) + 1
                    )

        # Transform daily dict into sorted list
        daily_list = []
        for date_key in sorted(daily.keys()):
            entry = daily[date_key]
            avg_interest = (
                entry["interest_sum"] / entry["interest_count"]
                if entry["interest_count"] > 0
                else None
            )
            daily_list.append({
                "date": date_key,
                "avg_interest": avg_interest,
                "mood_counts": entry["mood_counts"]
            })

        return {
            "session_id": session_id,
            "timeline": timeline,
            "daily": daily_list
        }

    except Exception as e:
        print("Error in session_metrics:", e)
        return {"session_id": session_id, "timeline": [], "daily": []}




templates = Jinja2Templates(directory="templates")


@app.get("/admin/")
async def admin_home(request: Request, db = Depends(get_db)):
    # Basic totals
    total_sessions = db.query(func.count(SessionModel.id)).scalar() or 0
    total_messages = db.query(func.count(MessageModel.id)).scalar() or 0

    # Highest conversation (session with most messages)
    highest_conversation = (
        db.query(
            MessageModel.session_id,
            func.count(MessageModel.id).label("message_count")
        )
        .group_by(MessageModel.session_id)
        .order_by(func.count(MessageModel.id).desc())
        .first()
    )

    highest_message_count = highest_conversation.message_count if highest_conversation else 0

    # Average messages per session (only sessions that have messages)
    subquery = (
        db.query(
            MessageModel.session_id,
            func.count(MessageModel.id).label("msg_count")
        )
        .group_by(MessageModel.session_id)
        .subquery()
    )
    avg_messages_per_session = db.query(func.avg(subquery.c.msg_count)).scalar() or 0
    avg_messages_per_session = round(avg_messages_per_session, 2)

    # Routing counts
    routing_high_value = db.query(func.count(SessionModel.id)).filter(SessionModel.routing == "high_value").scalar() or 0
    routing_nurturing = db.query(func.count(SessionModel.id)).filter(SessionModel.routing == "nurturing").scalar() or 0

    # Completion: sessions where phase in ('snip_q7', 'routing')
    completed_count = db.query(func.count(SessionModel.id)).filter(SessionModel.phase.in_(["routing","complete"])).scalar() or 0
    if total_sessions:
        completion_rate = round((completed_count / total_sessions) * 100, 2)
    else:
        completion_rate = 0.0


    context = {
        "request": request,
        "total_sessions": total_sessions,
        "total_messages": total_messages,
        "highest_message_count": highest_message_count,
        "avg_messages_per_session": avg_messages_per_session,
        "routing_high_value": routing_high_value,
        "routing_nurturing": routing_nurturing,
        "completed_count": completed_count,
        "completion_rate": completion_rate,
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
                    ts, current_status = await run_in_threadpool(lambda: insert_user_message(session_id, content))
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
                        bot_data = await run_in_threadpool(lambda: handle_bot_response(session_id, content))
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
    # --- Sync DB helpers (run inside run_in_threadpool) ---
    def set_admin_status(session_id: str):
        db: DBSession = SessionLocal()
        try:
            sess = db.get(SessionModel, session_id)
            if sess:
                sess.status = "admin"
                sess.updated_at = datetime.utcnow()
                db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def do_handover(session_id: str):
        db: DBSession = SessionLocal()
        try:
            sess = db.get(SessionModel, session_id)
            if sess:
                sess.status = "active"
                sess.updated_at = datetime.utcnow()
                db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def insert_admin_message(session_id: str, content: str) -> str:
        """
        Inserts an admin message and updates session.updated_at.
        Returns ISO timestamp string for broadcasting.
        """
        db: DBSession = SessionLocal()
        try:
            ts = datetime.utcnow()
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
            sess = db.get(SessionModel, session_id)
            if sess:
                sess.updated_at = datetime.utcnow()

            db.commit()
            # refresh to ensure id populated if needed
            db.refresh(msg)
            return ts.isoformat()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def set_active_status(session_id: str):
        db: DBSession = SessionLocal()
        try:
            sess = db.get(SessionModel, session_id)
            if sess:
                sess.status = "active"
                sess.updated_at = datetime.utcnow()
                db.commit()
        except Exception:
            db.rollback()
            # swallow errors similar to original finally block behavior
        finally:
            db.close()

    # --- Main websocket flow ---
    try:
        # Set session status = 'admin' (blocking DB op)
        try:
            await run_in_threadpool(lambda: set_admin_status(session_id))
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
                    await run_in_threadpool(lambda: do_handover(session_id))
                    handover_msg = {"type": "handover", "content": "Handed over to bot."}
                    await manager.broadcast(json.dumps(handover_msg), session_id)
                except Exception:
                    # swallow exceptions (same behavior as original)
                    pass

            elif msg_type == "message":
                content = parsed_data.get("content", "")
                try:
                    ts = await run_in_threadpool(lambda: insert_admin_message(session_id, content))
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
                    # swallow DB/broadcast errors (same as original)
                    pass

            # ignore other types (same as original)

    except WebSocketDisconnect:
        # client disconnected; fall through to finally
        pass
    finally:
        # restore session status -> active (best-effort)
        await run_in_threadpool(lambda: set_active_status(session_id))
        await manager.broadcast(json.dumps({"type": "status", "status": "active"}), session_id)
        manager.disconnect(websocket, session_id)
