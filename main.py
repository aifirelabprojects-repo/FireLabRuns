import math
import os
import shutil
import uuid
import json
import sqlite3
import asyncio
import re
from datetime import datetime, timedelta
from typing import Dict, Set, Optional
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

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

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
            details TEXT DEFAULT '{}'
        )
    """)
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


def extract_text_from_pdf(pdf_path):
    pdf = PdfReader(pdf_path)
    text = ""
    for page in pdf.pages:
        text += page.extract_text() or ""
    return text

def create_vectorstore(text, store_path=VECTORSTORE_PATH):
    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)  # Reduced from 800/100
    docs = [Document(page_content=chunk) for chunk in splitter.split_text(text)]
    vectorstore = FAISS.from_documents(docs, get_embedding_model())  # Use lazy loader
    vectorstore.save_local(store_path)
    return vectorstore

def load_vectorstore(store_path=VECTORSTORE_PATH):
    return FAISS.load_local(store_path, get_embedding_model(), allow_dangerous_deserialization=True)

def ensure_vectorstore_ready():
    if not os.path.exists(VECTORSTORE_PATH):
        if os.path.exists(DEFAULT_PDF):
            text = extract_text_from_pdf(DEFAULT_PDF)
            create_vectorstore(text)

def query_vectorstore(question, k=3):
    vectorstore = get_vectorstore()
    docs = vectorstore.similarity_search(question, k=k)
    return "\n".join([d.page_content for d in docs])

def get_bot_response(question: str, current_details: dict = None):
    if current_details is None:
        current_details = {}

    context = query_vectorstore(question)
    prompt = f"""
        You are a professional business chatbot for {SITE_NAME}.

        You must:
        1. Answer the user's question clearly using the given context.
        2. Extract any **new** personal details (name, email, phone, company, etc.) mentioned by the user.
        3. Merge the new details with the current known details below. Do not overwrite known values unless explicitly contradicted by new information. If no new details for a field, keep the existing value.
        4. Assess their **interest level** (high / medium / low) and **mood** (e.g., curious, polite, frustrated, confused, excited).
        5. Return everything strictly as a JSON object, nothing else.

        Current known details: {json.dumps(current_details)}

        Format:
        {{
        "answer": "<your message to the user>",
        "analysis": {{
            "interest": "<high|medium|low>",
            "mood": "<mood>",
            "details": {{
                "name": "<name or unknown>",
                "email": "<email or unknown>",
                "phone": "<phone or unknown>",
                "company": "<company or unknown>"
            }}
        }}
        }}

        Context:
        {context}

        User Question:
        {question}
    """

    try:
        completion = client.chat.completions.create(
            model="qwen/qwen-2.5-coder-32b-instruct:free",
            messages=[
                {"role": "system", "content": "You are a JSON-only business assistant. Always respond in valid JSON format."},
                {"role": "user", "content": prompt}
            ],
        )
        raw_output = completion.choices[0].message.content
        try:
            response_data = json.loads(raw_output)
        except json.JSONDecodeError:
            json_part = re.search(r'\{.*\}', raw_output, re.DOTALL)
            if json_part:
                response_data = json.loads(json_part.group(0))
            else:
                response_data = {
                    "answer": raw_output,
                    "analysis": {
                        "interest": "unknown",
                        "mood": "unknown",
                        "details": {
                            "name": "unknown",
                            "email": "unknown",
                            "phone": "unknown",
                            "company": "unknown"
                        }
                    }
                }
        return response_data

    except AuthenticationError as e:
        print(f"Authentication Error: {e}")
        return {
            "answer": "Authentication failed — check your API key or account permissions.",
            "analysis": {"interest": "unknown", "mood": "frustrated", "details": {}}
        }

    except RateLimitError as e:
        print(f"Rate Limit Error: {e}")
        return {
            "answer": "The system is receiving too many requests. Please try again later.",
            "analysis": {"interest": "high", "mood": "impatient", "details": {}}
        }

    except APIError as e:
        print(f"API Error: {e}")
        return {
            "answer": f"API Error: {str(e)}",
            "analysis": {"interest": "medium", "mood": "confused", "details": {}}
        }

    except Exception as e:
        print(f"Unexpected Error: {e}")
        return {
            "answer": "An unexpected error occurred while fetching the bot response.",
            "analysis": {"interest": "unknown", "mood": "confused", "details": {}}
        }

def insert_user_message(session_id: str, content: str):
    try:
        ts = datetime.utcnow().isoformat()
        cur.execute(
            "INSERT INTO messages (session_id, role, content, timestamp, interest, mood) VALUES (?, 'user', ?, ?, NULL, NULL)",
            (session_id, content, ts)
        )
        
        cur.execute("SELECT status FROM sessions WHERE id = ?", (session_id,))
        status_row = cur.fetchone()
        current_status = status_row[0] if status_row else 'active'
        
        if current_status != 'admin':
            cur.execute("""
                UPDATE sessions SET updated_at = CURRENT_TIMESTAMP, status = 'active' 
                WHERE id = ?
            """, (session_id,))
        else:
            cur.execute("""
                UPDATE sessions SET updated_at = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (session_id,))
        conn.commit()
        return ts, current_status
    except Exception as e:
        if conn.in_transaction:
            conn.rollback()
        raise e

def handle_bot_response(session_id: str, question: str):
    try:
        cur.execute("SELECT details FROM sessions WHERE id = ?", (session_id,))
        details_row = cur.fetchone()
        current_details = json.loads(details_row[0]) if details_row and details_row[0] else {}
        response_data = get_bot_response(question, current_details)
        answer = response_data["answer"]
        analysis = response_data["analysis"]
        details_json = json.dumps(analysis["details"])
        bot_ts = datetime.utcnow().isoformat()
        cur.execute(
            "INSERT INTO messages (session_id, role, content, timestamp, interest, mood) VALUES (?, 'bot', ?, ?, ?, ?)",
            (session_id, answer, bot_ts, analysis["interest"], analysis["mood"])
        )
        cur.execute("""
            UPDATE sessions SET 
            details = ?, interest = ?, mood = ?, updated_at = CURRENT_TIMESTAMP, status = 'active'
            WHERE id = ?
        """, (details_json, analysis["interest"], analysis["mood"], session_id))
        conn.commit()
        return {"answer": answer, "analysis": analysis, "bot_ts": bot_ts}
    except Exception as e:
        if conn.in_transaction:
            conn.rollback()
        raise e

def update_inactive_sessions():
    threshold_time = (datetime.utcnow() - INACTIVITY_THRESHOLD).strftime('%Y-%m-%d %H:%M:%S')
    try:
        cur.execute("""
            UPDATE sessions 
            SET status = 'inactive' 
            WHERE status = 'active' 
            AND updated_at < ?
        """, (threshold_time,))
        conn.commit()
    except Exception as e:
        if conn.in_transaction:
            conn.rollback()
        raise e

# =main

@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/api/sessions/")
def create_session():
    try:
        session_id = str(uuid.uuid4())
        cur.execute("INSERT INTO sessions (id) VALUES (?)", (session_id,))
        conn.commit()
        return {"session_id": session_id}
    except Exception as e:
        if conn.in_transaction:
            conn.rollback()
        raise HTTPException(status_code=500, detail="Failed to create session")

@app.get("/api/sessions/")
def get_sessions(active: bool = Query(False)):
    try:
        update_inactive_sessions()

        if active:
            cur.execute("""
                SELECT id, created_at, status, interest, mood, details 
                FROM sessions WHERE status = 'active' ORDER BY created_at DESC
            """)
        else:
            cur.execute("""
                SELECT id, created_at, status, interest, mood, details 
                FROM sessions ORDER BY created_at DESC
            """)
        rows = cur.fetchall()
        sessions_list = []

        # helper maps
        interest_score = {"low": 0.0, "medium": 1.0, "high": 2.0}
        score_to_interest = lambda s: "low" if s < 0.5 else ("medium" if s < 1.5 else "high")

        for row in rows:
            id_, created, status, sess_interest, sess_mood, details_json = row
            try:
                details = json.loads(details_json)
            except:
                details = {}
            name = details.get("name", "Unknown")
            usr_email = details.get("email", "Unknown")
            usr_phone = details.get("phone", "Unknown")
            usr_company = details.get("company", "Unknown")

            cur.execute("SELECT content FROM messages WHERE session_id = ? ORDER BY timestamp DESC LIMIT 1", (id_,))
            last_row = cur.fetchone()
            last_msg = (last_row[0][:50] + "..." if last_row and len(last_row[0]) > 50 else last_row[0] if last_row else "")

           
            cur.execute("SELECT role, interest, mood, timestamp FROM messages WHERE session_id = ? ORDER BY timestamp ASC", (id_,))
            msg_rows = cur.fetchall()

            overall_interest_label = sess_interest  
            overall_mood_label = sess_mood 

            if msg_rows:
             
                half_life_seconds = 3 * 24 * 3600 
                ln2 = math.log(2)

              
                parsed = []
                for role, interest_val, mood_val, ts in msg_rows:
                 
                    try:
                        ts_dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                    except Exception:
                    
                        try:
                            ts_dt = datetime.fromisoformat(ts)
                        except Exception:
                            ts_dt = None
                    parsed.append((role, (interest_val or "").lower(), (mood_val or ""), ts_dt))

                latest_ts = None
                for _, _, _, ts_dt in parsed:
                    if ts_dt:
                        latest_ts = ts_dt
                if latest_ts is None:
                    latest_ts = datetime.utcnow()

                weighted_sum = 0.0
                weight_total = 0.0
                mood_weights = {}
                for role, interest_val, mood_val, ts_dt in parsed:
                   
                    delta = (latest_ts - ts_dt).total_seconds() if ts_dt else 0.0
                    weight = math.exp(-ln2 * (delta / half_life_seconds))

                 
                    if interest_val and interest_val in interest_score:
                        s = interest_score[interest_val]
                        weighted_sum += s * weight
                        weight_total += weight

                    if role and role.lower() == "user" and mood_val:
                        mood_key = mood_val.lower()
                        mood_weights[mood_key] = mood_weights.get(mood_key, 0.0) + weight

                if weight_total > 0:
                    avg_score = weighted_sum / weight_total
                    overall_interest_label = score_to_interest(avg_score)

                if mood_weights:
                    # pick the mood with highest weighted score
                    overall_mood_label = max(mood_weights.items(), key=lambda kv: kv[1])[0]

            sessions_list.append({
                "id": id_,
                "created_at": created,
                "status": status,
                "interest": overall_interest_label,
                "mood": overall_mood_label,
                "name": name,
                "usr_email": usr_email,
                "usr_phone": usr_phone,
                "usr_company": usr_company,
                "last_message": last_msg
            })
        return sessions_list
    except Exception as e:
        return []

@app.get("/api/sessions/{session_id}/metrics")
def session_metrics(session_id: str):
    try:
        cur.execute("SELECT role, interest, mood, timestamp FROM messages WHERE session_id = ? ORDER BY timestamp ASC", (session_id,))
        rows = cur.fetchall()

        timeline = []
        # maps date -> {interest_sum, interest_count, mood_counts}
        daily = {}
        interest_score = {"low": 0.0, "medium": 1.0, "high": 2.0}

        for role, interest_val, mood_val, ts in rows:
            try:
                ts_dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            except Exception:
                try:
                    ts_dt = datetime.fromisoformat(ts)
                except Exception:
                    ts_dt = None

            # per-message numeric interest score (or null)
            iscore = None
            if interest_val and interest_val.lower() in interest_score:
                iscore = interest_score[interest_val.lower()]

            timeline.append({
                "timestamp": ts,  # raw string
                "role": role,
                "interest": (interest_val or None),
                "interest_score": iscore,
                "mood": (mood_val or None)
            })

            # monthly/daily aggregation - we will aggregate by date
            if ts_dt:
                date_key = ts_dt.strftime("%Y-%m-%d")
                if date_key not in daily:
                    daily[date_key] = {"interest_sum": 0.0, "interest_count": 0, "mood_counts": {}}
                if iscore is not None:
                    daily[date_key]["interest_sum"] += iscore
                    daily[date_key]["interest_count"] += 1
                # only user mood messages included
                if role and role.lower() == "user" and mood_val:
                    mk = mood_val.lower()
                    daily[date_key]["mood_counts"][mk] = daily[date_key]["mood_counts"].get(mk, 0) + 1

        # transform daily dict to list with average interest
        daily_list = []
        for d in sorted(daily.keys()):
            entry = daily[d]
            avg_interest = (entry["interest_sum"] / entry["interest_count"]) if entry["interest_count"] else None
            daily_list.append({
                "date": d,
                "avg_interest": avg_interest,
                "mood_counts": entry["mood_counts"]
            })

        return {
            "session_id": session_id,
            "timeline": timeline,
            "daily": daily_list
        }
    except Exception:
        return {"session_id": session_id, "timeline": [], "daily": []}

@app.post("/upload_pdf/")
def upload_pdf(file: UploadFile = File(...)):
    try:
        file_path = f"data/{file.filename}"
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        text = extract_text_from_pdf(file_path)
        global vectorstore
        vectorstore = create_vectorstore(text)
        return {"message": f"PDF '{file.filename}' uploaded and processed successfully!"}
    except Exception as e:
        if 'file_path' in locals() and os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=500, detail=f"Failed to process PDF: {str(e)}")

# Admin
@app.get("/admin/")
async def admin_home(request: Request):
    try:
      
        cur.execute("SELECT COUNT(*) FROM sessions")
        total_sessions = cur.fetchone()[0]

     
        cur.execute("""
            SELECT s.id, COUNT(m.id) as message_count
            FROM sessions s
            LEFT JOIN messages m ON s.id = m.session_id
            GROUP BY s.id
            ORDER BY message_count DESC
            LIMIT 1
        """)
        highest_conversation = cur.fetchone()
        if highest_conversation:
            highest_session_id, highest_message_count = highest_conversation
        else:
            highest_session_id, highest_message_count = None, 0

        cur.execute("SELECT COUNT(*) FROM messages")
        total_messages = cur.fetchone()[0]

        cur.execute("""
            SELECT AVG(message_count) 
            FROM (
                SELECT COUNT(m.id) as message_count
                FROM sessions s
                LEFT JOIN messages m ON s.id = m.session_id
                GROUP BY s.id
            )
        """)
        avg_messages_per_session = cur.fetchone()[0] or 0
        avg_messages_per_session = int(round(avg_messages_per_session))


     
        context = {
            "request": request,
            "total_sessions": total_sessions,
            "highest_message_count": highest_message_count,
            "total_messages": total_messages,
            "avg_messages_per_session": round(avg_messages_per_session, 2),
        }
        return templates.TemplateResponse("admin.html", context)
    except Exception:
        context = {
            "request": request,
            "total_sessions": 0,
            "highest_message_count": 0,
            "total_messages": 0,
            "avg_messages_per_session": 0,
        }
        return templates.TemplateResponse("admin.html", context)

@app.get("/admin/session/{session_id}")
async def admin_session(request: Request, session_id: str, mode: str = Query("view")):
    return templates.TemplateResponse("session.html", {
        "request": request, 
        "session_id": session_id, 
        "mode": mode,
        "site_name": SITE_NAME
    })


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
                        answer = bot_data["answer"]
                        analysis = bot_data["analysis"]
                        bot_ts = bot_data["bot_ts"]
                        bot_msg = {
                            "type": "message",
                            "role": "bot",
                            "content": answer,
                            "timestamp": bot_ts,
                            "interest": analysis["interest"],
                            "mood": analysis["mood"]
                        }
                        await manager.broadcast(json.dumps(bot_msg), session_id)
                    except Exception as e:
                        print(e)
                        bot_error = {"type": "message","role": "error", "content": "Bot is temporarily unavailable. Please try again."}
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
    def set_admin_status(session_id: str):
        try:
            cur.execute("UPDATE sessions SET status = 'admin' WHERE id = ?", (session_id,))
            conn.commit()
        except Exception as e:
            if conn.in_transaction:
                conn.rollback()
            raise e

    try:
        await run_in_threadpool(lambda: set_admin_status(session_id))
    except Exception:
        await websocket.close(code=1011)
        return

    await manager.broadcast(json.dumps({"type": "status", "status": "admin"}), session_id)
    await manager.connect(websocket, session_id)
    await manager.send_history(session_id, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                parsed_data = json.loads(data)
            except json.JSONDecodeError:
                continue
            if parsed_data.get("type") == "handover":
                def do_handover(session_id: str):
                    try:
                        cur.execute("UPDATE sessions SET status = 'active' WHERE id = ?", (session_id,))
                        conn.commit()
                    except Exception as e:
                        if conn.in_transaction:
                            conn.rollback()
                        raise e

                try:
                    await run_in_threadpool(lambda: do_handover(session_id))
                    handover_msg = {"type": "handover", "content": "Handed over to bot."}
                    await manager.broadcast(json.dumps(handover_msg), session_id)
                except Exception:
                    pass
            elif parsed_data.get("type") == "message":
                content = parsed_data["content"]
                def insert_admin_message(session_id: str, content: str):
                    try:
                        ts = datetime.utcnow().isoformat()
                        cur.execute(
                            "INSERT INTO messages (session_id, role, content, timestamp, interest, mood) VALUES (?, 'admin', ?, ?, NULL, NULL)",
                            (session_id, content, ts)
                        )
                        cur.execute("""
                            UPDATE sessions SET updated_at = CURRENT_TIMESTAMP 
                            WHERE id = ?
                        """, (session_id,))
                        conn.commit()
                        return ts
                    except Exception as e:
                        if conn.in_transaction:
                            conn.rollback()
                        raise e

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
                    pass  
    except WebSocketDisconnect:
        pass
    finally:
        def set_active_status(session_id: str):
            try:
                cur.execute("UPDATE sessions SET status = 'active' WHERE id = ?", (session_id,))
                conn.commit()
            except Exception:
                pass  

        await run_in_threadpool(lambda: set_active_status(session_id))
        await manager.broadcast(json.dumps({"type": "status", "status": "active"}), session_id)
        manager.disconnect(websocket, session_id)