"""
Repository Mapping & AST Symbol Extraction for Persistent CrewAI.
Scans Python project files, extracts class and function signatures,
and constructs a compact codebase map for LLM context injection.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


class ASTSymbolExtractor:
    """
    Parses Python abstract syntax trees across a workspace
    to generate an accurate map of classes, methods, functions, and API endpoints.
    """

    def __init__(self, workspace_root: Union[str, Path]) -> None:
        self.workspace_root = Path(workspace_root).resolve()

    def parse_file(self, file_path: Path) -> Dict[str, Any]:
        """Extract symbol definitions from a single Python source file."""
        rel_path = file_path.relative_to(self.workspace_root).as_posix()
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(file_path))
        except Exception as e:
            return {"file": rel_path, "error": str(e), "classes": [], "functions": []}

        classes = []
        functions = []

        for node in tree.body:
            # Classes
            if isinstance(node, ast.ClassDef):
                methods = []
                for sub in node.body:
                    if isinstance(sub, ast.FunctionDef) or isinstance(sub, ast.AsyncFunctionDef):
                        methods.append(self._format_func_sig(sub))

                doc = ast.get_docstring(node)
                classes.append({
                    "name": node.name,
                    "doc": doc.splitlines()[0] if doc else "",
                    "methods": methods,
                })

            # Top-level functions & endpoints
            elif isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                doc = ast.get_docstring(node)
                decorators = [self._format_expr(d) for d in node.decorator_list]
                functions.append({
                    "name": node.name,
                    "signature": self._format_func_sig(node),
                    "doc": doc.splitlines()[0] if doc else "",
                    "decorators": decorators,
                })

        return {
            "file": rel_path,
            "classes": classes,
            "functions": functions,
        }

    def _format_func_sig(self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> str:
        """Format function arguments and return annotation."""
        args_str = []
        for a in node.args.args:
            name = a.arg
            ann = f": {self._format_expr(a.annotation)}" if a.annotation else ""
            args_str.append(f"{name}{ann}")

        ret = f" -> {self._format_expr(node.returns)}" if node.returns else ""
        async_prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
        return f"{async_prefix}def {node.name}({', '.join(args_str)}){ret}"

    def _format_expr(self, expr: Optional[ast.AST]) -> str:
        """Format AST expression as string."""
        if expr is None:
            return ""
        if hasattr(ast, "unparse"):
            try:
                return ast.unparse(expr)
            except Exception:
                pass
        if isinstance(expr, ast.Name):
            return expr.id
        elif isinstance(expr, ast.Attribute):
            return f"{self._format_expr(expr.value)}.{expr.attr}"
        elif isinstance(expr, ast.Constant):
            return repr(expr.value)
        return ""

    def parse_web_file(self, file_path: Path) -> Dict[str, Any]:
        """Extract high-level structure and exported symbols from JS/TS, HTML, or JSON files."""
        rel_path = file_path.relative_to(self.workspace_root).as_posix()
        suffix = file_path.suffix.lower()
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return {"file": rel_path, "type": suffix, "symbols": [], "error": str(e)}

        symbols = []
        if suffix in (".js", ".jsx", ".ts", ".tsx"):
            import re
            # Extract ES6 imports
            imports = re.findall(r"import\s+.*?from\s+['\"](.*?)['\"]", content)
            if imports:
                symbols.append(f"Imports: {', '.join(imports[:6])}")
            # Extract classes
            classes = re.findall(r"class\s+([A-Za-z0-9_]+)", content)
            for c in classes:
                symbols.append(f"class {c}")
            # Extract functions / arrow functions
            funcs = re.findall(r"(?:export\s+)?(?:async\s+)?function\s+([A-Za-z0-9_]+)\s*\((.*?)\)", content)
            for fn_name, fn_args in funcs[:8]:
                symbols.append(f"function {fn_name}({fn_args.strip()})")
            const_funcs = re.findall(r"(?:export\s+)?const\s+([A-Za-z0-9_]+)\s*=\s*(?:async\s*)?\((.*?)\)\s*=>", content)
            for fn_name, fn_args in const_funcs[:8]:
                symbols.append(f"const {fn_name} = ({fn_args.strip()}) => ...")

        elif suffix == ".html":
            import re
            title = re.search(r"<title>(.*?)</title>", content, re.IGNORECASE)
            if title:
                symbols.append(f"<title>: {title.group(1).strip()}")
            # Find key section / component IDs
            ids = re.findall(r"id=['\"]([a-zA-Z0-9_\-]+)['\"]", content)
            if ids:
                symbols.append(f"DOM Elements (IDs): {', '.join(ids[:10])}")
            # Detect script and link tags
            scripts = re.findall(r"<script.*?src=['\"](.*?)['\"]", content, re.IGNORECASE)
            if scripts:
                symbols.append(f"Scripts: {', '.join(scripts[:4])}")

        elif file_path.name in ("Dockerfile", "docker-compose.yml", "package.json", "pyproject.toml"):
            lines = [l.strip() for l in content.splitlines() if l.strip() and not l.strip().startswith("#")]
            symbols.append(f"Manifest ({file_path.name}): {len(lines)} lines")

        return {
            "file": rel_path,
            "type": suffix,
            "symbols": symbols,
        }

    def generate_repo_map(self, max_length: int = 5000) -> str:
        """
        Builds a compact summary of all source files in the project workspace (Python, JS/TS, HTML, Manifests).
        """
        py_files = sorted(self.workspace_root.glob("**/*.py"))
        web_files = sorted([
            f for f in self.workspace_root.glob("**/*")
            if f.is_file() and f.suffix.lower() in (".js", ".jsx", ".ts", ".tsx", ".html")
            or f.name in ("Dockerfile", "docker-compose.yml", "package.json")
        ])

        if not py_files and not web_files:
            return "[Empty repository: No source files detected]"

        lines = ["### Active Codebase Symbol Map (Multi-File Context)"]

        # 1. Process Python AST
        for pf in py_files:
            if any(part in pf.parts for part in (".venv", "venv", "__pycache__", ".pytest_cache", ".git")):
                continue
            parsed = self.parse_file(pf)
            if not parsed["classes"] and not parsed["functions"]:
                continue
            lines.append(f"\n[File: `{parsed['file']}` (Python)]")
            for c in parsed["classes"]:
                doc_str = f" - {c['doc']}" if c["doc"] else ""
                lines.append(f"  class {c['name']}:{doc_str}")
                for m in c["methods"]:
                    lines.append(f"    - {m}")
            for f in parsed["functions"]:
                decs = f" [{' ,'.join(f['decorators'])}]" if f["decorators"] else ""
                doc_str = f" - {f['doc']}" if f["doc"] else ""
                lines.append(f"  {f['signature']}{decs}{doc_str}")

        # 2. Process Web, HTML and Manifest files
        for wf in web_files:
            if any(part in wf.parts for part in ("node_modules", ".git", "dist", "build")):
                continue
            parsed_web = self.parse_web_file(wf)
            if not parsed_web.get("symbols"):
                continue
            lines.append(f"\n[File: `{parsed_web['file']}`]")
            for sym in parsed_web["symbols"]:
                lines.append(f"  • {sym}")

        text = "\n".join(lines)
        if len(text) > max_length:
            return text[:max_length] + "\n... [Codebase map truncated for context budget]"
        return text
