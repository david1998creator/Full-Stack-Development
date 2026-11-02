"""
Dynamic Skill and Plugin Registry for Persistent CrewAI 10-Role Multi-Agent SDLC.
Provides discovery, metadata interrogation, hot-reloading, and runtime prompt injection
for modular agent skills and physical execution tools.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from persistent_crewai.config import CANONICAL_ROLES, normalize_role
from persistent_crewai.agents.role_definitions import (
    get_role_definition,
)
from persistent_crewai.persistence.state_manager import StateManager

logger = logging.getLogger("persistent_crewai.registry")

ROLE_MODEL_PRESETS: Dict[str, Dict[str, Any]] = {
    "Coordinator": {"preferred_provider": "gemini", "model": "gemini-3.5-flash-lite", "tier": "fast", "temperature": 0.5},
    "Architect": {"preferred_provider": "openrouter", "model": "qwen/qwen-2.5-coder-32b-instruct", "tier": "reasoning", "temperature": 0.3},
    "Designer": {"preferred_provider": "gemini", "model": "gemini-3.5-flash-lite", "tier": "fast", "temperature": 0.7},
    "Database Engineer": {"preferred_provider": "gemini", "model": "gemini-3.5-flash-lite", "tier": "fast", "temperature": 0.3},
    "Developer": {"preferred_provider": "gemini", "model": "gemini-3.5-flash-lite", "tier": "fast", "temperature": 0.4},
    "Tester": {"preferred_provider": "gemini", "model": "gemini-3.5-flash-lite", "tier": "fast", "temperature": 0.2},
    "Reviewer": {"preferred_provider": "openrouter", "model": "qwen/qwen-2.5-coder-32b-instruct", "tier": "reasoning", "temperature": 0.2},
    "Security Auditor": {"preferred_provider": "openrouter", "model": "qwen/qwen-2.5-coder-32b-instruct", "tier": "reasoning", "temperature": 0.2},
    "GitHub Manager": {"preferred_provider": "gemini", "model": "gemini-3.5-flash-lite", "tier": "fast", "temperature": 0.3},
    "Memory & Status Manager": {"preferred_provider": "gemini", "model": "gemini-3.5-flash-lite", "tier": "fast", "temperature": 0.1},
}

BUILTIN_ROLE_SKILLS: Dict[str, List[Dict[str, Any]]] = {
    "Coordinator": [
        {
            "name": "wbs_orchestration",
            "title": "Milestone & WBS Orchestration",
            "description": "Decomposes user requirements into sequential and hierarchical dependency tasks with milestone gates.",
            "instructions": "Decompose projects into discrete, non-overlapping work breakdown structure (WBS) tasks. Enforce dependency ordering before task transitions.",
            "builtin": True,
            "estimated_tokens": 180,
        },
        {
            "name": "budget_guard_governance",
            "title": "Budget Guard & Cost Governance",
            "description": "Calculates token expenditure projections and transitions pipelines into PAUSED_FOR_HUMAN approval.",
            "instructions": "Estimate required token budgets accurately. Never approve financial spend or destructive actions without human verification.",
            "builtin": True,
            "estimated_tokens": 190,
        },
    ],
    "Architect": [
        {
            "name": "adr_blueprints",
            "title": "ADR Formulation & System Blueprints",
            "description": "Generates architectural decision records (ADRs), module boundaries, and decoupled interface contracts.",
            "instructions": "Structure systems using decoupled hexagonal or service layers. Document rationale, context, and consequences for all technical decisions.",
            "builtin": True,
            "estimated_tokens": 210,
        },
        {
            "name": "container_manifests",
            "title": "Docker & Multi-Stage Deployment",
            "description": "Authors production Dockerfiles, docker-compose.yml files, and environment variable templates.",
            "instructions": "Author multi-stage Dockerfiles adhering to non-root execution and healthcheck endpoints.",
            "builtin": True,
            "estimated_tokens": 170,
        },
    ],
    "Designer": [
        {
            "name": "tailwind_design_tokens",
            "title": "Tailwind UI/UX Architecture",
            "description": "Designs modern, accessible UI specifications using responsive Tailwind CSS tokens and layout contracts.",
            "instructions": "Use high-contrast Tailwind CSS color palettes, accessible tap targets, and clean dark/light mode responsive viewports.",
            "builtin": True,
            "estimated_tokens": 160,
        },
    ],
    "Database Engineer": [
        {
            "name": "relational_schema_ddl",
            "title": "Relational Schema DDL & Constraints",
            "description": "Designs normalized relational tables, strict primary/foreign keys, B-tree indexes, and seed fixtures.",
            "instructions": "Always produce idempotent DDL with IF NOT EXISTS. Every foreign key relationship must declare an explicit ON DELETE action. Add indexes on foreign keys.",
            "builtin": True,
            "estimated_tokens": 240,
        },
    ],
    "Developer": [
        {
            "name": "fullstack_implementation",
            "title": "Full-Stack Implementation",
            "description": "Implements physical backend logic and frontend templates with AST symbol map alignment.",
            "instructions": "Write complete, production-ready code without placeholders or 'TODO' stubs. Follow clean architecture and modular separation.",
            "builtin": True,
            "estimated_tokens": 220,
        },
        {
            "name": "subagent_spawning",
            "title": "Dynamic Sub-Agent Spawning",
            "description": "Spawns targeted child agents for complex concurrent subtasks without blocking the main workflow.",
            "instructions": "When a task requires specialized deep analysis, spawn a child assistant with a focused subtask description.",
            "builtin": True,
            "estimated_tokens": 190,
        },
    ],
    "Tester": [
        {
            "name": "tdd_self_healing",
            "title": "TDD Self-Correction Loops",
            "description": "Executes automated test suites (pytest/unittest) and feeds back tracebacks to heal code until exit code 0.",
            "instructions": "Write tests with assertions covering both happy paths and boundary edge cases. Parse tracebacks systematically during healing iterations.",
            "builtin": True,
            "estimated_tokens": 230,
        },
    ],
    "Reviewer": [
        {
            "name": "pep8_clean_code_audit",
            "title": "PEP 8 & Architectural Review",
            "description": "Audits pull requests for PEP 8 compliance, cyclomatic complexity, and architectural integrity.",
            "instructions": "Review all code changes against architectural ADRs, type annotations, and maintainability best practices.",
            "builtin": True,
            "estimated_tokens": 200,
        },
    ],
    "Security Auditor": [
        {
            "name": "owasp_sast_scan",
            "title": "OWASP Top 10 & SAST Audit",
            "description": "Performs static application security testing (SAST) for SQL injection, XSS, and hardcoded secrets.",
            "instructions": "Scan for SQL string interpolation, raw query concatenation, hardcoded API keys/secrets, and missing parameter sanitization.",
            "builtin": True,
            "estimated_tokens": 250,
        },
    ],
    "GitHub Manager": [
        {
            "name": "conventional_git_flow",
            "title": "Conventional Commits & GitOps",
            "description": "Manages branch lifecycles, atomic conventional commit messages (feat, fix, refactor), and release tags.",
            "instructions": "Create atomic git commits with conventional commit message prefixes. Verify git status is clean before tagging releases.",
            "builtin": True,
            "estimated_tokens": 180,
        },
    ],
    "Memory & Status Manager": [
        {
            "name": "wal_state_synchronization",
            "title": "SQLite WAL State Resumption",
            "description": "Ensures transactional state checkpoints and Cytoscape knowledge graph memory synchronization.",
            "instructions": "Synchronize state checkpoints in SQLite WAL mode. Maintain graph connectivity between tasks, ADRs, and active milestones.",
            "builtin": True,
            "estimated_tokens": 190,
        },
    ],
}


class SkillPluginRegistry:
    """
    Manages discovery, inspection, and dynamic binding of skills and plugins.
    """

    def __init__(self, workspace_root: Optional[Union[str, Path]] = None, state_mgr: Optional[StateManager] = None) -> None:
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else Path.cwd().resolve()
        self.skills_dir = self.workspace_root / "custom_skills"
        self.plugins_dir = self.workspace_root / "plugins"
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.state_mgr = state_mgr

    def discover_custom_skills(self) -> List[Dict[str, Any]]:
        """Scan custom_skills/ directory for SKILL.md files and parse metadata."""
        custom_skills: List[Dict[str, Any]] = []
        if not self.skills_dir.exists():
            return custom_skills

        for skill_path in self.skills_dir.rglob("SKILL.md"):
            try:
                content = skill_path.read_text(encoding="utf-8")
                meta: Dict[str, Any] = {}
                instructions = content

                # Parse YAML frontmatter if present
                fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
                if fm_match:
                    frontmatter_raw, body = fm_match.group(1), fm_match.group(2)
                    instructions = body.strip()
                    for line in frontmatter_raw.splitlines():
                        if ":" in line:
                            k, v = line.split(":", 1)
                            k = k.strip()
                            v = v.strip().strip("'\"")
                            if v.startswith("[") and v.endswith("]"):
                                # Parse simple JSON-like list: ["RoleA", "RoleB"]
                                try:
                                    v = json.loads(v)
                                except Exception:
                                    v = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
                            meta[k] = v

                skill_name = meta.get("name") or skill_path.parent.name
                title = meta.get("title") or skill_name.replace("_", " ").title()
                target_roles = meta.get("target_roles", ["Developer"])
                if isinstance(target_roles, str):
                    target_roles = [target_roles]

                # Canonicalize target roles
                canon_target_roles = []
                for tr in target_roles:
                    try:
                        canon_target_roles.append(normalize_role(tr))
                    except Exception:
                        canon_target_roles.append(tr)

                est_tokens = max(50, int(len(instructions.split()) * 1.33))

                custom_skills.append({
                    "name": skill_name,
                    "title": title,
                    "description": meta.get("description", instructions[:120] + "..."),
                    "target_roles": canon_target_roles,
                    "instructions": instructions,
                    "builtin": False,
                    "path": str(skill_path),
                    "estimated_tokens": est_tokens,
                })
            except Exception as e:
                logger.warning("Failed parsing skill file %s: %s", skill_path, e)

        return custom_skills

    def discover_custom_plugins(self) -> List[Dict[str, Any]]:
        """Scan plugins/ directory for Python tool classes or MCP connections."""
        plugins: List[Dict[str, Any]] = []
        if not self.plugins_dir.exists():
            return plugins

        for py_file in self.plugins_dir.glob("*.py"):
            if py_file.name.startswith("__"):
                continue
            try:
                mod_name = f"custom_plugin_{py_file.stem}"
                spec = importlib.util.spec_from_file_location(mod_name, str(py_file))
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    doc = (mod.__doc__ or "").strip()
                    plugins.append({
                        "name": py_file.stem,
                        "title": py_file.stem.replace("_", " ").title(),
                        "description": doc or f"Custom Python tool plugin from {py_file.name}",
                        "type": "python_tool",
                        "path": str(py_file),
                        "builtin": False,
                    })
            except Exception as e:
                logger.warning("Failed loading plugin %s: %s", py_file, e)

        # Check mcp.json for external MCP plugins
        mcp_cfg = self.workspace_root / "mcp.json"
        if mcp_cfg.exists():
            try:
                cfg_data = json.loads(mcp_cfg.read_text(encoding="utf-8"))
                for s_name, s_conf in cfg_data.get("mcpServers", {}).items():
                    plugins.append({
                        "name": f"mcp_{s_name}",
                        "title": f"MCP: {s_name}",
                        "description": f"External MCP Server ({s_conf.get('command')})",
                        "type": "mcp_server",
                        "builtin": False,
                    })
            except Exception:
                pass

        return plugins

    def get_agent_manifest(self, role: str, project_id: str = "default_project") -> Dict[str, Any]:
        """
        Compile complete manifest for an agent:
        System Prompt & Config, Skills (built-in + custom), Plugins & Tools.
        """
        canon_role = normalize_role(role)
        role_def = get_role_definition(canon_role)
        llm_cfg = ROLE_MODEL_PRESETS.get(canon_role, {})

        # Query state manager for enabled skill/plugin states
        configured_skills_map = {}
        configured_plugins_map = {}
        if self.state_mgr:
            skill_rows = self.state_mgr.get_agent_skills(project_id=project_id, agent_role=canon_role)
            for r in skill_rows:
                configured_skills_map[r.get("skill_name")] = bool(r.get("enabled"))
            plugin_rows = self.state_mgr.get_agent_plugins(project_id=project_id, agent_role=canon_role)
            for r in plugin_rows:
                configured_plugins_map[r.get("plugin_name")] = bool(r.get("enabled"))

        # 1. Compile Skills
        agent_skills = []
        # Built-in skills (enabled by default unless explicitly disabled in DB)
        for s in BUILTIN_ROLE_SKILLS.get(canon_role, []):
            is_enabled = configured_skills_map.get(s["name"], True)
            agent_skills.append({**s, "enabled": is_enabled})

        # Custom skills targeting this role
        all_custom = self.discover_custom_skills()
        for cs in all_custom:
            if canon_role in cs.get("target_roles", []):
                is_enabled = configured_skills_map.get(cs["name"], True)
                agent_skills.append({**cs, "enabled": is_enabled})

        # 2. Compile Plugins & Tools
        agent_plugins = []
        # Built-in tools assigned to this role
        builtin_tools_desc = {
            "WorkspaceFileReadTool": "Read lines and files with workspace boundary sandboxing",
            "WorkspaceFileWriteTool": "Atomic workspace file creation with syntax checking",
            "WorkspaceFilePatchTool": "Surgical diff patching without whole-file rewrites",
            "WorkspaceFileListTool": "Recursive directory listing and file search",
            "RealGitRepoManager": "Local Git commits, branches, and diff tracking",
            "ASTSymbolExtractor": "Abstract Syntax Tree (AST) mapping of classes and functions",
            "SubprocessTestRunner": "Execution of test suites with traceback extraction",
            "PlaywrightSmokeTester": "Headless Chromium browser smoke and DOM inspection",
            "SpawnAssistantTool": "On-demand dynamic child agent spawning",
            "StateManager": "SQLite WAL transactional persistence and telemetry",
        }
        # Role to tool associations
        role_builtin_map = {
            "Coordinator": ["WorkspaceFileListTool", "StateManager"],
            "Architect": ["WorkspaceFileReadTool", "WorkspaceFileListTool", "RealGitRepoManager", "StateManager"],
            "Designer": ["WorkspaceFileReadTool", "WorkspaceFileWriteTool", "WorkspaceFileListTool"],
            "Database Engineer": ["WorkspaceFileReadTool", "WorkspaceFileWriteTool", "WorkspaceFilePatchTool", "StateManager"],
            "Developer": ["WorkspaceFileReadTool", "WorkspaceFileWriteTool", "WorkspaceFilePatchTool", "ASTSymbolExtractor", "SpawnAssistantTool"],
            "Tester": ["WorkspaceFileReadTool", "WorkspaceFileListTool", "SubprocessTestRunner", "PlaywrightSmokeTester"],
            "Reviewer": ["WorkspaceFileReadTool", "WorkspaceFileListTool", "ASTSymbolExtractor"],
            "Security Auditor": ["WorkspaceFileReadTool", "WorkspaceFileListTool", "ASTSymbolExtractor"],
            "GitHub Manager": ["RealGitRepoManager", "WorkspaceFileListTool"],
            "Memory & Status Manager": ["StateManager", "WorkspaceFileListTool"],
        }
        for tool_name in role_builtin_map.get(canon_role, ["WorkspaceFileReadTool"]):
            is_enabled = configured_plugins_map.get(tool_name, True)
            agent_plugins.append({
                "name": tool_name,
                "title": tool_name,
                "description": builtin_tools_desc.get(tool_name, "Standard SDLC Tool"),
                "builtin": True,
                "enabled": is_enabled,
            })

        # Custom plugins
        for cp in self.discover_custom_plugins():
            is_enabled = configured_plugins_map.get(cp["name"], True)
            agent_plugins.append({**cp, "enabled": is_enabled})

        total_skill_tokens = sum(s.get("estimated_tokens", 0) for s in agent_skills if s.get("enabled"))

        # Check SQLite for custom unified system prompt override
        custom_prompt = None
        if self.state_mgr and hasattr(self.state_mgr, "get_agent_custom_prompt"):
            custom_prompt = self.state_mgr.get_agent_custom_prompt(agent_role=canon_role, project_id=project_id)

        default_system_prompt = f"""=== {canon_role.upper()} AGENT SYSTEM PROMPT ===
Goal:
{role_def.goal}

Backstory & Persona:
{role_def.backstory}

{role_def.system_prompt.strip()}
"""
        active_system_prompt = custom_prompt if custom_prompt else default_system_prompt
        is_custom_prompt = bool(custom_prompt)
        base_prompt_tokens = int(len(active_system_prompt.split()) * 1.33)

        return {
            "role": canon_role,
            "status": "idle",
            "system_config": {
                "goal": role_def.goal,
                "backstory": role_def.backstory,
                "system_prompt": active_system_prompt,
                "default_system_prompt": default_system_prompt,
                "is_custom_prompt": is_custom_prompt,
                "provider_tier": llm_cfg.get("preferred_provider", "gemini"),
                "model_name": llm_cfg.get("model", "gemini-3.5-flash-lite"),
                "temperature": llm_cfg.get("temperature", 0.7),
                "allow_delegation": role_def.allow_delegation,
                "base_prompt_tokens": base_prompt_tokens,
            },
            "skills": agent_skills,
            "plugins": agent_plugins,
            "metrics": {
                "active_skills_count": sum(1 for s in agent_skills if s.get("enabled")),
                "active_plugins_count": sum(1 for p in agent_plugins if p.get("enabled")),
                "estimated_skill_tokens": total_skill_tokens,
            }
        }

    def get_all_crew_manifests(self, project_id: str = "default_project") -> List[Dict[str, Any]]:
        """Return full manifests for all 10 canonical roles."""
        return [self.get_agent_manifest(role, project_id=project_id) for role in CANONICAL_ROLES]

    def get_all_custom_skills(self) -> List[Dict[str, Any]]:
        """Return list of all discovered custom skills across all roles."""
        return self.discover_custom_skills()

    def save_custom_skill(
        self,
        name: str,
        title: Optional[str] = None,
        target_roles: Optional[Union[List[str], str]] = None,
        description: str = "",
        instructions: str = "",
        target_role: Optional[str] = None,
        content: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Save a new custom skill as a modular SKILL.md file."""
        slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", name.strip().lower())
        skill_dir = self.skills_dir / slug
        skill_dir.mkdir(parents=True, exist_ok=True)
        file_path = skill_dir / "SKILL.md"

        actual_title = title or slug.replace("_", " ").title()
        raw_roles = target_roles or ([target_role] if target_role else ["Developer"])
        if isinstance(raw_roles, str):
            raw_roles = [raw_roles]

        canon_roles = [normalize_role(r) for r in raw_roles]
        roles_json = json.dumps(canon_roles)
        actual_instructions = (instructions or content or "").strip()

        file_content = f"""---
name: {slug}
title: "{actual_title}"
target_roles: {roles_json}
description: "{description}"
---

### {actual_title} Guidelines
{actual_instructions}
"""
        file_path.write_text(file_content, encoding="utf-8")
        logger.info("Saved custom skill: %s at %s", slug, file_path)
        return {
            "name": slug,
            "title": actual_title,
            "target_roles": canon_roles,
            "description": description,
            "path": str(file_path),
        }

    def get_injected_prompt_skills(self, role: str, project_id: str = "default_project") -> str:
        """
        Compile enabled skill instructions into a clean markdown block
        ready to append to the agent prompt at runtime.
        """
        manifest = self.get_agent_manifest(role, project_id=project_id)
        enabled_skills = [s for s in manifest.get("skills", []) if s.get("enabled")]
        if not enabled_skills:
            return ""

        blocks = ["\n--- SPECIALIST SKILL GUIDELINES ---"]
        for s in enabled_skills:
            blocks.append(f"### Skill: {s.get('title')} ({s.get('name')})\n{s.get('instructions', '').strip()}\n")
        blocks.append("----------------------------------\n")
        return "\n".join(blocks)
