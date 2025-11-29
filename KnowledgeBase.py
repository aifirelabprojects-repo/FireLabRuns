import json
import os
import threading
import shutil
import time
from datetime import datetime
from typing import Any, Dict
try:
    import portalocker 
except Exception:
    portalocker = None 
if os.name == "nt" and portalocker is None:
    import msvcrt
    def _lock_file(f): # type: ignore
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
    def _unlock_file(f): # type: ignore
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLK, 1)
else:
    def _lock_file(f):
        if portalocker:
            portalocker.lock(f, portalocker.LOCK_EX)
    def _unlock_file(f):
        if portalocker:
            portalocker.unlock(f)

class _ThreadCache:
    def __init__(self, ttl: float = 0.5):
        self.ttl = ttl
        self._local = threading.local()
    def get(self, factory: callable) -> Dict[str, Any]:
        now = time.monotonic()
        cache = getattr(self._local, "cache", None)
        if cache and (now - cache["ts"]) < self.ttl:
            return cache["data"]
        data = factory()
        self._local.cache = {"data": data, "ts": now}
        return data
    def invalidate(self):
        self._local.cache = None

class AnalytxConfig:
    _instance = None
    _lock = threading.Lock()
    def __new__(cls, filepath: str = "inst.json"):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super().__new__(cls)
                    cls._instance.__initialized = False
        return cls._instance
    def __init__(self, filepath: str = "inst.json"):
        if self.__initialized:
            return
        self.__initialized = True
        self.filepath = os.path.abspath(filepath)
        self._write_lock = threading.Lock() 
        self._cache = _ThreadCache(ttl=0.5) 
        self._ensure_file()
        self.observer = None
        self._start_watcher()
    def get(self, key: str, default: Any = None) -> Any:
        """Thread-safe, cache-friendly read."""
        data = self._cache.get(self._load_from_disk)
        return data.get(key, default)
    def update(self, **kw) -> None:
        """Thread-safe atomic write + cache invalidation."""
        with self._write_lock:
            data = self._load_from_disk()
            data.update(kw)
            self._atomic_save(data)
            self._cache.invalidate()
    def stop(self) -> None:
        if self.observer:
            self.observer.stop()
            self.observer.join()
    def _ensure_file(self) -> None:
        if not os.path.exists(self.filepath):
            self._atomic_save(self._default())
    def _default(self) -> Dict[str, Any]:
        return {
            "name": "",
            "guidelines":"",
            "tones": "",
            "banned": "",
            "company_profile": "",
            "main_categories": [],
            "sub_services": {},
            "timeline_options": [],
            "budget_options": [],
            "last_synced": datetime.now().isoformat(),
        }
    def _load_from_disk(self) -> Dict[str, Any]:
        """Raw load with file-lock – called rarely (only on cache miss)."""
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                _lock_file(f)
                try:
                    data = json.load(f)
                finally:
                    _unlock_file(f)
            return data
        except Exception as e:
            print(f"Config load error ({self.filepath}): {e}")
            return self._default()
    def _atomic_save(self, data: Dict[str, Any]) -> None:
        tmp = self.filepath + ".tmp"
        data["last_synced"] = datetime.now().isoformat()
        # 1. Write to tmp
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        # 2. Atomic replace (POSIX + Windows)
        shutil.move(tmp, self.filepath)
    # reloads only when *external* change occurs
    def _start_watcher(self) -> None:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
        class _SafeHandler(FileSystemEventHandler):
            def __init__(self, cfg: "AnalytxConfig"):
                self.cfg = cfg
                self.last = 0
                self.debounce = 0.2
            def on_modified(self, event):
                if event.src_path != self.cfg.filepath:
                    return
                now = time.monotonic()
                if now - self.last < self.debounce:
                    return
                self.last = now
                # Invalidate *all* thread caches
                threading.Thread(target=self.cfg._cache.invalidate, daemon=True).start()
        observer = Observer()
        observer.schedule(_SafeHandler(self), os.path.dirname(self.filepath), recursive=False)
        observer.daemon = True
        observer.start()
        self.observer = observer

cfg = AnalytxConfig("inst.json")

