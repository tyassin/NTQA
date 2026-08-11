import os
import re
import json
import argparse
from google import genai
import subprocess
import shlex
import readline
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# === CLI ARGUMENTS ===
parser = argparse.ArgumentParser(description="Run Task Assistant with Gemini")
parser.add_argument('--api-key', type=str, default=os.getenv("GEMINI-API-KEY"), help="Gemini API key")
parser.add_argument('--model', type=str, default=os.getenv("GEMINI-MODEL"), help="Gemini model to use")
args = parser.parse_args()

# === CONFIGURE GEMINI ===
client = genai.Client(api_key=args.api_key)

TASK_FOLDER = "tasks"

class TaskManager:
    def __init__(self, folder):
        self.tasks = self.load_tasks(folder)

    def load_tasks(self, folder):
        tasks = []
        for file in os.listdir(folder):
            if file.endswith(".json"):
                with open(os.path.join(folder, file)) as f:
                    task = json.load(f)
                    task["output_schema"] = task.get("output_schema", {
                        self.clean_key(q): "" for q in task.get("questions", [])
                    })
                    task["key_map"] = {
                        self.clean_key(q): q for q in task.get("questions", [])
                    }
                    tasks.append(task)
        return tasks

    def clean_key(self, question):
        # cleaned = question.strip().rstrip("*").rstrip("?").lower()
        # cleaned = re.sub(r"[^a-z0-9\s_]", "", cleaned)
        # return re.sub(r"\s+", "_", cleaned)
        return question

    def get_required_keys(self, task):
        return [k for k, q in task["key_map"].items() if q.strip().endswith("*")]

    def get_task_by_name(self, name):
        for t in self.tasks:
            if t["task_name"].lower() in name.lower():
                return t
        return None

    def generate_prompt(self):
        base = (
            "You are a task assistant that helps users complete dynamic tasks.\n\n"
            "Instructions:\n"
            "- A user will describe one or more tasks.\n"
            "- Identify the task names and required questions for each task.\n"
            "- IMPORTANT: Before asking ANY question, carefully scan the entire user prompt for all possible answers to all identified tasks across all turns of the conversation. Only ask a required question if you are certain it is not anywhere in the prompt.\n"
            "- Ask only one unanswered required question at a time across all identified tasks.\n"
            "- Don't ask non-required questions, unless the user provides the question and its answer.\n"
            "- If a task's question can be answered using a reference to another identified task's output (e.g., '${Get Future Date.start_date}', '${Get Future Date.end_date}', or '${Select Group By Name.id}'), automatically fill in that reference as the answer instead of asking the user.\n"
            "- If the user prompt specifies an attribute name such as ('weather') or ('note'), that attribute is the exact name in a reference task's output (e.g., '${Get Weather in Period.weather}', '${Get Weather in Period.note}', or '${Select Group By Name.id}'), automatically fill in that reference as the answer instead of asking the user.\n"
            "- find the group then directly assign the user to the group. don't do 2 lookups for the group. just do one lookup and get the group id and then assign the user to the group.\n"
            "- If tasks are repeated is such a way find or selct and then create or update or assing, run the select/find task first and then the create/update/assign task after the select/find task since the data is related and IDs maybe overwritten in the memory due to the previous task's output.(e.g. find user and then update user, or find group and then add or assign user to group)\n"
            "- If all required questions for all identified tasks are answered, return a JSON list of completed tasks:\n"
            "  [\n"
            "    {\n"
            '      "task": "task_name_1",\n'
            '      "data": {\n'
            '        "key1": "value1"\n'
            '      }\n'
            "    },\n"
            "    {\n"
            '      "task": "task_name_2",\n'
            '      "data": {\n'
            '        "key2": "value2"\n'
            '      }\n'
            "    }\n"
            "  ]\n"
            "- If only a single task is identified and completed, you can return a single JSON object or a list containing it.\n"
            "- Only print JSON when all identified tasks are complete. Never partial.\n"
            "- If user says exit/quit/bye, print the final JSON (complete or not) and say thank you.\n"
            "- After printing final JSON, say:\n"
            "  💬 How can I help more? Do you still need me to change the query or start a new one?\n"
            '  You may also type "automate" or "run" to execute the completed task(s).\n'
            "\nTasks:"
        )
        for t in self.tasks:
            base += f"\n\nTask: {t['task_name']}\nDescription: {t['description']}\nQuestions: {json.dumps(t['questions'])}\nOutput Schema: {json.dumps(t['output_schema'])}"
        return base

def ask_user_single_line(prompt_text):
    return input(f"\n📝 {prompt_text}\n> ").strip()

def ask_user_multi_lines(prompt_text):
    print(f"\n📝 {prompt_text}")
    print("   (type your message across multiple lines — press Enter on a blank line to submit)")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == "" and lines:
            # blank line submits
            break
        lines.append(line)
    text = "\n".join(lines).strip()
    if text:
        readline.add_history(text)
    return text


def correct_spelling(text):
    """Fix spelling/grammar in user input silently before evaluation."""
    try:
        fix_prompt = (
            "Fix any spelling mistakes, typos, and grammar errors in the following text. "
            "Keep the original meaning, names, and intent exactly as-is. "
            "Return only the corrected text with no explanation:\n\n" + text
        )
        result = client.models.generate_content(model=args.model, contents=fix_prompt)
        corrected = result.text.strip()
        if corrected and corrected != text:
            print(f"✏️  (spell-corrected) {corrected}")
        return corrected if corrected else text
    except Exception:
        return text


def check_completed(parsed, tm):
    """Return (is_complete, normalized_list_or_None)."""
    if not parsed:
        return False, None
    if isinstance(parsed, dict):
        parsed = [parsed]
    if isinstance(parsed, list):
        for t_parsed in parsed:
            t_name = t_parsed.get("task")
            t_data = t_parsed.get("data", {})
            task = tm.get_task_by_name(t_name) if t_name else None
            if not task or not is_complete(tm.get_required_keys(task), t_data):
                return False, parsed
        return True, parsed
    return False, None


def merge_answers(existing, new_data):
    for k, v in new_data.items():
        if v is not None and str(v).strip():
            existing[k] = v
    return existing

def is_complete(required_keys, answers):
    return all(k in answers and str(answers[k]).strip() for k in required_keys)

def print_final(tasks):
    print(f"\n📦 Final JSON: (Executing {len(tasks)})")
    print(json.dumps(tasks, indent=2))
    print("\n✅ Thank you for using Task Assistant service.")
    print("👋 Exiting Task Assistant.")

def try_parse_json(text):
    # Strip markdown code block if present
    if text.strip().startswith("```json"):
        text = text.strip()[7:].strip()  # remove ```json and leading space/newlines
    if text.strip().endswith("```"):
        text = text.strip()[:-3].strip()  # remove ending ```
    match = re.search(r"(\[.*\]|\{.*\})", text, re.DOTALL)
    if match:
        raw_text = match.group(0)
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as e:
            # Fallback to ast.literal_eval for single-quoted or trailing-comma structures
            try:
                import ast
                return ast.literal_eval(raw_text)
            except Exception:
                print("⚠️ JSON decode error:", e)
    print ("DONE!!!!")
    return None


def execute_task(task_name, data, tm=None, context=None, pause=True):
    if context is None:
        context = {}
        
    print(f"\n⚙️ Now running task: {task_name or 'unknown'} with data:")
    print(json.dumps(data, indent=2))

    task_config = tm.get_task_by_name(task_name) if tm else None
    command_template = task_config.get("command") if task_config else None

    if command_template:
        # Support multi-line commands defined as a JSON array
        if isinstance(command_template, list):
            command_template = "\n".join(command_template)
            
        command = command_template
        if task_config:
            questions = task_config.get("questions", [])
            for i, q in enumerate(questions):
                ans = data.get(q, "")
                # Support ${answer1}, ${answer2}, etc. (1-indexed)
                command = command.replace(f"${{answer{i+1}}}", str(ans))
                # Also support exact question string replacement: ${What is the new okta group name?*}
                command = command.replace(f"${{{q}}}", str(ans))
                
        # Support context variable replacement (e.g. ${Create Okta Group.id})
        matches = re.findall(r'\$\{([^}]+)\}', command)
        for match in matches:
            if '.' in match:
                ref_task, ref_key = match.split('.', 1)
                if ref_task in context and isinstance(context[ref_task], dict):
                    val = context[ref_task].get(ref_key, "")
                    command = command.replace(f"${{{match}}}", str(val))
        
        # If the command template didn't use variable substitution, fallback to appending the JSON string
        if "${" not in command_template:
            json_data_str = json.dumps(data)
            command = f"{command_template} {shlex.quote(json_data_str)}"
            
        cmd_args = []
    else:
        cmd_list = list(data.values())
        if not cmd_list:
            print("❌ No command to run.")
            return
        command = cmd_list[0]
        cmd_args = cmd_list[1:]

    try:
        if command_template:
            print(f"▶️ Executing: {command}\n")
            result = subprocess.run(command, capture_output=True, shell=True, text=True)
        else:
            print(f"▶️ Executing: {command} {' '.join(cmd_args)}\n")
            result = subprocess.run([command] + cmd_args, capture_output=True, shell=True, text=True)
        output = result.stdout.strip()
        error = result.stderr.strip()
        print("📤 Output:")
        print(output or "[No Output]")
        if error:
            print("⚠️ Errors:")
            print(error)
            
        # Store JSON output in context for future tasks
        if output:
            try:
                parsed_output = json.loads(output)
                context[task_name] = parsed_output
            except json.JSONDecodeError:
                pass

        # Check if this task has auto_summarize enabled
        auto_summarize = task_config.get("auto_summarize", False) if task_config else False
        execution_convo = client.chats.create(model=args.model)
        output_sent = False

        print("\n✅ Task execution finished. Output is stored in memory.")

        if auto_summarize and output:
            print("🤖 Generating automatic summary...")
            summary_prompt = f"The following is the output of a task called '{task_name}':\n\n{output}"
            if error:
                summary_prompt += f"\n\nErrors encountered:\n{error}"
            summary_prompt += "\n\nPlease provide a clear, helpful, human-friendly summary of this result."
            reply = execution_convo.send_message(summary_prompt)
            output_sent = True
            print("🤖", reply.text.strip())
            print("")
        else:
            if pause:
                # Loop for user interaction in execution mode
                while True:
                    follow_up = ask_user_single_line("(Execution Mode) Ask about result or type 'Continue' to execute the next task:")
                    if follow_up.lower() in ['continue', 'c']:
                        print("🔙 Continuing to the next task...")
                        break

                    if not output_sent:
                        print("💬 Sending output to Gemini for analysis...")
                        summary_prompt = f"The output of the command `{command}` was:\n\n{output or '[No output]'}"
                        if error:
                            summary_prompt += f"\n\nThere were also errors:\n{error}"
                        summary_prompt += f"\n\nUser Question: {follow_up}"
                        reply = execution_convo.send_message(summary_prompt)
                        output_sent = True
                    else:
                        reply = execution_convo.send_message(follow_up)

                    print("🤖", reply.text.strip())
            else:
                print("🤖 Auto-summarizing the result of task: " + task_name)
                summary_prompt = f"The output of the command `{command}` was:\n\n{output or '[No output]'}"
                if error:
                    summary_prompt += f"\n\nThere were also errors:\n{error}"
                reply = execution_convo.send_message(summary_prompt)
                print("🤖", reply.text.strip())
    except FileNotFoundError:
        print(f"❌ Command not found: {command}")
    except Exception as e:
        print(f"❌ Error while executing: {e}")
    print("✅ Task completed.\n")

last_tasks = []

def main():
    global last_tasks
    tm = TaskManager(TASK_FOLDER)
    system_prompt = tm.generate_prompt()

    print(f"🤖 Gemini Task Assistant Initialized using model: {args.model}")
    while True:
        convo = client.chats.create(model=args.model, history=[{"role": "user", "parts": [{"text": system_prompt}]}])
        if last_tasks:
            sys_prompt = "🧠 What do you want to do? (type 'exit' to quit, 'run' to execute one task with a pause, or 'automate' to execute all tasks without pauses)"
            user_input = ask_user_single_line(sys_prompt)
        else:
            sys_prompt = "🧠 What do you want to do? (type 'exit' to quit)"
            user_input = ask_user_multi_lines(sys_prompt)
        #if not last_tasks:
        #    user_input += ". All required questions are answered in this prompt."
        if user_input.lower() in ['exit', 'quit', 'bye']:
            print_final(last_tasks)
            break
        #print("user_input we'll be using is: " + user_input)
        if user_input.lower() in ['run', 'automate']:
            if user_input.lower() == 'automate':
                pause = False
            else:
                pause = True
            print("pause is: " + str(pause))
            if last_tasks:
                print(f"⚠️ Running {len(last_tasks)} task(s) in sequence...")
                execution_context = {}
                for task_info in last_tasks:
                    execute_task(task_info.get("task"), task_info.get("data", {}), tm, execution_context, pause)
            else:
                print("⚠️ No previous task found to run.")
            continue

        # --- Step 1: fix spelling silently ---
        corrected_input = correct_spelling(user_input)

        # --- Step 2: auto-retry up to 3 times before asking the user ---
        MAX_AUTO_RETRIES = 3
        is_completed_flow = False
        parsed = None
        response = None

        for attempt in range(1, MAX_AUTO_RETRIES + 1):
            if attempt == 1:
                send_text = corrected_input
            else:
                # On retries, remind the LLM to look harder in the original prompt
                send_text = (
                    f"(Retry {attempt}/{MAX_AUTO_RETRIES}) "
                    "Please re-read the original prompt carefully and try again to extract ALL required answers from it. "
                    "Do not ask the user anything yet — all answers should be in the conversation so far."
                )
            response = convo.send_message(send_text)
            parsed = try_parse_json(response.text)
            done, normalized = check_completed(parsed, tm)
            if done:
                last_tasks = normalized
                is_completed_flow = True
                break
            if attempt < MAX_AUTO_RETRIES:
                print(f"🔄 Auto-retry {attempt}/{MAX_AUTO_RETRIES} — still extracting answers from your prompt...")

        # Show LLM response if still incomplete after all retries
        if not is_completed_flow:
            if not parsed:
                print(response.text)
            else:
                print(json.dumps(parsed, indent=2))

        # --- Step 3: interactive Q&A (only once auto-retries are exhausted) ---
        while not is_completed_flow:
            user_input = ask_user_single_line("Your answer (or type exit):")
            if user_input.lower() in ['exit', 'quit', 'bye']:
                print_final(last_tasks)
                return

            if user_input.lower() in ['run', 'automate']:
                if user_input.lower() == 'automate':
                    pause = False
                else:
                    pause = True
                if last_tasks:
                    print(f"⚠️ Running {len(last_tasks)} task(s) in sequence...")
                    execution_context = {}
                    for task_info in last_tasks:
                        execute_task(task_info.get("task"), task_info.get("data", {}), tm, execution_context, pause)
                else:
                    print("⚠️ No previous task found to run.")
                continue

            # Fix spelling in follow-up answers too
            corrected_answer = correct_spelling(user_input)
            response = convo.send_message(corrected_answer)
            parsed = try_parse_json(response.text)
            done, normalized = check_completed(parsed, tm)
            if done:
                last_tasks = normalized
                is_completed_flow = True
            else:
                if not parsed:
                    print(response.text)
                else:
                    print(json.dumps(parsed, indent=2))

        print_final(last_tasks)

if __name__ == "__main__":
    main()
