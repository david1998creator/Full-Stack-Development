---
name: crewai-orchestrator
description: >-
  Executive orchestrator for the 10-role persistent CrewAI SDLC engineering team.
  Use when an orchestrator or developer wants to spin up, build, decompose, or monitor software engineering projects
  using the persistent multi-agent framework. Handles physical multi-file project scaffolding,
  database migrations, dual-tier LLM routing, Budget Guard pre-execution estimation,
  TDD self-correction loops, in-browser Cytoscape knowledge graphs, and Playwright smoke testing.
---

# Persistent CrewAI 10-Role Multi-Agent SDLC Orchestrator Skill

This skill teaches autonomous AI agents and meta-orchestrators how to interact with, trigger, and monitor the **Persistent CrewAI 10-Role Software Engineering Framework**.

---

## 1. System Topology & Environment

* **Target Projects Directory (Physical Codebases):**  
  `./projects/<project_slug>/`
* **Persistent State Database (SQLite WAL Mode):**  
  `./.persistent_crewai/state.db`
* **Control Console Web Server & REST API:**  
  `http://127.0.0.1:8000` (backed by `ui/server.py` and `ui/index.html`)
* **10 Specialist Canonical Roles:**
  1. **Coordinator**: Orchestration, task decomposition, and milestone governance (`allow_delegation=True`).
  2. **Architect**: High-level system design, architecture decision records (ADRs), and technology selection.
  3. **Designer**: UI/UX specifications, responsive design systems, and frontend contracts.
  4. **Database Engineer**: Relational schema DDL, foreign key relationships, migrations, and seed data.
  5. **Developer**: Physical full-stack implementation with on-demand dynamic sub-agent spawning.
  6. **QA / Tester**: Automated test suite generation (`pytest` / `unittest`) and self-correction healing loops.
  7. **Security Auditor**: OWASP Top 10 scans, SQL injection prevention, and security audit checklists.
  8. **DevOps Engineer**: Multi-stage Dockerfiles, Docker Compose manifests, and environment configs.
  9. **Git Manager**: Atomic Git version control, conventional commit messages, and release tagging.
  10. **Status & Memory Manager**: SQLite WAL synchronization and Cytoscape.js vector memory graphs.

---

## 2. When to Activate This Skill

Activate this skill immediately whenever:
* The user asks to *"spin up the SDLC team"*, *"build an app using agents"*, or *"run the multi-agent framework"*.
* An application needs to be developed autonomously with real database schemas, backend APIs, and frontend UIs.
* You need to query real-time pipeline status, agent telemetry traces, milestones, or token expenditure.
* You need to trigger or resolve human-in-the-loop decisions (e.g. Budget Guard approvals).

---

## 3. Step-by-Step Orchestration Playbook

### Step 1: Pre-Flight Health Check & Startup
1. Check if the Control Console server is running on port 8000:
   ```bash
   # Windows PowerShell:
   Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
   
   # Linux / macOS:
   lsof -i :8000
   ```
2. If port 8000 is not active, start the background server:
   ```bash
   python -m ui.server
   ```
3. Verify server health:
   ```bash
   curl http://127.0.0.1:8000/api/overview
   ```

### Step 2: Goal Decomposition & Project Scaffolding
Send the project goal to the Coordinator API:
```python
import requests

SERVER_URL = "http://127.0.0.1:8000"
response = requests.post(f"{SERVER_URL}/api/decompose", json={
    "goal": "Build PayPulse: A SaaS subscription and invoicing dashboard with SQLite DB",
    "slug": "paypulse_billing"
})
data = response.json()
# Returns created tasks across Architect, Database Engineer, Developer, QA, etc.
```

### Step 3: Human-in-the-Loop & Budget Guard Resolution
The framework automatically calculates estimated token expenditure and transitions to `PAUSED_FOR_HUMAN` if budget confirmation is required:
1. Inspect pending actions:
   ```python
   pending = requests.get(f"{SERVER_URL}/api/actions/pending").json()
   ```
2. Resolve / Approve the Budget Guard action:
   ```python
   action_id = pending["actions"][0]["id"]
   requests.post(f"{SERVER_URL}/api/actions/{action_id}/resolve", json={
       "decision": "approved",
       "feedback": "Budget approved by orchestrator"
   })
   ```

### Step 4: Autonomous Task Execution & Self-Healing
Iterate through tasks or trigger execution:
```python
for task in data.get("created_tasks", []):
    res = requests.post(f"{SERVER_URL}/api/tasks/{task['id']}/execute").json()
    print(f"Executed {task['title']} via {task['role']}: {res.get('status')}")
```
* **Autonomous TDD Healing:** If tests fail during developer execution, tracebacks are automatically fed back to the Developer to apply patch iterations until `exit code 0`.
* **Database Engineering:** Schema DDL (`schema.sql`) and sample fixtures are generated before application code begins.

### Step 5: Verification & Delivery
1. **Query Milestones Roadmap:**
   ```python
   milestones = requests.get(f"{SERVER_URL}/api/milestones").json()
   # Verifies all 5 milestone phase criteria are green
   ```
2. **Inspect In-Browser Knowledge Graph:**
   ```python
   graph = requests.get(f"{SERVER_URL}/api/graph/memory").json()
   print(f"Nodes: {graph['stats']['node_count']}, Edges: {graph['stats']['edge_count']}")
   ```
3. **Execute Headless Browser Smoke Test:**
   ```python
   smoke = requests.post(f"{SERVER_URL}/api/smoke-test", json={
       "url": "http://127.0.0.1:8000",
       "slug": "paypulse_billing"
   }).json()
   assert smoke.get("success") is True
   ```

---

## 4. CLI Fallback Commands

If working directly in the terminal without HTTP REST calls:
```bash
# Execute autonomous build via CLI orchestrator:
python scripts/orchestrate.py --goal "Your project description" --slug "my_project"

# Offline / Deterministic Mock Mode (zero API keys required):
python scripts/orchestrate.py --mock --goal "Your project description" --slug "my_project"

# Interactive REPL:
crewai-team repl

# System Status:
crewai-team status
```
