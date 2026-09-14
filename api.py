import os
import sys
import json
import uuid
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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

app = FastAPI(title="NTQA API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5000",
        "http://localhost:5173",
        "http://localhost:3000",
        "https://runningly-chat-web.vercel.app",
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

@app.get("/tasks")
def get_tasks():
    """Return all available tasks for the UI to display in a collapse/expand structure."""
    tm.tasks = tm.load_tasks(TASK_FOLDER)
    return {"tasks": tm.tasks}

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
        response = convo.send_message(send_text)
        response_text = task_run.get_response_text(response)
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

@app.post("/execute")
def execute(request: ExecuteRequest):
    execution_context = {}
    results = []
    session_id = request.session_id or str(uuid.uuid4())
    
    for task_info in request.tasks:
        task_name = task_info.get("task")
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
                    
            import re
            matches = re.findall(r'\$\{([^}]+)\}', command)
            for match in matches:
                if '.' in match:
                    ref_task, ref_key = match.split('.', 1)
                    if ref_task in execution_context and isinstance(execution_context[ref_task], dict):
                        val = execution_context[ref_task].get(ref_key, "")
                        command = command.replace(f"${{{match}}}", str(val))
            
            if "${" not in command_template:
                import shlex
                json_data_str = json.dumps(data)
                command = f"{command_template} {shlex.quote(json_data_str)}"
                
            cmd_args = []
        else:
            cmd_list = list(data.values())
            if not cmd_list:
                results.append({"task": task_name, "error": "No command to run."})
                continue
            command = cmd_list[0]
            cmd_args = cmd_list[1:]

        try:
            import subprocess
            if command_template:
                result = subprocess.run(command, capture_output=True, shell=True, text=True)
            else:
                result = subprocess.run([command] + cmd_args, capture_output=True, shell=True, text=True)
            print(f"result for the task {task_name} and task number {i}: {result}")
            output = result.stdout.strip()
            error = result.stderr.strip()
            
            if output:
                try:
                    parsed_output = json.loads(output)
                    execution_context[task_name] = parsed_output
                except json.JSONDecodeError:
                    pass
            
            # If auto_summarize is true, summarize with Gemini
            auto_summarize = task_config.get("auto_summarize", False) if task_config else False
            summary = None
            if auto_summarize and output:
                summary_prompt = f"The following is the output of a task called '{task_name}':\n\n{output}"
                if error:
                    summary_prompt += f"\n\nErrors encountered:\n{error}"
                summary_prompt += "\n\nPlease provide a clear, helpful, human-friendly summary of this result."
                try:
                    reply = task_run.client.chats.create(model=task_run.args.model).send_message(summary_prompt)
                    summary = task_run.get_response_text(reply).strip()
                except Exception as sum_e:
                    summary = f"Execution finished (summary generation skipped: {str(sum_e)})"
            
            # Persist to SQLite
            ntqa_db.add_task_run(
                convo_id=session_id,
                task_name=task_name,
                command=command,
                status="error" if error and not output else "success",
                output=output,
                error=error,
                summary=summary
            )
            
            results.append({
                "task": task_name,
                "command": command,
                "output": output,
                "error": error,
                "summary": summary
            })
            
            # If pause is true and there are more tasks, stop for UI pause
            if request.pause and task_info != request.tasks[-1]:
                return {"status": "paused", "results": results, "execution_context": execution_context, "session_id": session_id}
            
        except Exception as e:
            ntqa_db.add_task_run(
                convo_id=session_id,
                task_name=task_name,
                command=command if 'command' in locals() else "unknown",
                status="error",
                output="",
                error=str(e),
                summary=None
            )
            results.append({
                "task": task_name,
                "command": command if 'command' in locals() else "",
                "error": str(e)
            })

    # Add machine execution summary message to conversation
    ntqa_db.add_message(
        convo_id=session_id,
        role="assistant",
        content=f"Execution completed for {len(results)} task(s).",
        msg_type="task_execution",
        meta={"results": results}
    )

    return {"status": "success", "results": results, "session_id": session_id}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)


