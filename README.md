# 🧠 NTQA: Narrative-Topics-Questions-Answers

![Python](https://img.shields.io/badge/python-3.8%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

An intelligent command-line assistant powered by Google's Gemini API. It reads dynamic task definitions from JSON files and guides you interactively to fill required fields. Once done, it outputs structured JSON and can simulate running the task.

---

## ✅ Features

- Dynamic task loading from JSON files (`tasks/`).
- Conversational question/answer flow.
- Required vs optional field detection.
- Handles "run", "automate", "exit", "quit", etc.
- Structured JSON output for integration with other systems.
- Simulation of task execution.

---
## 📌Workflow: How it Works
- Narrative Input: Users describe what they want to do in natural language.
- Template Matching: The system finds a local JSON template for the task.
- Question Extraction: Required questions are checked for answers.
- Conversational Loop: If any required answer is missing, the system asks for it.
- Task Execution: Once all answers are ready, the task runs in a subprocess.
- AI Summary: Gemini summarizes the output, and users can ask more about it.

<img src="./images/NTQA_Workflow_Handwritten.png">

---
## 📁 Project Structure

```
project-root/
├── tasks/
│   ├── add_okta_attribute.json
│   ├── assign_user_to_group.json
│   ├── create_group.json
│   ├── create_okta_user.json
│   ├── get_future_date.json
│   ├── get_weather_in_period.json
│   ├── okta_profile.json
│   ├── run_a_program.json
│   ├── select_group_by_name.json
│   ├── select_user_by_name.json
│   ├── update_okta_user.json
│   └── user_profile.json
├── images/
│   └── NTQA_Workflow_Handwritten.png
├── get_future_date.py
├── get_weather.py
├── okta_user_account.py
├── task_run.py
├── requirements.txt
├── .gitignore
├── LICENSE
└── README.md
```

---

## 🔧 Setup Instructions

### 1. **Install Python 3.8+**

Make sure Python is installed. You can verify using:

```bash
python --version
```

### 2. **Install Required Packages**

Use `pip` to install dependencies:

```bash
pip install google-generativeai
```

---

## 🔐 How to Get Gemini API Key

1. Go to [Google AI Studio (MakerSuite)](https://makersuite.google.com/app/apikey)
2. Click **"Create API Key"**
3. Copy the key. You will add this to your `.env` file as `GEMINI_API_KEY`.
4. You can also specify the model you want to use (e.g., `gemini-3.1-flash-lite`, or `gemini-2.5-flash`) by setting `GEMINI_MODEL` in your `.env` file.

---

## 🌐 What is Okta? (And How to Get an Account)

**Okta** is a popular cloud-based identity and access management (IAM) service. It allows organizations to securely manage user authentication, authorization, and single sign-on (SSO) for their applications.

If you don't have an Okta account, you can easily get a free Developer Edition account:
1. Go to [developer.okta.com/signup](https://developer.okta.com/signup/).
2. Fill out the registration form.
3. Verify your email address and log in to your new Okta Admin Console.
4. Note your **Okta Domain** (e.g., `https://dev-12345678.okta.com`), which you will find in the top-right corner of the dashboard.

---

## 🔑 How to Get an Okta API Token

To allow this script to interact with your Okta account (e.g., to create users or manage attributes), you need an API token:
1. Log in to your Okta Admin Console.
2. In the left sidebar, navigate to **Security** > **API**.
3. Click on the **Tokens** tab.
4. Click **Create Token**.
5. Give your token a name (e.g., `NTQA Script Token`) and click **Create Token**.
6. **Copy the token value immediately.** You will not be able to view it again after closing the window.

---

## ⚙️ Setting Up the `.env` File

Before running the `task_run.py` program, you must create a `.env` file in the root directory of the project to store your Okta credentials and Gemini API Key securely.

1. Create a new file named `.env` in the `project-root/` directory.
2. Add the following lines to the file, replacing the placeholder values with your actual data:

```env
OKTA_URL=https://your-okta-domain.okta.com
OKTA_API_TOKEN=your_copied_api_token_here

GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-3.1-flash-lite
```

> **Note:** Ensure there are no trailing slashes (`/`) at the end of your `OKTA_URL`.

---

## ▶️ How to Run the Assistant

Run the script using:

```bash
python task_run.py --api-key YOUR_GEMINI_API_KEY
```

> Optional: You can change the Gemini model with `--model=gemini-1.5-pro` or other versions.

---

## 💼 Example Task File

**`tasks/create_okta_user.json`**

```json
{
  "task_name": "create_okta_user",
  "description": "Create a new user in OKTA system",
  "questions": [
    "What is the new okta user's first name?*",
    "What is the new okta user's last name?*",
    "What is the new okta user's email?*",
    "What is the user's job title?",
    "What's the user's username?"
  ],
  "output_schema": {
    "what_is_the_new_okta_users_first_name": "",
    "what_is_the_new_okta_users_last_name": "",
    "what_is_the_new_okta_users_email": "",
    "what_is_the_users_job_title": "",
    "whats_the_users_username": ""
  }
}
```

> Add more `.json` files to the `tasks/` folder for additional task types.

---

## 🧪 Sample Flow

```bash
🤖 Gemini Task Assistant Initialized using model: gemini-2.0-flash

📝 🧠 What do you want to do? (type 'exit' to quit)
> create a new okta user

📝 What is the new okta user's first name?
> Joe

📝 What is the new okta user's last name?
> Doe

📝 What is the new okta user's email?
> joe.doe@example.com

...

📦 Final JSON:
{
  "task": "create_okta_user",
  "data": {
    "what_is_the_new_okta_users_first_name": "Joe",
    ...
  }
}

💬 How can I help more? Do you still need me to change the query or start a new one?
You may also type 'automate' or 'run' to execute the completed task.

📝
> run

⚙️ Now running task: create_okta_user with data:
{
  ...
}
✅ Task completed.
```

---

## 🌟 Complex Automation Example

NTQA shines when chaining multiple tasks together. Try copying and pasting the following prompt into the assistant to see it orchestrate multiple actions automatically:

> "Amanda Smith is traveling to Washington DC next week for 5 days. She wants to be informed about the weather during her stay. 
> Create a new okta user using her first dot last names at gmail as username and email. 
> Then create or update 4 new attributes one called location 'Location', title 'Location'. 
> The 2nd attribute is called 'Duration', title 'Duration' and set duration in days. 
> The 3rd attribute is called 'Weather', title 'Weather', updated with the ('weather') from weather api response. 
> The 4th attribute is called 'Weather_Note', title 'Weather Note', updated it with ('note') from the weather api response.
> Update the user with the new attribute values.
> The new user is manager and needs to be added to the managers group. 
> She also needs to be assigned to the following groups 'Smith_Family', 'Smiths', 'zone_one', 'zone_two', 'zone_four', and 'zone_five'"

**What to expect:**
1. The assistant will parse this complex narrative and identify all the necessary tasks (getting future dates, fetching weather, creating an Okta user, adding attributes, updating the user, and assigning groups).
2. It will automatically extract the required parameters from your prompt (e.g., "Amanda", "Smith", "Washington DC", "5 days").
3. If any required information for any of the tasks is missing, it will ask you for it.
4. Once all data is collected, type `run` or `automate`, and it will execute all the tasks sequentially without requiring any code changes!

**Sample Output:**
```bash
📦 Final JSON: (Executing 14)
[
  {
    "task": "get_future_date",
    "data": {
      "start_date": "today",
      "duration_in_days": "5"
    }
  },
  {
    "task": "get_weather_in_period",
    "data": {
      "location": "Washington DC",
      "start_date": "${Get Future Date.start_date}",
      "end_date": "${Get Future Date.end_date}"
    }
  },
  ...
]
```

---

## ⌨️ Available Commands

You can use the following commands at any prompt:

*   **`run`**: Executes the completed tasks sequentially, pausing after each task to let you ask questions about the output or type `continue` (or `c`) to proceed.
*   **`automate`**: Executes all completed tasks sequentially without pausing.
*   **`continue`** (or **`c`**): When in `run` mode, proceeds to the next task.
*   **`exit`**, **`quit`**, or **`bye`**: Exits the assistant immediately and prints the final JSON (complete or not).

---

## 📞 Support

💬 Need Help or Found a Bug?
Feel free to open a new issue to report bugs or ask for help.

🙌 Want to Contribute?
You're welcome to submit a Pull Request (PR) to improve this project.

---
## 🤝 Contributing

Contributions, suggestions, and improvements are welcome!

1. Fork the repo
2. Create your feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes (`git commit -m 'Add my feature'`)
4. Push to the branch (`git push origin feature/my-feature`)
5. Open a Pull Request

---
