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
