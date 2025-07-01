import os
import re
import json
import argparse
import google.generativeai as genai
import subprocess

# === CLI ARGUMENTS ===
parser = argparse.ArgumentParser(description="Run Task Assistant with Gemini")
parser.add_argument('--api-key', type=str, required=True, help="Gemini API key")
parser.add_argument('--model', type=str, default="gemini-2.0-flash", help="Gemini model to use")
args = parser.parse_args()

# === CONFIGURE GEMINI ===
genai.configure(api_key=args.api_key)
model = genai.GenerativeModel(args.model)

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
        cleaned = question.strip().rstrip("*").rstrip("?").lower()
        cleaned = re.sub(r"[^a-z0-9\s_]", "", cleaned)
        return re.sub(r"\s+", "_", cleaned)

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
            "- A user will describe a task.\n"
            "- Identify the task name and required questions. "
            "- Apply all questions to the given prompt to identify answers if found.\n"
            "- Ask only one unanswered required question at a time.\n"
            "- Don't ask non-required questions, unless the user provides the question and its answer.\n"
            "- If all required questions are answered, return:\n"
            "  {\n"
            '    "task": "task_name",\n'
            '    "data": {\n'
            '      "key1": "value1"\n'
            "    }\n"
            "  }\n"
            "- Only print JSON when it's complete. Never partial.\n"
            "- If user says exit/quit/bye, print the final JSON (complete or not) and say thank you.\n"
            "- After printing final JSON, say:\n"
            "  💬 How can I help more? Do you still need me to change the query or start a new one?\n"
            '  You may also type "automate" or "run" to execute the completed task.\n'
            "\nTasks:"
        )
        for t in self.tasks:
            base += f"\n\nTask: {t['task_name']}\nDescription: {t['description']}\nQuestions: {json.dumps(t['questions'])}\nOutput Schema: {json.dumps(t['output_schema'])}"
        return base

def ask_user(prompt_text):
    return input(f"\n📝 {prompt_text}\n> ").strip()

def merge_answers(existing, new_data):
    for k, v in new_data.items():
        if v is not None and str(v).strip():
            existing[k] = v
    return existing

def is_complete(required_keys, answers):
    return all(k in answers and str(answers[k]).strip() for k in required_keys)

def print_final(task_name, answers):
    print("\n📦 Final JSON:")
    print(json.dumps({"task": task_name or "unknown_task", "data": answers}, indent=2))
    print("\n✅ Thank you for using Task Assistant service.")
    print("👋 Exiting Task Assistant.")

def try_parse_json(text):
    # Strip markdown code block if present
    if text.strip().startswith("```json"):
        text = text.strip()[7:].strip()  # remove ```json and leading space/newlines
    if text.strip().endswith("```"):
        text = text.strip()[:-3].strip()  # remove ending ```
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as e:
            print("⚠️ JSON decode error:", e)
    print ("DONE!!!!")
    return None

def try_parse_json1(text):
    """
    Extract and parse the first JSON object from a text string.
    """
    try:
        # Match the first {...} block using a simple bracket balance algorithm
        start = text.find('{')
        if start == -1:
            return None
        brace_count = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                brace_count += 1
            elif text[i] == '}':
                brace_count -= 1
            if brace_count == 0:
                json_str = text[start:i+1]
                return json.loads(json_str)
    except Exception as e:
        print("⚠️ Failed to extract JSON:", e)
    return None

def run_task_simulation(task_name, data):
    print(f"\n⚙️ Now running task: {task_name or 'unknown'} with data:")
    print(json.dumps(data, indent=2))

    cmd_list = list(data.values())
    if not cmd_list:
        print("❌ No command to run.")
        return
    command = cmd_list[0]
    args = cmd_list[1:]
    try:
        print(f"▶️ Executing: {command} {' '.join(args)}\n")
        result = subprocess.run([command] + args, capture_output=True, text=True)
        output = result.stdout.strip()
        error = result.stderr.strip()
        print("📤 Output:")
        print(output or "[No Output]")
        if error:
            print("⚠️ Errors:")
            print(error)

        # ✨ Send summary request to Gemini
        # === Send output to new Gemini convo ===
        print("\n💬 Sending output to Gemini for analysis...")
        execution_convo = model.start_chat()
        summary_prompt = f"The output of the command `{command}` is:\n\n{output or '[No output]'}"
        if error:
            summary_prompt += f"\n\nThere were also errors:\n{error}"

        response = execution_convo.send_message(summary_prompt)
        print("\n🤖 Gemini Summary:")
        print(response.text.strip())

        # Loop for user interaction in execution mode
        while True:
            follow_up = ask_user("(Execution Mode) Ask about result or type 'go back':")
            if follow_up.lower() in ['go back']:
                print("🔙 Returning to main task assistant...")
                break
            reply = execution_convo.send_message(follow_up)
            print("🤖", reply.text.strip())
    except FileNotFoundError:
        print(f"❌ Command not found: {command}")
    except Exception as e:
        print(f"❌ Error while executing: {e}")
    print("✅ Task completed.\n")

last_task_name = None
last_collected = {}

def main():
    global last_task_name, last_collected
    tm = TaskManager(TASK_FOLDER)
    system_prompt = tm.generate_prompt()

    print(f"🤖 Gemini Task Assistant Initialized using model: {args.model}")
    while True:
        collected = {}
        task_name = None
        convo = model.start_chat(history=[{"role": "user", "parts": [system_prompt]}])
        if last_task_name:
            sys_prompt = "🧠 What do you want to do? (type 'exit' to quit, or 'run'/'automate' to execute the last task)"
        else:
            sys_prompt = "🧠 What do you want to do? (type 'exit' to quit)"
        
        user_input = ask_user(sys_prompt)
        if user_input.lower() in ['exit', 'quit', 'bye']:
            print_final(task_name, collected)
            break

        if user_input.lower() in ['run', 'automate']:
            if last_task_name and last_collected:
                print("⚠️ This is 1st.")
                task_config = tm.get_task_by_name(last_task_name)
                run_task_simulation(last_task_name, last_collected)
            else:
                print("⚠️ No previous task found to run.")
            continue

        response = convo.send_message(user_input)

        if hasattr(response, "usage_metadata"):
            print("🔍 Token usage:", response.usage_metadata)
        else:
            def estimate_token_count(text): return int(len(text.split()) * 1.3)
            print(f"🔍 Estimated token usage: ~{estimate_token_count(user_input)} tokens")

        parsed = try_parse_json(response.text)
        if parsed:
            task_name = parsed.get("task")
            collected = merge_answers(collected, parsed.get("data", {}))
        else:
            print(response.text)

        while True:
            task = tm.get_task_by_name(task_name) if task_name else None
            if task and is_complete(tm.get_required_keys(task), collected):
                last_task_name = task_name
                last_collected = collected.copy()
                break

            if collected:
                print(collected)
            user_input = ask_user("Your answer (or type exit):")
            if user_input.lower() in ['exit', 'quit', 'bye']:
                print_final(task_name, collected)
                return

            if user_input.lower() in ['run', 'automate']:
                if last_task_name and last_collected:
                    print("⚠️ This is 2nd.")
                    run_task_simulation(last_task_name, last_collected)
                    print("⚠️ No previous task found to run.")
                continue

            response = convo.send_message(user_input)

            if hasattr(response, "usage_metadata"):
                print("🔍 Token usage:", response.usage_metadata)
            else:
                def estimate_token_count(text): return int(len(text.split()) * 1.3)
                print(f"🔍 Estimated token usage: ~{estimate_token_count(user_input)} tokens")

            parsed = try_parse_json(response.text)
            if parsed:
                task_name = parsed.get("task", task_name)
                collected = merge_answers(collected, parsed.get("data", {}))
            else:
                print(response.text)

        print_final(task_name, collected)

if __name__ == "__main__":
    main()
