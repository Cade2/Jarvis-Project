# Jarvis Project – Local AI Agent (MK1 / MK1.5)

This project is my attempt to build a **local, privacy-first AI assistant** that runs on my own devices and can safely control certain parts of the system.

The long-term goal is a **Jarvis-style agent** that can:
- Chat naturally and answer questions
- Help with coding and summarising
- Perform actions on the device (open apps, create reminders, manage files, etc.)
- Stay **fully local** and **safe**, with strong control over what it is allowed to do

This repo contains the early versions:

- **MK1** – Basic CLI agent with tools, safety layer, and logging  
- **MK1.5** – Same agent, plus a small local language model for free-form chat

---

## Project goals

1. Run completely **on-device** (no external API calls by default).
2. Respect user **privacy** and **safety**:
   - Actions are limited to approved tools.
   - Risky actions must be confirmed by the user.
   - All actions are logged locally.
3. Be **extensible**:
   - Easy to add new tools and abilities.
   - Easy to swap out the language model later for a better one.
4. Eventually support **multiple platforms**:
   - Windows and macOS (desktop first)
   - Later: Android and iOS, using OS-specific integrations

---

## Current features

### 1. Command-line Jarvis (CLI)

- Start a simple text-based Jarvis in the terminal.
- You can type natural language commands like:
  - `remind me to study AI tomorrow`
  - `open notepad`
  - `tell me a joke`
- Jarvis decides whether:
  - It should call a **tool** (e.g. open an app, create a reminder), or
  - It should just **reply in text** (chat mode).

### 2. Tool system (MK1)

Tools are small actions Jarvis is allowed to perform.  
Right now, two tools are implemented:

- `create_reminder(text, when)`
  - For now, this just prints the reminder and records it in the log.
  - Later it will integrate with a real reminder/calendar system.

- `open_application(app_name)`
  - Opens a desktop application by name.
  - On Windows, this uses the `start` command to open apps like `notepad`.

All tools are registered in `agent/tools.py`.

### 3. Safety layer (risk levels + audit log)

Every tool has a **risk level**:

- `LOW` – Safe to auto-run (e.g. creating a reminder)
- `MEDIUM` – Needs user confirmation
- `HIGH` – Very sensitive actions (delete files, send emails, change security settings, etc.)

The safety logic lives in `agent/safety.py` and handles:

- **Risk-based confirmation**  
  If a tool is `MEDIUM` or `HIGH`, Jarvis explains what it wants to do and asks:

  > `Proceed? (y/n)`

- **Audit logging**  
  Every tool call is logged to `audit.log` with:
  - timestamp  
  - tool name  
  - parameters  
  - outcome (`success`, `cancelled`, or error message)

This gives a clear history of what the agent has done on the device.

---

## Language model (MK1.5)

MK1.5 adds a **local chat model** using Hugging Face and PyTorch.

- The class `ChatModel` in `agent/models.py`:
  - Loads a small text-generation model (currently `gpt2` as a placeholder).
  - Provides a simple `chat(messages)` method.

- In `agent/core.py`:
  - When no tool is chosen for a user message, Jarvis falls back to the chat model.
  - Example:
    - `tell me a joke`
    - `summarise: Today I studied AI agents and built a CLI Jarvis.`
  - These do not trigger tools; they just generate text responses.

⚠️ **Note:** `gpt2` is not an instruction-tuned or safety-tuned model.  
It is used here only as a **local test model** to prove the architecture.  
In later versions we will replace it with a better instruct model.

---

## Planner design

The agent has a **planner** that decides which tool (if any) to use.

There are two planner paths:

1. **Rule-based planner (current default)**  
   - Simple keyword logic in `plan_action_rule_based()`:
     - If the message starts with `remind me` → use `create_reminder`.
     - If the message starts with `open ` → use `open_application`.
   - This keeps behaviour predictable during early development.

2. **Model-based planner (planned for future)**  
   - `agent/planner.py` defines how to build a **planner prompt** listing:
     - available tools and descriptions
     - the user message
   - The model is expected to return **JSON** like:

     ```json
     {
       "tool_name": "create_reminder",
       "params": {
         "text": "remind me to study AI tomorrow",
         "when": "2025-12-14 18:00"
       }
     }
     ```

   - `parse_planner_output()` safely parses the JSON and falls back to “no tool” if the output is invalid.

In `agent/core.py`, the function `plan_action()` can choose between:

- rule-based planner  
- model-based planner (using `DummyPlannerModel`)  

For now, `USE_MODEL_PLANNER` is set to `False`, so the agent still uses the simple rule-based planner.

---

## Project structure

```text
jarvis-agent/
  ├─ agent/
  │   ├─ __init__.py          # makes 'agent' a package
  │   ├─ safety.py            # risk levels, Tool class, confirmation + audit log
  │   ├─ tools.py             # tool implementations + tool registry
  │   ├─ core.py              # main agent logic (planning, safety, execution, chat)
  │   ├─ planner.py           # planner prompt + JSON parsing for model-based planning
  │   └─ models.py            # Dummy planner model + local ChatModel wrapper
  ├─ cli.py                   # command-line interface entrypoint
  ├─ audit.log                # (generated) log of all tool calls
  ├─ requirements.txt         # Python dependencies
  └─ README.md                # this file
How to run it (Windows / Conda)
Create and activate the Conda environment:

bash
Copy code
conda create -n jarvis-agent python=3.11
conda activate jarvis-agent
Install dependencies:

bash
Copy code
pip install torch transformers
Clone this repo and go into the folder:

bash
Copy code
git clone <your-repo-url>
cd jarvis-agent
Run the CLI:

bash
Copy code
python cli.py
Try some commands:

remind me to study AI tomorrow

open notepad

tell me a joke

summarise: Today I studied AI agents and built a CLI Jarvis.

Safety and privacy
All logic runs locally on the device.

No external APIs are called by default.

Tool actions are restricted by:

risk levels

confirmation prompts

audit logging

Future versions will:

keep user data stored locally (e.g. in an encrypted folder/database)

add more careful control over high-risk tools (delete, send, install, etc.)

Roadmap
MK1 (done)
CLI interface

Basic tools (reminders, open app)

Safety layer with risk levels and action logging

Planner skeleton (rule-based + model-based design)

MK1.5 (in progress)
Local chat model for general conversation and simple tasks

Clean separation between:

Agent mode (tools/actions)

Chat mode (pure text replies)

MK2 (planned)
Replace GPT-2 with a stronger instruction-tuned model running locally

Turn on the model-based planner and let the model choose tools via JSON

Add more tools:

better reminders and calendar integration

simple file search and sandboxed file editing

basic “run code in sandbox” for dev assistance

MK3+ (future)
Desktop GUI (Tauri/Electron)

OS-native integrations (Windows, macOS)

Later: Android and iOS apps using the same core agent

Stronger safety, encryption, and configuration for power users

Disclaimer
This is an experimental personal project.
The current language model (gpt2) is only used as a local test model and is not aligned for production use.
Future versions will use better models and stronger safety rules before any real-world deployment.