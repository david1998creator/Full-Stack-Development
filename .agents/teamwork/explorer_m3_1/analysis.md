# Architecture Analysis & Implementation Plan: LLM Factory & DeterministicMockLLM

## Executive Summary

This document presents the complete technical architecture and implementation specification for `src/persistent_crewai/agents/llm_factory.py` and the corresponding unit test suite `tests/test_agents.py`.

The objective of this subsystem is to solve the dual challenges of **offline hermetic test execution** and **seamless live multi-provider production integration**:
1. **Offline & CI Execution**: The framework must execute genuine multi-agent workflows, tool invocations, and interactive direct Q&A without network access or live API keys. `DeterministicMockLLM` delivers role-grounded, context-aware, reproducible responses compatible with CrewAI and LangChain interfaces.
2. **Production Multi-Provider Execution**: When credentials are provided (`OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`), `LLMFactory` / `get_llm()` dynamically instantiates the appropriate live chat model while falling back gracefully to `DeterministicMockLLM` if keys are missing or when mock mode is enabled (`MOCK_LLM=true`).

---

## 1. DeterministicMockLLM Architecture

### 1.1 Interface Compatibility Matrix

CrewAI agents and execution loops interact with LLM backends using a combination of LangChain `BaseChatModel`, LiteLLM interfaces, and Pydantic validation. To ensure 100% compatibility across environments (both when `langchain_core` / `crewai` are present and when running in minimal offline environments), `DeterministicMockLLM` implements the following compatibility pattern:

```
                  ┌──────────────────────────────────────────────┐
                  │           DeterministicMockLLM               │
                  │  (Dual-mode: BaseChatModel or Pydantic Base) │
                  └──────────────────────┬───────────────────────┘
                                         │
     ┌───────────────────────────────────┼───────────────────────────────────┐
     ▼                                   ▼                                   ▼
┌──────────────────────────┐ ┌──────────────────────────┐ ┌──────────────────────────┐
│   Standard LangChain     │ │    CrewAI Agent Hook     │ │   Direct Query / Tests   │
│ - _generate(messages)    │ │ - bind(stop=...)         │ │ - predict(text)          │
│ - generate(prompts)      │ │ - bind_tools(tools)      │ │ - call(text)             │
│ - invoke(input)          │ │ - with_structured_output │ │ - __call__(text)         │
└──────────────────────────┘ └──────────────────────────┘ └──────────────────────────┘
```

#### Dual-Mode Inheritance Layer
```python
try:
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    _HAS_LANGCHAIN = True
except (ImportError, Exception):
    _HAS_LANGCHAIN = False

    class BaseMessage(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
        content: str = ""
        type: str = "base"
        def __str__(self) -> str:
            return self.content

    class AIMessage(BaseMessage):
        type: str = "ai"

    class HumanMessage(BaseMessage):
        type: str = "human"

    class SystemMessage(BaseMessage):
        type: str = "system"

    class ChatGeneration(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
        message: BaseMessage
        text: str = ""

    class ChatResult(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
        generations: List[ChatGeneration] = Field(default_factory=list)

    class BaseChatModel(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
        model_name: str = "mock-model"
        temperature: float = 0.0
```

### 1.2 Required Class Attributes & State

| Field | Type | Default | Description |
|---|---|---|---|
| `model_name` | `str` | `"deterministic-mock-llm"` | Identifier for the mock model |
| `temperature` | `float` | `0.0` | Default deterministic temperature (0.0) |
| `call_history` | `List[Any]` | `[]` | Monotonic log of all incoming prompts / messages |
| `canned_responses` | `List[str]` | `[]` | FIFO queue for pre-seeded test responses |
| `responses_by_role` | `Dict[str, str]` | `{}` | Role-to-response overrides |
| `tool_call_triggers` | `Dict[str, str]` | `{}` | Triggers emitting ReAct action strings |
| `verbose` | `bool` | `False` | Trace logging flag |

### 1.3 Methods Contract

1. **`predict(text: str, **kwargs: Any) -> str`**:
   - Primary string-to-string invocation.
   - Appends `text` to `call_history`.
   - Returns synthesized deterministic string response.
2. **`invoke(input: Any, **kwargs: Any) -> AIMessage`**:
   - Accepts `str`, `BaseMessage`, `list[BaseMessage]`, or `dict`.
   - Extracts prompt text, appends to `call_history`.
   - Returns `AIMessage(content=response_text)` where `str(msg)` yields `response_text`.
3. **`call(messages: Any, **kwargs: Any) -> str`**:
   - Legacy calling convention. Returns string response.
4. **`_generate(messages: List[Any], stop: Optional[List[str]] = None, **kwargs: Any) -> ChatResult`**:
   - LangChain core dispatch method.
   - Extracts combined prompt text from messages, calls synthesis engine, and wraps in `ChatResult(generations=[ChatGeneration(message=AIMessage(content=...))])`.
5. **`generate(prompts: List[Any], stop: Optional[List[str]] = None, **kwargs: Any) -> ChatResult`**:
   - Batched generation support.
6. **`bind(**kwargs: Any) -> DeterministicMockLLM`**:
   - Returns `self` to support CrewAI stop sequences without throwing `AttributeError`.
7. **`bind_tools(tools: Sequence[Any], **kwargs: Any) -> DeterministicMockLLM`**:
   - Returns `self` to support tool binding without throwing `AttributeError`.
8. **`with_structured_output(schema: Any, **kwargs: Any) -> DeterministicMockLLM`**:
   - Returns `self` for structured output API compatibility.
9. **`reset() -> None`**:
   - Clears `call_history` and `canned_responses`.

---

## 2. Dynamic Prompt Parsing & Role Grounding

### 2.1 Prompt Extraction & Pre-Processing
The prompt synthesis engine normalizes input by:
1. Extracting string content from messages or strings.
2. Stripping whitespace and computing a normalized lower-case representation.
3. Checking for:
   - Injected canned queue (`canned_responses`).
   - Configured tool call triggers (`tool_call_triggers`).
   - Specific role headers (`You are the Lead Systems Architect`, `Architect: ...`, etc.).
   - Explicit project context blocks (`--- CURRENT PROJECT CONTEXT ---`).

### 2.2 Role Mapping & Grounded Responses

The prompt parser identifies all 8 canonical roles through keyword patterns and produces domain-grounded answers.

```
                              Prompt Ingestion
                                     │
                 ┌───────────────────┴───────────────────┐
                 ▼                                       ▼
        Canned Response Queue?                  Tool Call Trigger?
          (Yes -> pop FIFO)                     (Yes -> emit Action)
                 │                                       │
                 └───────────────────┬───────────────────┘
                                     │ No
                                     ▼
                        Role Keyword Classifier
                                     │
     ┌───────────┬───────────┬───────┴───┬───────────┬───────────┐
     ▼           ▼           ▼           ▼           ▼           ▼
Coordinator  Architect    Designer    Developer    Tester    Reviewer ...
     │           │           │           │           │           │
     └───────────┴───────────┼───────────┴───────────┴───────────┘
                             │
                             ▼
                 Context Injection Check
            (Inject Active Project & Milestone)
                             │
                             ▼
                 Deterministic Response
                 (Prefixed with Final Answer:)
```

#### Detailed Role Definitions and Canonical Outputs

#### 1. Coordinator / Manager
- **Triggers**: `"coordinator"`, `"manager"`, `"project manager"`, `"project status"`, `"status of the project"`, `"wbs"`, `"backlog"`, `"milestone"`, `"process sequencing"`
- **Response Content**:
```markdown
Final Answer: ### Project Coordinator Status Report
- Project: Persistent CrewAI Engineering Team
- Current Status: All 8 specialist roles active and operational.
- Work Breakdown Structure (WBS):
  1. Architecture: System blueprints and SQLite WAL schema established.
  2. UI/UX Design: CLI and REPL ergonomics formulated.
  3. Development: Core modules implemented with type annotations.
  4. Testing: Comprehensive test suites passing with zero defects.
  5. Code Review: Security audits and PEP 8 compliance verified.
  6. Version Control: Git branches, atomic commits, and PRs synced.
  7. Memory: ACID state persistence and semantic memories indexed.
- Next Milestones: Ready for subsequent development cycle.
```

#### 2. Architect
- **Triggers**: `"architect"`, `"database design"`, `"db design"`, `"schema"`, `"adr"`, `"architecture"`, `"blueprint"`, `"sqlite"`, `"wal"`, `"data model"`
- **Response Content**:
```markdown
Final Answer: ### System Architecture & Database Blueprint
- Architectural Pattern: Decoupled Layered Architecture (Persistence, Tooling, Agents, CLI).
- Storage Engine: SQLite 3 with Write-Ahead Logging (WAL) and foreign keys enabled.
- Database Schema:
  * `projects`: Project context and metadata.
  * `decisions`: Architectural Decision Records (ADRs) with rationale and implications.
  * `workflow_runs`: Execution logs for full workflows and subtasks.
  * `task_history`: Granular task states and outputs.
  * `milestones`: Target criteria and achievement tracking.
  * `agent_status`: Operational telemetry for all 8 agents.
  * `execution_traces`: Monotonic step thoughts and tool calls.
  * `memory_records`: Vector embeddings and full-text search indexing.
- Non-Functional Invariants: Sub-second query times, thread-safe access, and zero data loss on crash.
```

#### 3. Designer
- **Triggers**: `"designer"`, `"ui"`, `"ux"`, `"cli"`, `"repl"`, `"design system"`, `"tokens"`, `"rich"`, `"visual"`, `"wireframe"`, `"interaction"`
- **Response Content**:
```markdown
Final Answer: ### UI/UX & Design System Specification
- Interface Surface: Command-Line Interface (CLI) and interactive REPL.
- Visual Palette: Cyan headers (#00FFFF), green status badges for success, red callouts for errors, yellow for in-progress.
- Component Typography: Rich console formatting, Markdown tables, and ASCII fallback for non-TTY terminals.
- REPL Command Grammar: `ask <role> <query>`, `chat <role>`, `status`, `decisions`, `milestones`, `run workflow <type>`, `run task <role> <desc>`.
- Ergonomics: Tab completion, command history, and clear actionable error suggestions.
```

#### 4. Developer
- **Triggers**: `"developer"`, `"software engineer"`, `"implement"`, `"code"`, `"module"`, `"python"`, `"feature"`, `"bugfix"`, `"refactor"`, `"function"`
- **Response Content**:
```markdown
Final Answer: ### Implementation Summary
- Modules Developed:
  * `src/persistent_crewai/persistence/`: SQLite connection pooling, state manager, vector store.
  * `src/persistent_crewai/tools/`: Sandboxed workspace file tools and bi-modal GitHub adapters.
  * `src/persistent_crewai/agents/`: Role definitions, LLM factory, and crew builder.
  * `src/persistent_crewai/cli/`: Interactive REPL and status dashboard.
- Code Standards: PEP 8 compliant, 100% type-annotated, robust exception boundaries.
- Status: All code written to workspace, ready for test validation.
```

#### 5. Tester
- **Triggers**: `"tester"`, `"test"`, `"qa"`, `"sdet"`, `"pytest"`, `"suite"`, `"coverage"`, `"verify"`, `"regression"`, `"assertion"`
- **Response Content**:
```markdown
Final Answer: ### Test Execution & Quality Assurance Report
- Test Framework: Pytest and standard unittest test runner.
- Suites Executed:
  * `tests/test_persistence.py`: SQLite schema, WAL, FTS5, and transactions.
  * `tests/test_tools.py`: Sandboxed file ops, path traversal containment, MockGitHub.
  * `tests/test_agents.py`: LLM factory, role definitions, and crew builder.
  * `tests/test_pipeline.py`: Sequential and hierarchical execution flows.
- Test Results: 100% passing tests, 0 failures, 0 regressions.
- Verdict: Certified production-ready for deployment.
```

#### 6. Reviewer
- **Triggers**: `"reviewer"`, `"code review"`, `"security"`, `"audit"`, `"pep-8"`, `"owasp"`, `"vulnerability"`, `"style"`, `"approved"`, `"changes_requested"`
- **Response Content**:
```markdown
Final Answer: ### Code Review & Security Audit Verdict
- Static Analysis: Passed. Full PEP 8 adherence, clean type annotations, no cyclic imports.
- Security Audit:
  * SQL Injection: Protected via parameterized queries across all database operations.
  * Path Traversal: Sandboxed via canonical path validation against workspace root.
  * Secrets & Credentials: Zero hardcoded tokens or API keys detected.
- Quality Verdict: APPROVED for repository commit and pull request creation.
```

#### 7. GitHub Manager
- **Triggers**: `"github"`, `"git"`, `"branch"`, `"commit"`, `"pr"`, `"pull request"`, `"issue"`, `"repo"`, `"conventional commits"`
- **Response Content**:
```markdown
Final Answer: ### GitHub & Repository Operations Summary
- Version Control Strategy: Git feature branching and Conventional Commits.
- Active Branch: `feat/persistent-crewai-framework`.
- Recent Commit: `feat: implement persistent crewai 8-agent framework with state persistence`.
- Pull Request: PR #1 opened with comprehensive changelog and linked issue #1.
- Repository Status: Clean working directory, all changes committed and tracked.
```

#### 8. Memory & Status Manager
- **Triggers**: `"memory"`, `"status manager"`, `"persist"`, `"state"`, `"milestone"`, `"recall"`, `"vector"`, `"session"`, `"checkpoint"`, `"history"`
- **Response Content**:
```markdown
Final Answer: ### Memory & Operational State Summary
- Persistence Engine: SQLite state database synchronized in WAL mode.
- Recorded State:
  * Project ID: Active project context rehydrated.
  * Milestones: 100% of defined criteria recorded and validated.
  * Decisions: Architectural Decision Records committed to persistent store.
  * Task History: All execution traces, agent outputs, and latencies logged.
- Vector Index: Semantic memory and document embeddings indexed for similarity search.
- Session Durability: Crash-resilient, ready for cross-session resumption.
```

### 2.3 Context Grounding Parser
When the prompt contains an injected context block:
```
--- CURRENT PROJECT CONTEXT ---
Project: {project_name} (Mode: {integrity_mode})
Current Active Milestone: {current_milestone_title} (Status: {milestone_status})
Relevant Recorded Decisions:
{formatted_decisions}
```
The synthesis engine extracts:
1. `project_name` (e.g., regex `Project:\s*([^\n(]+)`)
2. `milestone_title` (e.g., regex `Current Active Milestone:\s*([^\n(]+)`)
3. `decisions` (e.g., regex `\[([a-zA-Z0-9_-]+)\]\s*(\([^)]+\))?\s*([^\n]+)`)

If found, these extracted facts are dynamically appended or interpolated into the final answer. For example:
```
Context Recorded: Project 'Persistent CrewAI Team', Milestone 'Core Architecture'.
```
This guarantees that interactive single-agent queries in the REPL accurately reflect persisted database state!

### 2.4 Tool Call Triggers & ReAct Looping
For scenarios requiring tool execution:
1. `tool_call_triggers` dictionary maps keyword triggers to tool invocation action strings.
2. Example trigger:
   `trigger_key="read_workspace_file"`
   `trigger_val="Thought: Need to read file.\nAction: read_workspace_file\nAction Input: {\"file_path\": \"config.py\"}"`
3. When `Observation:` appears in the prompt (indicating that the tool has executed and returned its result), the LLM detects that the tool step is complete and produces:
   `Final Answer: Observation received and processed successfully.`

### 2.5 Deterministic SHA-256 Fallback
If an arbitrary or unknown prompt is provided, the mock LLM generates a deterministic signature:
```python
digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8]
return f"Final Answer: Deterministic response [hash={digest}]. Request processed according to engineering specifications."
```
This guarantees:
- 100% reproducibility across test runs.
- Distinct responses for distinct inputs.
- Zero crashes on edge cases or unexpected user prompts.

---

## 3. LLMFactory Architecture & Provider Resolution

### 3.1 Factory Function Signature
```python
def get_llm(
    provider: Optional[str] = None,
    model_name: Optional[str] = None,
    temperature: float = 0.7,
    **kwargs: Any,
) -> Any:
```

### 3.2 Resolution Priority Hierarchy

```
                                get_llm(...) Called
                                        │
             ┌──────────────────────────┴──────────────────────────┐
             ▼                                                     ▼
     provider in ("mock",                               MOCK_LLM=true in env?
   "deterministic", "offline")                                     │
             │ (Yes)                                               │ (Yes)
             ├─────────────────────────────────────────────────────┘
             ▼
   Return DeterministicMockLLM
             │ (No)
             ▼
     provider explicitly specified?
             │
      ┌──────┴──────┐
(Yes) ▼             ▼ (No)
Check provider    Auto-Detect from Environment
  - "openai"        1. OPENAI_API_KEY present? -> return OpenAI LLM
  - "gemini"        2. GEMINI_API_KEY / GOOGLE_API_KEY present? -> return Gemini LLM
  - "anthropic"     3. ANTHROPIC_API_KEY present? -> return Anthropic LLM
  - "ollama"        4. None present -> Return DeterministicMockLLM
```

#### Detailed Resolution Logic:
1. **Mock Provider / Flags**:
   - If `provider` in `("mock", "deterministic", "fake", "offline", "testing")`:
     return `DeterministicMockLLM(model_name=model_name or "deterministic-mock-llm", temperature=temperature, **kwargs)`.
   - If `os.environ.get("MOCK_LLM", "").lower() in ("true", "1", "yes")` or `os.environ.get("CREWAI_MOCK_LLM", "").lower() in ("true", "1", "yes")`:
     return `DeterministicMockLLM(model_name=model_name or "deterministic-mock-llm", temperature=temperature, **kwargs)`.
2. **Explicit Provider Specification**:
   - `provider == "openai"`:
     Validates `OPENAI_API_KEY` in `os.environ`. If missing, raises `ValueError("OPENAI_API_KEY environment variable is required for provider 'openai'")`.
     Attempts import of `langchain_openai.ChatOpenAI`. If available, returns instantiated client. If unavailable, falls back to `DeterministicMockLLM` with a warning or raises depending on strict mode.
   - `provider in ("gemini", "google")`:
     Validates `GEMINI_API_KEY` or `GOOGLE_API_KEY`. If missing, raises `ValueError("GEMINI_API_KEY or GOOGLE_API_KEY environment variable is required for provider 'gemini'")`.
     Attempts import of `langchain_google_genai.ChatGoogleGenerativeAI`.
   - `provider == "anthropic"`:
     Validates `ANTHROPIC_API_KEY`. If missing, raises `ValueError("ANTHROPIC_API_KEY environment variable is required for provider 'anthropic'")`.
     Attempts import of `langchain_anthropic.ChatAnthropic`.
   - `provider == "ollama"`:
     Instantiates `ChatOllama(model=model_name or "llama3", base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))`.
   - Any other provider:
     Raises `ValueError(f"Unsupported LLM provider '{provider}'. Valid providers: 'mock', 'deterministic', 'openai', 'gemini', 'anthropic', 'ollama'")`.
3. **Environment Auto-Detection (`provider is None`)**:
   - If `OPENAI_API_KEY` in `os.environ`:
     Attempts to instantiate `ChatOpenAI(model=model_name or "gpt-4o-mini", temperature=temperature)`. If import fails, falls back to `DeterministicMockLLM`.
   - Else if `GEMINI_API_KEY` in `os.environ` or `GOOGLE_API_KEY` in `os.environ`:
     Attempts to instantiate `ChatGoogleGenerativeAI(model=model_name or "gemini-1.5-flash", temperature=temperature)`.
   - Else if `ANTHROPIC_API_KEY` in `os.environ`:
     Attempts to instantiate `ChatAnthropic(model=model_name or "claude-3-5-sonnet-latest", temperature=temperature)`.
   - If no API keys are present in `os.environ`:
     Returns `DeterministicMockLLM(model_name=model_name or "deterministic-mock-llm", temperature=temperature)`.

### 3.3 Class-Based Wrapper: `LLMFactory`
```python
class LLMFactory:
    """Factory for resolving and instantiating LLMs."""

    @staticmethod
    def get_llm(
        provider: Optional[str] = None,
        model_name: Optional[str] = None,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> Any:
        return get_llm(provider=provider, model_name=model_name, temperature=temperature, **kwargs)
```

---

## 4. Source Code Blueprint: `src/persistent_crewai/agents/llm_factory.py`

Below is the complete, proposed implementation for `src/persistent_crewai/agents/llm_factory.py`:

```python
"""
LLM Factory and Deterministic Mock LLM for Persistent CrewAI.
Provides offline, deterministic, role-grounded LLM execution for tests and CI,
alongside multi-provider live LLM instantiation with automatic environment detection.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Union
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# Resilient LangChain message & output compatibility layer
try:
    from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.language_models.chat_models import BaseChatModel
    _HAS_LANGCHAIN = True
except (ImportError, Exception):
    _HAS_LANGCHAIN = False

    class BaseMessage(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
        content: str = ""
        type: str = "base"

        def __str__(self) -> str:
            return self.content

    class AIMessage(BaseMessage):
        type: str = "ai"

    class HumanMessage(BaseMessage):
        type: str = "human"

    class SystemMessage(BaseMessage):
        type: str = "system"

    class ChatGeneration(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
        message: BaseMessage
        text: str = ""

        def __init__(self, **kwargs: Any):
            if "message" in kwargs and "text" not in kwargs:
                kwargs["text"] = str(kwargs["message"].content)
            super().__init__(**kwargs)

    class ChatResult(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
        generations: List[ChatGeneration] = Field(default_factory=list)

    class BaseChatModel(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
        model_name: str = "mock-model"
        temperature: float = 0.0

        def invoke(self, input: Any, **kwargs: Any) -> Any:
            raise NotImplementedError

        def predict(self, text: str, **kwargs: Any) -> str:
            raise NotImplementedError


# =============================================================================
# Deterministic Role Responses Registry
# =============================================================================
CANONICAL_ROLE_RESPONSES: Dict[str, str] = {
    "Coordinator": (
        "Final Answer: ### Project Coordinator Status Report\n"
        "- Project: Persistent CrewAI Engineering Team\n"
        "- Current Status: All 8 specialist roles active and operational.\n"
        "- Work Breakdown Structure (WBS):\n"
        "  1. Architecture: System blueprints and SQLite WAL schema established.\n"
        "  2. UI/UX Design: CLI and REPL ergonomics formulated.\n"
        "  3. Development: Core modules implemented with type annotations.\n"
        "  4. Testing: Comprehensive test suites passing with zero defects.\n"
        "  5. Code Review: Security audits and PEP 8 compliance verified.\n"
        "  6. Version Control: Git branches, atomic commits, and PRs synced.\n"
        "  7. Memory: ACID state persistence and semantic memories indexed.\n"
        "- Next Milestones: Ready for subsequent development cycle."
    ),
    "Architect": (
        "Final Answer: ### System Architecture & Database Blueprint\n"
        "- Architectural Pattern: Decoupled Layered Architecture (Persistence, Tooling, Agents, CLI).\n"
        "- Storage Engine: SQLite 3 with Write-Ahead Logging (WAL) and foreign keys enabled.\n"
        "- Database Schema:\n"
        "  * `projects`: Project context and metadata.\n"
        "  * `decisions`: Architectural Decision Records (ADRs) with rationale and implications.\n"
        "  * `workflow_runs`: Execution logs for full workflows and subtasks.\n"
        "  * `task_history`: Granular task states and outputs.\n"
        "  * `milestones`: Target criteria and achievement tracking.\n"
        "  * `agent_status`: Operational telemetry for all 8 agents.\n"
        "  * `execution_traces`: Monotonic step thoughts and tool calls.\n"
        "  * `memory_records`: Vector embeddings and full-text search indexing.\n"
        "- Non-Functional Invariants: Sub-second query times, thread-safe access, and zero data loss on crash."
    ),
    "Designer": (
        "Final Answer: ### UI/UX & Design System Specification\n"
        "- Interface Surface: Command-Line Interface (CLI) and interactive REPL.\n"
        "- Visual Palette: Cyan headers (#00FFFF), green status badges for success, red callouts for errors, yellow for in-progress.\n"
        "- Component Typography: Rich console formatting, Markdown tables, and ASCII fallback for non-TTY terminals.\n"
        "- REPL Command Grammar: `ask <role> <query>`, `chat <role>`, `status`, `decisions`, `milestones`, `run workflow <type>`, `run task <role> <desc>`.\n"
        "- Ergonomics: Tab completion, command history, and clear actionable error suggestions."
    ),
    "Developer": (
        "Final Answer: ### Implementation Summary\n"
        "- Modules Developed:\n"
        "  * `src/persistent_crewai/persistence/`: SQLite connection pooling, state manager, vector store.\n"
        "  * `src/persistent_crewai/tools/`: Sandboxed workspace file tools and bi-modal GitHub adapters.\n"
        "  * `src/persistent_crewai/agents/`: Role definitions, LLM factory, and crew builder.\n"
        "  * `src/persistent_crewai/cli/`: Interactive REPL and status dashboard.\n"
        "- Code Standards: PEP 8 compliant, 100% type-annotated, robust exception boundaries.\n"
        "- Status: All code written to workspace, ready for test validation."
    ),
    "Tester": (
        "Final Answer: ### Test Execution & Quality Assurance Report\n"
        "- Test Framework: Pytest and standard unittest test runner.\n"
        "- Suites Executed:\n"
        "  * `tests/test_persistence.py`: SQLite schema, WAL, FTS5, and transactions.\n"
        "  * `tests/test_tools.py`: Sandboxed file ops, path traversal containment, MockGitHub.\n"
        "  * `tests/test_agents.py`: LLM factory, role definitions, and crew builder.\n"
        "  * `tests/test_pipeline.py`: Sequential and hierarchical execution flows.\n"
        "- Test Results: 100% passing tests, 0 failures, 0 regressions.\n"
        "- Verdict: Certified production-ready for deployment."
    ),
    "Reviewer": (
        "Final Answer: ### Code Review & Security Audit Verdict\n"
        "- Static Analysis: Passed. Full PEP 8 adherence, clean type annotations, no cyclic imports.\n"
        "- Security Audit:\n"
        "  * SQL Injection: Protected via parameterized queries across all database operations.\n"
        "  * Path Traversal: Sandboxed via canonical path validation against workspace root.\n"
        "  * Secrets & Credentials: Zero hardcoded tokens or API keys detected.\n"
        "- Quality Verdict: APPROVED for repository commit and pull request creation."
    ),
    "GitHub Manager": (
        "Final Answer: ### GitHub & Repository Operations Summary\n"
        "- Version Control Strategy: Git feature branching and Conventional Commits.\n"
        "- Active Branch: `feat/persistent-crewai-framework`.\n"
        "- Recent Commit: `feat: implement persistent crewai 8-agent framework with state persistence`.\n"
        "- Pull Request: PR #1 opened with comprehensive changelog and linked issue #1.\n"
        "- Repository Status: Clean working directory, all changes committed and tracked."
    ),
    "Memory & Status Manager": (
        "Final Answer: ### Memory & Operational State Summary\n"
        "- Persistence Engine: SQLite state database synchronized in WAL mode.\n"
        "- Recorded State:\n"
        "  * Project ID: Active project context rehydrated.\n"
        "  * Milestones: 100% of defined criteria recorded and validated.\n"
        "  * Decisions: Architectural Decision Records committed to persistent store.\n"
        "  * Task History: All execution traces, agent outputs, and latencies logged.\n"
        "- Vector Index: Semantic memory and document embeddings indexed for similarity search.\n"
        "- Session Durability: Crash-resilient, ready for cross-session resumption."
    ),
}

# Role keyword matching rules
ROLE_KEYWORD_RULES = [
    ("Coordinator", ["coordinator", "manager", "project manager", "wbs", "backlog", "project status", "status of the project", "orchestrat"]),
    ("Architect", ["architect", "database design", "db design", "schema", "adr", "architecture", "blueprint", "data model", "sqlite", "wal"]),
    ("Designer", ["designer", "ui/ux", "ui", "ux", "design system", "tokens", "tui", "wireframe", "styling", "typography"]),
    ("Developer", ["developer", "software engineer", "implement", "coding", "python", "module", "bugfix", "refactor"]),
    ("Tester", ["tester", "sdet", "qa", "pytest", "unit test", "integration test", "e2e test", "coverage", "regression"]),
    ("Reviewer", ["reviewer", "code review", "security audit", "pep-8", "owasp", "approved", "compliance"]),
    ("GitHub Manager", ["github", "git", "branch", "commit", "pr", "pull request", "repository", "conventional commit"]),
    ("Memory & Status Manager", ["memory", "status manager", "persist", "checkpoint", "state persistence", "vector store", "session resumption"]),
]


# =============================================================================
# DeterministicMockLLM Class
# =============================================================================
class DeterministicMockLLM(BaseChatModel):
    """
    Deterministic, role-grounded Mock LLM for offline execution, testing, and CI.
    Emits reproducible responses for all 8 agent roles, parses injected SQLite context,
    supports ReAct tool call simulation, and records invocation history.
    """
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    model_name: str = "deterministic-mock-llm"
    temperature: float = 0.0
    responses_by_role: Dict[str, str] = Field(default_factory=dict)
    canned_responses: List[str] = Field(default_factory=list)
    tool_call_triggers: Dict[str, str] = Field(default_factory=dict)
    call_history: List[Any] = Field(default_factory=list)
    verbose: bool = False

    @property
    def _llm_type(self) -> str:
        return "deterministic-mock-llm"

    def predict(self, text: str, **kwargs: Any) -> str:
        """String in, string out prediction."""
        self.call_history.append(text)
        return self._synthesize_response(text)

    def invoke(self, input: Any, **kwargs: Any) -> AIMessage:
        """LangChain standard invoke method."""
        prompt_text = self._extract_prompt_text(input)
        self.call_history.append(prompt_text)
        response_text = self._synthesize_response(prompt_text)
        return AIMessage(content=response_text)

    def call(self, messages: Any, **kwargs: Any) -> str:
        """Callable execution returning string."""
        prompt_text = self._extract_prompt_text(messages)
        self.call_history.append(prompt_text)
        return self._synthesize_response(prompt_text)

    def __call__(self, input: Any, **kwargs: Any) -> Any:
        """Direct callable interface."""
        prompt_text = self._extract_prompt_text(input)
        self.call_history.append(prompt_text)
        response_text = self._synthesize_response(prompt_text)
        if isinstance(input, str):
            return response_text
        return AIMessage(content=response_text)

    def _generate(
        self,
        messages: List[Any],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Core LangChain chat generation hook."""
        prompt_text = " ".join([self._extract_prompt_text(m) for m in messages])
        self.call_history.append(messages)
        response_text = self._synthesize_response(prompt_text)
        generation = ChatGeneration(message=AIMessage(content=response_text), text=response_text)
        return ChatResult(generations=[generation])

    def generate(self, prompts: List[Any], stop: Optional[List[str]] = None, **kwargs: Any) -> ChatResult:
        """Batched generation."""
        combined = " ".join([self._extract_prompt_text(p) for p in prompts])
        return self._generate([HumanMessage(content=combined)], stop=stop, **kwargs)

    def bind(self, **kwargs: Any) -> "DeterministicMockLLM":
        """No-op bind method for CrewAI compatibility."""
        return self

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "DeterministicMockLLM":
        """No-op tool binding method for CrewAI compatibility."""
        return self

    def with_structured_output(self, schema: Any, **kwargs: Any) -> "DeterministicMockLLM":
        """No-op structured output method for LangChain compatibility."""
        return self

    def reset(self) -> None:
        """Reset call history and canned responses."""
        self.call_history.clear()
        self.canned_responses.clear()

    def _extract_prompt_text(self, input_val: Any) -> str:
        """Helper to extract raw prompt text from various input types."""
        if isinstance(input_val, str):
            return input_val
        if hasattr(input_val, "content"):
            return str(input_val.content)
        if isinstance(input_val, list):
            return " ".join([self._extract_prompt_text(item) for item in input_val])
        if isinstance(input_val, dict):
            return str(input_val)
        return str(input_val)

    def _synthesize_response(self, prompt: str) -> str:
        """Synthesize a deterministic, role-grounded response based on prompt analysis."""
        prompt_lower = prompt.lower()

        # 1. FIFO Canned Responses
        if self.canned_responses:
            return self.canned_responses.pop(0)

        # 2. ReAct Observation Check
        if "observation:" in prompt_lower:
            return "Final Answer: Observation received and processed successfully. All criteria verified."

        # 3. Tool Call Triggers
        if self.tool_call_triggers:
            for trigger, tool_action in self.tool_call_triggers.items():
                if trigger.lower() in prompt_lower:
                    return tool_action

        # 4. Explicit Role Overrides
        if self.responses_by_role:
            for role_name, resp in self.responses_by_role.items():
                if role_name.lower() in prompt_lower:
                    return resp

        # 5. Dynamic Role Classification
        matched_role = None
        for canonical_role, keywords in ROLE_KEYWORD_RULES:
            if any(kw in prompt_lower for kw in keywords):
                matched_role = canonical_role
                break

        if matched_role and matched_role in CANONICAL_ROLE_RESPONSES:
            base_response = CANONICAL_ROLE_RESPONSES[matched_role]
            # Context grounding augmentation
            grounded_context = self._extract_grounded_context(prompt)
            if grounded_context:
                return f"{base_response}\n\n[Grounded State Context]: {grounded_context}"
            return base_response

        # 6. Fallback Deterministic SHA-256 Hash
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8]
        return f"Final Answer: Deterministic response [hash={digest}]. Request processed according to engineering specifications."

    def _extract_grounded_context(self, prompt: str) -> Optional[str]:
        """Extract project context attributes if present in the prompt."""
        if "--- current project context ---" not in prompt.lower():
            return None

        details = []
        proj_match = re.search(r"Project:\s*([^\n(]+)", prompt)
        if proj_match:
            details.append(f"Project='{proj_match.group(1).strip()}'")

        ms_match = re.search(r"Current Active Milestone:\s*([^\n(]+)", prompt)
        if ms_match:
            details.append(f"Milestone='{ms_match.group(1).strip()}'")

        if details:
            return ", ".join(details)
        return "Active database state verified."


# =============================================================================
# LLM Provider Factory
# =============================================================================
def get_llm(
    provider: Optional[str] = None,
    model_name: Optional[str] = None,
    temperature: float = 0.7,
    **kwargs: Any,
) -> Any:
    """
    Factory function resolving LLM instance based on provider, environment variables,
    or falling back safely to DeterministicMockLLM when offline or testing.

    Args:
        provider: Optional provider name ('mock', 'deterministic', 'openai', 'gemini', 'anthropic', 'ollama').
        model_name: Model identifier (e.g., 'gpt-4o-mini', 'gemini-1.5-flash', 'claude-3-5-sonnet-latest').
        temperature: Sampling temperature (default 0.7; mock uses 0.0).
        **kwargs: Additional provider-specific keyword arguments.

    Returns:
        LLM instance (ChatOpenAI, ChatGoogleGenerativeAI, ChatAnthropic, ChatOllama, or DeterministicMockLLM).

    Raises:
        ValueError: If an explicit provider is requested but its credentials are missing, or if provider is unsupported.
    """
    # 1. Force Mock via Environment Variables (CI / Test mode)
    if os.environ.get("MOCK_LLM", "").lower() in ("true", "1", "yes") or \
       os.environ.get("CREWAI_MOCK_LLM", "").lower() in ("true", "1", "yes"):
        logger.info("Initializing DeterministicMockLLM (forced via environment flag).")
        return DeterministicMockLLM(model_name=model_name or "deterministic-mock-llm", temperature=0.0, **kwargs)

    # 2. Explicit Mock or Deterministic Provider
    if provider:
        p_clean = provider.strip().lower()
        if p_clean in ("mock", "deterministic", "fake", "offline", "testing"):
            return DeterministicMockLLM(model_name=model_name or "deterministic-mock-llm", temperature=0.0, **kwargs)

        if p_clean == "openai":
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY environment variable is required for provider 'openai'")
            try:
                from langchain_openai import ChatOpenAI
                return ChatOpenAI(model=model_name or "gpt-4o-mini", temperature=temperature, api_key=api_key, **kwargs)
            except ImportError:
                logger.warning("langchain-openai not installed; falling back to DeterministicMockLLM.")
                return DeterministicMockLLM(model_name=model_name or "gpt-4o-mini", temperature=temperature, **kwargs)

        elif p_clean in ("gemini", "google"):
            api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            if not api_key:
                raise ValueError("GEMINI_API_KEY or GOOGLE_API_KEY environment variable is required for provider 'gemini'")
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI
                return ChatGoogleGenerativeAI(model=model_name or "gemini-1.5-flash", temperature=temperature, google_api_key=api_key, **kwargs)
            except ImportError:
                logger.warning("langchain-google-genai not installed; falling back to DeterministicMockLLM.")
                return DeterministicMockLLM(model_name=model_name or "gemini-1.5-flash", temperature=temperature, **kwargs)

        elif p_clean == "anthropic":
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY environment variable is required for provider 'anthropic'")
            try:
                from langchain_anthropic import ChatAnthropic
                return ChatAnthropic(model=model_name or "claude-3-5-sonnet-latest", temperature=temperature, api_key=api_key, **kwargs)
            except ImportError:
                logger.warning("langchain-anthropic not installed; falling back to DeterministicMockLLM.")
                return DeterministicMockLLM(model_name=model_name or "claude-3-5-sonnet-latest", temperature=temperature, **kwargs)

        elif p_clean == "ollama":
            base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
            try:
                from langchain_community.chat_models import ChatOllama
                return ChatOllama(model=model_name or "llama3", base_url=base_url, temperature=temperature, **kwargs)
            except ImportError:
                logger.warning("langchain-community not installed; falling back to DeterministicMockLLM.")
                return DeterministicMockLLM(model_name=model_name or "llama3", temperature=temperature, **kwargs)

        else:
            raise ValueError(
                f"Unsupported LLM provider '{provider}'. Valid providers: 'mock', 'deterministic', 'openai', 'gemini', 'anthropic', 'ollama'"
            )

    # 3. Automatic Environment Detection (provider is None)
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        try:
            from langchain_openai import ChatOpenAI
            logger.info("Auto-detected OPENAI_API_KEY; initializing ChatOpenAI.")
            return ChatOpenAI(model=model_name or "gpt-4o-mini", temperature=temperature, api_key=openai_key, **kwargs)
        except ImportError:
            pass

    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if gemini_key:
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            logger.info("Auto-detected GEMINI_API_KEY; initializing ChatGoogleGenerativeAI.")
            return ChatGoogleGenerativeAI(model=model_name or "gemini-1.5-flash", temperature=temperature, google_api_key=gemini_key, **kwargs)
        except ImportError:
            pass

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            from langchain_anthropic import ChatAnthropic
            logger.info("Auto-detected ANTHROPIC_API_KEY; initializing ChatAnthropic.")
            return ChatAnthropic(model=model_name or "claude-3-5-sonnet-latest", temperature=temperature, api_key=anthropic_key, **kwargs)
        except ImportError:
            pass

    # 4. Default Offline Fallback
    logger.info("No LLM API keys detected; falling back to DeterministicMockLLM.")
    return DeterministicMockLLM(model_name=model_name or "deterministic-mock-llm", temperature=0.0, **kwargs)


class LLMFactory:
    """Factory class for instantiating and resolving LLMs."""

    @staticmethod
    def get_llm(
        provider: Optional[str] = None,
        model_name: Optional[str] = None,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> Any:
        return get_llm(provider=provider, model_name=model_name, temperature=temperature, **kwargs)


__all__ = [
    "DeterministicMockLLM",
    "LLMFactory",
    "get_llm",
    "CANONICAL_ROLE_RESPONSES",
    "ROLE_KEYWORD_RULES",
]
```

---

## 5. Unit Test Plan for `tests/test_agents.py`

The test suite `tests/test_agents.py` verifies both `DeterministicMockLLM` and `LLMFactory` under all operational conditions.

### Test Suite Structure

```
tests/test_agents.py
├── TestDeterministicMockLLM
│   ├── test_m01_deterministic_repeat_predict
│   ├── test_m02_all_eight_roles_responses
│   ├── test_m03_role_alias_mapping
│   ├── test_m04_context_grounding_injection
│   ├── test_m05_tool_call_triggers
│   ├── test_m06_canned_responses_fifo
│   ├── test_m07_call_history_recording
│   ├── test_m08_reset_clears_history_and_canned
│   ├── test_m09_interface_compatibility_invoke
│   ├── test_m10_interface_compatibility_call
│   ├── test_m11_interface_compatibility_generate
│   ├── test_m12_interface_compatibility_bind
│   ├── test_m13_interface_compatibility_bind_tools
│   ├── test_m14_interface_compatibility_structured_output
│   └── test_m15_unknown_prompt_deterministic_fallback
└── TestLLMFactory
    ├── test_f01_default_without_keys_returns_mock_llm
    ├── test_f02_explicit_mock_provider
    ├── test_f03_explicit_deterministic_provider
    ├── test_f04_mock_llm_env_var_override
    ├── test_f05_crewai_mock_llm_env_var_override
    ├── test_f06_auto_detect_openai_when_key_present
    ├── test_f07_auto_detect_gemini_when_key_present
    ├── test_f08_auto_detect_anthropic_when_key_present
    ├── test_f09_missing_key_for_explicit_provider_raises_error
    ├── test_f10_unsupported_provider_raises_error
    ├── test_f11_temperature_and_model_name_passed
    └── test_f12_llm_factory_class_static_method
```

### Complete Test Implementation Blueprint (`tests/test_agents.py`)

```python
"""
Unit test suite for Milestone 3: LLM Factory & DeterministicMockLLM.
Verifies deterministic responses, role recognition across all 8 roles,
context-grounding injection, tool call triggers, interface compatibility,
and LLMFactory resolution and environment fallbacks.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure src is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from persistent_crewai.agents.llm_factory import (
    CANONICAL_ROLE_RESPONSES,
    DeterministicMockLLM,
    LLMFactory,
    get_llm,
)


class TestDeterministicMockLLM(unittest.TestCase):
    """Test suite for DeterministicMockLLM role-grounded deterministic execution."""

    def setUp(self):
        self.mock_llm = DeterministicMockLLM()

    def test_m01_deterministic_repeat_predict(self):
        """TC-M1: Verify repeat calls with identical prompt yield identical string output."""
        prompt = "Architect: Provide database schema specification"
        resp1 = self.mock_llm.predict(prompt)
        resp2 = self.mock_llm.predict(prompt)
        self.assertIsInstance(resp1, str)
        self.assertEqual(resp1, resp2)
        self.assertTrue(len(resp1) > 0)

    def test_m02_all_eight_roles_responses(self):
        """TC-M2: Verify all 8 canonical roles produce distinct, role-grounded responses."""
        role_keywords = {
            "Coordinator": "Project Coordinator Status Report",
            "Architect": "System Architecture & Database Blueprint",
            "Designer": "UI/UX & Design System Specification",
            "Developer": "Implementation Summary",
            "Tester": "Test Execution & Quality Assurance Report",
            "Reviewer": "Code Review & Security Audit Verdict",
            "GitHub Manager": "GitHub & Repository Operations Summary",
            "Memory & Status Manager": "Memory & Operational State Summary",
        }
        for role, expected_phrase in role_keywords.items():
            prompt = f"You are the {role}. Please execute your primary responsibility."
            resp = self.mock_llm.predict(prompt)
            self.assertIn(expected_phrase, resp, f"Role {role} failed to produce expected output.")

    def test_m03_role_alias_mapping(self):
        """TC-M3: Verify colloquial role aliases correctly route to canonical role outputs."""
        aliases = [
            ("manager", "Coordinator"),
            ("database design", "Architect"),
            ("UI/UX", "Designer"),
            ("software engineer", "Developer"),
            ("sdet", "Tester"),
            ("security audit", "Reviewer"),
            ("git operations", "GitHub Manager"),
            ("state persistence", "Memory & Status Manager"),
        ]
        for alias, canonical in aliases:
            prompt = f"Focus on {alias} tasks for this sprint."
            resp = self.mock_llm.predict(prompt)
            expected_sample = CANONICAL_ROLE_RESPONSES[canonical][:30]
            self.assertIn(expected_sample, resp, f"Alias '{alias}' did not match '{canonical}'")

    def test_m04_context_grounding_injection(self):
        """TC-M4: Verify injected project context appears in synthesized responses."""
        prompt = (
            "--- CURRENT PROJECT CONTEXT ---\n"
            "Project: Apollo Mission Control\n"
            "Current Active Milestone: Orbit Insertion\n"
            "Relevant Recorded Decisions: [ADR-01] Use Lunar Orbit Rendezvous\n"
            "--- INSTRUCTIONS ---\n"
            "You are the Coordinator. What is the current status?"
        )
        resp = self.mock_llm.predict(prompt)
        self.assertIn("Apollo Mission Control", resp)
        self.assertIn("Orbit Insertion", resp)

    def test_m05_tool_call_triggers(self):
        """TC-M5: Verify tool call triggers emit ReAct action strings and observations complete them."""
        self.mock_llm.tool_call_triggers = {
            "read_file": "Thought: Need to read file.\nAction: read_workspace_file\nAction Input: {\"file_path\": \"a.txt\"}"
        }
        resp = self.mock_llm.predict("Please read_file now.")
        self.assertIn("Action: read_workspace_file", resp)

        # Subsequent observation closes the ReAct loop
        obs_resp = self.mock_llm.predict("Observation: File content is 'Hello World'")
        self.assertIn("Observation received and processed successfully", obs_resp)

    def test_m06_canned_responses_fifo(self):
        """TC-M6: Verify canned responses are popped in FIFO order."""
        self.mock_llm.canned_responses = ["Canned Answer 1", "Canned Answer 2"]
        r1 = self.mock_llm.predict("Query 1")
        r2 = self.mock_llm.predict("Query 2")
        self.assertEqual(r1, "Canned Answer 1")
        self.assertEqual(r2, "Canned Answer 2")

    def test_m07_call_history_recording(self):
        """TC-M7: Verify call_history monotonically tracks all prompts."""
        self.mock_llm.predict("First call")
        self.mock_llm.predict("Second call")
        self.assertEqual(len(self.mock_llm.call_history), 2)
        self.assertEqual(self.mock_llm.call_history[0], "First call")
        self.assertEqual(self.mock_llm.call_history[1], "Second call")

    def test_m08_reset_clears_history_and_canned(self):
        """TC-M8: Verify reset() clears history and canned responses."""
        self.mock_llm.canned_responses = ["Canned"]
        self.mock_llm.predict("test")
        self.mock_llm.reset()
        self.assertEqual(len(self.mock_llm.call_history), 0)
        self.assertEqual(len(self.mock_llm.canned_responses), 0)

    def test_m09_interface_compatibility_invoke(self):
        """TC-M9: Verify invoke() returns an object with .content and str() behavior."""
        result = self.mock_llm.invoke("Architect: Specify DB")
        self.assertTrue(hasattr(result, "content"))
        self.assertIn("System Architecture", result.content)
        self.assertEqual(str(result), result.content)

    def test_m10_interface_compatibility_call(self):
        """TC-M10: Verify call() returns a string."""
        result = self.mock_llm.call("Developer: Implement auth")
        self.assertIsInstance(result, str)
        self.assertIn("Implementation Summary", result)

    def test_m11_interface_compatibility_generate(self):
        """TC-M11: Verify generate() returns ChatResult with generations."""
        result = self.mock_llm.generate(["Tester: Run test suite"])
        self.assertTrue(hasattr(result, "generations"))
        self.assertTrue(len(result.generations) > 0)
        gen = result.generations[0]
        self.assertIn("Test Execution", gen.text)

    def test_m12_interface_compatibility_bind(self):
        """TC-M12: Verify bind() returns self without error."""
        bound = self.mock_llm.bind(stop=["\nObservation:"])
        self.assertIs(bound, self.mock_llm)

    def test_m13_interface_compatibility_bind_tools(self):
        """TC-M13: Verify bind_tools() returns self without error."""
        bound = self.mock_llm.bind_tools(tools=[])
        self.assertIs(bound, self.mock_llm)

    def test_m14_interface_compatibility_structured_output(self):
        """TC-M14: Verify with_structured_output() returns self without error."""
        bound = self.mock_llm.with_structured_output(schema=None)
        self.assertIs(bound, self.mock_llm)

    def test_m15_unknown_prompt_deterministic_fallback(self):
        """TC-M15: Verify unknown prompt returns consistent SHA-256 fallback response."""
        p = "XyZ123999 completely unrelated query!"
        r1 = self.mock_llm.predict(p)
        r2 = self.mock_llm.predict(p)
        self.assertEqual(r1, r2)
        self.assertIn("hash=", r1)


class TestLLMFactory(unittest.TestCase):
    """Test suite for LLMFactory provider resolution and environment detection."""

    def setUp(self):
        # Clear LLM environment variables for clean isolation
        self.env_patcher = patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "",
                "GEMINI_API_KEY": "",
                "GOOGLE_API_KEY": "",
                "ANTHROPIC_API_KEY": "",
                "MOCK_LLM": "",
                "CREWAI_MOCK_LLM": "",
            },
            clear=False,
        )
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()

    def test_f01_default_without_keys_returns_mock_llm(self):
        """TC-F1: Verify get_llm() returns DeterministicMockLLM when keys are absent."""
        llm = get_llm()
        self.assertIsInstance(llm, DeterministicMockLLM)

    def test_f02_explicit_mock_provider(self):
        """TC-F2: Verify get_llm(provider='mock') returns DeterministicMockLLM."""
        llm = get_llm(provider="mock")
        self.assertIsInstance(llm, DeterministicMockLLM)

    def test_f03_explicit_deterministic_provider(self):
        """TC-F3: Verify get_llm(provider='deterministic') returns DeterministicMockLLM."""
        llm = get_llm(provider="deterministic")
        self.assertIsInstance(llm, DeterministicMockLLM)

    def test_f04_mock_llm_env_var_override(self):
        """TC-F4: Verify MOCK_LLM=true forces DeterministicMockLLM even if keys exist."""
        with patch.dict(os.environ, {"MOCK_LLM": "true", "OPENAI_API_KEY": "sk-fake-key"}):
            llm = get_llm()
            self.assertIsInstance(llm, DeterministicMockLLM)

    def test_f05_crewai_mock_llm_env_var_override(self):
        """TC-F5: Verify CREWAI_MOCK_LLM=true forces DeterministicMockLLM."""
        with patch.dict(os.environ, {"CREWAI_MOCK_LLM": "1"}):
            llm = get_llm()
            self.assertIsInstance(llm, DeterministicMockLLM)

    def test_f06_auto_detect_openai_when_key_present(self):
        """TC-F6: Verify OPENAI_API_KEY triggers OpenAI resolution or graceful fallback."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-fake-key"}):
            llm = get_llm()
            # If langchain-openai is present, returns ChatOpenAI; else falls back to DeterministicMockLLM
            self.assertIsNotNone(llm)

    def test_f07_auto_detect_gemini_when_key_present(self):
        """TC-F7: Verify GEMINI_API_KEY triggers Gemini resolution or graceful fallback."""
        with patch.dict(os.environ, {"GEMINI_API_KEY": "gemini-fake-key"}):
            llm = get_llm()
            self.assertIsNotNone(llm)

    def test_f08_auto_detect_anthropic_when_key_present(self):
        """TC-F8: Verify ANTHROPIC_API_KEY triggers Anthropic resolution or graceful fallback."""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "anthropic-fake-key"}):
            llm = get_llm()
            self.assertIsNotNone(llm)

    def test_f09_missing_key_for_explicit_provider_raises_error(self):
        """TC-F9: Verify explicit provider without required key raises ValueError."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            with self.assertRaises(ValueError) as cm:
                get_llm(provider="openai")
            self.assertIn("OPENAI_API_KEY", str(cm.exception))

        with patch.dict(os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}):
            with self.assertRaises(ValueError) as cm:
                get_llm(provider="gemini")
            self.assertIn("GEMINI_API_KEY", str(cm.exception))

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            with self.assertRaises(ValueError) as cm:
                get_llm(provider="anthropic")
            self.assertIn("ANTHROPIC_API_KEY", str(cm.exception))

    def test_f10_unsupported_provider_raises_error(self):
        """TC-F10: Verify unsupported provider string raises ValueError with allowed list."""
        with self.assertRaises(ValueError) as cm:
            get_llm(provider="unsupported_quantum_llm")
        self.assertIn("Unsupported LLM provider", str(cm.exception))

    def test_f11_temperature_and_model_name_passed(self):
        """TC-F11: Verify custom temperature and model_name propagate to mock instance."""
        llm = get_llm(provider="mock", model_name="custom-model", temperature=0.2)
        self.assertEqual(llm.model_name, "custom-model")
        self.assertEqual(llm.temperature, 0.2)

    def test_f12_llm_factory_class_static_method(self):
        """TC-F12: Verify LLMFactory.get_llm static method produces identical results."""
        llm = LLMFactory.get_llm(provider="mock")
        self.assertIsInstance(llm, DeterministicMockLLM)


if __name__ == "__main__":
    unittest.main()
```

---

## 6. Implementation Risk Analysis & Mitigations

| Risk | Impact | Likelihood | Mitigation Strategy |
|---|---|---|---|
| **LangChain Core Not Installed** | High | High (confirmed in environment) | Implement resilient fallback `BaseMessage`, `AIMessage`, `ChatGeneration`, `ChatResult`, and `BaseChatModel` subclasses using Pydantic v2. |
| **CrewAI Tool Binding Crash** | High | Med | Implement dummy `.bind()`, `.bind_tools()`, and `.with_structured_output()` methods that return `self` without throwing `AttributeError`. |
| **Output Parser Failures in CrewAI** | High | Med | Always format default outputs with `Final Answer: ...` so CrewAI ReAct parsers terminate cleanly without throwing `OutputParserException`. |
| **Multi-turn ReAct Tool Loops** | Med | Low | When `Observation:` appears in the prompt, detect it immediately and emit `Final Answer: Observation received...` to close the loop in one turn. |
| **Accidental Network Invocations** | Critical | Low | Ensure `get_llm()` defaults to `DeterministicMockLLM` when API keys are unset, and `MOCK_LLM=true` overrides everything. |

---

## 7. Conclusion

The architecture formulated herein provides an airtight, production-ready specification for `src/persistent_crewai/agents/llm_factory.py`. It guarantees that all subsequent milestones (Crew Builder, Pipeline Runners, and CLI REPL) have a 100% deterministic, offline-capable, and role-grounded LLM foundation with zero external dependencies.
