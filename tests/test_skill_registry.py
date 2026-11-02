"""
Unit and integration tests for Skill and Plugin Registry, SQLite persistence,
prompt injection, and UI API endpoints.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from persistent_crewai.persistence.db import DatabaseManager
from persistent_crewai.persistence.state_manager import StateManager
from persistent_crewai.agents.registry import SkillPluginRegistry
from persistent_crewai.config import CANONICAL_ROLES
import ui.server as ui_server


class TestSkillPluginRegistry(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_skill_reg_")
        self.workspace_root = Path(self.temp_dir)
        self.db_path = self.workspace_root / "state.db"
        DatabaseManager(self.db_path).initialize_schema()

        self.state_mgr = StateManager(db_path=self.db_path, workspace_root=self.workspace_root)
        self.registry = SkillPluginRegistry(workspace_root=self.workspace_root, state_mgr=self.state_mgr)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_all_canonical_roles_manifests(self):
        manifests = self.registry.get_all_crew_manifests(project_id="test_proj")
        self.assertEqual(len(manifests), 10)
        roles = [m["role"] for m in manifests]
        self.assertIn("Coordinator", roles)
        self.assertIn("Developer", roles)
        self.assertIn("Tester", roles)
        self.assertIn("Database Engineer", roles)

    def test_builtin_and_custom_skills_discovery(self):
        # Create a custom skill in workspace
        custom_dir = self.workspace_root / "custom_skills" / "sample_skill"
        custom_dir.mkdir(parents=True, exist_ok=True)
        (custom_dir / "SKILL.md").write_text(
            "---\nname: sample_skill\ntarget_roles: [\"Developer\"]\ndescription: Sample testing skill\n---\n# Sample Guide\nUse clean code.",
            encoding="utf-8"
        )

        manifest = self.registry.get_agent_manifest("Developer", project_id="test_proj")
        self.assertEqual(manifest["role"], "Developer")
        skill_names = [s["name"] for s in manifest["skills"]]
        self.assertIn("sample_skill", skill_names)
        self.assertIn("fullstack_implementation", skill_names)

    def test_skill_persistence_and_toggling(self):
        # Default state should be enabled (True)
        manifest = self.registry.get_agent_manifest("Developer", project_id="test_proj")
        impl_skill = next((s for s in manifest["skills"] if s["name"] == "fullstack_implementation"), None)
        self.assertIsNotNone(impl_skill)
        self.assertTrue(impl_skill["enabled"])

        # Toggle skill off
        self.state_mgr.set_agent_skill(agent_role="Developer", skill_name="fullstack_implementation", enabled=False, project_id="test_proj")
        skills_state = self.state_mgr.get_agent_skills(project_id="test_proj", agent_role="Developer")
        skills_map = {s["skill_name"]: bool(s["enabled"]) for s in skills_state}
        self.assertFalse(skills_map.get("fullstack_implementation"))

        # Re-fetch manifest and verify it reflects disabled state
        manifest_updated = self.registry.get_agent_manifest("Developer", project_id="test_proj")
        skill_updated = next(s for s in manifest_updated["skills"] if s["name"] == "fullstack_implementation")
        self.assertFalse(skill_updated["enabled"])

    def test_plugin_persistence_and_toggling(self):
        self.state_mgr.set_agent_plugin(agent_role="Developer", plugin_name="web_search", enabled=False, project_id="test_proj")
        plugin_state = self.state_mgr.get_agent_plugins(project_id="test_proj", agent_role="Developer")
        plugin_map = {p["plugin_name"]: bool(p["enabled"]) for p in plugin_state}
        self.assertFalse(plugin_map.get("web_search"))

        # Toggle back on
        self.state_mgr.set_agent_plugin(agent_role="Developer", plugin_name="web_search", enabled=True, project_id="test_proj")
        plugin_state_on = self.state_mgr.get_agent_plugins(project_id="test_proj", agent_role="Developer")
        plugin_map_on = {p["plugin_name"]: bool(p["enabled"]) for p in plugin_state_on}
        self.assertTrue(plugin_map_on.get("web_search"))

    def test_prompt_injection(self):
        # Initially enabled
        prompt_snippet = self.registry.get_injected_prompt_skills("Developer", project_id="test_proj")
        self.assertIn("SPECIALIST SKILL GUIDELINES", prompt_snippet)
        self.assertIn("fullstack_implementation", prompt_snippet)

        # Disable all skills for Developer
        for skill in self.registry.get_agent_manifest("Developer", project_id="test_proj")["skills"]:
            self.state_mgr.set_agent_skill(agent_role="Developer", skill_name=skill["name"], enabled=False, project_id="test_proj")

        prompt_snippet_disabled = self.registry.get_injected_prompt_skills("Developer", project_id="test_proj")
        self.assertEqual(prompt_snippet_disabled, "")

    def test_save_custom_skill(self):
        skill_meta = self.registry.save_custom_skill(
            target_role="Database Engineer",
            name="redis_caching",
            description="Production Redis caching patterns",
            instructions="# Redis Patterns\nAlways set TTL and handle cache stampedes."
        )
        self.assertEqual(skill_meta["name"], "redis_caching")
        self.assertTrue(Path(skill_meta["path"]).exists())

        # Check manifest
        db_manifest = self.registry.get_agent_manifest("Database Engineer", project_id="test_proj")
        names = [s["name"] for s in db_manifest["skills"]]
        self.assertIn("redis_caching", names)

    def test_custom_prompt_persistence_and_reset(self):
        # Default state
        manifest = self.registry.get_agent_manifest("Developer", project_id="test_proj")
        self.assertFalse(manifest["system_config"]["is_custom_prompt"])
        self.assertIn("=== DEVELOPER AGENT SYSTEM PROMPT ===", manifest["system_config"]["system_prompt"])

        # Override prompt
        custom_txt = "=== SPECIAL DEVELOPER DIRECTIVES ===\nPrioritize clean architecture."
        self.state_mgr.set_agent_custom_prompt("Developer", custom_txt, project_id="test_proj")

        stored = self.state_mgr.get_agent_custom_prompt("Developer", project_id="test_proj")
        self.assertEqual(stored, custom_txt)

        # Re-fetch manifest
        updated_manifest = self.registry.get_agent_manifest("Developer", project_id="test_proj")
        self.assertTrue(updated_manifest["system_config"]["is_custom_prompt"])
        self.assertEqual(updated_manifest["system_config"]["system_prompt"], custom_txt)

        # Reset prompt
        self.state_mgr.reset_agent_custom_prompt("Developer", project_id="test_proj")
        self.assertIsNone(self.state_mgr.get_agent_custom_prompt("Developer", project_id="test_proj"))

        reset_manifest = self.registry.get_agent_manifest("Developer", project_id="test_proj")
        self.assertFalse(reset_manifest["system_config"]["is_custom_prompt"])
        self.assertIn("=== DEVELOPER AGENT SYSTEM PROMPT ===", reset_manifest["system_config"]["system_prompt"])


class TestCrewApiServer(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_ui_api_")
        self.workspace_root = Path(self.temp_dir)
        self.db_path = self.workspace_root / "state.db"
        DatabaseManager(self.db_path).initialize_schema()

        self.state_mgr = StateManager(db_path=self.db_path, workspace_root=self.workspace_root)

        # Patch server state_mgr and workspace_root
        self.orig_state_mgr = ui_server.state_mgr
        self.orig_workspace_root = ui_server.workspace_root
        ui_server.state_mgr = self.state_mgr
        ui_server.workspace_root = self.workspace_root

        self.app = ui_server.app
        self.app.testing = True
        self.client = self.app.test_client()

    def tearDown(self):
        ui_server.state_mgr = self.orig_state_mgr
        ui_server.workspace_root = self.orig_workspace_root
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_crew_api(self):
        res = self.client.get("/api/crew")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        self.assertIn("crew", data)
        self.assertEqual(len(data["crew"]), 10)
        self.assertIn("total_skills", data)
        self.assertIn("total_plugins", data)

    def test_toggle_skill_api(self):
        res = self.client.post(
            "/api/crew/skill/toggle",
            data=json.dumps({"agent_role": "Developer", "skill_name": "fullstack_implementation", "enabled": False}),
            content_type="application/json"
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "ok")
        self.assertFalse(data["enabled"])

        # Verify via GET
        get_res = self.client.get("/api/crew")
        crew = get_res.get_json()["crew"]
        dev = next(a for a in crew if a["role"] == "Developer")
        skill = next(s for s in dev["skills"] if s["name"] == "fullstack_implementation")
        self.assertFalse(skill["enabled"])

    def test_toggle_plugin_api(self):
        res = self.client.post(
            "/api/crew/plugin/toggle",
            data=json.dumps({"agent_role": "Developer", "plugin_name": "web_search", "enabled": False}),
            content_type="application/json"
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "ok")
        self.assertFalse(data["enabled"])

    def test_create_custom_skill_api(self):
        res = self.client.post(
            "/api/crew/skill/create",
            data=json.dumps({
                "target_role": "Architect",
                "skill_name": "event_driven_architecture",
                "description": "Event-driven system design and Kafka message choreography",
                "content": "# Event-Driven Guidelines\nAlways ensure idempotency and schema registries."
            }),
            content_type="application/json"
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["skill_name"], "event_driven_architecture")

        # Verify skill appears in Architect manifest
        get_res = self.client.get("/api/crew")
        crew = get_res.get_json()["crew"]
        arch = next(a for a in crew if a["role"] == "Architect")
        names = [s["name"] for s in arch["skills"]]
        self.assertIn("event_driven_architecture", names)

    def test_update_and_reset_prompt_api(self):
        # 1. Update prompt
        custom_text = "=== CUSTOM DEVELOPER PROMPT ===\nWrite async code only."
        res = self.client.post(
            "/api/crew/prompt/update",
            data=json.dumps({"agent_role": "Developer", "custom_prompt": custom_text}),
            content_type="application/json"
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "ok")

        # Verify via GET /api/crew
        get_res = self.client.get("/api/crew")
        crew = get_res.get_json()["crew"]
        dev = next(a for a in crew if a["role"] == "Developer")
        self.assertTrue(dev["system_config"]["is_custom_prompt"])
        self.assertEqual(dev["system_config"]["system_prompt"], custom_text)

        # 2. Reset prompt
        reset_res = self.client.post(
            "/api/crew/prompt/reset",
            data=json.dumps({"agent_role": "Developer"}),
            content_type="application/json"
        )
        self.assertEqual(reset_res.status_code, 200)
        reset_data = reset_res.get_json()
        self.assertEqual(reset_data["status"], "ok")

        # Verify via GET /api/crew that it's back to canonical default
        get_res2 = self.client.get("/api/crew")
        crew2 = get_res2.get_json()["crew"]
        dev2 = next(a for a in crew2 if a["role"] == "Developer")
        self.assertFalse(dev2["system_config"]["is_custom_prompt"])
        self.assertIn("=== DEVELOPER AGENT SYSTEM PROMPT ===", dev2["system_config"]["system_prompt"])


if __name__ == "__main__":
    unittest.main()
