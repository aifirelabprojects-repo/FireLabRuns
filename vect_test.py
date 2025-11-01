# import os
# import json
# from datetime import timedelta
# from typing import List
# from PyPDF2 import PdfReader
# from langchain_text_splitters import RecursiveCharacterTextSplitter
# from langchain_community.vectorstores import FAISS
# from langchain_huggingface.embeddings import HuggingFaceEmbeddings
# from langchain_community.docstore.document import Document

# # ====================================================
# # CONFIG
# # ====================================================

# VECTORSTORE_PATH = "vectorstore/index"
# DEFAULT_PDF = "data/tester.pdf"
# INACTIVITY_THRESHOLD = timedelta(minutes=5)

# USE_PDF_CONTENT = False   

# os.makedirs("data", exist_ok=True)
# os.makedirs("vectorstore", exist_ok=True)

# # ====================================================
# # EMBEDDING MODEL (Singleton)
# # ====================================================

# _embedding_model = None

# def get_embedding_model():
#     global _embedding_model
#     if _embedding_model is None:
#         _embedding_model = HuggingFaceEmbeddings(
#             model_name="sentence-transformers/all-MiniLM-L6-v2",
#             model_kwargs={"device": "cpu"},
#             encode_kwargs={"normalize_embeddings": True}
#         )
#     return _embedding_model


# def extract_text_from_pdf(pdf_path: str) -> str:
#     """Extract and clean text from a PDF file accurately."""
#     pdf = PdfReader(pdf_path)
#     text_blocks = []
#     for page in pdf.pages:
#         text = page.extract_text() or ""
#         # Clean and normalize newlines and spaces
#         cleaned = " ".join(line.strip() for line in text.splitlines() if line.strip())
#         text_blocks.append(cleaned)
#     return "\n".join(text_blocks).strip()


# HARDCODED_CONTENT = """
# Company Details for Analytix - Services and Options 

# MAIN_CATEGORIES: 
# - "Market Entry & Business Setup" 
# - "Strategic Growth & Management" 
# - "Financial Services & Compliance" 
# - "Industrial & Specialized Operations" 
# - "Legal & Business Protection" 

# SUB_SERVICES: 
# For "Market Entry & Business Setup": 
# - {"text": "Business Setup Saudi Arabia", "value": "setup_sa"} 
# - {"text": "Business Setup in other GCC (UAE, Qatar, etc.)", "value": "setup_gcc"}  
# - {"text": "Premium Residency & Investor Visas", "value": "visas"}  
# - {"text": "Entrepreneur License (IT & Tech Services)", "value": "entrepreneur_license"}  
# - {"text": "Virtual Office & Business Center", "value": "virtual_office"}  

# For "Strategic Growth & Management": 
# - {"text": "Management Consultancy (Business Restructuring, Market Strategy, M&A)", "value": "consultancy"} 
# - {"text": "Vendor Registration & Certification (for NEOM, Aramco, etc.)", "value": "vendor_reg"}  
# - {"text": "HR & Talent Solutions (Recruitment, EOR, Training)", "value": "hr_solutions"}  

# For "Financial Services & Compliance": 
# - {"text": "Accounting & Bookkeeping", "value": "accounting"} 
# - {"text": "Tax Consulting & Audit", "value": "tax_audit"} 
# - {"text": "Bank Account & Finance Assistance", "value": "finance_assist"}  

# For "Industrial & Specialized Operations": 
# - {"text": "Industrial License & Factory Setup", "value": "industrial_license"} 
# - {"text": "ISO & Local Content Certification", "value": "iso_cert"}  
# - {"text": "Technology & Process Automation", "value": "automation"}  

# For "Legal & Business Protection": 
# - {"text": "Legal Advisory & Contract Drafting", "value": "legal_advisory"} 
# - {"text": "Debt Recovery & Dispute Resolution", "value": "debt_recovery"}  
# - {"text": "Trademark Registration", "value": "trademark"} 

# TIMELINE_OPTIONS: 
# - {"text": "Within 1 month", "value": "1_month"} 
# - {"text": "1-3 months", "value": "1_3_months"} 
# - {"text": "3-6 months", "value": "3_6_months"} 
# - {"text": "Just researching", "value": "researching"} 

#  BUDGET_RANGE = [
#     {"text": "Under 50,000 SAR", "value": "under_50k"},
#     {"text": "50,000 - 75,000 SAR", "value": "50_75k"},
#     {"text": "75,000 - 100,000 SAR", "value": "75_100k"},
#     {"text": "100,000 - 125,000 SAR", "value": "100_125k"},
#     {"text": "125,000 - 150,000 SAR", "value": "125_150k"},
#     {"text": "Over 150,000 SAR", "value": "over_150k"}
# ]
# Additional Company Info (for enrichment queries): 
# - Analytix Industry: Business Consulting 
# - Size: 100+ employees 
# - Location: Riyadh, Saudi Arabia 
# - Trusted by Ministry for fast-track market entry
# """


# def create_vectorstore(text: str, store_path: str = VECTORSTORE_PATH):
#     """Create a FAISS vectorstore from text."""
#     splitter = RecursiveCharacterTextSplitter(chunk_size=400, chunk_overlap=60)
#     docs = [Document(page_content=chunk) for chunk in splitter.split_text(text)]
#     vectorstore = FAISS.from_documents(docs, get_embedding_model())
#     vectorstore.save_local(store_path)
#     return vectorstore

# def load_vectorstore(store_path: str = VECTORSTORE_PATH):
#     """Load an existing FAISS index."""
#     return FAISS.load_local(store_path, get_embedding_model(), allow_dangerous_deserialization=True)

# def ensure_vectorstore_ready():
#     """Ensure a FAISS store exists."""
#     if not os.path.exists(VECTORSTORE_PATH):
#         print("⚙️ Building FAISS vectorstore...")
#         if USE_PDF_CONTENT and os.path.exists(DEFAULT_PDF):
#             text = extract_text_from_pdf(DEFAULT_PDF)
#         else:
#             print("⚠️ Using hardcoded fallback text for vectorstore (testing mode)")
#             text = HARDCODED_CONTENT
#         create_vectorstore(text)

# def get_vectorstore():
#     ensure_vectorstore_ready()
#     return load_vectorstore()

# # ====================================================
# # QUERY FUNCTION
# # ====================================================

# def query_vectorstore(question: str, k: int = 4) -> str:
#     """Query vectorstore and return the most relevant chunks."""
#     vectorstore = get_vectorstore()
#     docs = vectorstore.similarity_search(question, k=k)

#     # Relevance filter — prioritize chunks containing key query tokens
#     q = question.lower()
#     docs = sorted(docs, key=lambda d: q in d.page_content.lower(), reverse=True)

#     return "\n".join([d.page_content for d in docs[:3]])

# # ====================================================
# # TEST RUN
# # ====================================================

# if __name__ == "__main__":
#     print("Initializing vectorstore...")
#     ensure_vectorstore_ready()

#     print("\n==== QUERY: BUDGET_RANGES ====")
#     print(query_vectorstore("BUDGET_RANGES"))

#     print("\n==== QUERY: SUB_SERVICES for Legal & Business Protection ====")
#     print(query_vectorstore("SUB_SERVICES for Legal & Business Protection"))


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

selected_cats = "Legal & Business Protection"
options = []
for cat in selected_cats:
    options.extend(
        {"text": t, "value": v, "type": "multi_select"} for t, v in SUB_SERVICES.get(cat, [])
    )
    
print(options)