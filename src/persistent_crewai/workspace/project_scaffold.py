"""
Physical Project Workspace Scaffolding and File Management for Persistent CrewAI.
Guarantees atomic multi-file synthesis, directory structure generation, and production manifests.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


DEFAULT_PROJECTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "projects"


class ProjectScaffold:
    """
    Manages physical project directories on disk under the projects/ root.
    Provides atomic file writes, line-sliced reads, safe patch replacements,
    and boilerplate generation for deployment-ready applications.
    """

    def __init__(self, project_slug: str, base_dir: Optional[Union[str, Path]] = None) -> None:
        self.slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", project_slug.strip().lower()) or "default_app"
        self.base_dir = Path(base_dir).resolve() if base_dir else DEFAULT_PROJECTS_DIR.resolve()
        self.root_path = self.base_dir / self.slug
        self.root_path.mkdir(parents=True, exist_ok=True)

    def initialize_standard_structure(
        self,
        app_name: str,
        description: str = "",
        tech_stack: str = "fastapi",
    ) -> Dict[str, str]:
        """
        Creates directory skeleton and essential production configuration files.
        """
        created = {}

        # Standard subdirectories
        (self.root_path / "src").mkdir(exist_ok=True)
        (self.root_path / "tests").mkdir(exist_ok=True)
        (self.root_path / "docs").mkdir(exist_ok=True)

        # 1. .env.example
        env_example = (
            f"# Environment Variables for {app_name}\n"
            "ENVIRONMENT=development\n"
            "PORT=8080\n"
            "LOG_LEVEL=info\n"
            "DATABASE_URL=sqlite:///./app.db\n"
            "SECRET_KEY=change_this_to_a_secure_random_key_in_production\n"
        )
        self.write_file(".env.example", env_example)
        created[".env.example"] = env_example

        # 2. .gitignore
        gitignore = (
            "__pycache__/\n"
            "*.py[cod]\n"
            "*.so\n"
            ".env\n"
            ".venv/\n"
            "env/\n"
            "dist/\n"
            "build/\n"
            "*.egg-info/\n"
            ".pytest_cache/\n"
            ".coverage\n"
            "htmlcov/\n"
            "*.db\n"
            "*.sqlite\n"
            "node_modules/\n"
            ".DS_Store\n"
        )
        self.write_file(".gitignore", gitignore)
        created[".gitignore"] = gitignore

        # 3. Multi-stage Dockerfile
        dockerfile = (
            "# Multi-stage production Dockerfile\n"
            "FROM python:3.11-slim AS builder\n"
            "WORKDIR /app\n"
            "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1\n"
            "RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*\n"
            "COPY requirements.txt .\n"
            "RUN pip install --no-cache-dir --user -r requirements.txt\n"
            "\n"
            "FROM python:3.11-slim AS runner\n"
            "WORKDIR /app\n"
            "ENV PATH=/root/.local/bin:$PATH\n"
            "COPY --from=builder /root/.local /root/.local\n"
            "COPY . .\n"
            "EXPOSE 8080\n"
            "HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \\\n"
            "    CMD python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8080/health')\" || exit 1\n"
            "CMD [\"python\", \"-m\", \"uvicorn\", \"src.main:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8080\"]\n"
        )
        self.write_file("Dockerfile", dockerfile)
        created["Dockerfile"] = dockerfile

        # 4. docker-compose.yml
        compose = (
            "version: '3.8'\n"
            "services:\n"
            f"  {self.slug}:\n"
            "    build: .\n"
            "    ports:\n"
            "      - \"8080:8080\"\n"
            "    environment:\n"
            "      - ENVIRONMENT=production\n"
            "      - PORT=8080\n"
            "    restart: unless-stopped\n"
        )
        self.write_file("docker-compose.yml", compose)
        created["docker-compose.yml"] = compose

        # 5. README.md
        readme = (
            f"# {app_name}\n\n"
            f"{description}\n\n"
            "## Architecture & Quickstart\n"
            "- **Stack:** " + tech_stack + "\n"
            "- **Port:** 8080\n"
            "- **Health check:** `GET /health`\n\n"
            "### Running Locally\n"
            "```bash\n"
            "pip install -r requirements.txt\n"
            "python -m pytest tests/ -v\n"
            "uvicorn src.main:app --reload --port 8080\n"
            "```\n\n"
            "### Running with Docker\n"
            "```bash\n"
            "docker compose up --build\n"
            "```\n"
        )
        self.write_file("README.md", readme)
        created["README.md"] = readme

        return created

    def _resolve_safe_path(self, relative_path: str) -> Path:
        """Ensure path does not escape the project root."""
        clean = Path(relative_path).as_posix().lstrip("/\\")
        target = (self.root_path / clean).resolve()
        if not target.is_relative_to(self.root_path):
            raise PermissionError(f"Security violation: path escapes project root: {relative_path}")
        return target

    def write_file(self, relative_path: str, content: str) -> str:
        """Atomically write file to project workspace."""
        target = self._resolve_safe_path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        temp_dir = target.parent
        with tempfile.NamedTemporaryFile("w", dir=temp_dir, delete=False, encoding="utf-8") as tf:
            tf.write(content)
            temp_name = tf.name

        os.replace(temp_name, target)
        return str(target)

    def read_file(self, relative_path: str, start_line: int = 1, end_line: Optional[int] = None) -> str:
        """Read lines from a file in the project workspace."""
        target = self._resolve_safe_path(relative_path)
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(f"File not found: {relative_path}")

        lines = target.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        s_idx = max(0, start_line - 1)
        e_idx = end_line if end_line is not None else len(lines)
        return "".join(lines[s_idx:e_idx])

    def patch_file(self, relative_path: str, target_block: str, replacement_block: str) -> bool:
        """Exact search-and-replace block patch in a workspace file."""
        target = self._resolve_safe_path(relative_path)
        if not target.exists():
            raise FileNotFoundError(f"File not found to patch: {relative_path}")

        text = target.read_text(encoding="utf-8")
        if target_block not in text:
            raise ValueError(f"Target block to patch not found in {relative_path}")

        new_text = text.replace(target_block, replacement_block, 1)
        self.write_file(relative_path, new_text)
        return True

    def list_files(self, glob_pattern: str = "**/*") -> List[str]:
        """List relative file paths in workspace matching glob (excluding .git)."""
        results = []
        for p in self.root_path.glob(glob_pattern):
            if p.is_file():
                rel = p.relative_to(self.root_path).as_posix()
                if not rel.startswith(".git/"):
                    results.append(rel)
        return sorted(results)
