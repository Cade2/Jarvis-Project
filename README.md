# Jarvis Project — Local AI Agent (MK3)

Jarvis is a **local, privacy-first AI agent** that can:

- Chat naturally (**General model**)
- Help with coding (**Coder model**)
- Help with research / summaries (**Research model**)
- Safely run **approved system tools** on your PC via a Windows **Runner**
- Generate and test code changes **sandbox-first** before applying them to the real repo (**Dev Mode**)

> **Privacy-first:** no cloud APIs required by default  
> **Safety-first:** risky actions require confirmation + everything is logged

---

## Table of contents

- [What’s in MK3](#whats-in-mk3)
- [Project structure](#project-structure)
- [Fresh install (Windows)](#fresh-install-windows)
- [Models (Ollama)](#models-ollama)
- [Run Jarvis](#run-jarvis)
- [Usage examples](#usage-examples)
- [Dev Mode workflow](#dev-mode-workflow)
- [Performance + timeouts](#performance--timeouts)
- [IDE integration (optional)](#ide-integration-optional)
- [Safety / disclaimer](#safety--disclaimer)
- [Post-change test checklist](#post-change-test-checklist)

---

## What’s in MK3

### 1) Tools + Safety layer
- **Tools** are approved actions (display / audio / network / apps / storage / etc.)
- **Risk levels** control confirmation prompts
- **Every tool call** is written to local audit logs

### 2) Runner (Windows capabilities server)
Jarvis talks to a local **Runner** process that exposes deterministic system actions (e.g., via a small FastAPI server).

### 3) Multi-model roles
Jarvis supports 3 separate roles:
- **General**: everyday assistant
- **Coder**: code generation, refactors, diffs
- **Research**: summaries, structured writing

Configured in: `config/models.json`

### 4) Dev Mode (sandbox-first patch pipeline)
When you say something like:
- “refactor agent/core.py…”
- “fix this error…”
- “add a feature…”

Jarvis can:
1) Copy the repo into `workspace/repo_sandbox/`  
2) Generate a **unified diff**  
3) Apply it to the sandbox  
4) Run tests (example: `python -m compileall .`)  
5) Only then offer to apply the patch to the real repo  

---

## Project structure

```text
jarvis-agent/
├─ agent/                 # main agent logic (router, safety, models)
├─ runner/                # Windows runner server + tool implementations
├─ config/
│  ├─ policy.yaml         # allow/deny + confirmations
│  └─ models.json         # model roles + generation limits
├─ workspace/             # sandbox repo, patches, run logs (generated)
├─ logs/                  # audit logs + runner state (generated)
└─ cli.py                 # CLI entrypoint
Fresh install (Windows)
0) Prerequisites
Install these first:

Git

Miniconda/Anaconda (Python 3.11 env)

Ollama (local model runtime)

1) Clone the repo
cd /d C:\Users\%USERNAME%\Code
git clone <YOUR_REPO_URL>
cd Jarvis-Project\jarvis-agent
2) Create + activate the conda environment
conda create -n jarvis-agent python=3.11 -y
conda activate jarvis-agent
3) Install Python dependencies
If you have a requirements.txt, prefer:
pip install -r requirements.txt
If not, install the essentials (adjust as your repo evolves):

pip install fastapi uvicorn pydantic pyyaml
pip install torch transformers
Optional (useful for system stats/tools):

pip install psutil
4) Verify Ollama is running
Ollama usually runs as a background service after installation:
ollama --version
Models (Ollama)
Default MK3 models (example set)
These should match config/models.json:
ollama pull llama3.1:8b
ollama pull qwen2.5-coder:14b
ollama pull qwen2.5:14b-instruct-q4_K_M
Recommended model options for a 32GB RAM PC
If you want “better” models (or more stable performance), here are sensible swap options. Pick one per role and update config/models.json accordingly.

General (chat) options

llama3.1:8b (great default)

qwen2.5:14b-instruct-q4_K_M (strong, heavier)

Coder options

qwen2.5-coder:14b (strong, heavier)

If you hit timeouts: consider a smaller coder model (same family, smaller size if available in your setup)

Research options

qwen2.5:14b-instruct-q4_K_M (good structured writing)

Or use the same general model for research to reduce memory load

Tip: If performance is inconsistent, run one 14B model + two smaller models, rather than three “heavy” ones.

Run Jarvis
From inside jarvis-agent/ with the environment active:


conda activate jarvis-agent
cd /d C:\Users\%USERNAME%\Code\Jarvis-Project\jarvis-agent
python cli.py
Usage examples
Normal tool commands
Try phrases like:

audio status

wifi status

bluetooth on

list installed apps

storage usage

logs

logs last 50

Dev Mode commands
dev status

sandbox reset

fix this error: <paste traceback>

refactor agent/core.py: extract logs formatting into helper; output identical; minimal change

Dev Mode workflow
1) Reset sandbox
sandbox reset
2) Ask for a change
Example:
refactor agent/core.py: rename local variable 'lines_list' to 'tail_lines' in logs.tail formatting only; keep output identical
3) If Jarvis produces a patch
Confirm apply to sandbox

Confirm it runs sandbox checks (e.g., compileall)

4) If sandbox tests pass
Use dev apply patch to apply to the real repo (critical confirm).

Performance + timeouts
If the coder model times out during patch generation:

Option A — Reduce generation length
In config/models.json, lower:

generation.coder.num_predict (try 120)

Option B — Make the request smaller
Ask for minimal diffs:

“change only X function”

“minimal diff”

“no refactor, just fix the bug”

Option C — Increase Ollama HTTP timeout
In agent/models.py, look for something like a request timeout (example: timeout=120) and increase it (example: 600).

IDE integration (optional)
Jarvis is CLI-first. If you want an in-editor assistant in VS Code:
Use Continue (VS Code extension) connected to your local Ollama models for inline coding help
Keep Jarvis as your system tools + sandbox patch agent

Future MK3.x idea:
Build a VS Code extension that sends open file + diagnostics to Jarvis and applies returned diffs.

Safety / disclaimer
This is an experimental personal project.
Tools are gated by policy + confirmations
Logs are stored locally
Dev Mode is sandbox-first, but dev apply patch is critical — use carefully

