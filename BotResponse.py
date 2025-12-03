import json
import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Dict, List,Tuple
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import selectinload
from BotGraph import invoke_chat_async
from ConManager import ConnectionManager
from Config import _BUDGET_OPTIONS, _MAIN_CATEGORIES, _SUB_SERVICES
from SessionUtils import get_field, set_field
from database import AsyncSessionLocal, CompanyDetails, Session as SessionModel, Message as MessageModel, SessionPhase, VerificationDetails
from CompanyFinder import FindTheComp
from FindUser import find_existing_customer
from ClientModel import client



manager = ConnectionManager()

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
    if company_det is None and phase in ("snip_q3"):
        DetName= get_field(session_obj, "username")
        Detcompany= get_field(session_obj, "q1_company")
        DetEmail= get_field(session_obj, "q1_email")
        DetRole= get_field(session_obj, "q2_role")
        company_query=f"Using latest data (2025), give a comprehensive business intelligence report on  Company: {Detcompany} Key person: {DetName} - current title: {DetRole}, email: {DetEmail}"
        print("\n\nnew triggered :",company_query)
        asyncio.create_task(FindTheComp(company_query, get_field(session_obj, "id") or getattr(session_obj, "id", None)))

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

    if company_det is not None and phase in ("snip_q4", "snip_q5",) and not company_det_used:
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


def init(app):            
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
        try:
            try:
                await set_admin_status(session_id)
            except Exception:
                await websocket.close(code=1011)
                return

            await manager.broadcast(json.dumps({"type": "status", "status": "admin"}), session_id)
            await manager.connect(websocket, session_id)
            await manager.send_history(session_id, websocket)
            while True:
                data = await websocket.receive_text()
                try:
                    parsed_data = json.loads(data)
                except json.JSONDecodeError:
                    continue

                msg_type = parsed_data.get("type")

                if msg_type == "handover":
                    try:
                        await do_handover(session_id)
                        handover_msg = {"type": "handover", "content": "Handed over to bot."}
                        await manager.broadcast(json.dumps(handover_msg), session_id)
                    except Exception:
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
                        pass
        except WebSocketDisconnect:
            pass
        finally:
            await set_active_status(session_id)
            await manager.broadcast(json.dumps({"type": "status", "status": "active"}), session_id)
            manager.disconnect(websocket, session_id)
            
    active_sessions: Dict[str, List[dict]] = {}       
    @app.websocket("/ws/eco/chat/{session_id}")
    async def eco_chat_websocket(websocket: WebSocket, session_id: str):
        await websocket.accept()
        if session_id not in active_sessions:
            active_sessions[session_id] = [
                {
                "role": "system",
                "content": "You are a friendly, professional, and super-helpful live chat assistant for an online store. Your name is Sofia.\n\nYour goals:\n- Always be warm, patient, and polite 😊\n- Help customers quickly with sizing, shipping, tracking, discounts, product questions, or checkout issues\n- Reply fast and keep answers short and clear\n- If they have items in cart, gently help them complete the purchase\n- Never sound robotic or pushy\n- Use emojis sparingly to stay friendly (👋 😊 🚚)\n- If you don't know something, say 'Let me check that for you real quick!'"
                }
            ]

        try:
            while True:
                data = await websocket.receive_text()

                try:
                    payload = json.loads(data)
                    user_text = payload["content"]

                    print(user_text)
                except json.JSONDecodeError:
                    await websocket.send_text(json.dumps({"error": "Invalid JSON"}))
                    continue

                if not user_text:
                    continue

                # Append user message to history
                active_sessions[session_id].append({"role": "user", "content": user_text})

                # Echo user message back (optional, but nice for UI)
                await websocket.send_text(json.dumps({
                    "role": "user",
                    "content": user_text
                }))


                try:
                    # Call OpenAI — non-streaming
                    response = await client.chat.completions.create(
                        model="gpt-4o-mini",          
                        messages=active_sessions[session_id],
                        temperature=0.7,
                        max_tokens=800
                    )

                    bot_reply = response.choices[0].message.content.strip()

                    # Save bot reply to session history
                    active_sessions[session_id].append({
                        "role": "assistant",
                        "content": bot_reply
                    })

                    # Send bot response
                    await websocket.send_text(json.dumps({
                        "type": "message",
                        "role": "bot",
                        "content": bot_reply
                    }))

                except Exception as e:
                    error_msg = "Sorry, the AI is temporarily unavailable. Please try again."
                    print(f"OpenAI Error: {e}")
                    await websocket.send_text(json.dumps({
                        "role": "error",
                        "content": error_msg
                    }))

        except WebSocketDisconnect:
            print(f"Session {session_id} disconnected")
        except Exception as e:
            print(f"WebSocket error: {e}")
        finally:
            del active_sessions[session_id]
            pass