from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class SessionResponse(BaseModel):
    message: str
    id: str
    approved: bool

    class Config:
        from_attributes = True  

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


class ConsultantResponse(BaseModel):
    id: str
    name: str
    tier: str
    phone: Optional[str] = None 

    class Config:
        from_attributes = True 


class ConsultationScheduleRequest(BaseModel):
    session_id: str 
    schedule_time: datetime 
    consultant_id: str 
    consultant_name_display: str 

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

class ResearchPayload(BaseModel):
    id: str
    name: str
    email: str
    company: str
    email_domain: Optional[str] = None
    additional_info: Optional[str] = None


class InsightUpdate(BaseModel):
    guidelines: str = ""
    tones: str = ""
    name: str = ""
    banned: str = ""
    company_profile: str = ""
    main_categories: str = ""
    sub_services: str = ""
    timeline_options: str = ""
    budget_options: str = ""


    
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
    