# Original User Request

## Initial Request — 2026-10-13T19:59:31Z

Requested team: Coordinator/Manager, Developer, Architect, Designer, Tester, Reviewer, GitHub Manager, Memory/Status Manager

Build a persistent multi-agent software engineering framework in Python using CrewAI, featuring long-term memory, state tracking, and GitHub coordination across a specialized 8-agent team.

Working directory: C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team
Integrity mode: development

## Requirements

### R1. Agent Team & Role Definitions
Configure and implement 8 persistent, collaborative agents with explicit goals, backstories, tools, and processes:
1. Coordinator / Manager: Project orchestration, task delegation, backlog management, process sequencing.
2. Architect: System design, architecture blueprints, data schema, tech stack evaluation.
3. Designer: UI/UX specifications, design systems, interface flows, user interaction design.
4. Developer: Implementation of features, bug fixes, module integrations.
5. Tester: Unit, integration, and E2E test suite authoring, test execution, regression auditing.
6. Reviewer: Code review, security auditing, style compliance, refactoring guidance.
7. GitHub Manager: Issue/PR automation, branch management, commit hygiene, repository synchronization.
8. Memory & Status Manager: Centralized state management, operational status monitoring, memory persistence across session runs.

### R2. State & Memory Persistence
The framework must persist project context, decisions, task history, and operational status between runs using local persistent storage (e.g. SQLite database and vector store memory). Rerunning the system must resume from the saved state.

### R3. GitHub & Workspace Tooling
Provide operational integration with local workspace files and GitHub operations (via simulated mock adapter or GitHub API when tokens are available) for issue tracking and PR generation.

### R4. CLI & Interactive Direct Agent Communication
Provide a CLI / REPL interface where you can:
- Interactively query specific agents directly: e.g., prompt the Manager agent ("What is the current status of the project?"), the Architect ("Explain the DB design"), or the Memory Manager ("Show recorded milestones").
- Trigger full project workflows or targeted sub-tasks.
- View real-time traces and status dashboards.

## Acceptance Criteria

### Execution & Architecture
- [ ] Complete runnable Python package with dependencies configured (e.g. `requirements.txt` or `pyproject.toml`).
- [ ] All 8 agent roles are instantiated with distinct prompts, capabilities, and delegation rules.
- [ ] Interactive query mode: Users can select an individual agent (such as the Manager) and hold a Q&A conversation grounded in the persisted project state.
- [ ] System executes sequential or hierarchical tasks across all 8 roles without runtime errors.

### Persistence & Verification
- [ ] Persistence test: Running an initial task records state to disk; running a subsequent invocation retrieves past context and continues without losing history.
- [ ] Automated verification script (e.g. `pytest tests/`) validating state persistence, agent instantiation, and pipeline execution.
- [ ] Documentation (`README.md`) detailing setup, configuration, architecture diagram, and execution examples.
