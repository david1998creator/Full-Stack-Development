"""
Unit tests for Production-Ready Multi-Agent SWE Upgrades:
- Physical Project Workspace Scaffolding (ProjectScaffold)
- AST Symbol Extraction & Repo Mapping (ASTSymbolExtractor)
- Real Git Repo Automation (RealGitRepoManager)
- Subprocess Test Execution (SubprocessTestRunner)
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from persistent_crewai.workspace.project_scaffold import ProjectScaffold
from persistent_crewai.workspace.ast_mapper import ASTSymbolExtractor
from persistent_crewai.tools.git_tool import RealGitRepoManager
from persistent_crewai.tools.test_runner_tools import SubprocessTestRunner


class TestProductionUpgrades(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_crewai_prod_")
        self.scaffold = ProjectScaffold("test_app", base_dir=self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_scaffold_initialization(self):
        created = self.scaffold.initialize_standard_structure("Test App", "Description")
        self.assertIn(".env.example", created)
        self.assertIn("Dockerfile", created)
        self.assertIn("docker-compose.yml", created)
        self.assertIn("README.md", created)

        files = self.scaffold.list_files()
        self.assertIn(".env.example", files)
        self.assertIn("Dockerfile", files)

    def test_atomic_write_and_patch(self):
        self.scaffold.write_file("src/hello.py", "def greet():\n    return 'hello'\n")
        content = self.scaffold.read_file("src/hello.py")
        self.assertIn("def greet()", content)

        self.scaffold.patch_file("src/hello.py", "'hello'", "'world'")
        new_content = self.scaffold.read_file("src/hello.py")
        self.assertIn("return 'world'", new_content)

    def test_ast_symbol_extractor(self):
        py_code = (
            "class Calculator:\n"
            "    '''Performs math.'''\n"
            "    def add(self, a: int, b: int) -> int:\n"
            "        return a + b\n\n"
            "def multiply(x: int, y: int) -> int:\n"
            "    '''Multiplies two numbers.'''\n"
            "    return x * y\n"
        )
        self.scaffold.write_file("src/calc.py", py_code)
        extractor = ASTSymbolExtractor(self.scaffold.root_path)
        repo_map = extractor.generate_repo_map()

        self.assertIn("class Calculator", repo_map)
        self.assertIn("def add(self, a: int, b: int) -> int", repo_map)
        self.assertIn("def multiply(x: int, y: int) -> int", repo_map)

    def test_real_git_manager(self):
        git_mgr = RealGitRepoManager(self.scaffold.root_path)
        init_res = git_mgr.init_repo()
        self.assertTrue(init_res.success)

        self.scaffold.write_file("test.txt", "sample text")
        commit_res = git_mgr.commit_all("feat: test commit")
        self.assertTrue(commit_res.success)

        log_res = git_mgr.get_log(limit=1)
        self.assertTrue(log_res.success)
        self.assertIn("feat: test commit", log_res.output)

    def test_subprocess_test_runner(self):
        self.scaffold.write_file(
            "tests/test_sample.py",
            "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n"
        )
        runner = SubprocessTestRunner(self.scaffold.root_path)
        res = runner.run_tests()
        self.assertTrue(res.success)
        self.assertEqual(res.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
