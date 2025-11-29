from datetime import datetime, timedelta
from typing import Any, Dict
from fastapi import Depends, Query
from sqlalchemy.orm import selectinload
from sqlalchemy import func, or_, select,case,and_
from sqlalchemy.ext.asyncio import AsyncSession
from database import CompanyDetails, Session as SessionModel, Message as MessageModel, SessionPhase, get_db
from collections import defaultdict
from dateutil.relativedelta import relativedelta

def calculate_growth(current: int, previous: int) -> str:

    if previous == 0:
        return "+100%" if current > 0 else "0%"
    
    change = ((current - previous) / previous) * 100

    sign = "+" if change > 0 else ""
    return f"{sign}{int(change)}%"

def init(app):
    @app.get("/api/dashboard", response_model=dict)
    async def get_dashboard(db: AsyncSession = Depends(get_db)):
        now = datetime.utcnow()
        week_start = now - timedelta(days=7)
        last_week_start = week_start - timedelta(days=7)
        last_week_end = week_start

        stmt_total = select(func.count(SessionModel.id)).where(SessionModel.created_at >= week_start)
        total_leads = (await db.execute(stmt_total)).scalar() or 0

        stmt_high = select(func.count(SessionModel.id)).where(
            and_(SessionModel.created_at >= week_start, SessionModel.interest == "high")
        )
        high_engagement = (await db.execute(stmt_high)).scalar() or 0


        stmt_active = select(func.count(SessionModel.id)).where(SessionModel.status == "active")
        active_chats = (await db.execute(stmt_active)).scalar() or 0


        stmt_req = (
            select(func.count(SessionModel.id))
            .join(SessionPhase, SessionPhase.session_id == SessionModel.id)
            .where(and_(SessionModel.created_at >= week_start, SessionPhase.q4_services.isnot(None)))
        )
        requested_services = (await db.execute(stmt_req)).scalar() or 0

        # Last Week
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

        requested_change = calculate_growth(requested_services, last_requested)

        stmt_msgs = (
            select(MessageModel.session_id, MessageModel.role, MessageModel.timestamp)
            .join(SessionModel, SessionModel.id == MessageModel.session_id)
            .where(SessionModel.created_at >= week_start)
            .order_by(MessageModel.session_id, MessageModel.timestamp)
        )
        result_msgs = await db.execute(stmt_msgs)
        # result_msgs.all() returns tuples: (session_id, role, timestamp)
        all_msg_rows = result_msgs.all()

        session_messages = defaultdict(list)
        for sid, role, ts in all_msg_rows:
            session_messages[sid].append((role, ts))

        response_times = []
        for messages in session_messages.values():
            if len(messages) < 2:
                continue
            for i in range(1, len(messages)):
                prev_role, prev_ts = messages[i - 1]
                curr_role, curr_ts = messages[i]
                
                # Calculate time only between user -> bot
                if prev_role == "user" and curr_role == "bot":
                    diff_seconds = (curr_ts - prev_ts).total_seconds()
                    response_times.append(diff_seconds)

        avg_response_seconds = sum(response_times) / len(response_times) if response_times else 0
        if avg_response_seconds < 60:
            avg_response = f"{int(avg_response_seconds)}s"
        else:
            mins = int(avg_response_seconds // 60)
            secs = int(avg_response_seconds % 60)
            avg_response = f"{mins}m {secs}s"

        # Helper to count CSV services
        def count_services_from_rows(rows):
            counts = defaultdict(int)
            for row in rows:
                # row is a tuple (q4_services,)
                serv_str = row[0] or ""
                # Split by comma, strip whitespace, ignore empty strings
                services = [s.strip() for s in serv_str.split(",") if s.strip()]
                for s in services:
                    counts[s] += 1
            return counts

        # Current Week Services
        stmt_services = (
            select(SessionPhase.q4_services)
            .join(SessionModel, SessionModel.id == SessionPhase.session_id)
            .where(and_(SessionModel.created_at >= week_start, SessionPhase.q4_services.isnot(None)))
        )
        this_week_counts = count_services_from_rows((await db.execute(stmt_services)).fetchall())
        
        # Sort top 6
        sorted_services = sorted(this_week_counts.items(), key=lambda x: x[1], reverse=True)[:6]

        # Last Week Services (for comparison)
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
        last_week_counts = count_services_from_rows((await db.execute(stmt_last_services)).fetchall())

        service_demand = []
        for name, count in sorted_services:
            last_count = last_week_counts.get(name, 0)
            # FIX: Use robust helper here too
            change_str = calculate_growth(count, last_count)
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
        duration_rows = (await db.execute(stmt_durations)).fetchall()

        duration_data = []
        total_duration_secs = 0
        valid_durations_count = 0

        for row in duration_rows:
            sid, username, company, max_ts, min_ts = row
            if max_ts and min_ts:
                duration_td = max_ts - min_ts
                secs = duration_td.total_seconds()
                duration_data.append((username, company, secs))
                total_duration_secs += secs
                valid_durations_count += 1
                
        avg_this_week_seconds = total_duration_secs / valid_durations_count if valid_durations_count else 0
        
        avg_mins = int(avg_this_week_seconds // 60)
        avg_secs = int(avg_this_week_seconds % 60)
        avg_conversation_time = f"{avg_mins:02d}m {avg_secs:02d}s"

        top_durations = sorted(duration_data, key=lambda x: x[2], reverse=True)[:7]

        deepest_conversations = []
        for username, company, total_seconds in top_durations:
            mins = int(total_seconds // 60)
            secs = int(total_seconds % 60)
            duration_str = f"{mins}m {secs:02d}s"

            change_seconds = total_seconds - avg_this_week_seconds
            abs_change = abs(change_seconds)
            c_mins = int(abs_change // 60)
            c_secs = int(abs_change % 60)
            
            change_str = f"{c_mins}m {c_secs:02d}s"
            
            if change_seconds < 0:
                change_icon = "arrow_downward"
                change_color = "text-red-600"
            else:
                change_icon = "arrow_upward"
                change_color = "text-green-600"

            deepest_conversations.append({
                "name": username or "Anonymous",
                "company": company or "N/A",
                "duration": duration_str,
                "change": change_str,
                "change_icon": change_icon,
                "change_color": change_color,
            })

        stmt_hot = (
            select(SessionModel)
            .where(SessionModel.approved == True)
            .options(
                selectinload(SessionModel.phase_info),

                selectinload(SessionModel.company_details), 
            )
            .order_by(SessionModel.updated_at.desc())
            .limit(7)
        )
        hot_sessions = (await db.execute(stmt_hot)).scalars().all()
        
        hot_leads = []
        for ses in hot_sessions:
            # safely get attributes
            interest = getattr(ses, 'interest', 'low')
            updated_at = getattr(ses, 'updated_at', None)
            phase_info = getattr(ses, 'phase_info', None)
            company_details = getattr(ses, 'company_details', None)

            priority = "High" if interest == "high" else "Medium"
            
            delta = now - updated_at if updated_at else timedelta(0)
            if delta.days >= 2:
                time_ago = f"{delta.days} days ago"
            elif delta.days == 1:
                time_ago = "Yesterday"
            elif delta.seconds >= 3600:
                time_ago = f"{delta.seconds // 3600}h ago"
            else:
                mins = delta.seconds // 60
                time_ago = f"{mins}m ago" if mins > 0 else "Just now"

            q4 = phase_info.q4_services if phase_info else None
            service = q4.split(",")[0].strip() if q4 else "N/A"
            
            # Safe name retrieval
            q1_email = phase_info.q1_email if phase_info else None
            name = ses.username or q1_email or "Unknown"
            
            # Safe company retrieval
            comp_name = phase_info.q1_company if phase_info else "N/A"

            hot_leads.append({
                "name": name,
                "priority": priority,
                "time": time_ago,
                "company": comp_name,
                "service": service,
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
            "hot_leads_count": len(hot_leads),
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