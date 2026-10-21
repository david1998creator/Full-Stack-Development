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

# =============================================================================
# Resilient LangChain message & output compatibility layer
# =============================================================================
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
# Deterministic Role Responses Registry (All 8 Canonical Roles)
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

# Role keyword matching rules for dynamic prompt classification
ROLE_KEYWORD_RULES = [
    ("GitHub Manager", ["github manager", "github", "git", "branch", "commit", "pr", "pull request", "repository", "conventional commit"]),
    ("Memory & Status Manager", ["memory & status manager", "memory manager", "status manager", "memory", "persist", "checkpoint", "state persistence", "vector store", "session resumption"]),
    ("Coordinator", ["coordinator", "project manager", "engineering manager", "manager", "wbs", "backlog", "project status", "status of the project", "orchestrat"]),
    ("Architect", ["architect", "database design", "db design", "schema", "adr", "architecture", "blueprint", "data model", "sqlite", "wal"]),
    ("Designer", ["designer", "ui/ux", "ui", "ux", "design system", "tokens", "tui", "wireframe", "styling", "typography"]),
    ("Developer", ["developer", "software engineer", "implement", "coding", "python", "module", "bugfix", "refactor"]),
    ("Tester", ["tester", "sdet", "qa", "pytest", "unit test", "integration test", "e2e test", "coverage", "regression"]),
    ("Reviewer", ["reviewer", "code review", "security audit", "pep-8", "owasp", "approved", "compliance"]),
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
            if any(re.search(r"(?:\b|^)" + re.escape(kw) + r"(?:\b|$)", prompt_lower) for kw in keywords):
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
