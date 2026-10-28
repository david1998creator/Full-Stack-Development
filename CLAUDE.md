# CLAUDE.md: Guidelines for Claude Code & Claude Desktop

Welcome Claude! You are orchestrating the **Persistent CrewAI 10-Role Software Engineering Framework**.

---

## 🚨 MANDATORY STEP 0: API KEY & ENVIRONMENT CHECK
Before executing any build, check if `.env` exists in the project root:
- If `.env` is **missing** or has no keys, politely stop and prompt the user:
  > *"Before we run autonomous builds, please configure at least one API key in `.env` (copied from `.env.example`).*  
  > *🟢 **100% Free Keys Available:**  
  > - **Google Gemini** (`GEMINI_API_KEY`): https://aistudio.google.com/app/api-keys  
  > - **Groq Cloud** (`GROQ_API_KEY`): https://console.groq.com/keys  
  > - **Ollama** (`OLLAMA_BASE_URL`): Local & offline at http://localhost:11434  
  > (If you want to test offline for free without any keys, tell me to run with `--mock`)."*

---

## Essential Commands

### 1. Launch Control Console (GUI)
```bash
python -m ui.server
```
Runs at `http://127.0.0.1:8000` with Project Flow, Live Workspace, Milestones, and Knowledge Graph.

### 2. Autonomous CLI Project Orchestration
```bash
# Real execution (requires .env keys):
python scripts/orchestrate.py --goal "<project_requirements>" --slug "<project_slug>"

# Deterministic offline mock mode (free / CI):
python scripts/orchestrate.py --mock --goal "<project_requirements>" --slug "<project_slug>"
```

### 3. Model Context Protocol (MCP) Server
Claude can directly call Persistent CrewAI tools via stdio MCP:
```bash
python -m persistent_crewai.mcp_server
```
Config snippet for `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "persistent-crewai": {
      "command": "python",
      "args": ["-m", "persistent_crewai.mcp_server"]
    }
  }
}
```

### 4. Testing
```bash
# Offline unit/integration test suites:
python -m unittest tests/test_agents.py tests/test_pipeline.py

# End-to-end Playwright browser verification:
python tests/e2e/test_milestones_and_hitl.py
```

---

## Architecture Principles
1. **10 Canonical Roles**: Coordinator, Architect, Designer, Database Engineer, Developer, QA, Security Auditor, DevOps, Git Manager, Status & Memory Manager.
2. **ACID Persistence**: State is persisted in SQLite WAL at `.persistent_crewai/state.db`.
3. **Pre-Execution Budget Guard**: Always verify token and cost estimation in the HITL Drawer before executing extensive developer tasks.
4. **Portability**: Never commit personal machine paths or API keys.
