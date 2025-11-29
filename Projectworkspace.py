import os
import shutil
import uuid
import json
import asyncio
import re
from typing import Any, Dict, List, Optional
from fastapi import Depends, File, UploadFile, HTTPException,status, Form
from sqlalchemy.orm import selectinload
from sqlalchemy import func,select
from sqlalchemy.ext.asyncio import AsyncSession
from Config import UPLOAD_DIR
from Schemas import GenerateRequest, ProjectCreateSchema, ProjectDetailSchema, ProjectListSchema, ProjectStatusUpdate, ServiceTemplateSchema, TaskFileSchema
from database import CompanyDetails, Project, ProjectTask, ServiceTemplate, Session as SessionModel, SessionPhase, TaskFile, TemplateTask, get_db 
from ClientModel import MODEL_NAME, client



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
            

            raw_text = getattr(resp, "output_text", None) or getattr(resp, "text", None)
            
            if raw_text:
                return raw_text
                
            parts = []
            for item in getattr(resp, "output", []) or []:
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

def init(app):
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
        
    
        await db.commit()
        await db.refresh(project)
        
        return {
            "id": project.id, 
            "status": project.status, 
            "message": f"Project status successfully updated to '{project.status}'"
        }
    
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


        tasks_raw = _extract_json_from_text(raw_text)
        
        if not tasks_raw or not isinstance(tasks_raw, list):
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Model returned non-JSON output. Try again later.")

        clean_tasks = []
        for t in tasks_raw[: (req.max_tasks or 8)]:
            if isinstance(t, dict):
                title = (t.get("title") or "").strip()
                desc = (t.get("description") or "").strip()
            elif isinstance(t, str):
                title = t.strip()
                desc = ""
            else:
                continue
                
            if not title:
                continue
            title = title[:200]
            desc = desc[:400]
            clean_tasks.append({"title": title, "description": desc})

        if not clean_tasks:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Model returned empty/invalid task list.")
            
        return {"tasks": clean_tasks}


    @app.get("/api/service-templates", response_model=List[ServiceTemplateSchema])
    async def get_service_templates(db: AsyncSession = Depends(get_db)):
        # We MUST use selectinload to fetch the related 'default_tasks'
        result = await db.execute(
            select(ServiceTemplate).options(selectinload(ServiceTemplate.default_tasks))
        )
        templates = result.scalars().all()
        return templates


    @app.post("/api/sessions/{session_id}/project")
    async def create_project_from_session(
        session_id: str, 
        payload: ProjectCreateSchema, 
        db: AsyncSession = Depends(get_db)
    ):

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
        

        if session.phase_info:
            session.phase_info.q1_company = payload.company_name
            session.phase_info.q1_email = payload.email
        new_project = Project(
            id=str(uuid.uuid4()),
            name=payload.project_name, 
            notes=payload.notes,
            status="Active",
            progress_percent=0,
            template_id=payload.template_id,
            session_id=session_id
        )
        db.add(new_project)
        await db.flush() 

    
        task_sequence = 1
        

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

