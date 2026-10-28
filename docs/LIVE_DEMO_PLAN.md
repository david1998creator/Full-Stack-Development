# Live Demonstration Master Plan: Persistent CrewAI SDLC Swarm
**Target Framework:** Persistent CrewAI 10-Role Autonomous Software Engineering Team  
**Live LLM Providers:** Gemini (`GEMINI_API_KEY`), Groq (`GROQ_API_KEY`), OpenRouter (`OPENROUTER_API_KEY`), NVIDIA NIM (`NVIDIA_API_KEY`)  
**Web Console:** `http://127.0.0.1:8000`  
**Document Classification:** Confidential / Evaluator Demo Guide

---

## 1. Executive Demo Concept & Narrative

### Why This Demo Blows Typical "AI Coding Agents" Away
Most AI coding demos are either:
1. Single chat windows prompting ChatGPT to write code into one file.
2. Fragile scripts that break on syntax errors or loop endlessly burning API credits.

**Your Narrative Hook for Friends & Evaluators:**
> *"Today, I'm showing you an autonomous 10-role Software Development Life Cycle (SDLC) engineering team. Instead of an isolated chatbot, these agents operate like a real senior engineering pod: an Architect plans, a Database Engineer writes migrations, a Developer implements with dynamic sub-agents, a QA Engineer writes test suites and self-corrects bugs, and a Security Auditor runs OWASP scans—all governed by a Budget Guard that prevents token runaway and stores persistent state in SQLite WAL."*

---

## 2. Recommended Demo Project: "PayPulse"
**Concept:** A modern Micro-SaaS Subscription & Invoicing Hub.
- **Why PayPulse?**
  - **Requires UI Design:** Beautiful modern billing dashboard with plans, invoices, and analytics.
  - **Requires Database Engineering:** Foreign keys between `organizations`, `subscriptions`, `invoices`, and `payment_methods`.
  - **Requires Security:** API token verification, input sanitization, and OWASP audit.
  - **Perfect Scale:** Compact enough to generate live in under 4 minutes, but rich enough to demonstrate real architectural depth.

---

## 3. Demo Architecture & Key Features to Highlight

```
+-----------------------------------------------------------------------------------+
|                           CONTROL CONSOLE (PORT 8000)                             |
|                                                                                   |
|  [Header] Project: PayPulse | USD: $0.14 | Tokens: 42,500 | [🔔 HITL Bell: 1]     |
|                                                                                   |
|  Tabs: [1. Project Flow]  [2. Live Workspace]  [3. Milestones]  [4. Knowledge Graph]
+-----------------------------------------------------------------------------------+
```

### The 4 Feature Highlights in Order:
1. **The Human-in-the-Loop (HITL) Drawer & Budget Guard:**
   - Shows upfront estimation of token usage & dollar cost (~40k tokens / \$0.14).
   - Pauses safely (`PAUSED_FOR_HUMAN`) until you click **[Approve Budget]**.
2. **The 10-Role Assembly Line (Live Workspace):**
   - Designer passes requirements $\to$ Architect designs schema $\to$ Database Engineer creates SQL DDL $\to$ Developer builds files with dynamic sub-agents $\to$ QA runs TDD loops.
3. **The Milestones Roadmap Tab:**
   - Visual milestone cards (Architecture, Database, Implementation, Security & QA, Delivery) showing automated criteria checklists turning green.
4. **The Cytoscape.js Knowledge Graph Tab:**
   - Interactive in-browser memory graph showing architectural decisions, data models, and component links without requiring Obsidian.

---

## 4. Phase-by-Phase Live Demo Walkthrough Script

### Act 1: The Setup & First Impression (0:00 - 1:00)
1. **Open the Browser:** Navigate to `http://127.0.0.1:8000`.
2. **Point out the UI polish:**
   - Note the **dark-mode glassmorphism console**.
   - Point out the 4 tabs: `Project Flow`, `Live Workspace`, `Milestones Roadmap`, and `Knowledge Graph`.
   - Point out the **Upfront Status Banner**:
     > *"Architect & Coordinator are decomposing your requirements (~2 mins). Stay nearby if you'd like to customize the database or review the token budget before implementation begins."*

### Act 2: Initiating the Project & Budget Guard Pause (1:00 - 2:00)
1. **In the Terminal or Console, start PayPulse:**
   ```powershell
   python scripts/orchestrate.py --goal "Build PayPulse: A SaaS subscription and invoicing dashboard with SQLite DB, tiered pricing, and invoice export" --slug "paypulse_billing"
   ```
2. **Watch the Notification Bell ring:**
   - A pulsing badge `[1]` appears on the notification bell.
   - Click the bell to slide open the **HITL Decision Drawer**.
3. **What to say to the audience:**
   > *"Notice that before a single line of application code is generated, our **Budget Guard Agent** automatically analyzed the architectural scope. It estimated that this build will take ~45,000 tokens ($0.16). It has placed the pipeline into a graceful `PAUSED_FOR_HUMAN` state in SQLite. If I leave for lunch, it won't burn tokens in an infinite loop. It waits for my explicit authorization."*
4. **Click [Approve Budget & Proceed]**.
   - Watch the state smoothly transition to `ACTIVE` and resume the pipeline.

### Act 3: Live Workspace & Database Engineer in Action (2:00 - 3:30)
1. **Switch to Tab 2: `Live Workspace`**:
   - The active agent pulse moves across the pipeline:
     `Architect` $\to$ `Database Engineer` $\to$ `Developer`.
   - Click on `Database Engineer` in the pipeline:
     - The right-side telemetry drawer displays its live prompt, execution trace, and generated schema:
       ```sql
       CREATE TABLE subscriptions (
           id VARCHAR(36) PRIMARY KEY,
           org_id VARCHAR(36) NOT NULL,
           plan_tier VARCHAR(20) NOT NULL,
           status VARCHAR(20) DEFAULT 'active'
       );
       ```
2. **Dynamic Sub-Agent Spawning:**
   - Highlight the dynamic sub-agent badge appearing beneath the Developer (`DB Migration Assistant` & `Frontend Specialist`).
   - Mention: *"We don't keep redundant static agents idling; the primary Developer dynamically spawns specialist sub-workers when it detects complex multi-tier tasks."*

### Act 4: Milestones Roadmap & Knowledge Graph (3:30 - 4:30)
1. **Switch to Tab 3: `Milestones`**:
   - Show the 5 phases:
     1. Architecture & Threat Model $\to$ `COMPLETED`
     2. Database Schema & Migrations $\to$ `COMPLETED`
     3. Full-Stack Implementation $\to$ `IN_PROGRESS`
     4. TDD Verification & Security Audit $\to$ `PENDING`
     5. Production Packaging $\to$ `PENDING`
   - Point out the individual criteria checkboxes that automatically turn green as SQLite verification checks pass.
2. **Switch to Tab 4: `Knowledge Graph`**:
   - Show the Cytoscape graph rendering live:
     - Blue nodes: Architectural Decisions.
     - Emerald nodes: Database Entities.
     - Violet nodes: Code Modules.
   - Click any node to open the node inspector showing its rationale and timestamp.
   - Mention: *"No third-party apps like Obsidian needed—we embedded a hardware-accelerated graph renderer right in the browser."*

### Act 5: Verification & The Finale (4:30 - 5:00)
1. **Show the Physical Code on Disk:**
   - Open `projects/paypulse_billing/` in VS Code or File Explorer.
   - Show that real, well-structured files (`index.html`, `server.py`, `models.sql`, `tests/`) were created.
2. **Show the Token Metering Receipt in the Header:**
   - Points out the total token tally and exact cost:
     `Tokens: 46,280 | Cost: $0.16 USD`.
3. **Show the Automated Playwright Test:**
   ```powershell
   python -m pytest tests/e2e/test_milestones_and_hitl.py -v
   ```
   - 1 passed, 0 console errors.

---

## 5. Live Demo Cheat Sheet & Fallbacks

| Scenario | What Happened | Immediate One-Click Fix |
| :--- | :--- | :--- |
| **API Rate Limit (429)** | Free tier key hit temporary rate throttle. | The framework automatically falls back to secondary provider (`GROQ_API_KEY` $\to$ `GEMINI_API_KEY` $\to$ `OPENROUTER_API_KEY`). If offline, set `$env:MOCK_LLM="1"` for instantaneous deterministic mock mode. |
| **Console Server Stopped** | Port 8000 closed or background process stopped. | Run `python -m ui.server` in terminal. It automatically re-attaches to `.persistent_crewai/state.db` without losing data. |
| **Audience Asks: "Does it persist?"** | Evaluator tests if state survives server crash. | Kill the server (`Ctrl+C`), restart it, and refresh the browser. All tasks, milestones, and graph nodes reload instantly from SQLite WAL. |

---

## 6. Pre-Flight Checklist Before You Present

- [x] Control Console running on `http://127.0.0.1:8000`.
- [x] `.env` contains valid keys (`GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`).
- [x] Repo checked and verified clean of all machine paths (`C:\Users\...`) and AI dev logs.
- [x] Test suite passing (`pytest tests/e2e/test_milestones_and_hitl.py`).
- [x] Demo browser window open and sized to 1920x1080 for clean presentation.
