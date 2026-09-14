import sqlite3
import json
import os
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ntqa_history.db")

def get_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize the SQLite database with tables for conversations, messages, and task runs."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        meta_json TEXT DEFAULT '{}'
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        role TEXT NOT NULL, -- 'user', 'assistant', 'system', 'machine'
        content TEXT NOT NULL,
        msg_type TEXT DEFAULT 'text', -- 'text', 'task_prompt', 'task_execution'
        meta_json TEXT DEFAULT '{}',
        created_at TEXT NOT NULL,
        FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS task_runs (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        task_name TEXT NOT NULL,
        command TEXT,
        status TEXT, -- 'success', 'error', 'paused'
        output TEXT,
        error TEXT,
        summary TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
    )
    """)
    
    conn.commit()
    conn.close()

# Auto-initialize DB on module import
init_db()

# ────────────────── Conversation Operations ──────────────────

def list_conversations() -> List[Dict[str, Any]]:
    """Retrieve all conversations ordered by updated_at descending with message count."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            c.id, c.title, c.created_at, c.updated_at, c.meta_json,
            COUNT(m.id) as message_count,
            (SELECT content FROM messages WHERE conversation_id = c.id ORDER BY created_at DESC LIMIT 1) as last_message
        FROM conversations c
        LEFT JOIN messages m ON c.id = m.conversation_id
        GROUP BY c.id
        ORDER BY c.updated_at DESC
    """)
    rows = cursor.fetchall()
    results = []
    for r in rows:
        results.append({
            "id": r["id"],
            "title": r["title"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "meta": json.loads(r["meta_json"] or "{}"),
            "message_count": r["message_count"],
            "last_message": r["last_message"] or ""
        })
    conn.close()
    return results

def get_conversation(convo_id: str) -> Optional[Dict[str, Any]]:
    """Get full conversation including messages and task executions."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM conversations WHERE id = ?", (convo_id,))
    c_row = cursor.fetchone()
    if not c_row:
        conn.close()
        return None
    
    # Get messages
    cursor.execute("SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC", (convo_id,))
    msg_rows = cursor.fetchall()
    messages = []
    for m in msg_rows:
        messages.append({
            "id": m["id"],
            "role": m["role"],
            "content": m["content"],
            "msg_type": m["msg_type"],
            "meta": json.loads(m["meta_json"] or "{}"),
            "created_at": m["created_at"]
        })
    
    # Get task runs
    cursor.execute("SELECT * FROM task_runs WHERE conversation_id = ? ORDER BY created_at ASC", (convo_id,))
    run_rows = cursor.fetchall()
    task_runs = []
    for tr in run_rows:
        task_runs.append({
            "id": tr["id"],
            "task_name": tr["task_name"],
            "command": tr["command"],
            "status": tr["status"],
            "output": tr["output"],
            "error": tr["error"],
            "summary": tr["summary"],
            "created_at": tr["created_at"]
        })
    
    conn.close()
    return {
        "id": c_row["id"],
        "title": c_row["title"],
        "created_at": c_row["created_at"],
        "updated_at": c_row["updated_at"],
        "meta": json.loads(c_row["meta_json"] or "{}"),
        "messages": messages,
        "task_runs": task_runs
    }

def create_conversation(title: str = "New Chat", convo_id: Optional[str] = None, meta: Optional[Dict] = None) -> Dict[str, Any]:
    """Create a new conversation entry."""
    cid = convo_id or str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    meta_str = json.dumps(meta or {})
    
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO conversations (id, title, created_at, updated_at, meta_json)
        VALUES (?, ?, ?, ?, ?)
    """, (cid, title, now, now, meta_str))
    conn.commit()
    conn.close()
    
    return {
        "id": cid,
        "title": title,
        "created_at": now,
        "updated_at": now,
        "meta": meta or {}
    }

def update_conversation_title(convo_id: str, title: str):
    """Update conversation title."""
    now = datetime.utcnow().isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?", (title, now, convo_id))
    conn.commit()
    conn.close()

def delete_conversation(convo_id: str) -> bool:
    """Delete a conversation and all associated messages and task runs."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM messages WHERE conversation_id = ?", (convo_id,))
    cursor.execute("DELETE FROM task_runs WHERE conversation_id = ?", (convo_id,))
    cursor.execute("DELETE FROM conversations WHERE id = ?", (convo_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted

def clear_all_conversations():
    """Clear all history in the SQLite database."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM messages")
    cursor.execute("DELETE FROM task_runs")
    cursor.execute("DELETE FROM conversations")
    conn.commit()
    conn.close()

# ────────────────── Message Operations ──────────────────

def add_message(convo_id: str, role: str, content: str, msg_type: str = "text", meta: Optional[Dict] = None) -> Dict[str, Any]:
    """Add a message to a conversation and update conversation timestamp."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Ensure conversation exists
    cursor.execute("SELECT id, title FROM conversations WHERE id = ?", (convo_id,))
    existing = cursor.fetchone()
    now = datetime.utcnow().isoformat()
    
    if not existing:
        title = content[:40].strip().replace("\n", " ") if content and role == 'user' else "New Task Session"
        if len(content) > 40:
            title += "..."
        cursor.execute("""
            INSERT INTO conversations (id, title, created_at, updated_at, meta_json)
            VALUES (?, ?, ?, ?, ?)
        """, (convo_id, title or "New Task Session", now, now, "{}"))
    elif role == 'user' and (existing["title"] in ["Session initialized", "New Chat", "New Task Session"]):
        title = content[:40].strip().replace("\n", " ")
        if len(content) > 40:
            title += "..."
        cursor.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, convo_id))
    
    msg_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    meta_str = json.dumps(meta or {})
    
    cursor.execute("""
        INSERT INTO messages (id, conversation_id, role, content, msg_type, meta_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (msg_id, convo_id, role, content, msg_type, meta_str, now))
    
    cursor.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, convo_id))
    
    conn.commit()
    conn.close()
    
    return {
        "id": msg_id,
        "conversation_id": convo_id,
        "role": role,
        "content": content,
        "msg_type": msg_type,
        "meta": meta or {},
        "created_at": now
    }

# ────────────────── Task Run Operations ──────────────────

def add_task_run(convo_id: str, task_name: str, command: str, status: str, output: str, error: str = "", summary: Optional[str] = None) -> Dict[str, Any]:
    """Record a task execution in the SQLite database."""
    conn = get_connection()
    cursor = conn.cursor()
    
    run_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    
    cursor.execute("""
        INSERT INTO task_runs (id, conversation_id, task_name, command, status, output, error, summary, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (run_id, convo_id, task_name, command, status, output, error, summary, now))
    
    cursor.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, convo_id))
    
    conn.commit()
    conn.close()
    
    return {
        "id": run_id,
        "conversation_id": convo_id,
        "task_name": task_name,
        "command": command,
        "status": status,
        "output": output,
        "error": error,
        "summary": summary,
        "created_at": now
    }
