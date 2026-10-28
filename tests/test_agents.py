"""
Unit and integration tests for Persistent CrewAI Milestone 3 Agents Subsystem:
- DeterministicMockLLM & dynamic prompt parsing
- LLMFactory provider resolution & offline hermetic execution
- Role definitions & canonical constraints for all 8 specialist roles
- Crew builder agent, team, and crew instantiation with tool & state binding
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

# Ensure src is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from persistent_crewai.config import CANONICAL_ROLES, normalize_role
from persistent_crewai.persistence.state_manager import StateManager
from persistent_crewai.agents.llm_factory import (
    DeterministicMockLLM,
    LLMFactory,
    get_llm,
    CANONICAL_ROLE_RESPONSES,
    ROLE_KEYWORD_RULES,
)
from persistent_crewai.agents.role_definitions import (
    RoleDefinition,
    ROLE_DEFINITIONS,
    AGENT_ROLES,
    get_role_definition,
    list_role_definitions,
    list_all_roles,
    get_all_roles,
    get_role_tool_names,
)
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


class TestDeterministicMockLLM(unittest.TestCase):
    """Test suite for DeterministicMockLLM execution and prompt parsing."""

    def setUp(self):
        self.llm = DeterministicMockLLM()

    def test_m01_deterministic_repeat_predict(self):
        """Verify identical prompts yield identical deterministic outputs."""
        prompt = "Architect: specify the relational schema"
        res1 = self.llm.predict(prompt)
        res2 = self.llm.predict(prompt)
        self.assertIsInstance(res1, str)
        self.assertEqual(res1, res2)
        self.assertIn("Final Answer:", res1)
        self.assertEqual(len(self.llm.call_history), 2)

    def test_m02_all_eight_roles_responses(self):
        """Verify distinct, role-grounded responses for canonical roles."""
        role_prompts = {
            "Coordinator": "Coordinator: What is the current status of the project?",
            "Architect": "Architect: Explain the DB design and schema",
            "Designer": "Designer: Formulate the CLI and REPL ergonomics and design tokens",
            "Database Engineer": "Database Engineer: Create SQL DDL schema and indexes",
            "Developer": "Developer: Implement the persistence module and state manager",
            "Tester": "Tester: Execute pytest suite and verify test coverage",
            "Reviewer": "Reviewer: Perform code review, security audit, and PEP-8 check",
            "Security Auditor": "Security Auditor: Audit vulnerabilities for OWASP and secret leaks",
            "GitHub Manager": "GitHub Manager: Create a git branch, commit changes, and open a PR",
            "Memory & Status Manager": "Memory Manager: Persist state, decisions, and milestones to disk",
        }

        for role_name, prompt in role_prompts.items():
            resp = self.llm.predict(prompt)
            self.assertTrue(
                resp.startswith("Final Answer:"),
                f"Response for {role_name} must start with 'Final Answer:' for ReAct loop completion. Got: {resp[:40]}",
            )
            # Verify role content is specialized
            if role_name == "Architect":
                self.assertIn("SQLite", resp)
            elif role_name == "Database Engineer":
                self.assertIn("Database", resp)
            elif role_name == "Tester":
                self.assertIn("Test", resp)
            elif role_name == "Security Auditor":
                self.assertIn("Security", resp)
            elif role_name == "GitHub Manager":
                self.assertIn("GitHub", resp)

    def test_m03_role_alias_mapping(self):
        """Verify role keyword matching handles common aliases."""
        alias_checks = [
            ("project manager overview", "Coordinator"),
            ("wbs and backlog", "Coordinator"),
            ("database design and adr", "Architect"),
            ("ui/ux design system", "Designer"),
            ("database engineer sql ddl", "Database Engineer"),
            ("coding python module", "Developer"),
            ("qa sdet unit test", "Tester"),
            ("security auditor vulnerability audit", "Security Auditor"),
            ("git branch and pull request", "GitHub Manager"),
            ("vector store checkpoint", "Memory & Status Manager"),
        ]
        for query, expected_role in alias_checks:
            resp = self.llm.predict(query)
            expected_content = CANONICAL_ROLE_RESPONSES[expected_role]
            self.assertEqual(resp, expected_content, f"Failed keyword mapping for '{query}' -> {expected_role}")

    def test_m04_context_grounding_injection(self):
        """Verify injected SQLite project context is parsed and reflected in the answer."""
        injected_prompt = (
            "Coordinator: What is the current status?\n"
            "--- CURRENT PROJECT CONTEXT ---\n"
            "Project: Apollo Framework (Mode: strict)\n"
            "Current Active Milestone: Core Architecture (Status: in_progress)\n"
            "Relevant Recorded Decisions:\n"
            "[ADR-001] (accepted) Adopt SQLite with WAL mode\n"
        )
        resp = self.llm.predict(injected_prompt)
        self.assertIn("Final Answer:", resp)
        self.assertIn("[Grounded State Context]:", resp)
        self.assertIn("Apollo Framework", resp)
        self.assertIn("Core Architecture", resp)

    def test_m05_tool_call_triggers(self):
        """Verify configured tool call triggers return specified tool actions."""
        custom_llm = DeterministicMockLLM(
            tool_call_triggers={
                "read config": "Thought: Need to read file.\nAction: read_workspace_file\nAction Input: {\"file_path\": \"config.py\"}"
            }
        )
        resp = custom_llm.predict("Please read config file.")
        self.assertIn("Action: read_workspace_file", resp)

    def test_m06_react_observation_handling(self):
        """Verify that prompts containing Observation: return a successful Final Answer."""
        observation_prompt = (
            "Thought: Reading file.\n"
            "Action: read_workspace_file\n"
            "Observation: File contents of config.py loaded successfully.\n"
            "What next?"
        )
        resp = self.llm.predict(observation_prompt)
        self.assertTrue(resp.startswith("Final Answer:"))
        self.assertIn("Observation received and processed successfully", resp)

    def test_m07_deterministic_sha256_fallback(self):
        """Verify arbitrary prompts with no keywords produce deterministic SHA-256 hash responses."""
        prompt = "quantum entanglement in superconductive lattices"
        resp1 = self.llm.predict(prompt)
        resp2 = self.llm.predict(prompt)
        self.assertEqual(resp1, resp2)
        self.assertTrue(resp1.startswith("Final Answer: Deterministic response [hash="))

    def test_m08_canned_responses_fifo(self):
        """Verify canned responses are popped in FIFO order."""
        canned_llm = DeterministicMockLLM(
            canned_responses=["First seeded response", "Second seeded response"]
        )
        self.assertEqual(canned_llm.predict("hello"), "First seeded response")
        self.assertEqual(canned_llm.predict("world"), "Second seeded response")
        # After canned responses are exhausted, falls back to normal synthesis
        fallback_resp = canned_llm.predict("Architect: explain schema")
        self.assertIn("System Architecture & Database Blueprint", fallback_resp)

    def test_m09_resilient_interfaces(self):
        """Verify predict, invoke, call, __call__, generate, bind, bind_tools, and reset."""
        # 1. invoke
        ai_msg = self.llm.invoke("Architect: database")
        self.assertEqual(ai_msg.type, "ai")
        self.assertIn("Final Answer:", ai_msg.content)
        self.assertEqual(str(ai_msg), ai_msg.content)

        # 2. call
        call_resp = self.llm.call("Developer: implement")
        self.assertIn("Implementation Summary", call_resp)

        # 3. direct callable
        direct_resp = self.llm("Tester: test")
        self.assertIn("Test Execution", direct_resp)

        # 4. generate
        chat_result = self.llm.generate(["Reviewer: audit"])
        self.assertEqual(len(chat_result.generations), 1)
        self.assertIn("Code Review & Security Audit", chat_result.generations[0].text)

        # 5. bind & bind_tools & with_structured_output no-ops
        bound = self.llm.bind(stop=["\nObservation:"])
        self.assertIs(bound, self.llm)
        bound_tools = self.llm.bind_tools(["tool1", "tool2"])
        self.assertIs(bound_tools, self.llm)
        structured = self.llm.with_structured_output(dict)
        self.assertIs(structured, self.llm)

        # 6. reset
        self.assertTrue(len(self.llm.call_history) > 0)
        self.llm.reset()
        self.assertEqual(len(self.llm.call_history), 0)


class TestLLMFactory(unittest.TestCase):
    """Test suite for LLMFactory and get_llm provider resolution."""

    def setUp(self):
        self.orig_env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.orig_env)

    def test_f01_default_to_mock_when_no_keys(self):
        """Verify get_llm defaults safely to DeterministicMockLLM when offline without keys."""
        for key in [
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "ANTHROPIC_API_KEY",
            "OPENROUTER_API_KEY",
            "GROQ_API_KEY",
            "NVIDIA_API_KEY",
            "MOCK_LLM",
            "CREWAI_MOCK_LLM",
        ]:
            os.environ.pop(key, None)

        llm = get_llm()
        self.assertIsInstance(llm, DeterministicMockLLM)
        self.assertEqual(llm.model_name, "deterministic-mock-llm")

    def test_f02_forced_mock_env_flag(self):
        """Verify MOCK_LLM=true forces DeterministicMockLLM even if keys are set."""
        os.environ["MOCK_LLM"] = "true"
        os.environ["OPENAI_API_KEY"] = "sk-fake-openai-key-that-should-not-be-called"

        llm = get_llm()
        self.assertIsInstance(llm, DeterministicMockLLM)

    def test_f03_explicit_mock_provider(self):
        """Verify provider='mock' returns DeterministicMockLLM."""
        llm = get_llm(provider="mock")
        self.assertIsInstance(llm, DeterministicMockLLM)

        llm_offline = get_llm(provider="offline")
        self.assertIsInstance(llm_offline, DeterministicMockLLM)

    def test_f04_explicit_provider_missing_key_raises_error(self):
        """Verify requesting a live provider without required API key raises ValueError."""
        os.environ.pop("OPENAI_API_KEY", None)
        with self.assertRaises(ValueError) as ctx:
            get_llm(provider="openai")
        self.assertIn("OPENAI_API_KEY environment variable is required", str(ctx.exception))

        os.environ.pop("GEMINI_API_KEY", None)
        os.environ.pop("GOOGLE_API_KEY", None)
        with self.assertRaises(ValueError) as ctx:
            get_llm(provider="gemini")
        self.assertIn("GEMINI_API_KEY or GOOGLE_API_KEY", str(ctx.exception))

        os.environ.pop("ANTHROPIC_API_KEY", None)
        with self.assertRaises(ValueError) as ctx:
            get_llm(provider="anthropic")
        self.assertIn("ANTHROPIC_API_KEY environment variable is required", str(ctx.exception))

    def test_f05_unsupported_provider_raises_error(self):
        """Verify requesting an unsupported provider name raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            get_llm(provider="unsupported_quantum_ai")
        self.assertIn("Unsupported LLM provider 'unsupported_quantum_ai'", str(ctx.exception))

    def test_f06_llm_factory_class_wrapper(self):
        """Verify LLMFactory.get_llm() delegates correctly."""
        llm = LLMFactory.get_llm(provider="deterministic")
        self.assertIsInstance(llm, DeterministicMockLLM)


class TestRoleDefinitions(unittest.TestCase):
    """Test suite for 8 canonical specialist role definitions and constraints."""

    def test_r01_all_canonical_roles_defined(self):
        """Verify all canonical roles are present in AGENT_ROLES and ROLE_DEFINITIONS."""
        self.assertEqual(len(ROLE_DEFINITIONS), len(CANONICAL_ROLES))
        self.assertEqual(set(ROLE_DEFINITIONS.keys()), set(CANONICAL_ROLES))
        self.assertEqual(set(AGENT_ROLES), set(CANONICAL_ROLES))

    def test_r02_role_fields_and_subscript_access(self):
        """Verify each role definition has valid fields and supports dict-like & attribute access."""
        for role_name in CANONICAL_ROLES:
            role_def = get_role_definition(role_name)
            self.assertIsInstance(role_def, RoleDefinition)
            self.assertIsInstance(role_def, dict)

            # Dictionary subscripting
            self.assertIn("goal", role_def)
            self.assertIn("backstory", role_def)
            self.assertIn("system_prompt", role_def)
            self.assertTrue(len(role_def["goal"]) > 10)
            self.assertTrue(len(role_def["backstory"]) > 10)
            self.assertTrue(len(role_def["system_prompt"]) > 10)

            # Direct attribute access
            self.assertEqual(role_def.role, role_name)
            self.assertEqual(role_def.canonical_role, role_name)
            self.assertTrue(len(role_def.display_name) > 0)
            self.assertEqual(role_def.goal, role_def["goal"])

    def test_r03_delegation_rules(self):
        """Verify delegation is enabled ONLY for Coordinator and disabled for all 7 specialists."""
        coord_def = get_role_definition("Coordinator")
        self.assertTrue(coord_def.delegation_allowed)
        self.assertTrue(coord_def.allow_delegation)
        self.assertTrue(coord_def.get("allow_delegation"))

        for role_name in CANONICAL_ROLES:
            if role_name != "Coordinator":
                spec_def = get_role_definition(role_name)
                self.assertFalse(
                    spec_def.delegation_allowed,
                    f"Delegation must be False for specialist role '{role_name}'",
                )
                self.assertFalse(spec_def.allow_delegation)
                self.assertFalse(spec_def.get("allow_delegation"))

    def test_r04_tool_assignments(self):
        """Verify expected functional tool suites for each of the canonical roles."""
        expected_tools = {
            "Coordinator": ["list_workspace_files", "query_memory", "record_milestone"],
            "Architect": ["read_workspace_file", "list_workspace_files", "record_decision", "query_memory"],
            "Designer": ["read_workspace_file", "write_workspace_file", "list_workspace_files"],
            "Database Engineer": ["read_workspace_file", "write_workspace_file", "list_workspace_files", "record_decision", "query_memory"],
            "Developer": ["read_workspace_file", "write_workspace_file", "patch_workspace_file", "list_workspace_files"],
            "Tester": ["read_workspace_file", "list_workspace_files"],
            "Reviewer": ["read_workspace_file", "list_workspace_files", "query_memory"],
            "Security Auditor": ["read_workspace_file", "list_workspace_files", "record_decision", "query_memory"],
            "GitHub Manager": ["github_issue_tool", "github_pr_tool", "github_branch_tool", "github_commit_tool", "github_repo_status_tool"],
            "Memory & Status Manager": ["record_decision", "record_milestone", "query_memory"],
        }

        for role_name, tools in expected_tools.items():
            role_def = get_role_definition(role_name)
            self.assertEqual(
                role_def.assigned_tool_names,
                tools,
                f"Mismatch in assigned tools for role '{role_name}'",
            )
            self.assertEqual(get_role_tool_names(role_name), tools)

    def test_r05_role_normalization_and_aliases(self):
        """Verify normalize_role and get_role_definition handle case and aliases."""
        alias_map = {
            "coordinator": "Coordinator",
            "manager": "Coordinator",
            "coordinator / manager": "Coordinator",
            "architect": "Architect",
            "designer": "Designer",
            "developer": "Developer",
            "tester": "Tester",
            "reviewer": "Reviewer",
            "github": "GitHub Manager",
            "github manager": "GitHub Manager",
            "memory": "Memory & Status Manager",
            "memory manager": "Memory & Status Manager",
        }
        for alias, expected in alias_map.items():
            norm = normalize_role(alias)
            self.assertEqual(norm, expected)
            r_def = get_role_definition(alias)
            self.assertEqual(r_def.canonical_role, expected)

    def test_r06_unknown_role_raises_keyerror(self):
        """Verify querying an unrecognized role string raises KeyError."""
        with self.assertRaises(KeyError):
            get_role_definition("quantum_wizard")

    def test_r07_list_all_roles(self):
        """Verify list_role_definitions() and list_all_roles() return all roles."""
        all_defs = list_role_definitions()
        self.assertEqual(len(all_defs), len(CANONICAL_ROLES))
        self.assertEqual([d.canonical_role for d in all_defs], CANONICAL_ROLES)
        self.assertEqual(list_all_roles(), all_defs)
        self.assertEqual(get_all_roles(), CANONICAL_ROLES)


class TestCrewBuilder(unittest.TestCase):
    """Test suite for agent, team, and crew instantiation and state integration."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name)
        self.db_path = self.workspace_root / "test_state.db"
        self.state_manager = StateManager(
            db_path=self.db_path,
            workspace_root=self.workspace_root,
            project_id="test_crew_builder_proj",
        )
        self.state_manager.create_project(
            project_id="test_crew_builder_proj",
            name="Test Project",
            description="Test workspace for crew builder",
            root_path=self.workspace_root,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_b01_build_each_canonical_agent(self):
        """Verify build_agent instantiates valid agents for all 8 roles."""
        for role_name in CANONICAL_ROLES:
            agent = build_agent(
                role=role_name,
                state_manager=self.state_manager,
                workspace_root=self.workspace_root,
            )
            self.assertIsInstance(agent, Agent)
            self.assertEqual(agent.canonical_role, role_name)
            self.assertEqual(agent.role, role_name)
            self.assertTrue(len(agent.goal) > 0)
            self.assertTrue(len(agent.backstory) > 0)
            self.assertEqual(agent.allow_delegation, (role_name == "Coordinator"))

    def test_b02_agent_tools_binding(self):
        """Verify agent receives appropriate tools matching role specification."""
        dev_agent = build_agent("Developer", workspace_root=self.workspace_root, state_manager=self.state_manager)
        tool_names = [t.name for t in dev_agent.tools]
        self.assertIn("read_workspace_file", tool_names)
        self.assertIn("write_workspace_file", tool_names)
        self.assertIn("patch_workspace_file", tool_names)
        self.assertIn("list_workspace_files", tool_names)

        gh_agent = build_agent("GitHub Manager", workspace_root=self.workspace_root, state_manager=self.state_manager)
        gh_tool_names = [t.name for t in gh_agent.tools]
        self.assertIn("github_issue_tool", gh_tool_names)
        self.assertIn("github_pr_tool", gh_tool_names)

    def test_b03_agent_custom_llm_binding(self):
        """Verify passing a custom LLM binds correctly to agent.llm."""
        custom_llm = DeterministicMockLLM(canned_responses=["Custom agent response"])
        agent = build_agent("Architect", llm=custom_llm)
        self.assertIs(agent.llm, custom_llm)
        out = agent.run("Explain DB schema")
        self.assertEqual(out, "Custom agent response")

    def test_b04_build_default_team(self):
        """Verify build_team() creates all agents mapped by canonical role."""
        team = build_team(
            state_manager=self.state_manager,
            workspace_root=self.workspace_root,
        )
        self.assertEqual(len(team), len(CANONICAL_ROLES))
        self.assertEqual(set(team.keys()), set(CANONICAL_ROLES))
        for role, agent in team.items():
            self.assertEqual(agent.canonical_role, role)

    def test_b05_build_team_subset(self):
        """Verify build_team(roles=[...]) creates requested subset."""
        team = build_team(roles=["Developer", "Tester"])
        self.assertEqual(len(team), 2)
        self.assertIn("Developer", team)
        self.assertIn("Tester", team)

    def test_b06_build_crew_sequential(self):
        """Verify build_crew configures sequential process by default."""
        team = build_team()
        crew = build_crew(agents=team, process=Process.sequential)
        self.assertEqual(crew.process, Process.sequential)
        self.assertEqual(len(crew.agents), len(CANONICAL_ROLES))

    def test_b07_build_crew_hierarchical_auto_manager(self):
        """Verify hierarchical crew automatically selects Coordinator as manager."""
        team = build_team()
        crew = build_crew(agents=team, process="hierarchical")
        self.assertEqual(crew.process, Process.hierarchical)
        self.assertIsNotNone(crew.manager_agent)
        self.assertEqual(crew.manager_agent.canonical_role, "Coordinator")

    def test_b08_build_crew_hierarchical_missing_manager_raises_error(self):
        """Verify hierarchical crew without Coordinator and without explicit manager raises ValueError."""
        # Create team without Coordinator
        subset = [build_agent("Developer"), build_agent("Tester")]
        with self.assertRaises(ValueError) as ctx:
            build_crew(agents=subset, process=Process.hierarchical)
        self.assertIn("Hierarchical crew requires a manager_agent or a Coordinator", str(ctx.exception))

    def test_b09_agent_task_execution_and_status_tracking(self):
        """Verify task execution transitions agent status in StateManager: idle -> busy -> idle."""
        agent = build_agent(
            "Architect",
            state_manager=self.state_manager,
            workspace_root=self.workspace_root,
        )

        task = Task(description="Specify the database schema", task_id="task_test_001")
        output = agent.execute_task(task)
        self.assertIsInstance(output, str)
        self.assertTrue(len(output) > 0)
        self.assertEqual(task.output, output)

        # After completion, status must be idle
        status_rec = self.state_manager.get_agent_status("Architect")
        self.assertEqual(status_rec["status"], "idle")
        self.assertIsNone(status_rec["current_task_id"])

    def test_b10_agent_error_status_transition(self):
        """Verify uncaught exceptions in agent execution transition status to error."""
        class FailingLLM:
            def predict(self, *args, **kwargs):
                raise RuntimeError("LLM synthesis failure simulation")

        agent = build_agent(
            "Developer",
            llm=FailingLLM(),
            state_manager=self.state_manager,
            workspace_root=self.workspace_root,
        )

        task = Task(description="Implement broken feature")
        with self.assertRaises(RuntimeError):
            agent.execute_task(task)

        status_rec = self.state_manager.get_agent_status("Developer")
        self.assertEqual(status_rec["status"], "error")
        self.assertIn("LLM synthesis failure", status_rec["last_error"])

    def test_b11_fallback_crew_kickoff(self):
        """Verify sequential crew kickoff propagates context between tasks and produces CrewOutput."""
        agent_arch = build_agent("Architect", workspace_root=self.workspace_root)
        agent_dev = build_agent("Developer", workspace_root=self.workspace_root)

        task1 = Task(description="Architect the storage subsystem", agent=agent_arch)
        task2 = Task(description="Implement the storage subsystem in Python", agent=agent_dev)

        crew = build_crew(agents=[agent_arch, agent_dev], tasks=[task1, task2])
        output = crew.kickoff(inputs={"feature": "WAL Mode"})

        self.assertIsInstance(output, CrewOutput)
        self.assertTrue(len(output.raw) > 0)
        self.assertEqual(len(output.tasks_output), 2)
        self.assertEqual(str(output), output.raw)


if __name__ == "__main__":
    unittest.main()
