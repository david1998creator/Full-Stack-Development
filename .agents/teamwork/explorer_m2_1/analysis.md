# Architectural & Implementation Analysis: Sandboxed Workspace Tools (Milestone 2)

**Author**: Explorer M2-1 (Workspace Tools Explorer)  
**Date**: 2026-10-13  
**Target Module**: `src/persistent_crewai/tools/workspace_tools.py`  
**Test Suite**: `tests/test_tools.py` and `tests/e2e/`  

---

## 1. Executive Summary

Milestone 2 requires providing operational integration with local workspace files and GitHub operations for the persistent 8-agent CrewAI software engineering framework. Workspace tools allow autonomous agents (especially the Developer, Tester, and Reviewer roles) to inspect the repository, write code modules, run patches, and inspect project directories without exposing the host operating system to unauthorized access or path traversal escapes.

This analysis formulates the concrete, production-grade implementation strategy for `src/persistent_crewai/tools/workspace_tools.py` with rigorous sandbox containment, atomic file writes, exact block patching, and comprehensive CrewAI `BaseTool` interoperability.

---

## 2. Sandboxing Security Boundary & Containment Engine

### 2.1 Threat Model & Attack Vectors

The workspace sandbox must isolate all file operations within a designated `workspace_root` directory. In multi-agent autonomous execution, untrusted or hallucinated LLM inputs can present critical security threats:

1. **Relative Path Traversal (`../`, `../../`, `sub/../../`)**:
   - Attackers attempt to navigate up the directory hierarchy to read sensitive files (e.g., `/etc/shadow`, `../../.ssh/id_rsa`, `..\..\Windows\System32\drivers\etc\hosts`).
2. **Absolute Host Paths (`C:\Windows`, `/etc/passwd`)**:
   - Attackers bypass relative paths by providing absolute root paths directly.
3. **Drive Letter Hopping (Windows)**:
   - On Windows, if `workspace_root` is on `C:`, an attacker might pass `D:\sensitive\data`.
4. **Drive-Relative Paths (Windows `\Windows\System32`)**:
   - Paths beginning with a single slash/backslash on Windows target the drive root of the current working directory, escaping `workspace_root`.
5. **Universal Naming Convention (UNC) & Network Shares (`\\attacker-server\share`)**:
   - UNC paths could trigger NTLM credential leaks or access network shares outside local isolation.
6. **Symlink and Directory Junction Escapes**:
   - A symlink or NTFS junction inside the workspace pointing to a directory outside `workspace_root` would allow reading/writing external files.
7. **Race Conditions & Non-Atomic Writes**:
   - Partial writes or system crashes during file writes could corrupt source code files.
8. **Ambiguous or Partial Patch Collisions**:
   - Applying search-and-replace patches to non-unique code snippets can accidentally corrupt unintended functions.

### 2.2 Canonical Path Validation Algorithm (`validate_safe_path`)

To defend against all aforementioned vectors, the canonical validation algorithm must adhere to strict invariants:

```python
def validate_safe_path(target_path: Union[str, Path], workspace_root: Union[str, Path]) -> Path:
    """Validate that target_path resolves strictly within workspace_root.
    
    Args:
        target_path: Relative or absolute path requested by agent or caller.
        workspace_root: Base directory acting as security boundary.
        
    Returns:
        Canonical resolved Path guaranteed to be inside workspace_root.
        
    Raises:
        ValueError: If target_path is empty, whitespace, or contains null bytes.
        PermissionError: If target_path resolves outside workspace_root.
    """
    if target_path is None:
        raise ValueError("Target path cannot be None")
    
    raw_str = str(target_path).strip()
    if not raw_str:
        raise ValueError("Target path cannot be empty or whitespace")
    
    if "\x00" in raw_str:
        raise ValueError("Target path cannot contain null bytes")
    
    root_resolved = Path(workspace_root).resolve()
    target_p = Path(raw_str)
    
    # In Python pathlib:
    # If target_p is absolute, root_resolved / target_p yields target_p.
    # If target_p is relative, root_resolved / target_p anchors it to root_resolved.
    # On Windows, drive-relative paths like r"\Windows" require explicit root anchoring.
    if target_p.is_absolute():
        resolved_target = target_p.resolve()
    else:
        resolved_target = (root_resolved / target_p).resolve()
        
    # Python 3.9+ Path.is_relative_to verifies prefix containment
    try:
        if not resolved_target.is_relative_to(root_resolved):
            raise PermissionError(
                f"Security violation: Access outside workspace root is forbidden. Target: {target_path}"
            )
    except AttributeError:
        # Fallback for Python < 3.9
        try:
            resolved_target.relative_to(root_resolved)
        except ValueError:
            raise PermissionError(
                f"Security violation: Access outside workspace root is forbidden. Target: {target_path}"
            )
            
    return resolved_target
```

### 2.3 Symlink & Junction Containment

Because `Path.resolve()` canonicalizes all symbolic links and junction points:
- If a symlink points to a target *outside* `workspace_root`, `resolved_target` will point to the real external location.
- When `resolved_target.is_relative_to(root_resolved)` is checked, it will return `False` and immediately raise `PermissionError`.
- Symlink loops are caught by `Path.resolve()` which raises `RuntimeError("Symlink loop")` or resolves to canonical form.

### 2.4 Error Handling & Rejection Contracts

The sandboxed workspace tools must implement a dual-mode error contract:
1. **Direct API calls** (used by unit tests, script runners, and E2E harnesses):
   - Path outside root: `PermissionError` (explicitly required by `test_e2e_tier2_boundaries.py` lines 75, 78, 81, 93, 97).
   - Missing file for read/patch: `FileNotFoundError` (required by line 106).
   - Not a directory for list: `NotADirectoryError`.
   - Ambiguous patch (>1 matches) or not found (0 matches): `ValueError` (required by lines 121, 137).
   - Attempt to overwrite with `overwrite=False`: `FileExistsError`.
2. **CrewAI Agent Tool Invocations (`_run`)**:
   - The agent execution method `_run(...)` must catch expected domain exceptions (`PermissionError`, `FileNotFoundError`, `ValueError`, `FileExistsError`) and return formatted error messages (e.g. `Error: Security violation: Access outside workspace root is forbidden...`). This prevents crashing the multi-agent pipeline and gives the LLM actionable feedback to fix its input.

---

## 3. Tool Specifications & Implementations

### 3.1 Base Architecture & CrewAI Interoperability

To guarantee 100% test compatibility in environments where `crewai` may or may not yet be installed in the current Python interpreter:

```python
try:
    from crewai.tools import BaseTool
except ImportError:
    try:
        from langchain_core.tools import BaseTool
    except ImportError:
        from pydantic import BaseModel
        class BaseTool(BaseModel):
            name: str = ""
            description: str = ""
            args_schema: Optional[type] = None
            def run(self, *args, **kwargs):
                return self._run(*args, **kwargs)
```

Each tool class inherits from `BaseTool`, defines a `pydantic.BaseModel` for `args_schema`, stores `workspace_root: Path`, and exposes both the direct callable method (`read_file`, `write_file`, etc.) and the agent dispatch method (`_run`).

---

### 3.2 Tool 1: `WorkspaceFileReadTool`

- **Class Name**: `WorkspaceFileReadTool`
- **Tool Name**: `read_workspace_file`
- **Description**: `"Read the content of a file in the workspace. Supports optional line slice with 1-based indexing."`
- **Schema (`FileReadInput`)**:
  - `file_path: str`
  - `start_line: Optional[int] = None` (1-indexed start line)
  - `end_line: Optional[int] = None` (1-indexed inclusive end line)
- **Method**:
  ```python
  def read_file(
      self,
      file_path: Union[str, Path],
      start_line: Optional[int] = None,
      end_line: Optional[int] = None,
  ) -> str
  ```
- **Operational Logic**:
  1. Validates path via `validate_safe_path(file_path, self.workspace_root)`.
  2. If file does not exist: raises `FileNotFoundError(f"File not found: {file_path}")`.
  3. If path is a directory: raises `IsADirectoryError(f"Path is a directory, not a file: {file_path}")`.
  4. Reads file with `encoding="utf-8", errors="replace"`.
  5. If `start_line is None and end_line is None`:
     - Returns raw file content string.
  6. If line slice is requested:
     - Splits into lines (preserving newline structure).
     - Validates: `start_line >= 1` (raises `ValueError` if `< 1`).
     - Validates: `end_line >= start_line` (raises `ValueError` if `end_line < start_line`).
     - Extracts requested slice `lines[start_line - 1 : end_line]`.
     - Returns sliced lines.
  7. Records audit event if `state_manager` is attached:
     `state_manager.record_event("workspace_file_read", {"file_path": str(file_path)})`.

---

### 3.3 Tool 2: `WorkspaceFileWriteTool`

- **Class Name**: `WorkspaceFileWriteTool`
- **Tool Name**: `write_workspace_file`
- **Description**: `"Write or create a file in the workspace. Automatically creates parent directories and uses atomic write."`
- **Schema (`FileWriteInput`)**:
  - `file_path: str`
  - `content: str`
  - `overwrite: bool = True`
- **Method**:
  ```python
  def write_file(
      self,
      file_path: Union[str, Path],
      content: str,
      overwrite: bool = True,
  ) -> str
  ```
- **Operational Logic**:
  1. Validates path via `validate_safe_path(file_path, self.workspace_root)`.
  2. Checks existence: if `target.exists() and not overwrite`:
     - Raises `FileExistsError(f"File already exists and overwrite=False: {file_path}")`.
  3. Creates parent directories automatically:
     `target.parent.mkdir(parents=True, exist_ok=True)`.
  4. Performs **Atomic Write**:
     - Creates a temporary file in `target.parent`:
       ```python
       temp_file = tempfile.NamedTemporaryFile("w", dir=target.parent, delete=False, encoding="utf-8")
       try:
           temp_file.write(content)
           temp_file.flush()
           os.fsync(temp_file.fileno())
           temp_file.close()
           os.replace(temp_file.name, target)
       except Exception:
           if os.path.exists(temp_file.name):
               os.remove(temp_file.name)
           raise
       ```
     - Using `os.replace` guarantees atomicity across Windows and POSIX systems.
  5. Calculates written bytes: `bytes_written = len(content.encode("utf-8"))`.
  6. Records audit event via `state_manager.record_event("workspace_file_write", ...)`.
  7. Returns confirmation message:
     `f"Successfully written {bytes_written} bytes to '{file_path}'."` (verifies `self.assertIn("written", result.lower())`).

---

### 3.4 Tool 3: `WorkspaceFileListTool`

- **Class Name**: `WorkspaceFileListTool`
- **Tool Name**: `list_workspace_files`
- **Description**: `"List files and subdirectories in the workspace."`
- **Schema (`FileListInput`)**:
  - `directory_path: str = "."`
  - `recursive: bool = True`
  - `pattern: str = "*"`
- **Method Signature**:
  ```python
  def list_files(
      self,
      directory: str = ".",
      pattern: str = "*",
      recursive: bool = True,
      directory_path: Optional[str] = None,
  ) -> List[Dict[str, Any]]
  ```
  *(Accepts both `directory` and `directory_path` to accommodate varying caller conventions)*.
- **Operational Logic**:
  1. Resolves target directory: `target_dir = validate_safe_path(directory_path or directory, self.workspace_root)`.
  2. Validates: if not `target_dir.exists()`: raises `FileNotFoundError`. If not `target_dir.is_dir()`: raises `NotADirectoryError`.
  3. Traversal:
     - If `recursive`: iterates using `target_dir.rglob(pattern)`.
     - If not `recursive`: iterates using `target_dir.glob(pattern)`.
  4. Excludes internal dot-folders unless pattern explicitly requests:
     - Skips `.git`, `__pycache__`, `.venv`, `.pytest_cache`.
  5. For each entry, constructs structured metadata dictionary:
     ```python
     rel_path = str(item.relative_to(self.workspace_root)).replace("\\", "/")
     entry = {
         "path": rel_path,
         "name": item.name,
         "type": "directory" if item.is_dir() else "file",
         "size_bytes": item.stat().st_size if item.is_file() else 0,
         "modified_at": datetime.fromtimestamp(item.stat().st_mtime, tz=timezone.utc).isoformat(),
     }
     ```
  6. Returns list of dictionaries (verifies `test_r3_02`: `py_names = [f["path"] for f in py_files]`).

---

### 3.5 Tool 4: `WorkspaceFilePatchTool`

- **Class Name**: `WorkspaceFilePatchTool`
- **Tool Name**: `patch_workspace_file`
- **Description**: `"Apply an exact search-and-replace edit to an existing file in the workspace."`
- **Schema (`FilePatchInput`)**:
  - `file_path: str`
  - `search_block: str`
  - `replace_block: str`
- **Method Signature**:
  ```python
  def patch_file(
      self,
      path: Optional[str] = None,
      search_content: Optional[str] = None,
      replace_content: Optional[str] = None,
      file_path: Optional[str] = None,
      search_block: Optional[str] = None,
      replace_block: Optional[str] = None,
  ) -> str
  ```
  *(Accepts both `(file_path, search_block, replace_block)` and `(path, search_content, replace_content)`).*
- **Operational Logic**:
  1. Resolves arguments:
     - `eff_path = file_path or path`
     - `eff_search = search_block if search_block is not None else search_content`
     - `eff_replace = replace_block if replace_block is not None else replace_content`
  2. Validates path via `validate_safe_path(eff_path, self.workspace_root)`.
  3. Validates file existence: if not `target.exists()`, raises `FileNotFoundError`.
  4. Reads existing content: `content = target.read_text(encoding="utf-8")`.
  5. Counts occurrences: `matches = content.count(eff_search)`.
     - **0 matches**: raises `ValueError(f"Search block not found in target file: '{eff_path}'")` (verifies `test_b04`).
     - **>1 matches**: raises `ValueError(f"Ambiguous patch: {matches} occurrences found in '{eff_path}'. Provide broader unique context.")` (verifies `test_b05`).
  6. Replaces exact occurrence:
     `new_content = content.replace(eff_search, eff_replace, 1)`.
  7. Writes back atomically using temporary file rename.
  8. Records audit event via `state_manager`.
  9. Returns confirmation message:
     `f"Successfully patched '{eff_path}' (1 occurrence replaced)."` (verifies `test_r3_03`: `self.assertIn("patched", patch_result.lower())`).

---

## 4. Proposed Implementation Blueprint (`workspace_tools.py`)

Here is the exact proposed module design for `src/persistent_crewai/tools/workspace_tools.py`:

```python
"""
Sandboxed workspace file operations for Persistent CrewAI.
Provides path-traversal containment, atomic file writes, line-sliced reads,
glob-filtered file listings, and exact-block search-and-replace patching.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field

from persistent_crewai.config import get_workspace_root

# CrewAI BaseTool compatibility wrapper
try:
    from crewai.tools import BaseTool
except ImportError:
    try:
        from langchain_core.tools import BaseTool
    except ImportError:
        class BaseTool(BaseModel):
            name: str = ""
            description: str = ""
            args_schema: Optional[type] = None

            def run(self, *args, **kwargs) -> Any:
                return self._run(*args, **kwargs)


def validate_safe_path(target_path: Union[str, Path], workspace_root: Union[str, Path]) -> Path:
    """Validate that target_path strictly resolves within workspace_root.
    
    Raises:
        ValueError: If path is empty, whitespace, or contains null bytes.
        PermissionError: If path attempts to escape workspace_root.
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
    name: str = "read_workspace_file"
    description: str = "Read the content of a file in the workspace. Supports optional line slice with 1-based indexing."
    args_schema: type[BaseModel] = FileReadInput
    workspace_root: Path = Field(default_factory=get_workspace_root)
    state_manager: Any = Field(default=None, exclude=True)

    def __init__(
        self,
        workspace_root: Optional[Union[str, Path]] = None,
        state_manager: Any = None,
        **kwargs,
    ):
        root = Path(workspace_root).resolve() if workspace_root else get_workspace_root()
        super().__init__(workspace_root=root, **kwargs)
        self.state_manager = state_manager

    def read_file(
        self,
        file_path: Union[str, Path],
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> str:
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
            self.state_manager.record_event(
                "workspace_file_read",
                {"file_path": str(file_path), "bytes": len(content)},
                tool_name=self.name,
            )

        return content

    def _run(self, file_path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
        try:
            return self.read_file(file_path, start_line=start_line, end_line=end_line)
        except Exception as e:
            return f"Error: {e}"


class FileWriteInput(BaseModel):
    file_path: str = Field(..., description="Path to file to write")
    content: str = Field(..., description="Content to write")
    overwrite: bool = Field(True, description="Whether to overwrite existing file")


class WorkspaceFileWriteTool(BaseTool):
    name: str = "write_workspace_file"
    description: str = "Write or create a file in the workspace. Automatically creates parent directories and uses atomic write."
    args_schema: type[BaseModel] = FileWriteInput
    workspace_root: Path = Field(default_factory=get_workspace_root)
    state_manager: Any = Field(default=None, exclude=True)

    def __init__(
        self,
        workspace_root: Optional[Union[str, Path]] = None,
        state_manager: Any = None,
        **kwargs,
    ):
        root = Path(workspace_root).resolve() if workspace_root else get_workspace_root()
        super().__init__(workspace_root=root, **kwargs)
        self.state_manager = state_manager

    def write_file(
        self,
        file_path: Union[str, Path],
        content: str,
        overwrite: bool = True,
    ) -> str:
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
                os.remove(temp_file.name)
            raise

        bytes_written = len(content.encode("utf-8"))

        if self.state_manager and hasattr(self.state_manager, "record_event"):
            self.state_manager.record_event(
                "workspace_file_write",
                {"file_path": str(file_path), "bytes_written": bytes_written},
                tool_name=self.name,
            )

        return f"Successfully written {bytes_written} bytes to '{file_path}'."

    def _run(self, file_path: str, content: str, overwrite: bool = True) -> str:
        try:
            return self.write_file(file_path, content, overwrite=overwrite)
        except Exception as e:
            return f"Error: {e}"


class FileListInput(BaseModel):
    directory_path: str = Field(".", description="Subdirectory to list")
    recursive: bool = Field(True, description="Whether to recurse into subdirectories")
    pattern: str = Field("*", description="Glob pattern filter")


class WorkspaceFileListTool(BaseTool):
    name: str = "list_workspace_files"
    description: str = "List files and subdirectories in the workspace."
    args_schema: type[BaseModel] = FileListInput
    workspace_root: Path = Field(default_factory=get_workspace_root)
    state_manager: Any = Field(default=None, exclude=True)

    def __init__(
        self,
        workspace_root: Optional[Union[str, Path]] = None,
        state_manager: Any = None,
        **kwargs,
    ):
        root = Path(workspace_root).resolve() if workspace_root else get_workspace_root()
        super().__init__(workspace_root=root, **kwargs)
        self.state_manager = state_manager

    def list_files(
        self,
        directory: str = ".",
        pattern: str = "*",
        recursive: bool = True,
        directory_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        target_sub = directory_path if directory_path is not None else directory
        canonical = validate_safe_path(target_sub, self.workspace_root)
        if not canonical.exists():
            raise FileNotFoundError(f"Directory not found: {target_sub}")
        if not canonical.is_dir():
            raise NotADirectoryError(f"Target is not a directory: {target_sub}")

        results: List[Dict[str, Any]] = []
        iterator = canonical.rglob(pattern) if recursive else canonical.glob(pattern)

        for p in sorted(iterator):
            # Skip hidden environment/cache directories unless explicitly queried
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

        return results

    def _run(self, directory_path: str = ".", recursive: bool = True, pattern: str = "*") -> str:
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
    name: str = "patch_workspace_file"
    description: str = "Apply an exact search-and-replace edit to an existing file in the workspace."
    args_schema: type[BaseModel] = FilePatchInput
    workspace_root: Path = Field(default_factory=get_workspace_root)
    state_manager: Any = Field(default=None, exclude=True)

    def __init__(
        self,
        workspace_root: Optional[Union[str, Path]] = None,
        state_manager: Any = None,
        **kwargs,
    ):
        root = Path(workspace_root).resolve() if workspace_root else get_workspace_root()
        super().__init__(workspace_root=root, **kwargs)
        self.state_manager = state_manager

    def patch_file(
        self,
        path: Optional[str] = None,
        search_content: Optional[str] = None,
        replace_content: Optional[str] = None,
        file_path: Optional[str] = None,
        search_block: Optional[str] = None,
        replace_block: Optional[str] = None,
    ) -> str:
        eff_path = file_path or path
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

        # Atomic write
        temp_file = tempfile.NamedTemporaryFile("w", dir=canonical.parent, delete=False, encoding="utf-8")
        try:
            temp_file.write(patched)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_file.close()
            os.replace(temp_file.name, canonical)
        except Exception:
            if os.path.exists(temp_file.name):
                os.remove(temp_file.name)
            raise

        if self.state_manager and hasattr(self.state_manager, "record_event"):
            self.state_manager.record_event(
                "workspace_file_patch",
                {"file_path": str(eff_path), "search_len": len(eff_search), "replace_len": len(eff_replace)},
                tool_name=self.name,
            )

        return f"Successfully patched '{eff_path}' (1 occurrence replaced)."

    def _run(self, file_path: str, search_block: str, replace_block: str) -> str:
        try:
            return self.patch_file(file_path=file_path, search_block=search_block, replace_block=replace_block)
        except Exception as e:
            return f"Error: {e}"
```

---

## 5. Comprehensive Unit Test Plan (`tests/test_tools.py`)

A new dedicated test suite `tests/test_tools.py` should be implemented to thoroughly verify all isolated features and edge cases:

| Test ID | Test Method | Tested Invariant |
|---|---|---|
| **TC-T1** | `test_validate_safe_path_valid` | Standard relative and inside-root paths resolve safely |
| **TC-T2** | `test_validate_safe_path_upward_traversal` | `../../etc/shadow` raises `PermissionError` |
| **TC-T3** | `test_validate_safe_path_windows_system` | `..\..\Windows\System32` raises `PermissionError` |
| **TC-T4** | `test_validate_safe_path_absolute_outside` | Absolute path to external directory raises `PermissionError` |
| **TC-T5** | `test_validate_safe_path_empty_and_null` | Empty string and `\x00` raise `ValueError` |
| **TC-T6** | `test_read_file_full_content` | Writes file and reads complete UTF-8 content |
| **TC-T7** | `test_read_file_line_slice` | Reads lines 2 to 4 with 1-based indexing |
| **TC-T8** | `test_read_file_invalid_bounds` | Negative lines or end < start raise `ValueError` |
| **TC-T9** | `test_read_file_not_found` | Nonexistent file raises `FileNotFoundError` |
| **TC-T10** | `test_read_file_directory` | Attempting to read a directory raises `IsADirectoryError` |
| **TC-T11** | `test_write_file_atomic_and_nested` | Automatically creates directories and writes file atomically |
| **TC-T12** | `test_write_file_overwrite_false` | Overwrite=False on existing file raises `FileExistsError` |
| **TC-T13** | `test_list_files_filtering` | Filters by glob pattern (`*.py`), returns structured metadata |
| **TC-T14** | `test_list_files_nonexistent_dir` | Nonexistent directory raises `FileNotFoundError` |
| **TC-T15** | `test_list_files_file_as_dir` | Passing a file to list_files raises `NotADirectoryError` |
| **TC-T16** | `test_patch_file_success` | Replaces unique snippet and preserves file integrity |
| **TC-T17** | `test_patch_file_zero_matches` | Missing search snippet raises `ValueError` ("not found") |
| **TC-T18** | `test_patch_file_ambiguous_matches` | Multiple matching snippets raise `ValueError` ("ambiguous") |
| **TC-T19** | `test_state_manager_audit_events` | File operations log events to `state_manager.record_event` |
| **TC-T20** | `test_crewai_base_tool_run_fallback` | `_run` handles exceptions and returns safe error strings |

---

## 6. Compatibility & Verification Against Existing Test Suites

The proposed implementation was cross-checked against existing test harnesses:

1. **`tests/e2e/helpers.py`**:
   - Imports `WorkspaceFileReadTool`, `WorkspaceFileWriteTool`, `WorkspaceFileListTool`, `WorkspaceFilePatchTool`, and `validate_safe_path`.
   - Sets `HAS_TOOLS = True`.
2. **`tests/e2e/test_e2e_tier1_features.py`**:
   - `test_r3_01_workspace_file_write_and_read`: invokes `writer.write_file(nested_file, content)` and `reader.read_file(nested_file)`. Checks `"written" in result.lower()` and `"calculate_hash" in read_content`. (Matches).
   - `test_r3_02_workspace_file_list_and_pattern`: invokes `lister.list_files(directory_path=".", recursive=True, pattern="*.py")`. Checks `f["path"]`. (Matches).
   - `test_r3_03_workspace_file_patch`: invokes `patcher.patch_file(file_path=target, search_block=..., replace_block=...)`. Checks `"patched" in patch_result.lower()`. (Matches).
3. **`tests/e2e/test_e2e_tier2_boundaries.py`**:
   - `test_b01_path_traversal_parent_directory_blocked`: checks `validate_safe_path`, `reader.read_file`, `writer.write_file` raise `PermissionError`. (Matches).
   - `test_b02_path_traversal_windows_system_blocked`: checks `reader.read_file` raises `PermissionError`. (Matches).
   - `test_b03_file_read_nonexistent_file_raises_error`: checks `FileNotFoundError`. (Matches).
   - `test_b04_file_patch_zero_occurrences_raises_value_error`: checks `ValueError` with `"not found"`. (Matches).
   - `test_b05_file_patch_multiple_ambiguous_occurrences_raises_value_error`: checks `ValueError` with `"ambiguous"`. (Matches).
4. **`tests/e2e/test_e2e_tier3_combinations.py`**:
   - `test_c02_github_tools_and_workspace_tools`: Developer writes code via `WorkspaceFileWriteTool`, Reviewer reads via `WorkspaceFileReadTool`. (Matches).
5. **`tests/e2e/test_e2e_tier4_scenarios.py`**:
   - Scenario 1 & Scenario 2: Multiple agents read, write, and patch files in workspace. (Matches).
