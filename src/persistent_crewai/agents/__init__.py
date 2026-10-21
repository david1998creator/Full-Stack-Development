"""
Persistent CrewAI Agents Package.
Exports all 8 specialized role definitions, LLM factory & DeterministicMockLLM,
CrewAI builder engine, and Sequential/Hierarchical pipeline runners.
"""

from __future__ import annotations

from persistent_crewai.agents.crew_builder import (
    Agent,
    Crew,
    CrewOutput,
    Process,
    Task,
    build_agent,
    build_crew,
    build_team,
)
from persistent_crewai.agents.llm_factory import (
    CANONICAL_ROLE_RESPONSES,
    ROLE_KEYWORD_RULES,
    DeterministicMockLLM,
    LLMFactory,
    get_llm,
)
from persistent_crewai.agents.role_definitions import (
    AGENT_ROLES,
    CANONICAL_ROLES,
    ROLE_DEFINITIONS,
    RoleDefinition,
    UnknownRoleError,
    get_all_roles,
    get_role_definition,
    get_role_tool_names,
    list_all_roles,
    list_role_definitions,
    normalize_role,
)
from persistent_crewai.agents.runner import (
    WORKFLOW_STEPS,
    HierarchicalPipelineRunner,
    SequentialPipelineRunner,
    run_project_workflow,
)

__all__ = [
    # LLM Factory & Mocks
    "DeterministicMockLLM",
    "LLMFactory",
    "get_llm",
    "CANONICAL_ROLE_RESPONSES",
    "ROLE_KEYWORD_RULES",
    # Role Definitions
    "RoleDefinition",
    "UnknownRoleError",
    "ROLE_DEFINITIONS",
    "AGENT_ROLES",
    "CANONICAL_ROLES",
    "get_role_definition",
    "list_role_definitions",
    "list_all_roles",
    "get_all_roles",
    "get_role_tool_names",
    "normalize_role",
    # Crew Builder
    "Process",
    "CrewOutput",
    "Task",
    "Agent",
    "Crew",
    "build_agent",
    "build_team",
    "build_crew",
    # Pipeline Runners
    "SequentialPipelineRunner",
    "HierarchicalPipelineRunner",
    "run_project_workflow",
    "WORKFLOW_STEPS",
]
