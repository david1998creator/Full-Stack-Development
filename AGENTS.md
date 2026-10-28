# AGENTS.md: Autonomous Agent & Orchestrator Playbook
**Framework:** Persistent CrewAI 10-Role Autonomous Software Engineering Team  
**Standard:** OpenCode, Claude Code, Cursor, Antigravity, and Multi-Agent Orchestrators  

---

## 🚨 STEP 0: MANDATORY PRE-FLIGHT ACTION FOR AGENTS
**When a user asks you to run, build, or test this repository, ALWAYS execute this check first:**

1. **Check if `.env` exists:**
   - Look in the workspace root for `.env`.
   - If `.env` is **missing** or contains no keys:
     > **Prompt the user immediately:**  
     > *"Welcome to Persistent CrewAI! Before we can run real autonomous builds, please create a `.env` file from `.env.example` and supply your API key(s). Supported providers: `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, or `NVIDIA_API_KEY`.  
     > (If you want to run offline tests or explore the interface without spending tokens, let me know and I can run in deterministic `--mock` mode)."*
   - **DO NOT** attempt a live build without an API key, as runtime execution will halt with a `RuntimeError`.

2. **Verify Python Environment:**
   ```bash
   pip install -r requirements.txt
   ```

---

## 1. System Topology & Architecture

* **Physical Project Output:** Every generated app is created in `./projects/<slug>/` (completely sandboxed).
* **ACID Persistence Engine:** All tasks, agent states, milestone criteria, and decisions are stored in SQLite WAL mode at `./.persistent_crewai/state.db`. If a process crashes, it resumes without losing progress.
* **Pre-Execution Budget Guard:** Analyzes requirements before code generation, calculates estimated tokens and USD cost, and pauses gracefully (`PAUSED_FOR_HUMAN`) until approved.
* **Embedded Knowledge Graph:** In-browser Cytoscape.js visual graph at `http://127.0.0.1:8000` (Tab 4). Zero external software like Obsidian needed.
* **10 Specialist Canonical Roles:**
  1. `Coordinator`: Meta-orchestration and task decomposition (`allow_delegation=True`).
  2. `Architect`: High-level system design and architecture decision records (ADRs).
  3. `Designer`: UI/UX specifications, design systems, and frontend ergonomics.
  4. `Database Engineer`: Schema DDL, foreign keys, migrations, and seed fixtures.
  5. `Developer`: Full-stack coding with on-demand dynamic sub-agent spawning.
  6. `Tester` (QA): Automated test generation and self-correcting test loops.
  7. `Security Auditor`: OWASP Top 10 audits and injection prevention.
  8. `DevOps`: Containerization (`Dockerfile`, `docker-compose.yml`) and environments.
  9. `Git Manager`: Atomic conventional commits and version tags.
  10. `Status & Memory Manager`: SQLite WAL syncing and Cytoscape knowledge graph memory.

---

## 2. How to Run the Framework

### Option A: Web Control Console (Interactive GUI)
```bash
python -m ui.server
```
Opens at `http://127.0.0.1:8000` with 4 interactive tabs:
- **Project Flow**: Kanban board for tasks and live executions.
- **Live Workspace**: 10-role animated assembly line with dynamic sub-agents.
- **Milestones**: 5-phase delivery tracker with automated criteria checkmarks.
- **Knowledge Graph**: In-browser interactive Cytoscape.js memory graph.

### Option B: Command-Line Orchestrator (CLI)
```bash
# Real build with live API keys:
python scripts/orchestrate.py --goal "Build PayPulse: A SaaS subscription dashboard with SQLite DB" --slug "paypulse_billing"

# Offline / Free Testing Mode:
python scripts/orchestrate.py --mock --goal "Build PayPulse: A SaaS subscription dashboard" --slug "paypulse_billing"
```

### Option C: Model Context Protocol (MCP) Server
External orchestrators (Claude Desktop, Cursor, OpenCode, ChatGPT) can plug directly into this framework via MCP:
```bash
python -m persistent_crewai.mcp_server
# or
python mcp_server.py
```
*(See `mcp.json` or `claude_desktop_config.json` for 1-click configuration).*

### Option D: Verification & Automated Tests
```bash
# Run unit & integration tests (runs offline deterministically):
python -m unittest tests/test_agents.py tests/test_pipeline.py

# Run Playwright E2E browser verification:
python tests/e2e/test_milestones_and_hitl.py
```

---

## 3. Strict Rules for Coding Agents

1. **No Personal / Machine-Specific Paths:** Always use relative paths (`./projects/<slug>`, `./docs/`). NEVER write hardcoded paths like `C:\Users\...` or `/home/...`.
2. **Canonical Roles Only:** Do NOT invent non-canonical roles. Map all activities to the 10 canonical roles.
3. **Respect the Budget Guard:** When initiating a new project, check `/api/actions/pending`. If the Budget Guard is paused (`PAUSED_FOR_HUMAN`), resolve it explicitly before executing subsequent tasks.
4. **Preserve Documentation:** Keep all comments, docstrings, and verified test suites intact.
