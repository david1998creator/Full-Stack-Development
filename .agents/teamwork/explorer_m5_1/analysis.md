# Architectural Blueprint & Analysis: README.md Core Specifications
**Author**: Explorer M5-1 (Milestone 5 Documentation Architecture & Role Matrix Explorer)  
**Target Files**: `README.md` (Sections 1 through 5)  
**Project**: Persistent CrewAI 8-Agent Software Engineering Framework  
**Date**: 2026-10-13  

---

## Executive Summary
This document establishes the exhaustive, verbatim-grounded blueprint for Sections 1 through 5 of the project `README.md`. The specifications are derived directly from the audited codebase (`src/persistent_crewai/` and `tests/`), incorporating every architectural invariant, data schema, delegation rule, security guarantee, and persistence mechanism implemented across Milestones 1 through 4.

---

## 1. System Overview & Problem Statement

### 1.1 Problem Statement
Modern multi-agent AI frameworks (such as conventional CrewAI configurations) suffer from several critical engineering limitations when applied to real-world software engineering lifecycles:
1. **Context Evaporation & Ephemerality**: Agent states, design decisions, execution traces, and milestone deliverables exist solely in volatile process memory. A process termination or crash wipes the entire project context, forcing teams to restart workflows from scratch.
2. **Ungoverned Delegation Chaos**: Without strict role-based delegation boundaries, agents frequently enter delegation loops, misroute specialized tasks, or bypass review processes.
3. **Flaky External Dependencies**: Frameworks tightly coupled to paid LLM cloud APIs and live GitHub endpoints cannot run reliably in offline environments, local air-gapped developer workstations, or deterministic CI test runners.
4. **Filesystem Vulnerability**: Standard agent tooling grants broad disk access without path boundary sandboxing, risking path traversal attacks (`../` escapes, symlink redirection) and partial write corruption during system crashes.
5. **Lack of Human Observability & Steering**: Users are typically constrained to batch pipeline execution without the ability to pause, query an individual specialist (e.g., asking the Architect about schema trade-offs), inspect real-time telemetry, or resume interrupted pipelines.

### 1.2 System Overview & Solution Architecture
**Persistent CrewAI** is an enterprise-grade, persistent multi-agent software engineering framework developed in Python. It orchestrates a specialized 8-agent engineering team that operates with human-in-the-loop steering, transactional ACID state persistence, semantic vector memory, sandboxed file operations, and bi-modal version control adapters.

#### Key Architectural Pillars
- **8 Specialized Canonical Roles**: A rigid, production-tested team consisting of Coordinator, Architect, Designer, Developer, Tester, Reviewer, GitHub Manager, and Memory & Status Manager.
- **Strict Delegation Hierarchy**: Only the **Coordinator** is permitted to delegate (`allow_delegation=True`). All 7 specialist roles are strictly leaf executors (`allow_delegation=False`), eliminating delegation loops and guaranteeing predictable execution flow.
- **Transactional SQLite WAL Persistence**: 8 relational tables plus a schema migration ledger operating in Write-Ahead Logging (`WAL`) mode with foreign key enforcement and busy-timeout protection, ensuring zero context loss across runs.
- **Hybrid Semantic & Lexical Vector Memory**: An offline, deterministic vector store (`SQLiteVectorStore`) powered by 64-dimensional unit L2-normalized SHA-256 token hashing (`DeterministicHashEmbeddingProvider`) combined with SQLite `FTS5` full-text search with automatic sync triggers.
- **Sandboxed Workspace Tooling**: Strict canonical path validation (`validate_safe_path`), atomic writes utilizing sibling temporary files with physical disk synchronization (`os.fsync`) and atomic file replacement (`os.replace`), and single-occurrence exact search-and-replace patching.
- **Bi-Modal GitHub Adapters**: A unified `GitHubAdapterInterface` that operates transparently via `MockGitHubAdapter` (local JSON-backed simulation for 100% offline testing/CI) or `RealGitHubAdapter` (live GitHub REST API v3 when `GITHUB_TOKEN` is supplied).
- **Dual-Mode Execution & Offline Fallback Engine**: Runs natively on CrewAI and LangChain when installed, with an automatic, zero-dependency offline fallback engine (`Agent`, `Task`, `Crew`, `CrewOutput`, `Process`) driven by `DeterministicMockLLM`.
- **Interactive REPL & CLI Interface**: A terminal interface supporting state-grounded direct agent Q&A (`ask <role> <prompt>`), targeted subtask triggers (`task <role> <desc>`), multi-agent workflow dispatch (`run <workflow>`), Rich / ASCII status dashboards, and portable JSON state export/import.

---

## 2. Architecture Diagram

Below is the comprehensive ASCII architecture diagram illustrating the interaction between the user interface, orchestration engine, canonical specialist roles, persistence layer, sandboxed workspace, and GitHub adapters:

```text
=============================================================================================================
                          PERSISTENT CREWAI 8-AGENT SOFTWARE ENGINEERING FRAMEWORK
=============================================================================================================

                                       +-----------------------------+
                                       |   User / Terminal Client    |
                                       +--------------+--------------+
                                                      |
                                    [Interactive CLI / REPL Shell]
                                                      |
                   +----------------------------------+----------------------------------+
                   |                                                                     |
                   v                                                                     v
     +----------------------------+                                        +----------------------------+
     |   Interactive Query Mode   |                                        |  Multi-Agent Pipeline      |
     |   (Single-Agent Direct Q&A)|                                        |  Runner (Seq / Hier)       |
     +-------------+--------------+                                        +-------------+--------------+
                   |                                                                     |
                   | Injects Persisted SQLite Context                                    | Chains Tasks / Context
                   +----------------------------------+----------------------------------+
                                                      |
                                                      v
     +==================================================================================================+
     |                                    8 CANONICAL SPECIALIST AGENTS                                 |
     |                                                                                                  |
     |    [COORDINATOR]  <=== (ONLY role with allow_delegation=True; orchestrates, plans, synthesizes) |
     |          |                                                                                       |
     |          +--- delegates to ---> [ARCHITECT]             (Blueprints, ADRs, SQLite Schemas)      |
     |          +--- delegates to ---> [DESIGNER]              (UI/UX Specs, CLI Grammar, Design Tokens)|
     |          +--- delegates to ---> [DEVELOPER]             (PEP-8 Code, Type Hints, Bugfixes)       |
     |          +--- delegates to ---> [TESTER]                (pytest Suites, SDET, QA Sign-off)       |
     |          +--- delegates to ---> [REVIEWER]              (Security Audits, Static Analysis)       |
     |          +--- delegates to ---> [GITHUB MANAGER]        (Branches, Commits, PRs, Issue Sync)     |
     |          +--- delegates to ---> [MEMORY/STATUS MANAGER] (ACID Durability, Telemetry, Checkpoints)|
     |                                                                                                  |
     |    * ALL 7 Specialists have strictly: allow_delegation = False (Leaf Executors)                  |
     +================================================+=================================================+
                                                      |
                             +------------------------+------------------------+
                             |                                                 |
                             v                                                 v
     +------------------------------------------------+       +-----------------------------------------+
     |        Execution & Orchestration Engine        |       |        Workspace & GitHub Tooling       |
     |                                                |       |                                         |
     |  - Dual-Mode: Native CrewAI or Offline Engine  |       |  [Sandboxed Workspace File Operations]  |
     |  - SequentialPipelineRunner (Linear Chaining)  |       |   * validate_safe_path (traversal guard)|
     |  - HierarchicalPipelineRunner (Manager Flow)   |       |   * Atomic writes (tempfile+fsync+rename|
     |  - LLM Factory (DeterministicMockLLM / Live)   |       |   * Exact search-and-replace patching   |
     |  - Step Callbacks & Monotonic Trace Logging    |       |   * Line-sliced reads (1-based index)   |
     |  - Crash Recovery & Resumption Engine          |       |                                         |
     +-----------------------+------------------------+       |  [Bi-Modal GitHub Adapters]             |
                             |                                |   * GitHubAdapterInterface (Abstract)   |
                             | Synchronizes State / Traces    |   * MockGitHubAdapter (JSON-backed, CI) |
                             |                                |   * RealGitHubAdapter (REST API v3)     |
                             |                                +--------------------+--------------------+
                             |                                                     |
                             +------------------------+----------------------------+
                                                      |
                                                      v
     +==================================================================================================+
     |                                    STATE & MEMORY PERSISTENCE                                    |
     |                                                                                                  |
     |   +------------------------------------------------------------------------------------------+   |
     |   |                         SQLite Relational Database (WAL Mode)                            |   |
     |   |                                                                                          |   |
     |   |  - PRAGMAs: journal_mode=WAL, foreign_keys=ON, busy_timeout=5000, synchronous=NORMAL    |   |
     |   |  - 8 Normalized Tables:                                                                  |   |
     |   |    1. projects          (Workspace roots, integrity mode, project lifecycle status)      |   |
     |   |    2. decisions         (Architectural Decision Records with tags & superseded_by FK)    |   |
     |   |    3. workflow_runs     (Execution runs, trigger source, input prompt, final output)     |   |
     |   |    4. task_history      (Per-agent task execution lifecycle, status, latency_ms)         |   |
     |   |    5. milestones        (Project milestones, target criteria array, achievement time)    |   |
     |   |    6. agent_status      (Composite PK [agent_role, project_id], operational health)      |   |
     |   |    7. execution_traces  (Monotonic thoughts, tool calls, audit event payloads)           |   |
     |   |    8. memory_records    (Semantic text chunks, JSON metadata, float vector embeddings)   |   |
     |   |    + schema_migrations  (Version tracking, applied_at timestamps, descriptions)          |   |
     |   +--------------------------------------------+---------------------------------------------+   |
     |                                                |                                                 |
     |                 +------------------------------+-----------------------------+                   |
     |                 |                                                            |                   |
     |                 v                                                            v                   |
     |   +-----------------------------------------+    +-------------------------------------------+   |
     |   |   SQLite FTS5 Full-Text Virtual Table   |    |         Persistent Vector Store           |   |
     |   |               (memory_fts)              |    |            (SQLiteVectorStore)            |   |
     |   |  - Porter unicode61 lexical tokenizer   |    |  - DeterministicHashEmbeddingProvider     |   |
     |   |  - BM25 ranked relevance score          |    |    (64-dim unit L2 SHA-256 token hashing) |   |
     |   |  - Automated SQLite Triggers:           |    |  - Cosine similarity vector search        |   |
     |   |    * trg_memory_insert (sync on insert) |    |  - Hybrid fusion formula:                 |   |
     |   |    * trg_memory_delete (sync on delete) |    |    Score = alpha*Cosine + (1-alpha)*BM25  |   |
     |   |    * trg_memory_update (sync on update) |    |  - Metadata filtering by role & category  |   |
     |   +-----------------------------------------+    +-------------------------------------------+   |
     |                                                                                                  |
     |   +------------------------------------------------------------------------------------------+   |
     |   |                        Session Resumption & Portability Engine                           |   |
     |   |  - Crash inspection: detects orphaned in_progress tasks, marks interrupted, resets idle |   |
     |   |  - WorkflowResumePlan: partitions completed, interrupted, pending; retries seamlessly    |   |
     |   |  - Portable State Export / Import: Full JSON serialization with deferred FK resolution   |   |
     |   +------------------------------------------------------------------------------------------+   |
     +==================================================================================================+
```

---

## 3. 8 Specialist Roles Matrix

The framework defines 8 canonical agent roles. Each agent possesses a dedicated goal, professional backstory, specialized system prompt guidelines, assigned tools, and strict delegation boundaries:

| Canonical Role Name | Display Name | Specialist Purpose | Assigned Tools | Delegation Allowed | System Prompt Operational Rules & Highlights |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Coordinator** | Engineering Coordinator & Project Manager | End-to-end orchestration, work breakdown structure (WBS), task delegation, milestone tracking, and executive release synthesis. | `list_workspace_files`, `query_memory`, `record_milestone` | **`True`** *(Only role permitted)* | Deconstructs goals into sequential phases: Architecture &rarr; Design &rarr; Development &rarr; Testing &rarr; Review &rarr; Version Control &rarr; State Persistence. **Never writes code or executes git operations directly.** Requires Reviewer and Tester approval before release. |
| **Architect** | Lead Systems & Software Architect | System design, Architectural Decision Records (ADRs), SQLite schemas, component boundaries, and technology stack evaluation. | `read_workspace_file`, `list_workspace_files`, `record_decision`, `query_memory` | **`False`** | Formulates concrete file paths, class signatures, and interface contracts. Validates relational schemas against persistence invariants. **Must record all architectural decisions using `record_decision`.** |
| **Designer** | Staff Product & UI/UX Designer | UI/UX specifications, design systems, design tokens, CLI command ergonomics, TUI output formatting, and user interaction flows. | `read_workspace_file`, `write_workspace_file`, `list_workspace_files` | **`False`** | Specifies CLI command grammar, Rich terminal formatting, table aesthetics, and ASCII fallback views. Hands off specifications to Developer for coding and Tester for UX validation. |
| **Developer** | Senior Software Engineer | Production-grade Python feature implementation, bug fixes, module integrations, and clean refactoring. | `read_workspace_file`, `write_workspace_file`, `patch_workspace_file`, `list_workspace_files` | **`False`** | Implements clean PEP-8 compliant code with complete type annotations (PEP 484/526), docstrings, and robust exception boundaries. Submits all code to Reviewer and Tester. **Forbidden from merging code directly.** |
| **Tester** | Lead SDET & QA Automation Engineer | Unit, integration, and E2E test suite authoring in `tests/`, test execution, regression auditing, and QA sign-off certification. | `read_workspace_file`, `list_workspace_files` | **`False`** | Builds deterministic, isolated test suites using pytest fixtures and mocks. Validates cross-session state persistence. Issues `FAIL` verdicts to Developer and `PASS` certifications to Coordinator. **Forbidden from writing production business logic.** |
| **Reviewer** | Principal Code Reviewer & Security Auditor | Rigorous static analysis, security audits (OWASP Top 10, CWE), PEP-8 compliance, typing checks, and architectural verification. | `read_workspace_file`, `list_workspace_files`, `query_memory` | **`False`** | Scans for SQL injection, path traversal escapes, and hardcoded secrets. Categorizes findings into `CRITICAL`, `MAJOR`, `MINOR`, and `OPTIMIZATION`. Renders unambiguous verdicts: `APPROVED` or `CHANGES_REQUESTED`. **Does not modify source files directly.** |
| **GitHub Manager** | Lead DevOps & Repository Operations Specialist | Git repository lifecycles, branch strategies, Conventional Commits, Pull Request automation, and issue tracking. | `github_issue_tool`, `github_pr_tool`, `github_branch_tool`, `github_commit_tool`, `github_repo_status_tool` | **`False`** | Enforces Conventional Commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`). Creates branches and PRs **only after Reviewer and Tester sign-off**. Operates offline via `MockGitHubAdapter` or online via `RealGitHubAdapter`. **Does not write application code.** |
| **Memory & Status Manager** | State Persistence & Knowledge Memory Specialist | Transactional state persistence, operational health telemetry, task execution history, and semantic memory indexing. | `record_decision`, `record_milestone`, `query_memory` | **`False`** | Persists decisions, milestones, and task latency metrics into SQLite WAL storage. Maintains vector embeddings and FTS5 indices. Rehydrates state for cross-session resumption. **Never modifies application code or Git repositories directly.** |

---

## 4. Persistence & Session Resumption

### 4.1 Transactional SQLite WAL Database
The persistence engine (`DatabaseManager` and `StateManager`) enforces ACID transactions across multi-agent executions. It initializes a local SQLite database (`.persistent_crewai/state.db`) configured with high-performance, fault-tolerant pragmas:

- `PRAGMA journal_mode = WAL;`: Enables Write-Ahead Logging. Readers never block writers, and writers never block readers, permitting high-throughput concurrent reads during active agent writes.
- `PRAGMA foreign_keys = ON;`: Enforces relational integrity across projects, workflow runs, tasks, decisions, and traces.
- `PRAGMA synchronous = NORMAL;`: Ensures durability across unexpected application crashes while reducing disk I/O latency compared to `FULL` synchronous mode.
- `PRAGMA busy_timeout = 5000;`: Waits up to 5,000 milliseconds when acquiring locks, eliminating transient SQLite locking errors under concurrent agent threads.
- `PRAGMA encoding = 'UTF-8';`: Guarantees UTF-8 string encoding across all platforms, preventing Windows `cp1252` encoding corruption.

### 4.2 Relational Schema & Migration Architecture
The database schema defines 8 normalized tables and a migration history ledger:
1. `schema_migrations`: Tracks migration versions (`CURRENT_SCHEMA_VERSION = "1.0.0"`), execution timestamps, and descriptions.
2. `projects`: Stores project metadata, unique `project_id`, workspace root directory path, integrity mode (`development`, `strict`, `production`), and lifecycle status (`active`, `archived`, `completed`).
3. `decisions`: Architectural Decision Records (ADRs) with proposing `agent_role`, `title`, `rationale`, `implications`, `status` (`proposed`, `accepted`, `superseded`, `rejected`), JSON tags array, and self-referencing `superseded_by` foreign key.
4. `workflow_runs`: High-level workflow execution records (`run_id`, `workflow_type`, `trigger_source`, `input_prompt`, `final_output`, `status`, `error_message`, and timestamps).
5. `task_history`: Granular per-agent task executions with `execution_order`, expected vs actual outputs, status transitions, error details, and high-resolution execution latency (`metadata_json.latency_ms`).
6. `milestones`: Project milestone definitions, target criteria JSON array, status (`pending`, `in_progress`, `achieved`, `missed`), and achievement timestamps.
7. `agent_status`: Composite primary key `(agent_role, project_id)` tracking real-time operational state (`idle`, `active`, `busy`, `blocked`, `completed`, `error`), currently assigned `current_task_id`, `tasks_completed_count`, and `last_error`.
8. `execution_traces`: Granular audit log of agent thoughts, tool invocations, and system events with timestamps.
9. `memory_records`: Text snippets, JSON metadata, and vector embeddings for semantic and contextual agent memory.

### 4.3 Hybrid Semantic & Full-Text Search
The framework pairs relational persistence with a hybrid retrieval mechanism:
- **`DeterministicHashEmbeddingProvider`**: A zero-dependency, 100% offline embedding provider implementing `EmbeddingProviderInterface`. It transforms text into 64-dimensional float vectors by hashing word tokens and character 3-grams using SHA-256, normalized to unit L2 length:
  $$\|\vec{v}\|_2 = \sqrt{\sum_{i=1}^{64} v_i^2} = 1.0$$
- **`SQLiteVectorStore`**: Persists document vectors in `memory_records.embedding_json`. Computes cosine similarity between query and stored vectors:
  $$\text{CosineSimilarity}(\vec{q}, \vec{d}) = \sum_{i=1}^{64} q_i \cdot d_i$$
- **`memory_fts` & Automated Triggers**: An SQLite FTS5 virtual table tokenized via `porter unicode61`. Synchronized automatically with `memory_records` using three native SQLite triggers:
  - `trg_memory_insert`: Automatically indexes inserted content.
  - `trg_memory_delete`: Automatically purges deleted content.
  - `trg_memory_update`: Automatically re-indexes modified content.
- **Hybrid Fusion Formula**:
  $$\text{FinalScore} = \alpha \cdot \max(0, \text{CosineSimilarity}) + (1 - \alpha) \cdot \text{NormalizedBM25}$$
  (default $\alpha = 0.7$), combining semantic concept similarity with exact keyword matching.

### 4.4 Crash Recovery & Session Resumption
The `SessionResumptionManager` provides resilience against abrupt process termination, power loss, or execution interruptions:
1. **Crash Inspection & Auto-Recovery**:
   - Scans `task_history` for tasks orphaned in `in_progress` state or linked to `busy` agents.
   - Transitions orphaned tasks to `interrupted` status with detailed diagnostic context (`"Session interrupted: process terminated during execution"`).
   - Resets all `busy` or `active` agents back to `idle`.
   - Transitions uncompleted `running` workflow runs to `interrupted`.
2. **Resumption Planning (`WorkflowResumePlan`)**:
   - `resume_workflow(run_id)`: Analyzes past tasks, extracts context artifacts from all `completed` steps, and identifies the next actionable task.
   - Sets `recommended_action`: `retry_interrupted` if interrupted tasks exist, `continue_pending` if pending tasks remain, or `all_completed`.
   - Injects accumulated upstream deliverables into the resuming agent so execution continues seamlessly without duplicate work.
3. **Session Rehydration (`ResumptionContext`)**:
   - Synthesizes active project metadata, current milestones, unfulfilled criteria, recent accepted ADRs, and operational agent health into a structured context block injected into agent system prompts during interactive sessions.
4. **State Export & Import**:
   - `export_state(target_path)`: Atomically exports all 8 relational tables, schema migrations, and traces into a portable JSON backup file.
   - `import_state(source_path, overwrite=False)`: Atomically re-populates the database using `PRAGMA defer_foreign_keys = ON;` and two-pass decision insertion to resolve self-referential foreign key dependencies.

---

## 5. Sandboxed Security & Path Traversal Containment

To ensure agent operations cannot compromise host systems or corrupt the workspace, all filesystem interactions are governed by defense-in-depth security controls in `src/persistent_crewai/tools/workspace_tools.py`:

### 5.1 Strict Path Containment (`validate_safe_path`)
Every tool accepting a filesystem path executes `validate_safe_path(target_path, workspace_root)` prior to any disk I/O:
1. **Sanitization**:
   - Rejects `None`, empty strings, and whitespace-only targets with `ValueError`.
   - Rejects embedded null bytes (`\x00`) with `ValueError` to prevent C-level string termination exploits.
2. **Canonical Resolution**:
   - Resolves both `workspace_root` and `target_path` to absolute, canonical, symlink-dereferenced paths:
     ```python
     root_resolved = Path(workspace_root).resolve()
     if target_p.is_absolute():
         resolved = target_p.resolve()
     else:
         resolved = (root_resolved / target_p).resolve()
     ```
3. **Boundary Verification**:
   - Evaluates whether the canonical path resides strictly within the workspace directory tree:
     ```python
     if not resolved.is_relative_to(root_resolved):
         raise PermissionError(f"Security violation: Access outside workspace root is forbidden. Target: {target_path}")
     ```
   - Blocks all directory traversal techniques, including relative dot-dot sequences (`../../`), absolute drive/root escapes (`/etc/passwd`, `C:\Windows\System32`), and path traversal via symlinks.

### 5.2 Atomic File Writes with Physical Disk Sync (`os.fsync`)
To eliminate the risk of partial, truncated, or corrupted files during crashes or interruptions, `WorkspaceFileWriteTool` and `WorkspaceFilePatchTool` utilize atomic write semantics:
1. **Parent Directory Bootstrapping**:
   - Automatically provisions parent directories: `canonical.parent.mkdir(parents=True, exist_ok=True)`.
2. **Sibling Temporary File**:
   - Allocates a temporary file in the *same* filesystem directory as the target (`tempfile.NamedTemporaryFile("w", dir=canonical.parent, delete=False, encoding="utf-8")`), guaranteeing that the subsequent replacement is an atomic same-filesystem inode/directory entry operation.
3. **Buffer Flush & Disk Fsync**:
   - Calls `temp_file.flush()` followed by `os.fsync(temp_file.fileno())` to force OS write buffers to be flushed physically to non-volatile storage.
4. **Atomic Rename / Replace**:
   - Calls `os.replace(temp_file.name, canonical)`. On POSIX platforms, `rename()` is an atomic operation. On Windows, `os.replace()` performs an atomic replacement of the target file.
5. **Exception Handling Clean-up**:
   - If an error occurs prior to replacement, the temporary file is deleted immediately, leaving the original target file completely unmodified.

### 5.3 Ambiguity-Free Search-and-Replace Patching
The `WorkspaceFilePatchTool` enforces surgical code modifications:
- Requires exact substring matching between `search_block` and target file content.
- Evaluates occurrence count:
  - If `count == 0`: Raises `ValueError("Search block not found in target file")`.
  - If `count > 1`: Raises `ValueError(f"Ambiguous patch: {count} occurrences found... Provide broader unique context")`.
  - If `count == 1`: Replaces exactly that single occurrence and commits via atomic write with `fsync`.
- Prevents unintended code edits across repetitive code patterns.

### 5.4 Line-Sliced Reads & Directory Guards
The `WorkspaceFileReadTool`:
- Validates 1-based line slicing parameters (`start_line >= 1`, `end_line >= start_line`).
- Reads text with UTF-8 encoding and `errors="replace"` to prevent unhandled decode crashes on mixed-encoding or binary files.
- Explicitly guards against directory targets (`if canonical.is_dir(): raise IsADirectoryError(...)`).

### 5.5 Operational Event Auditing
All workspace file operations (`read`, `write`, `patch`, `list`) automatically emit operational telemetry to the active `StateManager`. Each file access logs the tool name, relative path, bytes affected, and timestamp to the persistent `execution_traces` audit table.

---

## 6. Synthesis & Verification Plan

### 6.1 Verification Evidence
| Architectural Claim | Source Code Verification Location | Test Suite Verification |
| :--- | :--- | :--- |
| 8 Canonical Roles & Tools | `src/persistent_crewai/agents/role_definitions.py:95-338` | `tests/test_agents.py` |
| Coordinator Delegation Permitted Only | `src/persistent_crewai/agents/role_definitions.py:124, 154, 183, 213, 244, 274, 304, 334` | `tests/test_agents.py`, `tests/test_pipeline.py` |
| SQLite WAL & 8 Relational Tables | `src/persistent_crewai/persistence/db.py:20-175, 298-304` | `tests/test_persistence.py` |
| FTS5 Virtual Table & 3 Triggers | `src/persistent_crewai/persistence/db.py:154-174` | `tests/test_persistence.py` |
| DeterministicHashEmbedding (64-dim) | `src/persistent_crewai/persistence/vector_store.py:42-89` | `tests/test_persistence.py` |
| Hybrid Cosine/BM25 Search | `src/persistent_crewai/persistence/vector_store.py:191-301` | `tests/test_persistence.py` |
| Crash Recovery & Resumption Plan | `src/persistent_crewai/persistence/session.py:120-210, 284-318` | `tests/test_persistence.py` |
| Path Traversal Defense (`validate_safe_path`) | `src/persistent_crewai/tools/workspace_tools.py:43-89` | `tests/test_tools.py`, `tests/test_adversarial_workspace_tools.py` |
| Atomic File Write with Fsync | `src/persistent_crewai/tools/workspace_tools.py:192-233` | `tests/test_tools.py` |
| Exact Patch Validation | `src/persistent_crewai/tools/workspace_tools.py:353-420` | `tests/test_tools.py` |

### 6.2 Implementation Readiness
This blueprint is immediately actionable and provides all structural, technical, and tabular content necessary for the final `README.md` authoring task.
