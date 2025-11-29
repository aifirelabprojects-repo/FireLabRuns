import json
from typing import Dict, Set
from fastapi import  WebSocket
from database import AsyncSessionLocal, Message as MessageModel


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, session_id: str):
        await websocket.accept()
        if session_id not in self.active_connections:
            self.active_connections[session_id] = set()
        self.active_connections[session_id].add(websocket)

    def disconnect(self, websocket: WebSocket, session_id: str):
        if session_id in self.active_connections:
            self.active_connections[session_id].discard(websocket)
            if not self.active_connections[session_id]:
                del self.active_connections[session_id]

    async def broadcast(self, message: str, session_id: str):
        if session_id not in self.active_connections:
            return
        disconnected = set()
        for connection in list(self.active_connections[session_id]):
            try:
                await connection.send_text(message)
            except:
                disconnected.add(connection)
        for conn in disconnected:
            self.active_connections[session_id].discard(conn)

    async def send_history(self, session_id: str, websocket: WebSocket):
        async def fetch_history():
            async with AsyncSessionLocal() as db: 
                try:
                    from sqlalchemy import select
                    stmt = select(MessageModel).filter(MessageModel.session_id == session_id).order_by(MessageModel.timestamp.asc())
                    result = await db.execute(stmt)
                    messages = result.scalars().all()
                    message_list = [
                        {
                            "role": msg.role,
                            "content": msg.content,
                            "timestamp": msg.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ") if msg.timestamp else "2025-01-01T00:00:00Z",
                            "interest": msg.interest,
                            "mood": msg.mood,
                        }
                        for msg in messages
                    ]
                    return json.dumps({"type": "history", "messages": message_list})
                except Exception as e:
                    print(f"[DB ERROR] {e}")
                    return json.dumps({"type": "history", "messages": []})

        # run in threadpool if needed, but since it's async, just await
        history_json = await fetch_history()
        await websocket.send_text(history_json)