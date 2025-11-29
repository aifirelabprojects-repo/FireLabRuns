from datetime import timedelta
import os
from KnowledgeBase import cfg
from cachetools import TTLCache  
from dotenv import load_dotenv

load_dotenv()

_MAIN_CATEGORIES = cfg.get("main_categories", "")
_SUB_SERVICES = cfg.get("sub_services", "")
_TIMELINE_OPTIONS = cfg.get("timeline_options", "")
_BUDGET_OPTIONS = cfg.get("budget_options", "")


EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")

MODEL_NAME = "gpt-4o-mini"
SITE_NAME = "Business Chatbot"
INACTIVITY_THRESHOLD = timedelta(minutes=5)  
SESSION_CACHE = TTLCache(maxsize=1000, ttl=300)
UPLOAD_DIR = "uploads"

MAX_OUTBOUND_CONCURRENCY = 200          
HTTPX_MAX_CONNECTIONS = 500
UPSTREAM_TIMEOUT = 5.0                 
CACHE_TTL = 60                         
RATE_LIMIT_PER_SECOND = 200             
TOKEN_BUCKET_CAPACITY = RATE_LIMIT_PER_SECOND
