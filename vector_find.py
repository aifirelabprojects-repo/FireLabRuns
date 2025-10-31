import math
import os
import shutil
import uuid
import json
import sqlite3
import asyncio
import re
from datetime import datetime, timedelta
from typing import Any, Dict, Set, Optional
from fastapi import FastAPI, File, Request, UploadFile, Form, WebSocket, WebSocketDisconnect, Query, HTTPException
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
#langchain importss
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_community.chat_message_histories import SQLChatMessageHistory
from sqlalchemy import create_engine
load_dotenv()

OPENROUTER_API_KEY = os.getenv("key5")

SITE_NAME = "Business Chatbot"

DEFAULT_PDF = "data/tester.pdf"
VECTORSTORE_PATH = "vectorstore/index"
INACTIVITY_THRESHOLD = timedelta(minutes=5)  

os.makedirs("data", exist_ok=True)
os.makedirs("vectorstore", exist_ok=True)

app = FastAPI(title="Business Chatbot API")

@app.api_route("/health", methods=["GET", "HEAD"])
async def health_check():
    return {"status": "ok"}

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


conn = sqlite3.connect("chatbot.db", check_same_thread=False)
cur = conn.cursor()
def init_db():
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'active',
            interest TEXT DEFAULT 'low',
            mood TEXT DEFAULT 'neutral',
            details TEXT DEFAULT '{}',
            phase TEXT DEFAULT 'initial',
            routing TEXT DEFAULT 'none'
        )
    """)
    # Add columns if missing (SQLite ALTER)
    try:
        cur.execute("ALTER TABLE sessions ADD COLUMN phase TEXT DEFAULT 'initial'")
        cur.execute("ALTER TABLE sessions ADD COLUMN routing TEXT DEFAULT 'none'")
    except sqlite3.OperationalError:
        pass  # Columns exist
    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            interest TEXT,
            mood TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions (id)
        )
    """)
    cur.execute("PRAGMA journal_mode = WAL;")
    cur.execute("PRAGMA synchronous = NORMAL;")
    conn.commit()

@app.on_event("startup")
def startup_event():
    init_db()


client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY
)

_embedding_model = None

def get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = HuggingFaceEmbeddings(
            model_name="sentence-transformers/all-MiniLM-L6-v2",
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": False}
        )
    return _embedding_model

vectorstore = None

def get_vectorstore():
    global vectorstore
    if vectorstore is None:
        ensure_vectorstore_ready()
        if os.path.exists(VECTORSTORE_PATH):
            vectorstore = load_vectorstore()
        else:
            vectorstore = FAISS.from_documents([], get_embedding_model())
    return vectorstore

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
                cur.execute("""
                    SELECT role, content, strftime('%Y-%m-%dT%H:%M:%SZ', timestamp) as ts, interest, mood
                    FROM messages WHERE session_id = ? ORDER BY timestamp ASC
                """, (session_id,))
                rows = cur.fetchall()
                messages = [{"role": row[0], "content": row[1], "timestamp": row[2], "interest": row[3], "mood": row[4]} for row in rows]
                return json.dumps({"type": "history", "messages": messages})
            except Exception:
                return json.dumps({"type": "history", "messages": []})

        history_json = await run_in_threadpool(fetch_history)
        await websocket.send_text(history_json)

manager = ConnectionManager()


def extract_text_from_pdf(pdf_path: str) -> str:
    pdf = PdfReader(pdf_path)
    text = ""
    for page in pdf.pages:
        text += page.extract_text() or ""
    return text

def create_vectorstore(text: str, store_path: str = VECTORSTORE_PATH):
    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    docs = [Document(page_content=chunk) for chunk in splitter.split_text(text)]
    vectorstore = FAISS.from_documents(docs, get_embedding_model())
    vectorstore.save_local(store_path)
    return vectorstore

def load_vectorstore(store_path: str = VECTORSTORE_PATH):
    return FAISS.load_local(store_path, get_embedding_model(), allow_dangerous_deserialization=True)

def ensure_vectorstore_ready():
    if not os.path.exists(VECTORSTORE_PATH):
        if os.path.exists(DEFAULT_PDF):
            text = extract_text_from_pdf(DEFAULT_PDF)
            create_vectorstore(text)
        else:
            # Fallback: Create a basic vectorstore with hardcoded data if PDF missing
            hardcoded_text = """MAIN_CATEGORIES = [
            "Market Entry & Business Setup",
            "Strategic Growth & Management",
            "Financial Services & Compliance",
            "Industrial & Specialized Operations",
            "Legal & Business Protection"
            ]

            SUB_SERVICES = {
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
            }

            TIMELINE_OPTIONS = [
            {"text": "Within 1 month", "value": "1_month"},
            {"text": "1-3 months", "value": "1_3_months"},
            {"text": "3-6 months", "value": "3_6_months"},
            {"text": "Just researching", "value": "researching"}
            ]

            BUDGET_RANGES = "Our packages typically range from 35,000 to 150,000 SAR."""
            create_vectorstore(hardcoded_text)

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

_BUDGET_RANGES = """BUDGET_RANGES = "Our packages typically range from 35,000 to 150,000 SAR."""
           
def get_vectorstore():
    ensure_vectorstore_ready()
    return load_vectorstore()

def query_vectorstore(question: str, k: int = 3) -> str:
    vectorstore = get_vectorstore()
    docs = vectorstore.similarity_search(question, k=k)
    return "\n".join([d.page_content for d in docs])

memory_engine = create_engine("sqlite:///chat_memory.db", connect_args={"check_same_thread": False})

# LLM wrapper (reuse your Qwen config)
llm = ChatOpenAI(
    model="qwen/qwen-2.5-coder-32b-instruct:free",
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1",
    temperature=0.2,
    max_tokens=800,
)

def _get_memory(session_id: str):
    return SQLChatMessageHistory(session_id=session_id, connection=memory_engine)

# Enhanced prompt template to include SNIP flow guidance (made more robust: added explicit JSON enforcement, fallback handling, and clearer phase instructions)
prompt = ChatPromptTemplate.from_messages([
    ("system", """You are Sofia, a dedicated, empathetic guide at Analytix, the government-approved partner for business expansion in Saudi Arabia. Speak in a natural, human-like way: use contractions (you're, we'll), occasional emojis, empathy ("That's exciting!"), and encouragement ("Great choice!"). Position Analytix as a trusted partner, not a salesperson. Connect services to benefits (e.g., "This helps you secure government contracts quickly").

        Follow this strict SNIP qualification flow for new customers. Track state in 'phase'. Use open-ended questions for rapport. Personalize using company data from context.

        Phases:
        1. Initial Engagement: Greet and identify new/existing. If existing, ask company/WhatsApp code, simulate Odoo fetch (use context), route to CRE.
        - Greeting: "Welcome! I am Sofia, your dedicated guide at Analytix. We are the government-approved partner for business expansion in Saudi Arabia, trusted by the Ministry to fast-track market entry. Are you looking to setup a new business here, or are you an existing client with a question?"
        - If new: Proceed to Phase 2.
        - If existing: "What's your company name or WhatsApp group code?" -> Simulate fetch -> Answer or route.

        2. SNIP Qualification (New Customers):
        - Q1 (Size - Company): "Great! Could you please tell me your company name and email id?" -> Enrich: Simulate lookup (use context for industry/size/location). Personalize response.
        - Q2 (Size - Role): "And what is your role in the company? (e.g., Founder, CEO, Managing Director)" -> Rapport: "A Founder! What's the most exciting part about bringing your business to Saudi Arabia?"
        - Q2a (Upsell if personal email): If @gmail.com etc., "I see you're using a personal email. To ensure secure docs, may I ask for your business email? As incentive, verified business emails get 20% off advisory services." -> Tag "High-Intent" if provided.
        - Q3 (Need - Category): "To fast-track, which core areas? (Clickable list)" -> Show categories as clickable options. Categories from vectorstore/context. Allow multi-select.
        - Q4 (Interest - Services): Based on Q3, "Which specific services?" -> Dynamic clickable sub-list (from vectorstore). Log selections.
        - Q5 (Pain - Activity): "What is the primary business activity to license? (e.g., IT services, general trading)"
        - Q6 (Implication - Timeline): "How soon to start? (Within 1 month, 1-3 months, 3-6 months)" -> Clickable from vectorstore.
        - Q7 (Budget): "Estimated budget for incorporation/first-year? (35k-150k SAR)" -> Connect to packages from vectorstore.
        - After Q4: "Many clients use [services] to [benefit, e.g., reduce costs 30%]."
        - Advance phase after each Q (e.g., 'snip_q1' -> 'snip_q2').

        3. Routing:
        - High-Value (Within 1-3 months, high budget, business email, existing co.): "Assigning senior consultant in [industry] within 1 hour." -> routing: "high_value"
        - Nurturing (3-6 months, low budget): "Sending guide/case studies. Consultant follow-up." -> routing: "nurturing"
        - Human Request/Unclear: "Connecting to expert." -> routing: "cre"
        - Log all Qs to lead_data. Simulate Odoo log.

        For clickable options: ALWAYS include the full "options" array in your JSON response if relevant to the phase. Format: "options": [{{ "text": "Option Text", "value": "unique_value", "type": "select" }}]. If multi-select, mention "You can select more than one." Weave options into your natural answer.

        Handle objections empathetically.

        CRITICAL: Respond ONLY with valid JSON. No extra text. Use current_details for state. Update lead_data. Merge details. Assess interest/mood. If flow complete, set "routing".

        Format EXACTLY:
        {{
        "answer": "Your natural response here.",
        "options": [] or [{{ "text": "...", "value": "...", "type": "multi_select" }}],
        "phase": "snip_q2",
        "lead_data": {{ "q1_company": "ABC Corp", "q3_categories": ["Market Entry & Business Setup"] }},
        "routing": "",
        "analysis": {{
            "interest": "high",
            "mood": "excited",
            "details": {{ "name": "", "email": "", "phone": "", "company": "" }}
        }}
        }}"""),
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



def simulate_company_enrichment(company_name: str, question: str) -> str:
    """Simulate data enrichment via vectorstore or placeholder. Enhance with real lookup if available."""
    # Use vectorstore for company-specific data
    context = query_vectorstore(f"Company info for {company_name}: industry, size, location")
    if "industry" in context.lower() or "size" in context.lower():
        # Placeholder parsing (enhance as needed)
        enrichment = f"{context}"  # Use full relevant context
    else:
        enrichment = "No prior data found—exciting new venture!"
    return enrichment

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
        enrichment = simulate_company_enrichment(company, question)
    else:
        enrichment = ""

    # Dynamic options: Now primarily from vectorstore for modularity; fallback to hardcoded if query fails
    options = []
    try:
        if phase == "snip_q3":
            cat_context = query_vectorstore("MAIN_CATEGORIES for services")
            cats = [
                line.strip().strip('"')
                for line in cat_context.split("\n")
                if line.strip().startswith('"') and line.strip().endswith('"')
            ] or MAIN_CATEGORIES

            options = [
                        {
                            "text": cat,
                            "value": cat.lower()
                                    .replace(" & ", "_")
                                    .replace(" ", "_"),
                            "type": "multi_select"
                        }
                        for cat in cats
                    ]
        elif phase == "snip_q4":
            selected_cats = lead_data.get("q3_categories", [])
            options = []

            for cat in selected_cats:
                sub_context = query_vectorstore(f"SUB_SERVICES for {cat}")

                # ---- extract {"text": "...", "value": "..."} pairs -----------------
                import re
                subs = re.findall(
                    r'{"text":\s*"([^"]+)",\s*"value":\s*"([^"]+)"}',
                    sub_context
                )

                # If the regex finds nothing, fall back to the hard-coded dict
                if subs:
                    options.extend(
                        {"text": t, "value": v, "type": "multi_select"} for t, v in subs
                    )
                else:
                    options.extend(SUB_SERVICES.get(cat, []))

                # Normalise the list so the frontend always receives the same shape
                options = [
                    {"text": opt["text"], "value": opt["value"], "type": "multi_select"}
                    for opt in options
                ]
        elif phase == "snip_q6":
            # timeline_context = query_vectorstore("TIMELINE_OPTIONS")
            timeline_context = _TIMELINE_OPTIONS

            import re
            timelines = re.findall(
                r'{"text":\s*"([^"]+)",\s*"value":\s*"([^"]+)"}',
                timeline_context
            ) or TIMELINE_OPTIONS

            options = [
                {"text": t, "value": v, "type": "select"} for t, v in timelines
            ]
    except Exception:
        # Fallback to hardcoded
        if phase == "snip_q3":
            options = [{"text": cat, "value": cat.lower().replace(" & ", "_").replace(" ", "_"), "type": "multi_select"} for cat in MAIN_CATEGORIES]
        elif phase == "snip_q4":
            selected_cats = lead_data.get("q3_categories", [])
            for cat in selected_cats:
                options.extend(SUB_SERVICES.get(cat, []))
            if options:
                options = [{"text": opt["text"], "value": opt["value"], "type": "multi_select"} for opt in options]
        elif phase == "snip_q6":
            options = [{"text": opt["text"], "value": opt["value"], "type": "select"} for opt in TIMELINE_OPTIONS]

    # Phase-specific context to reduce length and improve focus (replaces general query_vectorstore(question))
    context_parts = []
    if enrichment:
        context_parts.append(f"Enrichment: {enrichment}")

    try:
        if phase == "snip_q3":
            # Q3: Focus on main categories for services/benefits
            cat_context = query_vectorstore("MAIN_CATEGORIES for services")
            context_parts.append(cat_context)
        elif phase == "snip_q4":
            # Q4: Focus on selected sub-services and related benefits
            main_cats = query_vectorstore("MAIN_CATEGORIES for services")
            context_parts.append(main_cats)
            selected_cats = lead_data.get("q3_categories", [])
            for cat in selected_cats:
                sub_context = query_vectorstore(f"SUB_SERVICES for {cat}")
                context_parts.append(sub_context)
        elif phase == "snip_q5":
            # Q5: Focus on business activities (assume a vectorstore query for activities/pain points; fallback to general if not)
            activity_context = query_vectorstore("BUSINESS_ACTIVITIES for licensing and pain points")
            if activity_context:
                context_parts.append(activity_context)
            else:
                context_parts.append(query_vectorstore("general business activities"))
        elif phase == "snip_q6":
            # Q6: Focus on timeline options and implications
            timeline_context = query_vectorstore("TIMELINE_OPTIONS")
            context_parts.append(timeline_context)
        elif phase == "snip_q7":
            # Q7: Focus on budget ranges and packages
            budget_info = query_vectorstore("BUDGET_RANGES")
            if budget_info:
                context_parts.append(f"Budget info: {budget_info}")
            package_context = query_vectorstore("INCORPORATION_PACKAGES")
            if package_context:
                context_parts.append(package_context)
        else:
            # Fallback for other phases: general query, but limited
            general_context = query_vectorstore(question)
            context_parts.append(general_context)
    except Exception:
        # Fallback to minimal context if vectorstore fails
        pass

    context = "\n".join(context_parts)

