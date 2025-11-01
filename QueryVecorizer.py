import os
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_huggingface.embeddings import HuggingFaceEmbeddings
from langchain_community.docstore.document import Document
from PyPDF2 import PdfReader

DEFAULT_PDF = "data/tester.pdf"
VECTORSTORE_PATH = "vectorstore/index"
_embedding_model = None
vectorstore = None

def get_embedding_model():
    print("embedding_model loaded")
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = HuggingFaceEmbeddings(
            model_name="sentence-transformers/all-MiniLM-L6-v2",
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": False}
        )
    return _embedding_model


def get_vectorstore():
    global vectorstore
    if vectorstore is None:
        ensure_vectorstore_ready()
        if os.path.exists(VECTORSTORE_PATH):
            vectorstore = load_vectorstore()
        else:
            vectorstore = FAISS.from_documents([], get_embedding_model())
    return vectorstore


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

def get_vectorstore():
    ensure_vectorstore_ready()
    return load_vectorstore()

def query_vectorstore(question: str, k: int = 3) -> str:
    vectorstore = get_vectorstore()
    docs = vectorstore.similarity_search(question, k=k)
    return "\n".join([d.page_content for d in docs])