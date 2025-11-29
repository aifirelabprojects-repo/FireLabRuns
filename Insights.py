import mimetypes
from pathlib import Path
from datetime import datetime
from typing import Dict, List
from fastapi import File, UploadFile, HTTPException
from BotGraph import reload_system_prompt
from fastapi.responses import FileResponse
from KnowledgeBase import cfg
from dataclasses import dataclass

from Schemas import InsightUpdate


def format_last_synced(last_synced_str):
    if not last_synced_str:
        return "Never"
    try:
        dt = datetime.fromisoformat(last_synced_str.replace('Z', '+00:00'))
        now = datetime.utcnow() 
        delta = now - dt
        if delta.total_seconds() < 60:
            return "Just now"
        elif delta.total_seconds() < 3600:
            mins = int(delta.total_seconds() / 60)
            return f"{mins}m ago"
        elif delta.days < 1:
            hours = int(delta.total_seconds() / 3600)
            return f"{hours}h ago"
        elif delta.days < 7:
            return f"{delta.days}d ago"
        else:
            return dt.strftime("%b %d, %Y")
    except ValueError:
        return last_synced_str 

DEFAULT_DATA_FOLDER = Path("./data")
DEFAULT_DATA_FOLDER.mkdir(exist_ok=True)

@dataclass
class FileInfo:
    name: str
    type: str  
    size: str  
    size_bytes: int
    status: str = "Indexed"

def calculate_sources_and_storage(folder: Path) -> Dict:
    if not folder.exists() or not folder.is_dir():
        raise ValueError(f"{folder} is not a valid folder path")
    
    def sizeof_fmt(num: int, suffix="B") -> str:
        for unit in ["", "K", "M", "G", "T"]:
            if abs(num) < 1024.0:
                return f"{num:.1f}{unit}{suffix}"
            num /= 1024.0
        return f"{num:.1f}P{suffix}"
    
    total_size = 0
    file_count = 0
    files: List[FileInfo] = []
    
    for file_path in folder.rglob('*'):
        if file_path.is_file():
            file_count += 1
            size_bytes = file_path.stat().st_size
            total_size += size_bytes
            
            ext = file_path.suffix[1:].upper() if file_path.suffix else "Unknown"  # e.g., "PDF"
            formatted_size = sizeof_fmt(size_bytes)
            
            files.append(FileInfo(
                name=file_path.name,
                type=ext,
                size=formatted_size,
                size_bytes=size_bytes
            ))
    
    return {
        "file_count": file_count,
        "total_size_bytes": total_size,
        "total_size_readable": sizeof_fmt(total_size),
        "files": [f.__dict__ for f in sorted(files, key=lambda f: f.name)]  # Sort by name
    }

def init(app):
    @app.get("/insight")
    def get_insight():
        stats = calculate_sources_and_storage(DEFAULT_DATA_FOLDER)
        last_synced = cfg.get("last_synced", "")
        return {
            "guidelines": cfg.get("guidelines", ""),
            "tones": cfg.get("tones", ""),
            "name": cfg.get("name", ""),
            "banned": cfg.get("banned", ""),
            "company_profile": cfg.get("company_profile", ""),
            "main_categories": cfg.get("main_categories", ""),
            "sub_services": cfg.get("sub_services", ""),
            "timeline_options": cfg.get("timeline_options", ""),
            "budget_options": cfg.get("budget_options", ""),
            "last_synced": last_synced,
            "num_sources": stats['file_count'],
            "storage_used": stats['total_size_readable'],
            "last_sync_display": format_last_synced(last_synced),
            "files": stats['files'] 
        }

    @app.post("/insight")
    def update_insight(update: InsightUpdate):
        try:
            cfg.update(
                guidelines=update.guidelines,
                tones=update.tones,
                name=update.name,
                banned=update.banned,
                company_profile=update.company_profile,
                main_categories=update.main_categories,
                sub_services=update.sub_services,
                timeline_options=update.timeline_options,
                budget_options=update.budget_options,
                last_synced=datetime.utcnow().isoformat() + 'Z'
            )
            reload_system_prompt()
            return {"status": "updated successfully"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Update failed: {e}")

    @app.post("/upload")
    async def upload_file(file: UploadFile = File(...)):
        if not file.content_type or not file.content_type.startswith(('application/pdf', 'text/', 'text/csv')):
            raise HTTPException(status_code=400, detail="Unsupported file type. Only PDFs, Markdown, CSV, or plain text allowed.")
        
        # Sanitize filename
        safe_filename = "".join(c for c in file.filename if c.isalnum() or c in ('.', '_', '-')).rstrip('.')
        if not safe_filename:
            raise HTTPException(status_code=400, detail="Invalid filename.")
        
        filepath = DEFAULT_DATA_FOLDER / safe_filename
        if filepath.exists():
            raise HTTPException(status_code=409, detail="File already exists.")
        
        content = await file.read()
        with open(filepath, "wb") as f:
            f.write(content)
        
        # Update last_synced
        cfg.update(last_synced=datetime.utcnow().isoformat() + 'Z')
        
        return {"status": "uploaded successfully", "filename": safe_filename}

    @app.delete("/files/{filename}")
    def delete_file(filename: str):
        filepath = DEFAULT_DATA_FOLDER / filename
        if not filepath.exists():
            raise HTTPException(status_code=404, detail="File not found.")
        
        filepath.unlink()

        return {"status": "deleted successfully", "filename": filename}

    @app.get("/files/{filename}")
    async def download_file(filename: str):
        filepath = DEFAULT_DATA_FOLDER / filename
        if not filepath.exists():
            raise HTTPException(status_code=404, detail="File not found.")

        mime_type, _ = mimetypes.guess_type(filepath)
        return FileResponse(
            path=filepath,
            media_type=mime_type or "application/octet-stream",
            filename=filename
        )