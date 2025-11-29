from io import StringIO
import math
import os
import uuid
import json
import asyncio
from datetime import datetime
from typing import Any, Dict, List, AsyncGenerator, Tuple,Union
from fastapi import Depends, Response, Query, HTTPException
from sqlalchemy.orm import selectinload
from sqlalchemy import func, or_, select, outerjoin
from sqlalchemy.ext.asyncio import AsyncSession
from Config import INACTIVITY_THRESHOLD, SESSION_CACHE
from Schemas import SessionResponse
from SessionUtils import get_field
from database import AsyncSessionLocal, Session as SessionModel, Message as MessageModel, SessionPhase, get_db 
from collections import defaultdict
import csv
from fastapi.responses import StreamingResponse
from functools import partial
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy.sql import Select


_MAX_WORKERS = min(32, (os.cpu_count() or 4) * 4)
_THREAD_POOL = ThreadPoolExecutor(max_workers=_MAX_WORKERS)


async def _safe_count(db, base_select: Select) -> int:
    stmt_no_order = base_select.order_by(None)
    count_stmt = select(func.count()).select_from(stmt_no_order.subquery())
    result = await db.execute(count_stmt)
    return int(result.scalar() or 0)

def invalidate_leads_cache():
    SESSION_CACHE.clear()  
    
async def _compute_session_data_async(sess: SessionModel, msg_rows: List[MessageModel],
                                      half_life_seconds: float, ln2: float) -> Dict[str, Any]:
    loop = asyncio.get_running_loop()
    # schedule on shared thread pool to limit concurrency
    return await loop.run_in_executor(
        _THREAD_POOL,
        partial(_compute_session_data, sess, msg_rows, half_life_seconds, ln2),
    )


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
    }


async def update_inactive_sessions(): 
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

    compute_tasks = [
        _compute_session_data_async(sess, messages_by_session.get(sess.id, []), half_life_seconds, ln2)
        for sess in sessions
    ]
    sessions_list = await asyncio.gather(*compute_tasks)

    pages = math.ceil(total / per_page) if total else 0
    return sessions_list, total, pages


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


    result = await db.stream(query)
    async for row in result:

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
        

def init(app):
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
            if 'SESSION_CACHE' in globals() and cache_key in SESSION_CACHE:
                cached = SESSION_CACHE[cache_key]
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

            if 'SESSION_CACHE' in globals():
                SESSION_CACHE[cache_key] = response

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
        SESSION_CACHE.clear()
        return {"message": "Cache refreshed successfully. Next leads request will fetch fresh data."}