# ragr_manager.py
import os
import asyncio
import glob
import json
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any, Coroutine
import logging
import time

from lightrag import LightRAG, QueryParam
from lightrag.llm.openai import gpt_4o_mini_complete, openai_embed
from lightrag.kg.shared_storage import initialize_pipeline_status
from lightrag.utils import setup_logger

import PyPDF2
from docx import Document
from dotenv import load_dotenv

# ---- Basic logging setup ----
setup_logger("lightrag", level="ERROR")
logger = logging.getLogger("ragr_manager")
logger.setLevel(logging.INFO)

# Avoid noisy vector DB logs unless errors
nano_logger = logging.getLogger("nano-vectordb")
nano_logger.setLevel(logging.ERROR)
nano_logger.propagate = False

# ---- Environment ----
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY

# ---- Defaults / configuration ----
DEFAULT_DATA_FOLDER = Path("./data")
DEFAULT_STORAGE_DIR = Path("./lightrag_storage")
FILE_HASHES_PATH = DEFAULT_STORAGE_DIR / "file_hashes.json"

# Tunables
DEFAULT_CHUNK_SIZE = 1200              # characters (tweak depending on tokenizer)
DEFAULT_CHUNK_OVERLAP = 200
DEFAULT_MAX_PARALLEL_INSERT = 4
DEFAULT_MAX_CONCURRENT_QUERIES = 8
DEFAULT_POLL_INTERVAL = 5              # seconds for the file watcher


# ---- Utility functions ----
def read_file_content(file_path: str) -> str:
    """Read supported file types and return text (resilient)."""
    ext = Path(file_path).suffix.lower()
    try:
        if ext in [".txt", ".md"]:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        elif ext == ".pdf" and PyPDF2:
            with open(file_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                text_parts = []
                for page in reader.pages:
                    # extract_text can return None
                    page_text = page.extract_text() or ""
                    text_parts.append(page_text)
                return "\n".join(text_parts)
        elif ext == ".docx" and Document:
            doc = Document(file_path)
            return "\n".join(p.text for p in doc.paragraphs)
        else:
            logger.warning("Unsupported file type or missing library for %s", file_path)
            return ""
    except Exception as e:
        logger.exception("Failed to read file %s: %s", file_path, e)
        return ""


def chunk_text(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP) -> List[str]:
    """Naive character-based chunking with overlap (works reasonably well for many languages)."""
    if not text:
        return []
    text = text.strip()
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    L = len(text)
    while start < L:
        end = start + chunk_size
        chunk = text[start:end]
        chunks.append(chunk.strip())
        # advance with overlap
        start = max(end - overlap, end) if overlap < chunk_size else end
    return [c for c in chunks if c]


def compute_file_hash(path: Path) -> str:
    """Hash file contents + mtime to cheaply detect changes."""
    h = hashlib.sha256()
    try:
        # include size and mtime to speed up (content fallback)
        stat = path.stat()
        h.update(str(stat.st_mtime).encode())
        h.update(str(stat.st_size).encode())
        # small files -> include content; for large, reading is still ok but we can skip if huge
        # we'll read entire file (safe for typical doc sizes); adapt if you expect huge files
        with open(path, "rb") as f:
            while True:
                data = f.read(8192)
                if not data:
                    break
                h.update(data)
    except Exception as e:
        logger.warning("Could not hash %s: %s", path, e)
    return h.hexdigest()


def list_supported_files(folder: Path) -> List[Path]:
    patterns = ["*.txt", "*.md", "*.pdf", "*.docx", "*.TXT", "*.MD", "*.PDF", "*.DOCX"]
    files = []
    for pat in patterns:
        files.extend(folder.glob(pat))
    # dedupe and sort for determinism
    unique = sorted({p.resolve() for p in files})
    return unique


# ---- RAG Manager ----
class RAGManager:
    """Concurrency-safe manager that handles initialization, reinitialization, file watching, queries, and graceful shutdown."""

    def __init__(
        self,
        working_dir: str = str(DEFAULT_STORAGE_DIR),
        data_folder: str = str(DEFAULT_DATA_FOLDER),
        llm_model_func: Callable[..., Coroutine] = gpt_4o_mini_complete,
        embedding_func: Callable = openai_embed,
        branded_prompt_prefix: Optional[str] = None,
        max_parallel_insert: int = DEFAULT_MAX_PARALLEL_INSERT,
        max_concurrent_queries: int = DEFAULT_MAX_CONCURRENT_QUERIES,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
        fallback_doc_text: Optional[str] = None,
    ):
        self.working_dir = Path(working_dir)
        self.data_folder = Path(data_folder)
        self.llm_model_func = llm_model_func
        self.embedding_func = embedding_func
        self.branded_prompt_prefix = branded_prompt_prefix or ""
        self.max_parallel_insert = max_parallel_insert
        self.query_semaphore = asyncio.Semaphore(max_concurrent_queries)
        self.insert_semaphore = asyncio.Semaphore(max_parallel_insert)
        self._init_lock = asyncio.Lock()
        self._rag_lock = asyncio.Lock()  # protects rag instance across queries
        self._initialized = False
        self._rag: Optional[LightRAG] = None
        self._watcher_task: Optional[asyncio.Task] = None
        self._stop_watcher = asyncio.Event()
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.fallback_doc_text = fallback_doc_text or (
            "Analytix — No business data loaded. That detail isn’t currently in our records at Analytix."
        )
        # persisted file hashes to detect precise changes between runs
        self.file_hashes_path = self.working_dir / "file_hashes.json"
        self.file_hashes: Dict[str, str] = {}
        self._ensure_dirs()

    def _ensure_dirs(self):
        self.working_dir.mkdir(parents=True, exist_ok=True)
        self.data_folder.mkdir(parents=True, exist_ok=True)

    # ---------- internal helpers ----------
    def _wrap_branded(self, prompt: str) -> str:
        if not self.branded_prompt_prefix:
            return prompt
        return f"{self.branded_prompt_prefix.strip()}\n\n--- Business Data Context ---\n{prompt.strip()}"

    async def _create_rag(self) -> LightRAG:
        """Constructs a LightRAG instance (kept local to manager)."""
        # wrap LLM func once to always add the branded prefix
        async def branded_llm_complete(prompt: str, *args, **kwargs):
            full = self._wrap_branded(prompt)
            return await self.llm_model_func(full, *args, **kwargs)

        logger.info("Creating LightRAG instance at %s", self.working_dir)
        rag = LightRAG(
            working_dir=str(self.working_dir),
            llm_model_func=branded_llm_complete,
            llm_model_name="gpt-4o-mini",
            embedding_func=self.embedding_func,
            max_parallel_insert=self.max_parallel_insert,
        )
        return rag

    async def _insert_chunks(self, rag: LightRAG, chunks: List[str], source: str):
        """Insert a list of chunks into the vector store using limited concurrency."""
        async def _insert_single(text_chunk: str, idx: int):
            try:
                async with self.insert_semaphore:
                    # You can pass metadata with a source filename if LightRAG supports it.
                    await rag.ainsert(text_chunk)
                    logger.debug("Inserted chunk %s for %s", idx, source)
            except Exception:
                logger.exception("Failed to insert chunk %s from %s", idx, source)

        tasks = [asyncio.create_task(_insert_single(c, i)) for i, c in enumerate(chunks)]
        # await all tasks and allow them to fail individually
        await asyncio.gather(*tasks, return_exceptions=True)

    def _load_saved_hashes(self):
        if self.file_hashes_path.exists():
            try:
                with open(self.file_hashes_path, "r", encoding="utf-8") as f:
                    self.file_hashes = json.load(f)
            except Exception:
                logger.warning("Could not read file_hashes; starting fresh.")
                self.file_hashes = {}
        else:
            self.file_hashes = {}

    def _save_hashes(self):
        try:
            with open(self.file_hashes_path, "w", encoding="utf-8") as f:
                json.dump(self.file_hashes, f, indent=2)
        except Exception:
            logger.exception("Could not save file_hashes.")

    # ---------- public lifecycle methods ----------
    async def initialize(self, force: bool = False):
        """
        Initialize the RAG storages and insert documents.
        - thread-safe (single initializer at a time).
        - if force is True, existing rag will be finalized and recreated.
        """
        async with self._init_lock:
            if self._initialized and not force:
                logger.info("Already initialized; skipping.")
                return

            logger.info("Beginning initialization (force=%s)...", force)
            # finalize previous rag if forcing
            if force and self._rag:
                try:
                    logger.info("Finalizing previous storages...")
                    await self._rag.finalize_storages()
                except Exception:
                    logger.exception("Error finalizing old storages during force-init.")
                self._rag = None
                self._initialized = False

            # create rag
            self._rag = await self._create_rag()

            # ensure storages exist
            try:
                await self._rag.initialize_storages()
                await initialize_pipeline_status()
            except Exception:
                logger.exception("Failed initializing storages.")
                # if storages can't be initialized, keep rag as None so queries fallback
                self._rag = None
                self._initialized = False
                return

            # load file hashes and file list
            self._load_saved_hashes()
            files = list_supported_files(self.data_folder)
            inserted_any = False

            if files:
                logger.info("Found %d files to load.", len(files))
                # insert each file (chunks) with concurrency control
                for path in files:
                    try:
                        text = read_file_content(str(path))
                        if not text or not text.strip():
                            logger.info("Skipping empty or unreadable file %s", path)
                            continue
                        chunks = chunk_text(text, chunk_size=self.chunk_size, overlap=self.chunk_overlap)
                        if not chunks:
                            continue
                        await self._insert_chunks(self._rag, chunks, source=str(path))
                        # compute and store hash on success
                        self.file_hashes[str(path)] = compute_file_hash(path)
                        inserted_any = True
                        logger.info("Inserted file %s (%d chunks)", path.name, len(chunks))
                    except Exception:
                        logger.exception("Failed to process file %s", path)
            else:
                logger.info("No files found in data folder %s", self.data_folder)

            # if nothing was inserted, insert a fallback document to avoid crashes
            if not inserted_any:
                logger.info("No documents were inserted; inserting fallback doc.")
                try:
                    await self._rag.ainsert(self.fallback_doc_text)
                except Exception:
                    logger.exception("Failed to insert fallback document.")
                    # If even this fails, finalize rag and mark uninitialized
                    try:
                        await self._rag.finalize_storages()
                    except Exception:
                        pass
                    self._rag = None
                    self._initialized = False
                    return

            # persist hashes
            self._save_hashes()

            self._initialized = True
            logger.info("Initialization completed.")

    async def reinitialize(self):
        """Public method to force a full reinitialize (safe)."""
        logger.info("Reinitializing RAG storages...")
        await self.initialize(force=True)

    # ---------- file watcher ----------
    async def _compute_current_hashes(self) -> Dict[str, str]:
        current = {}
        for path in list_supported_files(self.data_folder):
            current[str(path)] = compute_file_hash(path)
        return current

    async def _watcher_loop(self, poll_interval: int):
        logger.info("File watcher started (poll_interval=%s)", poll_interval)
        while not self._stop_watcher.is_set():
            try:
                current = await self._compute_current_hashes()
                # compare with saved
                if current != self.file_hashes:
                    logger.info("Detected change in data folder; performing reinitialize.")
                    # update local file_hashes to the latest state to avoid repeated triggers
                    self.file_hashes = current
                    # persist immediate (best-effort)
                    self._save_hashes()
                    # reinitialize in background (awaiting it here so watcher loop serializes reloads)
                    await self.reinitialize()
                await asyncio.wait_for(self._stop_watcher.wait(), timeout=poll_interval)
            except asyncio.TimeoutError:
                continue  # normal path: loop again
            except Exception:
                logger.exception("Error in watcher loop; continuing.")
        logger.info("File watcher stopped.")

    def start_watcher(self, poll_interval: int = DEFAULT_POLL_INTERVAL):
        """Start background watcher task (non-blocking)."""
        if self._watcher_task and not self._watcher_task.done():
            logger.info("Watcher already running.")
            return
        self._stop_watcher.clear()
        self._watcher_task = asyncio.create_task(self._watcher_loop(poll_interval))
        logger.info("Watcher task created.")

    async def stop_watcher(self):
        """Stop background watcher and wait for it to finish."""
        if not self._watcher_task:
            return
        self._stop_watcher.set()
        try:
            await self._watcher_task
        except Exception:
            logger.exception("Exception while stopping watcher.")
        self._watcher_task = None

    # ---------- queries ----------
    async def aquery(self, query: str, param: Optional[QueryParam] = None) -> Any:
        """
        Asynchronous query that ensures initialization and is concurrency-limited.
        Returns whatever LightRAG.aquery returns, or a fallback response on error.
        """
        async with self.query_semaphore:
            # Ensure initialized (lazy-init for the first query)
            if not self._initialized:
                logger.info("Lazy-initializing RAG before query.")
                await self.initialize()

            if not self._rag:
                logger.warning("RAG not available; returning fallback response.")
                return {"text": self.fallback_doc_text}

            try:
                # Use rag safely
                async with self._rag_lock:
                    res = await self._rag.aquery(query, param=param or QueryParam())
                    return res
            except Exception:
                logger.exception("Query failed; returning fallback message.")
                return {"text": self.fallback_doc_text}

    # ---------- cleanup ----------
    async def close(self):
        logger.info("Closing RAG manager...")
        # stop watcher
        await self.stop_watcher()
        # finalize rag
        if self._rag:
            try:
                await self._rag.finalize_storages()
            except Exception:
                logger.exception("Error during finalize_storages.")
            self._rag = None
        self._initialized = False
        logger.info("RAG manager closed.")


# ---- Example CLI-style main for demonstration ----
async def main_demo():
    BRANDED_PROMPT = """
    You are a professional representative of **Analytix**, 
    the government-approved partner for business expansion in Saudi Arabia.

    Always speak as part of Analytix (use "we", "our", "at Analytix"). 
    Never mention AI, ChatGPT, or OpenAI.

    Base every response *only* on the provided business data — 
    if information is missing, say:
    "That detail isn’t currently in our records at Analytix."

    Avoid references, citations, or source markers like [1].
    """

    # create manager
    manager = RAGManager(
        working_dir=str(DEFAULT_STORAGE_DIR),
        data_folder=str(DEFAULT_DATA_FOLDER),
        llm_model_func=gpt_4o_mini_complete,
        embedding_func=openai_embed,
        branded_prompt_prefix=BRANDED_PROMPT,
        max_parallel_insert=4,
        max_concurrent_queries=8,
    )

    # initialize once at startup
    await manager.initialize()

    manager.start_watcher(poll_interval=DEFAULT_POLL_INTERVAL)

    # Example queries (demonstration)
    try:
        param = QueryParam(mode="hybrid", top_k=2, history_turns=0, stream=False, conversation_history=[])
        queries = ["number of employees", "success Stories", "any contact info available?"]

        # run some concurrent queries to demonstrate concurrency control
        results = await asyncio.gather(*(manager.aquery(q, param) for q in queries))
        for q, r in zip(queries, results):
            print("=== QUERY ===")
            print(q)
            print("=== RESPONSE ===")
            print(r)
            print()
      
        print("Watcher running. Press Ctrl+C to stop.")
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        print("Shutting down...")
    finally:
        await manager.close()


if __name__ == "__main__":
    try:
        asyncio.run(main_demo())
    except Exception as e:
        logger.exception("Unhandled exception in main: %s", e)
