"""
Sandboxed workspace file operations for Persistent CrewAI.
Provides path-traversal containment, atomic file writes, line-sliced reads,
glob-filtered file listings, and exact-block search-and-replace patching.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from persistent_crewai.config import get_workspace_root

# Resilient BaseTool compatible with CrewAI and Pydantic v2
try:
    from crewai.tools import BaseTool
except (ImportError, Exception):
    try:
        from langchain_core.tools import BaseTool
    except (ImportError, Exception):
        class BaseTool(BaseModel):
            model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
            name: str = ""
            description: str = ""
            args_schema: Optional[type[BaseModel]] = None

            def run(self, *args: Any, **kwargs: Any) -> Any:
                return self._run(*args, **kwargs)

            def __call__(self, *args: Any, **kwargs: Any) -> Any:
                return self.run(*args, **kwargs)

            def _run(self, *args: Any, **kwargs: Any) -> Any:
                raise NotImplementedError


def validate_safe_path(target_path: Union[str, Path], workspace_root: Union[str, Path]) -> Path:
    """Validate that target_path resolves strictly within workspace_root.

    Args:
        target_path: Relative or absolute path requested by agent or caller.
        workspace_root: Base directory acting as security boundary.

    Returns:
        Canonical resolved Path guaranteed to be inside workspace_root.

    Raises:
        ValueError: If target_path is None, empty, whitespace, or contains null bytes.
        PermissionError: If target_path attempts to escape workspace_root.
    """
    if target_path is None:
        raise ValueError("Target path cannot be None")

    raw = str(target_path).strip()
    if not raw:
        raise ValueError("Target path cannot be empty or whitespace")

    if "\x00" in raw:
        raise ValueError("Target path cannot contain null bytes")

    root_resolved = Path(workspace_root).resolve()
    target_p = Path(raw)

    if target_p.is_absolute():
        resolved = target_p.resolve()
    else:
        resolved = (root_resolved / target_p).resolve()

    try:
        if not resolved.is_relative_to(root_resolved):
            raise PermissionError(
                f"Security violation: Access outside workspace root is forbidden. Target: {target_path}"
            )
    except AttributeError:
        try:
            resolved.relative_to(root_resolved)
        except ValueError:
            raise PermissionError(
                f"Security violation: Access outside workspace root is forbidden. Target: {target_path}"
            )

    return resolved


class FileReadInput(BaseModel):
    file_path: str = Field(..., description="Path to file within workspace")
    start_line: Optional[int] = Field(None, description="Optional 1-based start line")
    end_line: Optional[int] = Field(None, description="Optional 1-based end line")


class WorkspaceFileReadTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    name: str = "read_workspace_file"
    description: str = (
        "Read the content of a file in the workspace. Supports optional line slice with 1-based indexing."
    )
    args_schema: type[BaseModel] = FileReadInput
    workspace_root: Path = Field(default_factory=get_workspace_root)
    state_manager: Any = Field(default=None, exclude=True)

    def __init__(
        self,
        workspace_root: Optional[Union[str, Path]] = None,
        state_manager: Any = None,
        **kwargs: Any,
    ):
        root = Path(workspace_root).resolve() if workspace_root is not None else get_workspace_root()
        super().__init__(workspace_root=root, state_manager=state_manager, **kwargs)

    def read_file(
        self,
        file_path: Union[str, Path],
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> str:
        """Read content of file, with optional 1-based line slicing."""
        canonical = validate_safe_path(file_path, self.workspace_root)
        if not canonical.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        if canonical.is_dir():
            raise IsADirectoryError(f"Target path is a directory: {file_path}")

        content = canonical.read_text(encoding="utf-8", errors="replace")

        if start_line is not None or end_line is not None:
            lines = content.splitlines(keepends=True)
            s = start_line if start_line is not None else 1
            e = end_line if end_line is not None else len(lines)
            if s < 1:
                raise ValueError(f"start_line must be >= 1, got {s}")
            if e < s:
                raise ValueError(f"end_line ({e}) cannot be less than start_line ({s})")
            sliced_lines = lines[s - 1 : e]
            content = "".join(sliced_lines)

        if self.state_manager and hasattr(self.state_manager, "record_event"):
            try:
                self.state_manager.record_event(
                    "workspace_file_read",
                    {"file_path": str(file_path), "bytes": len(content.encode("utf-8"))},
                    tool_name=self.name,
                )
            except Exception:
                pass

        return content

    def _run(
        self,
        file_path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        **kwargs: Any,
    ) -> str:
        try:
            return self.read_file(file_path, start_line=start_line, end_line=end_line)
        except Exception as e:
            return f"Error: {e}"


class FileWriteInput(BaseModel):
    file_path: str = Field(..., description="Path to file to write")
    content: str = Field(..., description="Content to write")
    overwrite: bool = Field(True, description="Whether to overwrite existing file")


class WorkspaceFileWriteTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    name: str = "write_workspace_file"
    description: str = (
        "Write or create a file in the workspace. Automatically creates parent directories and uses atomic write."
    )
    args_schema: type[BaseModel] = FileWriteInput
    workspace_root: Path = Field(default_factory=get_workspace_root)
    state_manager: Any = Field(default=None, exclude=True)

    def __init__(
        self,
        workspace_root: Optional[Union[str, Path]] = None,
        state_manager: Any = None,
        **kwargs: Any,
    ):
        root = Path(workspace_root).resolve() if workspace_root is not None else get_workspace_root()
        super().__init__(workspace_root=root, state_manager=state_manager, **kwargs)

    def write_file(
        self,
        file_path: Union[str, Path],
        content: str,
        overwrite: bool = True,
    ) -> str:
        """Write content to file atomically."""
        canonical = validate_safe_path(file_path, self.workspace_root)
        if canonical.exists() and not overwrite:
            raise FileExistsError(f"File already exists and overwrite=False: {file_path}")

        canonical.parent.mkdir(parents=True, exist_ok=True)

        temp_file = tempfile.NamedTemporaryFile("w", dir=canonical.parent, delete=False, encoding="utf-8")
        try:
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_file.close()
            os.replace(temp_file.name, canonical)
        except Exception:
            if os.path.exists(temp_file.name):
                try:
                    os.remove(temp_file.name)
                except OSError:
                    pass
            raise

        bytes_written = len(content.encode("utf-8"))

        if self.state_manager and hasattr(self.state_manager, "record_event"):
            try:
                self.state_manager.record_event(
                    "workspace_file_write",
                    {"file_path": str(file_path), "bytes_written": bytes_written},
                    tool_name=self.name,
                )
            except Exception:
                pass

        return f"Successfully written {bytes_written} bytes to '{file_path}'."

    def _run(
        self,
        file_path: str,
        content: str,
        overwrite: bool = True,
        **kwargs: Any,
    ) -> str:
        try:
            return self.write_file(file_path, content, overwrite=overwrite)
        except Exception as e:
            return f"Error: {e}"


class FileListInput(BaseModel):
    directory_path: str = Field(".", description="Subdirectory to list")
    recursive: bool = Field(True, description="Whether to recurse into subdirectories")
    pattern: str = Field("*", description="Glob pattern filter")


class WorkspaceFileListTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    name: str = "list_workspace_files"
    description: str = "List files and subdirectories in the workspace."
    args_schema: type[BaseModel] = FileListInput
    workspace_root: Path = Field(default_factory=get_workspace_root)
    state_manager: Any = Field(default=None, exclude=True)

    def __init__(
        self,
        workspace_root: Optional[Union[str, Path]] = None,
        state_manager: Any = None,
        **kwargs: Any,
    ):
        root = Path(workspace_root).resolve() if workspace_root is not None else get_workspace_root()
        super().__init__(workspace_root=root, state_manager=state_manager, **kwargs)

    def list_files(
        self,
        directory: str = ".",
        pattern: str = "*",
        recursive: bool = True,
        directory_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List files matching pattern in workspace directory."""
        target_sub = directory_path if directory_path is not None else directory
        canonical = validate_safe_path(target_sub, self.workspace_root)
        if not canonical.exists():
            raise FileNotFoundError(f"Directory not found: {target_sub}")
        if not canonical.is_dir():
            raise NotADirectoryError(f"Target is not a directory: {target_sub}")

        results: List[Dict[str, Any]] = []
        iterator = canonical.rglob(pattern) if recursive else canonical.glob(pattern)

        for p in sorted(iterator):
            parts = p.relative_to(self.workspace_root).parts
            if any(part.startswith((".", "__pycache__", ".venv")) for part in parts):
                continue

            rel_str = str(p.relative_to(self.workspace_root)).replace("\\", "/")
            stat = p.stat()
            results.append({
                "path": rel_str,
                "name": p.name,
                "type": "directory" if p.is_dir() else "file",
                "size_bytes": stat.st_size if p.is_file() else 0,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })

        if self.state_manager and hasattr(self.state_manager, "record_event"):
            try:
                self.state_manager.record_event(
                    "workspace_file_list",
                    {"directory": str(target_sub), "count": len(results)},
                    tool_name=self.name,
                )
            except Exception:
                pass

        return results

    def _run(
        self,
        directory_path: str = ".",
        recursive: bool = True,
        pattern: str = "*",
        **kwargs: Any,
    ) -> str:
        try:
            items = self.list_files(directory_path=directory_path, recursive=recursive, pattern=pattern)
            return json.dumps(items, indent=2)
        except Exception as e:
            return f"Error: {e}"


class FilePatchInput(BaseModel):
    file_path: str = Field(..., description="File to patch")
    search_block: str = Field(..., description="Exact string block to search for")
    replace_block: str = Field(..., description="Replacement string block")


class WorkspaceFilePatchTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    name: str = "patch_workspace_file"
    description: str = "Apply an exact search-and-replace edit to an existing file in the workspace."
    args_schema: type[BaseModel] = FilePatchInput
    workspace_root: Path = Field(default_factory=get_workspace_root)
    state_manager: Any = Field(default=None, exclude=True)

    def __init__(
        self,
        workspace_root: Optional[Union[str, Path]] = None,
        state_manager: Any = None,
        **kwargs: Any,
    ):
        root = Path(workspace_root).resolve() if workspace_root is not None else get_workspace_root()
        super().__init__(workspace_root=root, state_manager=state_manager, **kwargs)

    def patch_file(
        self,
        target_or_path: Optional[str] = None,
        search_block: Optional[str] = None,
        replace_block: Optional[str] = None,
        *,
        file_path: Optional[str] = None,
        search_content: Optional[str] = None,
        replace_content: Optional[str] = None,
        path: Optional[str] = None,
    ) -> str:
        """Apply an exact single-occurrence search-and-replace edit to target file."""
        eff_path = file_path or path or target_or_path
        if not eff_path:
            raise ValueError("File path must be provided")

        eff_search = search_block if search_block is not None else search_content
        if eff_search is None:
            raise ValueError("search_block / search_content must be provided")

        eff_replace = replace_block if replace_block is not None else replace_content
        if eff_replace is None:
            raise ValueError("replace_block / replace_content must be provided")

        canonical = validate_safe_path(eff_path, self.workspace_root)
        if not canonical.exists():
            raise FileNotFoundError(f"File not found: {eff_path}")
        if canonical.is_dir():
            raise IsADirectoryError(f"Target is a directory, not a file: {eff_path}")

        original = canonical.read_text(encoding="utf-8")
        matches = original.count(eff_search)

        if matches == 0:
            raise ValueError(f"Search block not found in target file: '{eff_path}'")
        if matches > 1:
            raise ValueError(
                f"Ambiguous patch: {matches} occurrences found in '{eff_path}'. Provide broader unique context."
            )

        patched = original.replace(eff_search, eff_replace, 1)

        temp_file = tempfile.NamedTemporaryFile("w", dir=canonical.parent, delete=False, encoding="utf-8")
        try:
            temp_file.write(patched)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_file.close()
            os.replace(temp_file.name, canonical)
        except Exception:
            if os.path.exists(temp_file.name):
                try:
                    os.remove(temp_file.name)
                except OSError:
                    pass
            raise

        if self.state_manager and hasattr(self.state_manager, "record_event"):
            try:
                self.state_manager.record_event(
                    "workspace_file_patch",
                    {"file_path": str(eff_path), "search_len": len(eff_search), "replace_len": len(eff_replace)},
                    tool_name=self.name,
                )
            except Exception:
                pass

        return f"Successfully patched '{eff_path}' (1 occurrence replaced)."

    def _run(
        self,
        file_path: str,
        search_block: str,
        replace_block: str,
        **kwargs: Any,
    ) -> str:
        try:
            return self.patch_file(file_path=file_path, search_block=search_block, replace_block=replace_block)
        except Exception as e:
            return f"Error: {e}"
