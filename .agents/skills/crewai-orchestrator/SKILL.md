---
name: crewai-orchestrator
description: >-
  Executive orchestrator for the 8-agent persistent CrewAI software engineering team.
  Use when the user wants to spin up, build, decompose, or monitor software engineering projects
  using the persistent multi-agent framework located at C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team.
  Handles physical multi-file project scaffolding, dual-tier LLM routing, TDD self-correction loops,
  AST codebase mapping, dynamic sub-agent spawning, and Playwright browser smoke testing.
---

# CrewAI Multi-Agent Framework Orchestrator Skill

This skill teaches Antigravity how to act as the **Executive Meta-Orchestrator** for the autonomous 8-agent persistent CrewAI engineering framework.

---

## 1. System Topology & Environment

*   **Framework Root Directory:**  
    `C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team`
*   **Target Projects Directory (Physical Codebases):**  
    `C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\projects\<project_slug>/`
*   **Database (SQLite WAL Mode):**  
    `C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.persistent_crewai\state.db`
*   **Control Console Web Dashboard & API:**  
    `http://127.0.0.1:8000` (backed by `ui/server.py` and `ui/index.html`)
*   **Agent Roles & Assigned Tiers:**
    *   **Orchestration Tier (`gemini-3.5-flash-lite` / `groq`):** Coordinator, GitHub Manager, Status & Memory Manager, Designer.
    *   **Deep Reasoning Tier (`OpenRouter: qwen/qwen-2.5-coder-32b-instruct` / `gemini-2.5-pro`):** Architect, Developer (Lead), Tester, Reviewer.

---

## 2. When to Activate This Skill

Activate this skill immediately whenever the user:
*   Asks to *"spin up the crew"*, *"spin up the agent framework"*, or *"build an app using agents"*.
*   Wants to generate a production-ready application (FastAPI, React, SQLite, full-stack, microservice).
*   Asks about the multi-agent pipeline status, live agent traces, or browser smoke tests.
*   Requests to decompose a project goal into Kanban / pipeline tasks across the 8 specialist agents.

---

## 3. Step-by-Step Orchestration Playbook

### Step 1: Pre-Flight Health Check
1. Verify if the Control Console server is running on port 8000:
   ```powershell
   Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
   ```
2. If port 8000 is not listening, launch the server in the background:
   ```powershell
   $env:PYTHONPATH="C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\src;C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team"
   python "C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\ui\server.py"
   ```

### Step 2: Goal Decomposition & Workspace Scaffolding
Send the user's high-level goal to the Coordinator API:
```python
import requests
res = requests.post("http://127.0.0.1:8000/api/decompose", json={"goal": user_goal})
data = res.json()
```
*   The **Architect** automatically scaffolds the project directory:
    *   `Dockerfile` (multi-stage container)
    *   `docker-compose.yml`
    *   `.env.example`
    *   `README.md`
    *   Initial local Git repository (`git init -b main`).

### Step 3: Execute Tasks via Assigned Specialists
Iterate through the decomposed tasks in order (or run targeted tasks):
```python
res = requests.post(f"http://127.0.0.1:8000/api/tasks/{task_id}/execute")
task_result = res.json()
```
*   **For Developer Tasks:**
    *   The framework injects the **AST Codebase Map** (via `ASTSymbolExtractor`).
    *   The Developer writes physical code directly to `projects/<project_slug>/`.
    *   If modular UI/DB components are needed, the Developer invokes `spawn_subagent_assistant`.
    *   **Autonomous Test Healing:** The framework runs `SubprocessTestRunner`. If any test fails, the traceback is routed back to the Developer to apply a patch until all tests exit code `0`.
*   **For Tester Tasks:**
    *   Runs the full automated test suite in a subprocess (`pytest` / `unittest`).
    *   Runs **Playwright Headless Chromium** smoke test against the live URL, validating HTTP status and zero console errors.
*   **For GitHub Manager Tasks:**
    *   Stages all verified code and commits with conventional commits (`feat:`, `test:`, `fix:`).

### Step 4: Verification & Telemetry Inspection
1. Query `/api/pipeline/live` to check agent statuses and active transit.
2. Run on-demand live browser smoke test:
   ```python
   smoke_res = requests.post("http://127.0.0.1:8000/api/smoke-test", json={"url": "http://127.0.0.1:8000", "slug": project_slug}).json()
   ```

### Step 5: Executive Delivery Summary
Present a clear release report to the user:
*   Project root path on disk (`projects/<project_slug>/`).
*   File tree and production manifests generated (`Dockerfile`, `docker-compose.yml`, `.env.example`).
*   Test verification metrics (100% unit tests passed).
*   Live browser smoke test confirmation and screenshot link.
*   Git commit hashes recorded in the local repository.
