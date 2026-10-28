"""
Configuration, path resolution, and global constants for Persistent CrewAI.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# Attempt to load .env if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Current Schema Version
CURRENT_SCHEMA_VERSION = "1.0.0"

# Default persistence directory and file names
DEFAULT_PERSISTENCE_DIR_NAME = ".persistent_crewai"
DEFAULT_DB_NAME = "state.db"
DEFAULT_MOCK_GITHUB_NAME = "mock_github.json"

# Integrity modes
VALID_INTEGRITY_MODES = ("development", "strict", "production")
DEFAULT_INTEGRITY_MODE = "development"

# 8 Canonical agent roles
CANONICAL_ROLES: list[str] = [
    "Coordinator",
    "Architect",
    "Designer",
    "Developer",
    "Tester",
    "Reviewer",
    "GitHub Manager",
    "Memory & Status Manager",
]

# Canonical role normalization map
ROLE_ALIASES: dict[str, str] = {
    "coordinator": "Coordinator",
    "manager": "Coordinator",
    "coordinator / manager": "Coordinator",
    "coordinator/manager": "Coordinator",
    "architect": "Architect",
    "designer": "Designer",
    "developer": "Developer",
    "tester": "Tester",
    "reviewer": "Reviewer",
    "github": "GitHub Manager",
    "github manager": "GitHub Manager",
    "github_manager": "GitHub Manager",
    "memory": "Memory & Status Manager",
    "memory manager": "Memory & Status Manager",
    "memory_manager": "Memory & Status Manager",
    "memory & status manager": "Memory & Status Manager",
}


def normalize_role(role: str) -> str:
    """Normalize agent role name to canonical casing and title.
    
    Raises:
        KeyError: If the role is unrecognized.
    """
    cleaned = role.strip().lower()
    if cleaned in ROLE_ALIASES:
        return ROLE_ALIASES[cleaned]
    # Check if exact title case matches one of canonical roles
    for canonical in CANONICAL_ROLES:
        if canonical.lower() == cleaned:
            return canonical
    raise KeyError(f"Unknown agent role '{role}'. Valid roles: {', '.join(CANONICAL_ROLES)}")


def get_workspace_root() -> Path:
    """Resolve the active workspace root directory."""
    env_root = os.environ.get("CREWAI_WORKSPACE_ROOT")
    if env_root:
        return Path(env_root).resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".persistent_crewai").exists() or parent.name == "persistent_crewai_team":
            return parent
    return Path.cwd().resolve()


def get_default_db_path(workspace_root: Optional[Path | str] = None) -> Path:
    """Resolve the default SQLite state database path."""
    env_db = os.environ.get("CREWAI_STATE_DB_PATH")
    if env_db:
        return Path(env_db).resolve()
    root = Path(workspace_root).resolve() if workspace_root else get_workspace_root()
    return root / DEFAULT_PERSISTENCE_DIR_NAME / DEFAULT_DB_NAME


def get_default_mock_github_path(workspace_root: Optional[Path | str] = None) -> Path:
    """Resolve the default mock GitHub JSON state path."""
    env_gh = os.environ.get("CREWAI_MOCK_GITHUB_PATH")
    if env_gh:
        return Path(env_gh).resolve()
    root = Path(workspace_root).resolve() if workspace_root else get_workspace_root()
    return root / DEFAULT_PERSISTENCE_DIR_NAME / DEFAULT_MOCK_GITHUB_NAME


# Model Pricing Rate Cards per 1,000 tokens (input_cost, output_cost) in USD
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # OpenRouter models
    "qwen/qwen-2.5-coder-32b-instruct": (0.0002, 0.0002),
    "anthropic/claude-3.5-sonnet": (0.003, 0.015),
    "deepseek/deepseek-r1": (0.00055, 0.00219),
    "openai/gpt-4o-mini": (0.00015, 0.0006),
    # Google Gemini models
    "gemini-3.5-flash-lite": (0.000075, 0.0003),
    "gemini-2.5-flash": (0.000075, 0.0003),
    "gemini-1.5-flash": (0.000075, 0.0003),
    "gemini-2.5-pro": (0.00125, 0.005),
    # Groq models
    "llama-3.3-70b-versatile": (0.0, 0.0),
    "default": (0.0002, 0.0004),
}


def calculate_token_cost(model_name: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate estimated USD cost based on token counts and model pricing."""
    pricing = MODEL_PRICING.get(model_name.lower())
    if not pricing:
        for k, v in MODEL_PRICING.items():
            if k in model_name.lower() or model_name.lower() in k:
                pricing = v
                break
    if not pricing:
        pricing = MODEL_PRICING["default"]

    in_cost = (prompt_tokens / 1000.0) * pricing[0]
    out_cost = (completion_tokens / 1000.0) * pricing[1]
    return round(in_cost + out_cost, 6)
