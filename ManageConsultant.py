from typing import List
from fastapi import Depends, HTTPException,status, BackgroundTasks
from sqlalchemy.orm import selectinload,joinedload
import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from Schemas import ConsultantCreate, ConsultantOut, ConsultantResponse, ConsultationScheduleRequest, MessageResponse, ServiceTemplateCreate, ServiceTemplateRead, StatusResponse
from database import Consultant, Consultation, ServiceTemplate, Session as SessionModel, SessionPhase, TemplateTask, get_db 
from pydantic import BaseModel
from Config import EMAIL_PASS, EMAIL_USER



async def send_email_notification(to_email: str, subject: str, body: str):
    message = MIMEMultipart("alternative")
    message["From"] = EMAIL_USER
    message["To"] = to_email
    message["Subject"] = subject

    message.attach(MIMEText(f"Your consultation is confirmed: {subject}.", "plain"))
    message.attach(MIMEText(body, "html"))

    try:
        await aiosmtplib.send(
            message,
            hostname="smtp.gmail.com",
            port=587,
            start_tls=True,
            username=EMAIL_USER,
            password=EMAIL_PASS,
        )
    except Exception as e:
        print(f"Failed to send email to {to_email}: {e}")
        


async def send_whatsapp_notification(mobile: str, details: dict):
    """Simulates sending a WhatsApp notification."""
    print(f"--- WA Notification Sent to {mobile} ---")
    print(f"Details: {details}")
    return {"status": "success", "platform": "whatsapp"}


def init(app):
    @app.post("/schedule_consultant", status_code=status.HTTP_201_CREATED)
    async def schedule_consultant(request: ConsultationScheduleRequest,background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
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


        except HTTPException:
            raise
        except Exception as e:
            print(f"Database error during fetch: {e}") 
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error retrieving session data.")

        new_consultation = Consultation(
            schedule_time=request.schedule_time,
            status="Pending",
            # Store the ID
            consultant_id=consultant_id, 
            session_id=session_id

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
        background_tasks.add_task(send_email_notification,to_email=client_email,subject= email_subject,body= email_body)
        return {
            "message": "Consultation scheduled successfully",
            "consultation_id": new_consultation.id,
            "schedule_time": schedule_time_str,
            "consultant_name": consultant_display_name 
        }

    @app.get("/consultants", response_model=List[ConsultantResponse])
    async def get_consultants(db: AsyncSession = Depends(get_db)):
        result = await db.execute(
            select(Consultant).order_by(Consultant.tier.desc(), Consultant.name)
        )
        consultants = result.scalars().all()
        
        return consultants

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
        

    @app.get("/api/templates/", response_model=List[ServiceTemplateRead])
    async def get_all_templates(db: AsyncSession = Depends(get_db)):

        stmt = select(ServiceTemplate).options(selectinload(ServiceTemplate.default_tasks))
        
        result = await db.execute(stmt)
        templates = result.scalars().all()
        return templates

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