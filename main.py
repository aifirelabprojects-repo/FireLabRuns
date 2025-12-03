import os
from fastapi import Depends, FastAPI, Request
import httpx
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from Config import  HTTPX_MAX_CONNECTIONS, UPLOAD_DIR, UPSTREAM_TIMEOUT
from database import get_db, init_db 
from KnowledgeBase import cfg
import VerifyEmail, Projectworkspace, ManageConsultant, Insights, BotResponse, VerifyUser, SessionAndLeadView, DeepResearch,DashboardAndAnalyticsView

os.makedirs("data", exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)
app = FastAPI(title="Business Chatbot API")

templates = Jinja2Templates(directory="templates")

app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/static", StaticFiles(directory="static"), name="static")

VerifyEmail.init(app)
Projectworkspace.init(app)
ManageConsultant.init(app)
Insights.init(app)
BotResponse.init(app)
VerifyUser.init(app)
SessionAndLeadView.init(app)
DeepResearch.init(app)
DashboardAndAnalyticsView.init(app)

@app.api_route("/health", methods=["GET", "HEAD"])
async def health_check():
    return {"status": "ok"}

@app.on_event("startup")
async def startup():
    await init_db()
    limits = httpx.Limits(max_connections=HTTPX_MAX_CONNECTIONS,
                          max_keepalive_connections=HTTPX_MAX_CONNECTIONS)
    VerifyEmail.httpx_client = httpx.AsyncClient(
        timeout=httpx.Timeout(UPSTREAM_TIMEOUT),
        limits=limits
    )


@app.on_event("shutdown")
async def shutdown():
    cfg.stop()
    if VerifyEmail.httpx_client:
        await VerifyEmail.httpx_client.aclose()

@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/admin/")
async def admin_home(request: Request, db = Depends(get_db)):
    context = {
        "request": request,
    }
    return templates.TemplateResponse("admin.html", context)


    

