import os
import sys
import json
import re
import uuid
import time
import logging
from logging.handlers import RotatingFileHandler
import asyncio
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

# Import SQLite database module
import ntqa_db

# Mock sys.argv to prevent argparse from crashing when importing task_run
original_argv = sys.argv
sys.argv = ['task_run.py']
import task_run
sys.argv = original_argv

load_dotenv()

# ─── Access Logging Setup ────────────────────────────────────────────────────
_log_dir = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(_log_dir, exist_ok=True)

access_logger = logging.getLogger("ntqa.access")
access_logger.setLevel(logging.INFO)
access_logger.propagate = False
_handler = RotatingFileHandler(
    os.path.join(_log_dir, "access.log"),
    maxBytes=5 * 1024 * 1024,  # 5 MB per file
    backupCount=7,
)
_handler.setFormatter(logging.Formatter("%(message)s"))
access_logger.addHandler(_handler)
# Also mirror to stdout so uvicorn terminal still shows it
access_logger.addHandler(logging.StreamHandler(sys.stdout))

app = FastAPI(title="NTQA API")


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration_ms = round((time.time() - start) * 1000)
        ip = (
            request.headers.get("CF-Connecting-IP")       # real IP via Cloudflare tunnel
            or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or (request.client.host if request.client else "unknown")
        )
        ua = request.headers.get("User-Agent", "-")
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        access_logger.info(
            f"{ts} {ip} \"{request.method} {request.url.path}\" "
            f"{response.status_code} {duration_ms}ms \"{ua}\""
        )
        return response


app.add_middleware(AccessLogMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5000",
        "http://localhost:5173",
        "http://localhost:3000",
        "https://runningly-chat-web.vercel.app",
        "https://runningly.t-chrome.com",
        "https://runningly-chat.t-chrome.com",
        "*"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store active sessions
# session_id -> {"convo": convo_object, "last_tasks": list}
sessions = {}

TASK_FOLDER = "tasks"
tm = task_run.TaskManager(TASK_FOLDER)

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None

class ExecuteRequest(BaseModel):
    tasks: List[Dict[str, Any]]
    pause: bool = False
    session_id: Optional[str] = None

class CreateConversationRequest(BaseModel):
    title: Optional[str] = "New Task Session"

class GenerateTaskRequest(BaseModel):
    command_snippet: str
    task_name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = "custom"

class SaveTaskRequest(BaseModel):
    task: Dict[str, Any]
    category: Optional[str] = None
    filename: Optional[str] = None

class PromptRequest(BaseModel):
    prompt: Optional[str] = None
    content: Optional[str] = ""
    data: Optional[str] = None
    text: Optional[str] = None
    instruction: Optional[str] = None
    system_prompt: Optional[str] = None

SummarizeRequest = PromptRequest

@app.get("/tasks")
def get_tasks():
    """Return all available tasks categorized in packs for the UI."""
    tm.tasks = tm.load_tasks(TASK_FOLDER)
    categories = sorted(list(set(t.get("category", "General") for t in tm.tasks)))
    return {"tasks": tm.tasks, "categories": categories}

@app.get("/tasks/categories")
def get_task_categories():
    """Return unique task pack category names."""
    tm.tasks = tm.load_tasks(TASK_FOLDER)
    categories = sorted(list(set(t.get("category", "General") for t in tm.tasks)))
    return {"categories": categories}

@app.post("/tasks/generate")
def generate_task_from_snippet(req: GenerateTaskRequest):
    """Use Gemini to auto-generate a valid NTQA JSON task schema from either a raw cURL/CLI snippet OR a natural language prompt description."""
    prompt = (
        "You are an expert NTQA Task Builder & Engineer. Your role is to generate a production-ready NTQA JSON task definition "
        "from either: (A) a raw cURL / CLI snippet, OR (B) a detailed natural language prompt describing what the task should do.\n\n"
        "Generation Rules:\n"
        "1. EXECUTABLE COMMAND:\n"
        "   - If the user gave a raw command (curl, bash, python, aws, kubectl), optimize and parameterize it.\n"
        "   - If the user gave a natural language description (e.g. 'Query database for active users' or 'Find AWS S3 buckets and check encryption'), "
        "write the complete, runnable command (using curl, python3 -c '...', aws CLI, or bash).\n"
        "   - Where appropriate, ensure the command outputs valid JSON to stdout (e.g. using python3 -c 'import json; ... print(json.dumps(...))') so future tasks can bind variables.\n"
        "2. DYNAMIC PARAMETER BINDING:\n"
        "   - Replace dynamic runtime inputs (e.g. usernames, IDs, dates, queries, parameters) in the command with ${answer1}, ${answer2}, ${answer3}, etc. in sequential 1-based order.\n"
        "   - Environment variables (like $OKTA_API_TOKEN, $OKTA_URL, $AWS_REGION, $API_KEY) must be preserved as literal environment variable references.\n"
        "3. QUESTIONS ARRAY:\n"
        "   - Build a 'questions' array where index 0 matches ${answer1}, index 1 matches ${answer2}, etc.\n"
        "   - Questions for required parameters MUST end with an asterisk '*'.\n"
        "   - Questions for optional parameters must NOT end with '*'.\n"
        "   - Write human-friendly, descriptive question prompts (e.g. 'What is the user ID or email?*').\n"
        "4. TASK METADATA:\n"
        "   - 'task_name': Concise, title-case task name (e.g. 'Find User By Email', 'Stop EC2 Instance', 'Backup Database Table').\n"
        "   - 'description': Clear summary of what the task executes.\n"
        "   - 'category': Task category / pack name (e.g. '" + (req.category or "Custom") + "').\n"
        "5. OUTPUT FORMAT:\n"
        "   - Return ONLY a single raw JSON object (no markdown, no backticks, no explanatory text):\n"
        "   {\n"
        "     \"task_name\": \"...\",\n"
        "     \"description\": \"...\",\n"
        "     \"command\": \"...\" (or [\"line1\", \"line2\"]),\n"
        "     \"questions\": [\"Question 1*\", \"Question 2\"],\n"
        "     \"category\": \"" + (req.category or "Custom") + "\"\n"
        "   }\n\n"
        "User Input (cURL, CLI Command, or Prompt Description):\n" + req.command_snippet
    )
    if req.task_name:
        prompt += f"\nPreferred Task Name: {req.task_name}"
    if req.description:
        prompt += f"\nPreferred Description: {req.description}"

    try:
        response = task_run.client.models.generate_content(
            model=task_run.args.model,
            contents=prompt
        )
        resp_text = task_run.get_response_text(response).strip()
        parsed_task = task_run.try_parse_json(resp_text)
        if not parsed_task or not isinstance(parsed_task, dict):
            # Fallback cleanup
            clean_json = re.sub(r'^```(?:json)?\s*', '', resp_text, flags=re.MULTILINE)
            clean_json = re.sub(r'```$', '', clean_json, flags=re.MULTILINE).strip()
            parsed_task = json.loads(clean_json)

        parsed_task["category"] = req.category or parsed_task.get("category", "Custom")
        return {"status": "generated", "task": parsed_task}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to auto-generate task schema: {str(e)}")

@app.post("/tasks/save")
def save_new_task(req: SaveTaskRequest):
    """Save a new or edited task JSON into the specified task pack folder and reload tasks."""
    task_data = req.task
    if not task_data.get("task_name"):
        raise HTTPException(status_code=400, detail="task_name is required")

    category = (req.category or task_data.get("category") or "custom").lower().strip()
    category = re.sub(r'[^a-z0-9_-]', '_', category) or "custom"
    
    # Generate clean filename
    raw_name = req.filename or task_data.get("task_name", "task")
    filename = re.sub(r'[^a-z0-9_]', '_', raw_name.lower().strip())
    if not filename.endswith(".json"):
        filename += ".json"

    pack_dir = os.path.join(TASK_FOLDER, category)
    os.makedirs(pack_dir, exist_ok=True)
    file_path = os.path.join(pack_dir, filename)

    try:
        # Write clean task file
        save_payload = {
            "task_name": task_data.get("task_name"),
            "description": task_data.get("description", ""),
            "command": task_data.get("command"),
            "questions": task_data.get("questions", [])
        }
        if task_data.get("auto_summarize"):
            save_payload["auto_summarize"] = True

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(save_payload, f, indent=2)

        # Reload TaskManager
        tm.tasks = tm.load_tasks(TASK_FOLDER)
        return {
            "status": "saved",
            "file_path": os.path.relpath(file_path, TASK_FOLDER),
            "category": category.title(),
            "task": save_payload,
            "total_tasks": len(tm.tasks)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write task file: {str(e)}")

# ────────────────── Generic Standalone Conversation Endpoints ──────────────────

@app.post("/prompt")
@app.post("/api/prompt")
@app.post("/summarize")
@app.post("/api/summarize")
def execute_standalone_prompt_endpoint(req: PromptRequest):
    """Internal endpoint that starts a separate/standalone conversation with Gemini for any prompt (e.g. summarize, HTML table, formatting, analysis)."""
    try:
        user_prompt = (req.prompt or req.instruction or "").strip()
        raw_content = (req.content or req.data or req.text or "").strip()
        
        if not user_prompt and not raw_content:
            return {
                "status": "error",
                "message": "No prompt or content provided."
            }
            
        system_instruction = req.system_prompt or (
            "You are an expert AI task assistant and content processor. "
            "Follow the user's instructions with high precision. "
            "If asked to generate an HTML table, return clean, beautiful, well-styled HTML. "
            "If asked for an email or summary, return clear, structured content. "
            "If returning JSON, ensure it is strictly valid JSON without markdown code backticks unless requested."
        )
        
        # Start a dedicated 2nd conversation with Gemini
        convo = task_run.client.chats.create(
            model=task_run.args.model,
            history=[{"role": "user", "parts": [{"text": system_instruction}]}]
        )
        
        if user_prompt and raw_content:
            message_to_send = f"{user_prompt}\n\nInput Data / Content:\n{raw_content}"
        elif user_prompt:
            message_to_send = user_prompt
        else:
            message_to_send = f"Please summarize the following content into a clean subject and body:\n\n{raw_content}"
            
        response = convo.send_message(message_to_send)
        resp_text = task_run.get_response_text(response).strip()
        
        # Check if response is JSON
        parsed = task_run.try_parse_json(resp_text)
        if not parsed or not isinstance(parsed, dict):
            clean_json = re.sub(r'^```(?:json)?\s*', '', resp_text, flags=re.MULTILINE)
            clean_json = re.sub(r'```$', '', clean_json, flags=re.MULTILINE).strip()
            try:
                parsed = json.loads(clean_json)
            except Exception:
                parsed = None
                
        # Extract or construct subject, body, summary, content, html
        if isinstance(parsed, dict):
            subject = parsed.get("subject") or parsed.get("title") or "Task Result"
            body = parsed.get("body") or parsed.get("content") or parsed.get("summary") or resp_text
            summary = parsed.get("summary") or body
            html = parsed.get("html") or parsed.get("table")
        else:
            # Check if response contains HTML table / content
            html_match = re.search(r'(<table[\s\S]*?</table>|<html[\s\S]*?</html>|<div[\s\S]*?</div>)', resp_text, re.IGNORECASE)
            html = html_match.group(0) if html_match else None
            lines = [l.strip() for l in resp_text.splitlines() if l.strip()]
            subject = lines[0][:80] if lines else "Task Result"
            body = resp_text
            summary = resp_text

        result_payload = {
            "status": "success",
            "subject": subject,
            "body": body,
            "summary": summary,
            "content": body,
            "response": resp_text
        }
        if html:
            result_payload["html"] = html
        if isinstance(parsed, dict):
            for k, v in parsed.items():
                if k not in result_payload:
                    result_payload[k] = v
                    
        return result_payload
    except Exception as e:
        return {
            "status": "error",
            "message": f"Standalone conversation execution failed: {str(e)}"
        }

# ────────────────── Auth & Compatibility Endpoints ──────────────────

@app.get("/api/current-user")
def get_current_user():
    """Return local admin user session profile for standalone/Docker usage."""
    return {
        "name": "Admin",
        "email": "admin@runningly.ai",
        "picture": ""
    }

@app.get("/api/auth/login")
def auth_login():
    """Redirect login back to home in local/Docker standalone mode."""
    return RedirectResponse(url="/", status_code=302)

@app.get("/api/logout")
@app.get("/api/revoke")
def auth_logout():
    """Handle logout by returning to home."""
    return RedirectResponse(url="/", status_code=302)

@app.get("/download")
@app.get("/download_file")
@app.get("/api/download")
def download_server_file_endpoint(file: Optional[str] = None, vf: Optional[str] = None, path: Optional[str] = None):
    """Serve any workspace, report, snapshot, or static download file directly to the browser as an attachment."""
    target = file or vf or path
    if not target:
        raise HTTPException(status_code=400, detail="Missing file query parameter (?file=... or ?vf=...)")
    
    clean_target = os.path.normpath(target.strip().lstrip("/"))
    # Search candidates
    candidates = [
        clean_target,
        os.path.join(".", clean_target),
        os.path.join("snapshots", clean_target),
        os.path.join("static", "downloads", clean_target),
        os.path.join("../runningly-chat-web/static/downloads", clean_target),
        os.path.basename(clean_target)
    ]
    found_path = None
    for c in candidates:
        if os.path.isfile(c):
            found_path = os.path.abspath(c)
            break
    
    if not found_path:
        raise HTTPException(status_code=404, detail=f"File '{clean_target}' not found on server")
    
    filename = os.path.basename(found_path)
    return FileResponse(
        path=found_path,
        filename=filename,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@app.get("/api/downloads")
def get_downloads():
    """Return downloads list."""
    downloads = []
    seen = set()
    for d in ["static/downloads", "../runningly-chat-web/static/downloads", "snapshots"]:
        if os.path.exists(d):
            for f in sorted(os.listdir(d)):
                if not f.startswith(".") and f not in seen and os.path.isfile(os.path.join(d, f)):
                    seen.add(f)
                    downloads.append({
                        "title": f,
                        "file_name": f,
                        "videoUrl": f"/download?file={f}",
                        "type": "download"
                    })
    return {"downloads": downloads}

@app.get("/api/history")
def get_legacy_history():
    """Compatibility endpoint for /api/history."""
    return {
        "conversations": ntqa_db.list_conversations(),
        "videos": []
    }

@app.post("/api/history")
def mutate_legacy_history():
    """Compatibility endpoint for /api/history POST mutations."""
    return {
        "conversations": ntqa_db.list_conversations(),
        "videos": []
    }

# ────────────────── SQLite History Endpoints ──────────────────

@app.get("/history")
def get_all_history():
    """Return all past conversations stored in SQLite."""
    return {"conversations": ntqa_db.list_conversations()}

@app.get("/history/{convo_id}")
def get_history_conversation(convo_id: str):
    """Retrieve full history for a specific conversation ID."""
    data = ntqa_db.get_conversation(convo_id)
    if not data:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return data

@app.post("/history")
def create_new_history_convo(req: CreateConversationRequest):
    """Create a new conversation record in SQLite."""
    convo = ntqa_db.create_conversation(title=req.title or "New Task Session")
    return convo

@app.delete("/history/{convo_id}")
def delete_history_convo(convo_id: str):
    """Delete a conversation from SQLite."""
    success = ntqa_db.delete_conversation(convo_id)
    if not success:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "deleted", "id": convo_id}

@app.delete("/history")
def clear_history():
    """Clear all past conversations in SQLite."""
    ntqa_db.clear_all_conversations()
    return {"status": "cleared"}

# ────────────────── Chat & Execution Endpoints ──────────────────

@app.post("/chat")
def chat(request: ChatRequest):

    session_id = request.session_id
    if not session_id or session_id not in sessions:
        session_id = session_id or str(uuid.uuid4())
        system_prompt = tm.generate_prompt()
        convo = task_run.client.chats.create(
            model=task_run.args.model,
            history=[{"role": "user", "parts": [{"text": system_prompt}]}]
        )
        sessions[session_id] = {"convo": convo, "last_tasks": []}
        # Record conversation in SQLite if not existing
        ntqa_db.add_message(session_id, "system", "Session initialized", msg_type="status")
    
    session = sessions[session_id]
    convo = session["convo"]
    
    # Save user message to SQLite
    ntqa_db.add_message(session_id, "user", request.message, msg_type="text")
    
    msg = request.message.lower().strip()
    
    # Handle special commands
    if msg in ['exit', 'quit', 'bye']:
        reply_text = "Exiting Task Assistant."
        ntqa_db.add_message(session_id, "assistant", reply_text, msg_type="status")
        return {
            "session_id": session_id,
            "status": "exit",
            "tasks": session["last_tasks"],
            "message": reply_text
        }
        
    if msg in ['run', 'automate']:
        reply_text = f"Ready to execute {len(session['last_tasks'])} task(s)."
        ntqa_db.add_message(session_id, "assistant", reply_text, msg_type="ready_to_execute", meta={"tasks": session["last_tasks"]})
        return {
            "session_id": session_id,
            "status": "ready_to_execute",
            "tasks": session["last_tasks"],
            "pause": msg == 'run',
            "message": reply_text
        }
    
    corrected_input = task_run.correct_spelling(request.message)

    # Auto-retry logic to extract all answers from prompt before asking user
    MAX_AUTO_RETRIES = 3
    parsed = None
    response_text = ""
    done = False
    normalized = None

    for attempt in range(1, MAX_AUTO_RETRIES + 1):
        if attempt == 1:
            send_text = corrected_input
        else:
            send_text = (
                f"(Retry {attempt}/{MAX_AUTO_RETRIES}) "
                "Please re-read the original prompt carefully and try again to extract ALL required answers from it. "
                "Do not ask the user anything yet — all answers should be in the conversation so far."
            )
        try:
            response = convo.send_message(send_text)
            response_text = task_run.get_response_text(response)
            print(f"\n--- DEBUG LLM RESULT ---\n{response_text}\n------------------------\n")
        except Exception as e:
            err_msg = str(e)
            if "503" in err_msg or "UNAVAILABLE" in err_msg:
                user_msg = "Gemini API is currently experiencing temporary high traffic (503). Please retry in a few moments."
            else:
                user_msg = f"Gemini API error: {err_msg}"
            ntqa_db.add_message(session_id, "assistant", user_msg, msg_type="error")
            return {
                "session_id": session_id,
                "status": "error",
                "message": user_msg,
                "tasks": []
            }
        parsed = task_run.try_parse_json(response_text)
        done, normalized = task_run.check_completed(parsed, tm)
        if done:
            break
    
    if done:
        session["last_tasks"] = normalized
        reply_text = "All required questions are answered. Ready to execute (type 'run' or 'automate' or click Run below)."
        ntqa_db.add_message(session_id, "assistant", reply_text, msg_type="complete", meta={"tasks": normalized})
        return {
            "session_id": session_id,
            "status": "complete",
            "tasks": normalized,
            "message": reply_text
        }
    else:
        if parsed and isinstance(parsed, list) and len(parsed) > 0:
            session["last_tasks"] = normalized if normalized else parsed
        reply_text = response_text if response_text and not response_text.strip().startswith(('[', '{')) else (
            "Identified tasks with partial answers. Ready to execute or answer remaining questions." if parsed else response_text
        )
        ntqa_db.add_message(session_id, "assistant", reply_text, msg_type="incomplete", meta={"parsed": parsed, "tasks": session["last_tasks"]})
        return {
            "session_id": session_id,
            "status": "incomplete",
            "message": reply_text,
            "tasks": session["last_tasks"],
            "parsed": parsed
        }

def resolve_context_reference(expr: str, execution_context: Dict[str, Any]) -> Any:
    """
    Resolve a reference expression like '${Select Users in Group.user_ids}'
    against execution_context. Returns the resolved value (which can be a list, dict, or str),
    or None if not found.
    """
    if not isinstance(expr, str):
        return None
    match = re.search(r'\$\{([^}]+)\}', expr)
    if not match:
        return None
    ref_body = match.group(1).strip()
    if '.' not in ref_body:
        return None
    ref_task, ref_key = ref_body.split('.', 1)
    ref_task_clean = ref_task.strip().lower()
    ref_key_clean = ref_key.strip()

    matched_dict = None
    for ctx_name, ctx_val in execution_context.items():
        if isinstance(ctx_val, dict):
            if ctx_name.lower() == ref_task_clean or ref_task_clean in ctx_name.lower() or ctx_name.lower() in ref_task_clean:
                matched_dict = ctx_val
                break

    if matched_dict is not None and ref_key_clean in matched_dict:
        return matched_dict[ref_key_clean]
    return None

def get_loop_context(task_info: Dict[str, Any], execution_context: Dict[str, Any]):
    """
    Detect if task_info explicitly specifies a loop over an array of items via 'for_each' or 'loop'.
    Returns (loop_items: list, for_each_ref: str) or (None, None).
    """
    for_each_expr = task_info.get("for_each") or task_info.get("loop")
    if not for_each_expr:
        return None, None

    if isinstance(for_each_expr, str) and "${" in for_each_expr:
        val = resolve_context_reference(for_each_expr, execution_context)
        if isinstance(val, list):
            return val, for_each_expr
    elif isinstance(for_each_expr, list):
        return for_each_expr, "explicit_list"

    return None, None

def build_iteration_data(data: Dict[str, Any], item: Any, for_each_ref: Optional[str]) -> Dict[str, Any]:
    """
    Construct data dict for a single iteration of a loop over 'item'.
    Substitutes ${item}, ${item.id}, or the for_each_ref string with the item's value.
    """
    new_data = {}
    if not isinstance(data, dict):
        return new_data

    item_str = ""
    if isinstance(item, dict):
        item_str = str(item.get("id") or item.get("login") or item.get("email") or json.dumps(item))
    else:
        item_str = str(item)

    for k, v in data.items():
        if not isinstance(v, str):
            new_data[k] = v
            continue

        val_str = v
        val_str = val_str.replace("${item}", item_str)
        val_str = val_str.replace("$item", item_str)

        if isinstance(item, dict):
            for prop_match in re.findall(r'\$\{item\.([^}]+)\}', val_str):
                val_str = val_str.replace(f"${{item.{prop_match}}}", str(item.get(prop_match, "")))

        if for_each_ref and for_each_ref in val_str:
            val_str = val_str.replace(for_each_ref, item_str)

        new_data[k] = val_str

    return new_data

def run_single_task(task_idx: int, task_info: Dict[str, Any], execution_context: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    task_name = task_info.get("real_task_name") or task_info.get("task")
    display_title = task_info.get("task")
    data = task_info.get("data", {})
    
    task_config = tm.get_task_by_name(task_name)
    command_template = task_config.get("command") if task_config else None

    if command_template:
        if isinstance(command_template, list):
            command_template = "\n".join(command_template)
            
        command = command_template
        if task_config:
            questions = task_config.get("questions", [])
            for i, q in enumerate(questions):
                ans = data.get(q, "")
                command = command.replace(f"${{answer{i+1}}}", str(ans))
                command = command.replace(f"${{{q}}}", str(ans))
                
        matches = re.findall(r'\$\{([^}]+)\}', command)
        for match in matches:
            if '.' in match:
                ref_task, ref_key = match.split('.', 1)
                matched_val = None
                if ref_task in execution_context and isinstance(execution_context[ref_task], dict):
                    matched_val = execution_context[ref_task].get(ref_key, "")
                else:
                    for ctx_name, ctx_dict in execution_context.items():
                        if isinstance(ctx_dict, dict) and (ref_task.lower() in ctx_name.lower() or ctx_name.lower() in ref_task.lower()):
                            matched_val = ctx_dict.get(ref_key, "")
                            break
                if matched_val is not None:
                    if isinstance(matched_val, (dict, list)):
                        val_str = json.dumps(matched_val)
                    else:
                        val_str = str(matched_val)
                    if f'"{match}"' in command or f'"${{{match}}}"' in command:
                        val_str_escaped = val_str.replace('\\', '\\\\').replace('"', '\\"')
                        command = command.replace(f"${{{match}}}", val_str_escaped)
                    else:
                        command = command.replace(f"${{{match}}}", val_str)
        
        if "${" not in command_template:
            import shlex
            json_data_str = json.dumps(data)
            command = f"{command_template} {shlex.quote(json_data_str)}"
            
        cmd_args = []
    else:
        cmd_list = list(data.values())
        if not cmd_list:
            return {"task": display_title, "error": "No command to run.", "status": "error"}
        command = cmd_list[0]
        cmd_args = cmd_list[1:]

    try:
        import subprocess
        if command_template:
            result = subprocess.run(command, capture_output=True, shell=True, text=True)
        else:
            result = subprocess.run([command] + cmd_args, capture_output=True, shell=True, text=True)
        print(f"result for task {display_title} (#{task_idx + 1}): {result}")
        output = result.stdout.strip()
        error = result.stderr.strip()
        
        if output:
            parsed_output = None
            try:
                parsed_output = json.loads(output)
            except json.JSONDecodeError:
                for line in reversed(output.splitlines()):
                    line_s = line.strip()
                    if line_s.startswith("{") and line_s.endswith("}"):
                        try:
                            parsed_output = json.loads(line_s)
                            break
                        except Exception:
                            pass
            if parsed_output and isinstance(parsed_output, dict):
                execution_context[task_name] = parsed_output
                execution_context[task_name.lower()] = parsed_output
        
        # If auto_summarize is true, summarize with Gemini
        auto_summarize = task_config.get("auto_summarize", False) if task_config else False
        summary = None
        if auto_summarize and output:
            summary_prompt = (
                f"The following is the output of a task called '{task_name}':\n\n{output}"
            )
            if error:
                summary_prompt += f"\n\nErrors encountered:\n{error}"
            summary_prompt += (
                "\n\nProvide a concise, plain-text summary of this result."
                "\nFormatting rules — follow strictly:"
                "\n- Use plain newlines to separate items, NOT markdown bold (**text**), NOT bullet symbols (* or -), NOT numbered lists."
                "\n- Use a single colon after a label, like: 'Status: Success' or 'Users recreated: 1'"
                "\n- Each distinct piece of information goes on its own line."
                "\n- No headers, no markdown syntax of any kind."
                "\nExample good output:"
                "\nStatus: Completed successfully"
                "\nSnapshot used: snapshots/users_snapshot_before.json"
                "\nUsers recreated: 1"
                "\nUsers updated: 0"
                "\nUsers skipped: 188"
            )
            try:
                reply = task_run.client.chats.create(model=task_run.args.model).send_message(summary_prompt)
                summary = task_run.get_response_text(reply).strip()
                print(f"\n--- DEBUG LLM SUMMARY ---\n{summary}\n-------------------------\n")
            except Exception as sum_e:
                summary = f"Execution finished (summary generation skipped: {str(sum_e)})"
        
        # Determine status
        task_status = "success"
        if result.returncode != 0:
            task_status = "error"
        elif 'parsed_output' in locals() and parsed_output and isinstance(parsed_output, dict):
            if "errorCode" in parsed_output or "error" in parsed_output or "errorSummary" in parsed_output:
                task_status = "error"

        ntqa_db.add_task_run(
            convo_id=session_id,
            task_name=display_title,
            command=command,
            status=task_status,
            output=output,
            error=error,
            summary=summary
        )
        
        return {
            "task": display_title,
            "real_task_name": task_name,
            "command": command,
            "output": output,
            "error": error,
            "summary": summary,
            "status": task_status
        }
    except Exception as e:
        ntqa_db.add_task_run(
            convo_id=session_id,
            task_name=display_title,
            command=command if 'command' in locals() else "unknown",
            status="error",
            output="",
            error=str(e),
            summary=None
        )
        return {
            "task": display_title,
            "real_task_name": task_name,
            "command": command if 'command' in locals() else "",
            "output": "",
            "error": str(e),
            "status": "error"
        }

@app.post("/execute")
def execute(request: ExecuteRequest):
    execution_context = {}
    results = []
    session_id = request.session_id or str(uuid.uuid4())
    
    for task_idx, task_info in enumerate(request.tasks):
        loop_items, for_each_ref = get_loop_context(task_info, execution_context)
        if loop_items is not None:
            base_name = task_info.get("task")
            if len(loop_items) == 0:
                skip_res = {
                    "task": f"{base_name} (0 items)",
                    "real_task_name": base_name,
                    "command": "noop",
                    "output": json.dumps({"status": "skipped", "message": f"0 items found in {for_each_ref}"}),
                    "error": "",
                    "summary": "Loop skipped: 0 items returned from upstream task.",
                    "status": "success"
                }
                results.append(skip_res)
                continue

            loop_failed = False
            for i, item in enumerate(loop_items):
                item_label = item.get("login") or item.get("id") if isinstance(item, dict) else str(item)
                iter_task_info = {
                    "task": f"{base_name} [{i+1}/{len(loop_items)}: {item_label}]",
                    "real_task_name": base_name,
                    "data": build_iteration_data(task_info.get("data", {}), item, for_each_ref)
                }
                iter_res = run_single_task(task_idx, iter_task_info, execution_context, session_id)
                results.append(iter_res)
                if iter_res.get("status") == "error":
                    loop_failed = True
                    break
            if loop_failed:
                break
        else:
            task_res = run_single_task(task_idx, task_info, execution_context, session_id)
            results.append(task_res)
            
            # Stop on failure
            if task_res.get("status") == "error":
                break
                
            # Pause handling
            if request.pause and task_info != request.tasks[-1]:
                return {"status": "paused", "results": results, "execution_context": execution_context, "session_id": session_id}

    has_failures = any(r.get("status") == "error" for r in results)
    content_msg = f"Execution stopped because a task failed. Please review the logs and try again." if has_failures else f"Execution completed for {len(results)} task(s)."
    
    ntqa_db.add_message(
        convo_id=session_id,
        role="assistant",
        content=content_msg,
        msg_type="task_execution",
        meta={"tasks": request.tasks, "results": results}
    )

    return {"status": "success", "results": results, "session_id": session_id}

@app.post("/execute_stream")
async def execute_stream(request: ExecuteRequest):
    session_id = request.session_id or str(uuid.uuid4())

    async def event_generator():
        execution_context = {}
        results = []
        
        for task_idx, task_info in enumerate(request.tasks):
            task_name = task_info.get("task", f"Task {task_idx + 1}")
            loop_items, for_each_ref = get_loop_context(task_info, execution_context)

            if loop_items is not None:
                total_items = len(loop_items)
                if total_items == 0:
                    start_payload = {
                        "type": "task_start",
                        "task_index": task_idx,
                        "task_name": f"{task_name} (0 items)"
                    }
                    yield f"data: {json.dumps(start_payload)}\n\n"
                    skip_res = {
                        "task": f"{task_name} (0 items)",
                        "real_task_name": task_name,
                        "command": "noop",
                        "output": json.dumps({"status": "skipped", "message": f"0 items found in {for_each_ref}"}),
                        "error": "",
                        "summary": "Loop skipped: 0 items returned from upstream task.",
                        "status": "success"
                    }
                    results.append(skip_res)
                    yield f"data: {json.dumps({'type': 'task_result', 'task_index': task_idx, 'result': skip_res})}\n\n"
                    continue

                # Emit task_start for the loop task
                start_payload = {
                    "type": "task_start",
                    "task_index": task_idx,
                    "task_name": f"{task_name} (Looping {total_items} items)"
                }
                yield f"data: {json.dumps(start_payload)}\n\n"

                iter_results = []
                loop_failed = False
                for i, item in enumerate(loop_items):
                    item_label = item.get("login") or item.get("id") if isinstance(item, dict) else str(item)
                    iter_task_info = {
                        "task": f"{task_name} [{i+1}/{total_items}: {item_label}]",
                        "real_task_name": task_name,
                        "data": build_iteration_data(task_info.get("data", {}), item, for_each_ref)
                    }
                    iter_res = await asyncio.to_thread(
                        run_single_task, task_idx, iter_task_info, execution_context, session_id
                    )
                    iter_results.append(iter_res)
                    if iter_res.get("status") == "error":
                        loop_failed = True
                        break

                all_success = all(r.get("status") == "success" for r in iter_results)
                succ_count = sum(1 for r in iter_results if r.get("status") == "success")
                fail_count = sum(1 for r in iter_results if r.get("status") == "error")
                summary_text = (
                    f"Status: {'Completed successfully' if all_success else 'Finished with errors'}\n"
                    f"Total items processed: {len(iter_results)} of {total_items}\n"
                    f"Succeeded: {succ_count}\n"
                    f"Failed: {fail_count}"
                )
                combined_output = "\n".join(
                    f"[{r.get('task')}]: {r.get('output') or r.get('error')}"
                    for r in iter_results
                )
                consolidated_res = {
                    "task": f"{task_name} ({len(iter_results)} items processed)",
                    "real_task_name": task_name,
                    "command": f"for item in {for_each_ref}: {task_name} (x{len(iter_results)})",
                    "output": combined_output,
                    "error": "\n".join(r.get("error", "") for r in iter_results if r.get("error")),
                    "summary": summary_text,
                    "status": "success" if all_success else "error",
                    "iterations": iter_results
                }
                results.append(consolidated_res)

                # Emit task_result with consolidated status
                result_payload = {
                    "type": "task_result",
                    "task_index": task_idx,
                    "result": consolidated_res
                }
                yield f"data: {json.dumps(result_payload)}\n\n"

                if loop_failed:
                    break

                if request.pause and task_info != request.tasks[-1]:
                    yield f"data: {json.dumps({'type': 'paused', 'task_index': task_idx})}\n\n"
                    break
            else:
                # Normal single task execution
                start_payload = {
                    "type": "task_start",
                    "task_index": task_idx,
                    "task_name": task_name
                }
                yield f"data: {json.dumps(start_payload)}\n\n"
                
                # Execute task in background thread
                task_res = await asyncio.to_thread(
                    run_single_task, task_idx, task_info, execution_context, session_id
                )
                results.append(task_res)
                
                # Emit task_result event
                result_payload = {
                    "type": "task_result",
                    "task_index": task_idx,
                    "result": task_res
                }
                yield f"data: {json.dumps(result_payload)}\n\n"
                
                if task_res.get("status") == "error":
                    break
                    
                if request.pause and task_info != request.tasks[-1]:
                    yield f"data: {json.dumps({'type': 'paused', 'task_index': task_idx})}\n\n"
                    break
        
        has_failures = any(r.get("status") == "error" for r in results)
        content_msg = f"Execution stopped because a task failed. Please review the logs and try again." if has_failures else f"Execution completed for {len(results)} task(s)."
        
        ntqa_db.add_message(
            convo_id=session_id,
            role="assistant",
            content=content_msg,
            msg_type="task_execution",
            meta={"tasks": request.tasks, "results": results}
        )
        
        complete_payload = {
            "type": "complete",
            "status": "error" if has_failures else "success",
            "results": results,
            "session_id": session_id
        }
        yield f"data: {json.dumps(complete_payload)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

# Serve built frontend static files if present
from fastapi.staticfiles import StaticFiles
if os.path.exists("static") and os.path.exists("static/index.html"):
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
elif os.path.exists("../runningly-chat-web/dist"):
    app.mount("/", StaticFiles(directory="../runningly-chat-web/dist", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)


