"""
Empirical Adversarial Stress Test Suite for Persistent CrewAI Milestone 3:
DeterministicMockLLM, Role Definitions, Dynamic Context Extraction, and LLMFactory.

Author: Challenger M3-1 (LLM & Role Grounding Challenger)
Milestone: Milestone 3 - 8-Agent Team & CrewAI Orchestration

Empirically tests:
1. Prompt Fuzzing:
   - Multi-role keyword collision and rule priority bias
   - Negative constraints and prompt injection resistance
   - Realistic Agent.execute_task() prompt role fidelity across all 8 canonical roles
   - Extreme payloads (empty string, 100K chars, whitespace, unicode/emojis, null bytes, special chars)
   - Heterogeneous input types to invoke/call/predict/generate (None, int, dict, list, BaseMessage)
   - ReAct observation format fuzzing (case sensitivity, embedded observations)
2. Dynamic Project Context Extraction:
   - Arbitrary project titles, milestones, and statuses parsing from --- CURRENT PROJECT CONTEXT ---
   - Relevant recorded decisions extraction auditing
   - Context injection handling when prompt lacks role keywords (SHA-256 fallback)
   - Malformed context sections resilience
3. Canned Responses FIFO & Fallback:
   - Strict FIFO ordering under sequential calls
   - Seamless fallback to deterministic synthesis upon exhaustion
   - reset() behavior clearing both queue and invocation history
   - Interleaved interface calling across the FIFO queue
   - Batch generation generate() behavior with canned queues
4. LLMFactory Permutations & Environment Variables:
   - MOCK_LLM flag permutations ("true", "1", "yes", "TRUE", "True")
   - CREWAI_MOCK_LLM flag permutations
   - Missing API keys for all supported live providers (openai, gemini, google, anthropic)
   - Invalid and unsupported provider names
   - Whitespace and case-insensitivity in provider specification
   - Offline auto-detection safe fallback
5. Role Definition Contracts:
   - Mutability / singleton reference hazard
   - UnknownRoleError dual inheritance (KeyError and ValueError)
   - Strict isolation of allow_delegation on Coordinator only
   - Canonical role tool mappings completeness
"""

from __future__ import annotations

import copy
import hashlib
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure src is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from persistent_crewai.config import CANONICAL_ROLES, normalize_role
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
    UnknownRoleError,
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


class TestPromptFuzzingAndAdversarialInputs(unittest.TestCase):
    """Category 1: Empirical stress tests for prompt fuzzing, multi-role collisions, and extreme payloads."""

    def setUp(self):
        self.llm = DeterministicMockLLM()

    def test_adv_01_multi_role_keyword_collision_priority(self):
        """Verify behavior when multiple canonical role keywords compete in a single prompt."""
        # Prompt mentioning Coordinator, Architect, Developer, Tester, Reviewer, and GitHub Manager
        competing_prompt = (
            "Coordinator needs the Architect to specify the DB design while the Developer "
            "writes code, the Tester runs pytest, the Reviewer audits PEP-8, and the GitHub Manager commits to git."
        )
        resp = self.llm.predict(competing_prompt)
        self.assertTrue(resp.startswith("Final Answer:"), "Response must start with 'Final Answer:'")

        # Because ROLE_KEYWORD_RULES orders GitHub Manager first, the presence of 'github manager' or 'git'
        # deterministically classifies this competing prompt as GitHub Manager.
        first_rule_role = ROLE_KEYWORD_RULES[0][0]
        self.assertEqual(first_rule_role, "GitHub Manager")
        self.assertIn("GitHub & Repository Operations Summary", resp)

    def test_adv_02_negative_constraint_keyword_matching(self):
        """Verify prompt with explicit negative instructions ('Do NOT act as GitHub Manager')."""
        negative_prompt = (
            "You are the Tester. Do NOT act like the GitHub Manager. Do not touch git. "
            "Focus strictly on unit tests and pytest suites."
        )
        resp = self.llm.predict(negative_prompt)
        # Because classification is regex keyword presence based without negation parsing,
        # 'github manager' and 'git' match first before 'tester'
        self.assertTrue(resp.startswith("Final Answer:"))
        self.assertIn("GitHub & Repository Operations Summary", resp)

    def test_adv_03_agent_execute_task_role_fidelity_audit(self):
        """Empirically audit role output fidelity when canonical agents execute tasks via Agent.execute_task()."""
        # When Agent.execute_task() constructs its prompt, it embeds the full backstory and operating guidelines.
        # Several agent operating guidelines mention coordinating with other roles (e.g. GitHub Manager, Coordinator).
        results: Dict[str, str] = {}
        for role in CANONICAL_ROLES:
            agent = build_agent(role)
            out = agent.execute_task("Execute assigned engineering responsibilities.")
            results[role] = out

        # Verify all return valid Final Answers
        for role, out in results.items():
            self.assertTrue(
                out.startswith("Final Answer:"),
                f"Agent '{role}' must return response starting with 'Final Answer:'",
            )

        # Document exact role fidelity:
        # Architect, Designer, and GitHub Manager match their own canonical roles.
        self.assertIn("System Architecture & Database Blueprint", results["Architect"])
        self.assertIn("UI/UX & Design System Specification", results["Designer"])
        self.assertIn("GitHub & Repository Operations Summary", results["GitHub Manager"])

        # Roles whose guidelines cross-reference higher-priority roles:
        # Developer mentions Architect ("Adhere strictly to the Architect's blueprints") -> matches Architect
        self.assertIn("System Architecture & Database Blueprint", results["Developer"])
        # Tester, Reviewer, Coordinator, and Memory Manager mention GitHub Manager/git -> match GitHub Manager
        self.assertIn("GitHub & Repository Operations Summary", results["Coordinator"])
        self.assertIn("GitHub & Repository Operations Summary", results["Tester"])
        self.assertIn("GitHub & Repository Operations Summary", results["Reviewer"])
        self.assertIn("GitHub & Repository Operations Summary", results["Memory & Status Manager"])

    def test_adv_04_isolated_role_prompts_match_all_eight_roles(self):
        """Verify that when prompts explicitly target each role without cross-role pollution, all 8 distinct responses match."""
        isolated_prompts = {
            "Coordinator": "Coordinator: provide executive backlog and project orchestration status",
            "Architect": "Architect: author the system blueprints and SQLite schema ADR",
            "Designer": "Designer: define the CLI design tokens and TUI ergonomics",
            "Developer": "Developer: implement software engineering modules and code",
            "Tester": "Tester: run SDET QA regression coverage",
            "Reviewer": "Reviewer: perform security audit and pep-8 compliance check",
            "GitHub Manager": "GitHub Manager: commit changes and create pull request",
            "Memory & Status Manager": "Memory Manager: persist state checkpoints and session resumption",
        }

        for role, prompt in isolated_prompts.items():
            resp = self.llm.predict(prompt)
            expected = CANONICAL_ROLE_RESPONSES[role]
            self.assertEqual(
                resp,
                expected,
                f"Isolated prompt for role '{role}' did not produce canonical response",
            )

    def test_adv_05_extreme_prompt_payloads_resilience(self):
        """Stress-test DeterministicMockLLM with extreme, boundary, and hostile string inputs."""
        extreme_payloads = [
            ("empty_string", ""),
            ("whitespace_only", "   \t\n\r\n   "),
            ("huge_100k_chars", "Developer coding python " + "A" * 100_000),
            ("unicode_and_emojis", "🚀 Coordinator: 项目管理 and 測試 👨‍💻 WBS 🎯"),
            ("null_bytes_and_escapes", "Architect: schema\x00\x01\x02\\n\\r\\t'\""),
            ("html_and_xml_injection", "<script>alert('xss')</script><role>Tester</role>"),
            ("unbalanced_brackets_and_regex", "Developer: [({*+?^$|\\[]{{implement}}"),
        ]

        for desc, payload in extreme_payloads:
            with self.subTest(payload_type=desc):
                resp = self.llm.predict(payload)
                self.assertIsInstance(resp, str)
                self.assertTrue(
                    resp.startswith("Final Answer:"),
                    f"Payload '{desc}' failed to produce 'Final Answer:' prefix",
                )
                self.assertTrue(len(resp) > 0)

    def test_adv_06_heterogeneous_non_string_inputs(self):
        """Verify handling of non-string inputs across invoke, call, callable, and predict interfaces."""
        class DummyContentObject:
            def __init__(self, content):
                self.content = content

        hetero_inputs = [
            ("integer", 12345),
            ("float", 99.99),
            ("list_of_strings", ["Architect", "database design"]),
            ("dict_input", {"role": "Designer", "query": "UI tokens"}),
            ("content_object", DummyContentObject("Tester: pytest coverage")),
            ("none_value", None),
        ]

        for desc, val in hetero_inputs:
            with self.subTest(input_type=desc):
                # 1. invoke() applies _extract_prompt_text and succeeds
                res_invoke = self.llm.invoke(val)
                self.assertTrue(res_invoke.content.startswith("Final Answer:"))

                # 2. call() applies _extract_prompt_text and succeeds
                res_call = self.llm.call(val)
                self.assertTrue(res_call.startswith("Final Answer:"))

                # 3. direct callable applies _extract_prompt_text and succeeds
                res_callable = self.llm(val)
                content = getattr(res_callable, "content", res_callable)
                self.assertTrue(str(content).startswith("Final Answer:"))

                # 4. predict() expects str and does NOT call _extract_prompt_text.
                # Passing non-string directly to predict() causes AttributeError in _synthesize_response.
                with self.assertRaises(AttributeError):
                    self.llm.predict(val)

    def test_adv_07_react_observation_variations(self):
        """Verify observation handling across casing and formatting permutations."""
        observation_cases = [
            "Observation: Tool output received successfully.",
            "observation: tool output received successfully.",
            "OBSERVATION: TOOL OUTPUT RECEIVED.",
            "Thought: Thinking...\nAction: test\nObservation: DB written\nThought: done",
        ]
        for obs_prompt in observation_cases:
            resp = self.llm.predict(obs_prompt)
            self.assertEqual(
                resp,
                "Final Answer: Observation received and processed successfully. All criteria verified.",
            )


class TestDynamicProjectContextExtraction(unittest.TestCase):
    """Category 2: Empirical stress tests for dynamic project context extraction from --- CURRENT PROJECT CONTEXT ---."""

    def setUp(self):
        self.llm = DeterministicMockLLM()

    def test_adv_08_arbitrary_project_title_and_milestone_extraction(self):
        """Verify that arbitrary project names and milestones are dynamically extracted and reflected in responses."""
        test_matrix = [
            ("Alpha-Omega_Core (Mode: strict)", "M1: Foundation [100%] (Status: done)"),
            ("SuperSecretProject 9000", "Sprint 42 - Quantum Security"),
            ("HyperDrive Platform v3.1.2-alpha", "Milestone 99: Final Production Sign-off"),
        ]

        for proj, ms in test_matrix:
            prompt = (
                f"Coordinator: What is the current project status?\n"
                f"--- CURRENT PROJECT CONTEXT ---\n"
                f"Project: {proj}\n"
                f"Current Active Milestone: {ms}\n"
            )
            resp = self.llm.predict(prompt)
            self.assertIn("Final Answer:", resp)
            self.assertIn("[Grounded State Context]:", resp)

            clean_proj = re.split(r"[\n(]", proj)[0].strip()
            clean_ms = re.split(r"[\n(]", ms)[0].strip()
            self.assertIn(clean_proj, resp)
            self.assertIn(clean_ms, resp)

    def test_adv_09_decision_text_extraction_audit(self):
        """Audit whether decision texts in --- CURRENT PROJECT CONTEXT --- are reflected in generated outputs."""
        prompt = (
            "Coordinator: What decisions were made?\n"
            "--- CURRENT PROJECT CONTEXT ---\n"
            "Project: Enterprise Security Framework (Mode: strict)\n"
            "Current Active Milestone: Milestone 3 (Status: in_progress)\n"
            "Relevant Recorded Decisions:\n"
            "- [ADR-042] (accepted) Use Argon2id for password hashing\n"
            "- [ADR-043] (accepted) Adopt AES-256-GCM for payload encryption\n"
        )
        resp = self.llm.predict(prompt)
        self.assertIn("[Grounded State Context]:", resp)
        self.assertIn("Enterprise Security Framework", resp)
        self.assertIn("Milestone 3", resp)

        # Empirical check: DeterministicMockLLM._extract_grounded_context currently captures
        # Project and Current Active Milestone. Decision lines are not parsed into [Grounded State Context].
        has_adr_42 = "Argon2id" in resp
        has_adr_43 = "AES-256-GCM" in resp
        # Document empirical observation
        self.assertFalse(
            has_adr_42 and has_adr_43,
            "Decision texts are currently not extracted into [Grounded State Context] by _extract_grounded_context",
        )

    def test_adv_10_context_injection_without_role_keyword_fallback(self):
        """Verify behavior when --- CURRENT PROJECT CONTEXT --- is injected into a prompt without role keywords."""
        prompt_no_role = (
            "What is the system latency under 1000 QPS?\n"
            "--- CURRENT PROJECT CONTEXT ---\n"
            "Project: Apollo Platform (Mode: strict)\n"
            "Current Active Milestone: Performance Optimization\n"
        )
        resp = self.llm.predict(prompt_no_role)
        # When no role keywords match, execution falls through to SHA-256 fallback synthesis
        self.assertTrue(resp.startswith("Final Answer: Deterministic response [hash="))
        # Context grounding is attached only when a canonical role is matched
        self.assertNotIn("[Grounded State Context]:", resp)

    def test_adv_11_malformed_and_partial_context_headers(self):
        """Verify resilience to empty, incomplete, or corrupted context sections."""
        # 1. Context header present but no project or milestone fields
        prompt_empty_ctx = (
            "Architect: specify the relational schema\n"
            "--- CURRENT PROJECT CONTEXT ---\n"
            "Some unstructured notes without standard fields.\n"
        )
        resp1 = self.llm.predict(prompt_empty_ctx)
        self.assertIn("[Grounded State Context]: Active database state verified.", resp1)

        # 2. Lowercase context header
        prompt_lower_ctx = (
            "Architect: specify the relational schema\n"
            "--- current project context ---\n"
            "Project: Delta System\n"
        )
        resp2 = self.llm.predict(prompt_lower_ctx)
        self.assertIn("Project='Delta System'", resp2)


class TestCannedResponsesFIFOAndFallback(unittest.TestCase):
    """Category 3: Empirical stress tests for canned response FIFO queuing, exhaustion fallback, and reset."""

    def test_adv_12_strict_fifo_ordering(self):
        """Verify canned responses are popped strictly in first-in, first-out order."""
        seed_responses = [f"Canned Response #{i}" for i in range(1, 6)]
        llm = DeterministicMockLLM(canned_responses=list(seed_responses))

        for expected in seed_responses:
            actual = llm.predict("Any arbitrary prompt")
            self.assertEqual(actual, expected)

    def test_adv_13_exhaustion_seamless_fallback_to_deterministic_synthesis(self):
        """Verify that once canned responses are exhausted, subsequent calls fall back to deterministic synthesis."""
        llm = DeterministicMockLLM(canned_responses=["Sole Canned Response"])

        # Call 1: returns canned response
        r1 = llm.predict("Architect: explain schema")
        self.assertEqual(r1, "Sole Canned Response")

        # Call 2: exhausted -> falls back to Architect canonical response
        r2 = llm.predict("Architect: explain schema")
        self.assertIn("System Architecture & Database Blueprint", r2)
        self.assertTrue(r2.startswith("Final Answer:"))

        # Call 3: prompt without role keyword -> falls back to SHA-256 hash
        r3 = llm.predict("unmatched query")
        self.assertTrue(r3.startswith("Final Answer: Deterministic response [hash="))

    def test_adv_14_reset_clears_queue_and_history(self):
        """Verify reset() empties both the canned response queue and invocation history."""
        llm = DeterministicMockLLM(canned_responses=["Unconsumed 1", "Unconsumed 2"])
        llm.predict("First call")
        self.assertEqual(len(llm.call_history), 1)
        self.assertEqual(len(llm.canned_responses), 1)

        llm.reset()
        self.assertEqual(len(llm.call_history), 0)
        self.assertEqual(len(llm.canned_responses), 0)

        # Immediate next call must not return canned response
        resp = llm.predict("Architect: db schema")
        self.assertIn("System Architecture & Database Blueprint", resp)

    def test_adv_15_interleaved_method_calls_across_fifo_queue(self):
        """Verify FIFO consumption across diverse calling conventions: predict, invoke, call, callable."""
        llm = DeterministicMockLLM(canned_responses=["R_predict", "R_invoke", "R_call", "R_callable"])

        # 1. predict
        out1 = llm.predict("p1")
        self.assertEqual(out1, "R_predict")

        # 2. invoke (returns AIMessage)
        out2 = llm.invoke("p2")
        self.assertEqual(out2.content, "R_invoke")

        # 3. call
        out3 = llm.call("p3")
        self.assertEqual(out3, "R_call")

        # 4. callable
        out4 = llm("p4")
        self.assertEqual(out4, "R_callable")

        # 5. exhausted
        out5 = llm.predict("Developer: coding")
        self.assertIn("Implementation Summary", out5)

    def test_adv_16_batch_generate_canned_behavior(self):
        """Verify behavior of generate() with canned responses."""
        llm = DeterministicMockLLM(canned_responses=["Batch Result"])
        chat_res = llm.generate(["Prompt A", "Prompt B"])
        self.assertEqual(len(chat_res.generations), 1)
        self.assertEqual(chat_res.generations[0].text, "Batch Result")
        self.assertEqual(len(llm.canned_responses), 0)

    def test_adv_16b_concurrent_canned_consumption(self):
        """Verify thread-safety of concurrent canned response popping and exhaustion."""
        import threading

        seed_count = 20
        thread_count = 40
        llm = DeterministicMockLLM(canned_responses=[f"Canned_{i}" for i in range(seed_count)])

        results: List[str] = []
        errors: List[Exception] = []
        lock = threading.Lock()

        def worker():
            try:
                res = llm.predict("Architect: db schema")
                with lock:
                    results.append(res)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(thread_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Thread errors encountered: {errors}")
        self.assertEqual(len(results), thread_count)
        self.assertEqual(len(llm.call_history), thread_count)

        # First 20 results must be the canned responses
        canned_hits = [r for r in results if r.startswith("Canned_")]
        fallback_hits = [r for r in results if "System Architecture & Database Blueprint" in r]
        self.assertEqual(len(canned_hits), seed_count)
        self.assertEqual(len(fallback_hits), thread_count - seed_count)

    def test_adv_16c_noop_method_chaining(self):
        """Verify bind, bind_tools, and with_structured_output return self and support fluent chaining."""
        llm = DeterministicMockLLM()
        chained = llm.bind(stop=["\n"]).bind_tools(["tool_a", "tool_b"]).with_structured_output(dict)
        self.assertIs(chained, llm)


class TestLLMFactoryPermutationsAndEnvVars(unittest.TestCase):
    """Category 4: Empirical stress tests for LLMFactory environment variables and provider permutations."""

    def setUp(self):
        self.orig_env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.orig_env)

    def test_adv_17_mock_llm_flag_permutations(self):
        """Verify MOCK_LLM environment variable permutations force DeterministicMockLLM."""
        truthy_permutations = ["true", "True", "TRUE", "1", "yes", "Yes", "YES"]

        for val in truthy_permutations:
            os.environ["MOCK_LLM"] = val
            # Set a fake live API key that would normally raise or attempt connection
            os.environ["OPENAI_API_KEY"] = "sk-live-should-be-ignored"

            llm = get_llm()
            self.assertIsInstance(
                llm,
                DeterministicMockLLM,
                f"Failed to force DeterministicMockLLM with MOCK_LLM='{val}'",
            )
            self.assertEqual(llm.model_name, "deterministic-mock-llm")

    def test_adv_18_crewai_mock_llm_flag_permutations(self):
        """Verify CREWAI_MOCK_LLM environment variable permutations force DeterministicMockLLM."""
        os.environ.pop("MOCK_LLM", None)
        truthy_permutations = ["true", "1", "yes"]

        for val in truthy_permutations:
            os.environ["CREWAI_MOCK_LLM"] = val
            llm = get_llm()
            self.assertIsInstance(
                llm,
                DeterministicMockLLM,
                f"Failed to force DeterministicMockLLM with CREWAI_MOCK_LLM='{val}'",
            )

    def test_adv_19_missing_api_keys_for_explicit_live_providers(self):
        """Verify requesting live providers without credentials raises descriptive ValueError."""
        # Ensure MOCK flags and keys are cleared
        for k in ["MOCK_LLM", "CREWAI_MOCK_LLM", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"]:
            os.environ.pop(k, None)

        provider_key_checks = [
            ("openai", "OPENAI_API_KEY environment variable is required"),
            ("gemini", "GEMINI_API_KEY or GOOGLE_API_KEY environment variable is required"),
            ("google", "GEMINI_API_KEY or GOOGLE_API_KEY environment variable is required"),
            ("anthropic", "ANTHROPIC_API_KEY environment variable is required"),
        ]

        for prov, expected_err in provider_key_checks:
            with self.subTest(provider=prov):
                with self.assertRaises(ValueError) as ctx:
                    get_llm(provider=prov)
                self.assertIn(expected_err, str(ctx.exception))

    def test_adv_20_empty_string_api_keys_rejected(self):
        """Verify setting an empty string API key also raises ValueError for live providers."""
        for k in ["MOCK_LLM", "CREWAI_MOCK_LLM"]:
            os.environ.pop(k, None)

        os.environ["OPENAI_API_KEY"] = ""
        with self.assertRaises(ValueError) as ctx:
            get_llm(provider="openai")
        self.assertIn("OPENAI_API_KEY", str(ctx.exception))

        os.environ["ANTHROPIC_API_KEY"] = ""
        with self.assertRaises(ValueError) as ctx:
            get_llm(provider="anthropic")
        self.assertIn("ANTHROPIC_API_KEY", str(ctx.exception))

    def test_adv_21_invalid_and_unsupported_provider_names(self):
        """Verify unsupported or malformed provider strings raise descriptive ValueError."""
        invalid_providers = [
            "unsupported_quantum_provider",
            "cohere",
            "deepseek_nonexistent",
            "   ",  # whitespace only string
        ]

        for bad_prov in invalid_providers:
            with self.subTest(provider=repr(bad_prov)):
                with self.assertRaises(ValueError) as ctx:
                    get_llm(provider=bad_prov)
                self.assertIn("Unsupported LLM provider", str(ctx.exception))

    def test_adv_22_provider_casing_and_whitespace_tolerance(self):
        """Verify provider name normalization handles mixed case and whitespace."""
        for prov_str in [" MOCK ", "deterministic ", "  OFFLINE", "Testing"]:
            llm = get_llm(provider=prov_str)
            self.assertIsInstance(llm, DeterministicMockLLM)

    def test_adv_23_offline_auto_detection_fallback(self):
        """Verify default offline fallback when provider is None and no API keys exist."""
        for k in ["MOCK_LLM", "CREWAI_MOCK_LLM", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"]:
            os.environ.pop(k, None)

        llm = get_llm()
        self.assertIsInstance(llm, DeterministicMockLLM)
        self.assertEqual(llm.model_name, "deterministic-mock-llm")
        self.assertEqual(llm.temperature, 0.0)


class TestRoleDefinitionContractsAndIntegrity(unittest.TestCase):
    """Category 5: Empirical stress tests for RoleDefinition contracts, mutability, and constraints."""

    def test_adv_24_role_definition_mutability_singleton_hazard(self):
        """Empirically test whether get_role_definition returns a shared mutable dictionary reference."""
        # Retrieve role definition for Developer
        dev_def1 = get_role_definition("Developer")
        dev_def2 = get_role_definition("Developer")
        self.assertIs(dev_def1, dev_def2, "get_role_definition returns shared singleton reference")

        # Verify initial invariant
        self.assertFalse(dev_def1.allow_delegation)

        # Mutate the dictionary
        dev_def1["allow_delegation"] = True
        try:
            self.assertTrue(
                dev_def2.allow_delegation,
                "Mutating a returned RoleDefinition mutates subsequent references in process runtime",
            )
        finally:
            # Restore canonical invariant
            dev_def1["allow_delegation"] = False

    def test_adv_25_unknown_role_error_exception_hierarchy(self):
        """Verify UnknownRoleError subclasses both KeyError and ValueError."""
        self.assertTrue(issubclass(UnknownRoleError, KeyError))
        self.assertTrue(issubclass(UnknownRoleError, ValueError))

        # Test catching via KeyError
        caught_key = False
        try:
            get_role_definition("non_existent_role_xyz")
        except KeyError:
            caught_key = True
        self.assertTrue(caught_key, "UnknownRoleError must be catchable as KeyError")

        # Test catching via ValueError
        caught_val = False
        try:
            get_role_definition("non_existent_role_xyz")
        except ValueError:
            caught_val = True
        self.assertTrue(caught_val, "UnknownRoleError must be catchable as ValueError")

    def test_adv_26_strict_delegation_invariants_across_all_roles(self):
        """Verify that across all 8 canonical roles, Coordinator is the ONLY role with allow_delegation=True."""
        for role in CANONICAL_ROLES:
            role_def = get_role_definition(role)
            if role == "Coordinator":
                self.assertTrue(role_def.allow_delegation)
                self.assertTrue(role_def.delegation_allowed)
                self.assertTrue(role_def["allow_delegation"])
            else:
                self.assertFalse(
                    role_def.allow_delegation,
                    f"Specialist role '{role}' must strictly have allow_delegation=False",
                )
                self.assertFalse(role_def.delegation_allowed)
                self.assertFalse(role_def["allow_delegation"])

    def test_adv_27_role_tool_names_completeness(self):
        """Verify each of the 8 canonical roles has assigned functional tools."""
        for role in CANONICAL_ROLES:
            tools = get_role_tool_names(role)
            self.assertIsInstance(tools, list)
            self.assertTrue(len(tools) >= 2, f"Role '{role}' must have at least 2 assigned tools")
            for t_name in tools:
                self.assertIsInstance(t_name, str)
                self.assertTrue(len(t_name) > 0)


if __name__ == "__main__":
    unittest.main()
