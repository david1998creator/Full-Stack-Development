"""
Adversarial Empirical Stress Test Suite for Sandboxed Workspace Tools.
Milestone 2 Path Traversal & Sandbox Escape Challenger (M2-1).

Empirically tests:
1. Path traversal attacks: ../, ..\\, absolute paths (C:\\Windows, C:/), drive-relative,
   UNC paths (\\\\server\\share), null bytes (\\x00), mixed slash permutations.
2. Symlink & Junction attacks: directory junctions pointing outside workspace root,
   symlink traversal, circular junction containment.
3. Extreme file operations: 10MB large file writes/reads with SHA-256 integrity,
   line slicing boundary validation, overwrite protection, directory collision safety.
4. Concurrency stress: multi-threaded writes across distinct files, concurrent writes
   to the same target file, atomicity and lock contention recovery without temp leaks.
5. Patch semantics: non-existent blocks, ambiguous multi-occurrence blocks, special
   characters, empty search blocks, parameter validation.
6. CrewAI BaseTool interface: graceful exception trapping in _run() / run() / __call__().
7. Canary integrity: external host directory verification ensuring host files are NEVER
   read, written, patched, or deleted.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from typing import Any, List
from unittest.mock import MagicMock

# Ensure src is on sys.path
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from persistent_crewai.tools.workspace_tools import (
    WorkspaceFileListTool,
    WorkspaceFilePatchTool,
    WorkspaceFileReadTool,
    WorkspaceFileWriteTool,
    validate_safe_path,
)

# Optional junction support on Windows
try:
    import _winapi

    HAS_WINAPI_JUNCTION = hasattr(_winapi, "CreateJunction")
except ImportError:
    HAS_WINAPI_JUNCTION = False


class AdversarialTestBase(unittest.TestCase):
    """Base test harness with isolated workspace root and external canary directory."""

    def setUp(self):
        self.temp_ws = tempfile.TemporaryDirectory()
        self.workspace_root = pathlib.Path(self.temp_ws.name).resolve()

        self.temp_outside = tempfile.TemporaryDirectory()
        self.outside_dir = pathlib.Path(self.temp_outside.name).resolve()

        # Seed an outside canary file that must NEVER be accessed or modified
        self.canary_file = self.outside_dir / "host_secret_canary.txt"
        self.canary_original_content = "CANARY_INTEGRITY_TOKEN_1337_CONFIDENTIAL"
        self.canary_file.write_text(self.canary_original_content, encoding="utf-8")

        self.mock_sm = MagicMock()
        self.mock_sm.record_event = MagicMock(return_value="evt_audit_01")

        self.reader = WorkspaceFileReadTool(
            workspace_root=self.workspace_root, state_manager=self.mock_sm
        )
        self.writer = WorkspaceFileWriteTool(
            workspace_root=self.workspace_root, state_manager=self.mock_sm
        )
        self.lister = WorkspaceFileListTool(
            workspace_root=self.workspace_root, state_manager=self.mock_sm
        )
        self.patcher = WorkspaceFilePatchTool(
            workspace_root=self.workspace_root, state_manager=self.mock_sm
        )

    def tearDown(self):
        # Assert canary integrity: host file outside workspace was NEVER modified or deleted
        self.assertTrue(
            self.canary_file.exists(),
            "SECURITY CRITICAL: External canary file was deleted or unlinked by test!",
        )
        self.assertEqual(
            self.canary_file.read_text(encoding="utf-8"),
            self.canary_original_content,
            "SECURITY CRITICAL: External canary file was corrupted or modified by test!",
        )
        try:
            self.temp_ws.cleanup()
        except OSError:
            pass
        try:
            self.temp_outside.cleanup()
        except OSError:
            pass


class TestAdversarialPathTraversal(AdversarialTestBase):
    """Empirically test path traversal vectors across validate_safe_path and all 4 tools."""

    def test_parent_directory_traversal_blocked(self):
        """Test ../ and ..\\ traversal escaping workspace root."""
        vectors = [
            "../outside.txt",
            "..\\outside.txt",
            "../../etc/shadow",
            "..\\..\\Windows\\win.ini",
            "..//..//outside.txt",
            "..\\\\..\\\\outside.txt",
            "/..//..//outside.txt",
            "\\..\\..\\outside.txt",
            "sub/../../outside.txt",
            "sub/..\\..\\outside.txt",
            "sub/nested/../../../outside.txt",
            "sub/nested/..\\..\\..\\outside.txt",
            "a/b/c/../../../../../../Windows",
        ]
        for vec in vectors:
            with self.subTest(vector=vec):
                # validate_safe_path must raise PermissionError
                with self.assertRaises(
                    PermissionError,
                    msg=f"Expected PermissionError for traversal vector: {vec!r}",
                ):
                    validate_safe_path(vec, self.workspace_root)

                # WorkspaceFileReadTool must raise PermissionError
                with self.assertRaises(PermissionError):
                    self.reader.read_file(vec)

                # WorkspaceFileWriteTool must raise PermissionError
                with self.assertRaises(PermissionError):
                    self.writer.write_file(vec, "malicious_payload")

                # WorkspaceFileListTool must raise PermissionError
                with self.assertRaises(PermissionError):
                    self.lister.list_files(directory_path=vec)

                # WorkspaceFilePatchTool must raise PermissionError
                with self.assertRaises(PermissionError):
                    self.patcher.patch_file(vec, search_block="foo", replace_block="bar")

    def test_absolute_system_paths_blocked(self):
        """Test Windows absolute system paths and drive roots."""
        vectors = [
            r"C:\Windows\System32\drivers\etc\hosts",
            r"C:\Windows\win.ini",
            r"C:/Windows/System32/cmd.exe",
            r"C:\Program Files",
            r"C:\\",
            r"C:/",
            str(self.canary_file),
            str(self.outside_dir),
            r"D:\secret.txt",
            r"D:/secret.txt",
            r"Z:\nonexistent\drive\file.txt",
        ]
        for vec in vectors:
            with self.subTest(vector=vec):
                with self.assertRaises(PermissionError):
                    validate_safe_path(vec, self.workspace_root)
                with self.assertRaises(PermissionError):
                    self.reader.read_file(vec)
                with self.assertRaises(PermissionError):
                    self.writer.write_file(vec, "payload")
                with self.assertRaises(PermissionError):
                    self.lister.list_files(directory_path=vec)
                with self.assertRaises(PermissionError):
                    self.patcher.patch_file(vec, search_block="a", replace_block="b")

    def test_posix_and_drive_rooted_paths_blocked(self):
        """Test POSIX-style rooted paths and drive-relative paths on Windows."""
        vectors = [
            "/etc/passwd",
            "/etc/shadow",
            "/Windows/win.ini",
            r"\Windows\System32",
            r"\Windows\win.ini",
            r"\System32",
            r"D:relative_path.txt",
            r"Z:relative.txt",
        ]
        for vec in vectors:
            with self.subTest(vector=vec):
                with self.assertRaises(PermissionError):
                    validate_safe_path(vec, self.workspace_root)

    def test_unc_and_device_namespace_paths_blocked(self):
        """Test UNC network paths, device namespaces, and physical drive paths."""
        vectors = [
            r"\\127.0.0.1\c$\Windows",
            r"\\localhost\c$\Windows\win.ini",
            r"\\server\share\file.txt",
            r"\\?\C:\Windows\System32\drivers\etc\hosts",
            r"\\?\C:\secret.txt",
            r"\\.\C:\Windows\System32\cmd.exe",
            r"\\.\PhysicalDrive0",
            r"\\?\Volume{00000000-0000-0000-0000-000000000000}\secret",
        ]
        for vec in vectors:
            with self.subTest(vector=vec):
                with self.assertRaises(PermissionError):
                    validate_safe_path(vec, self.workspace_root)
                with self.assertRaises(PermissionError):
                    self.reader.read_file(vec)
                with self.assertRaises(PermissionError):
                    self.writer.write_file(vec, "payload")

    def test_null_bytes_raise_value_error(self):
        """Test that paths containing embedded null bytes raise ValueError."""
        null_vectors = [
            "file\x00name.txt",
            "\x00/../../etc/passwd",
            "safe_dir/test\x00.py",
            "file.txt\x00",
            "\x00",
            "dir\x00/file.txt",
        ]
        for vec in null_vectors:
            with self.subTest(vector=vec):
                with self.assertRaises(ValueError):
                    validate_safe_path(vec, self.workspace_root)
                with self.assertRaises(ValueError):
                    self.reader.read_file(vec)
                with self.assertRaises(ValueError):
                    self.writer.write_file(vec, "payload")
                with self.assertRaises(ValueError):
                    self.lister.list_files(directory_path=vec)
                with self.assertRaises(ValueError):
                    self.patcher.patch_file(vec, search_block="a", replace_block="b")

    def test_empty_and_whitespace_paths_raise_value_error(self):
        """Test empty, whitespace-only, and None paths raise ValueError."""
        empty_vectors = [
            "",
            "   ",
            "\t",
            "\n",
            "\r\n",
            "   \t  \n  ",
            None,
        ]
        for vec in empty_vectors:
            with self.subTest(vector=vec):
                with self.assertRaises(ValueError):
                    validate_safe_path(vec, self.workspace_root)

    def test_read_and_patch_on_directory_raises_is_a_directory_error(self):
        """Test that reading or patching a directory raises IsADirectoryError."""
        sub = self.workspace_root / "test_dir"
        sub.mkdir()

        with self.assertRaises(IsADirectoryError):
            self.reader.read_file("test_dir")
        with self.assertRaises(IsADirectoryError):
            self.reader.read_file(".")

        with self.assertRaises(IsADirectoryError):
            self.patcher.patch_file("test_dir", search_block="a", replace_block="b")
        with self.assertRaises(IsADirectoryError):
            self.patcher.patch_file(".", search_block="a", replace_block="b")

    def test_list_on_file_raises_not_a_directory_error(self):
        """Test that listing a regular file raises NotADirectoryError."""
        self.writer.write_file("regular.txt", "content")
        with self.assertRaises(NotADirectoryError):
            self.lister.list_files(directory_path="regular.txt")


class TestAdversarialSymlinkAndJunctionContainment(AdversarialTestBase):
    """Empirically test symlink redirection and junction containment."""

    def test_external_junction_redirection_blocked(self):
        """Verify that an NTFS junction pointing to outside directory is blocked."""
        if not HAS_WINAPI_JUNCTION:
            self.skipTest("_winapi.CreateJunction not available on this platform")

        # Create junction in workspace pointing to outside_dir
        junction_link = self.workspace_root / "ext_junction"
        _winapi.CreateJunction(str(self.outside_dir), str(junction_link))

        target_file = "ext_junction/host_secret_canary.txt"

        # Attempt read through junction
        with self.assertRaises(PermissionError):
            self.reader.read_file(target_file)

        # Attempt write through junction
        with self.assertRaises(PermissionError):
            self.writer.write_file("ext_junction/infiltrate.txt", "evil_payload")
        self.assertFalse((self.outside_dir / "infiltrate.txt").exists())

        # Attempt list through junction
        with self.assertRaises(PermissionError):
            self.lister.list_files(directory_path="ext_junction")

        # Attempt patch through junction
        with self.assertRaises(PermissionError):
            self.patcher.patch_file(
                target_file,
                search_block="CANARY",
                replace_block="PWNED",
            )

    def test_internal_junction_allowed_and_contained(self):
        """Verify that a junction pointing INSIDE workspace root is allowed and safe."""
        if not HAS_WINAPI_JUNCTION:
            self.skipTest("_winapi.CreateJunction not available on this platform")

        internal_target = self.workspace_root / "real_internal"
        internal_target.mkdir()
        (internal_target / "nested.txt").write_text("internal_hello", encoding="utf-8")

        internal_link = self.workspace_root / "internal_junction"
        _winapi.CreateJunction(str(internal_target), str(internal_link))

        # Safe read through internal junction
        content = self.reader.read_file("internal_junction/nested.txt")
        self.assertEqual(content, "internal_hello")

        # Safe write through internal junction
        res = self.writer.write_file("internal_junction/new.txt", "new_content")
        self.assertIn("Successfully written", res)
        self.assertEqual(
            (internal_target / "new.txt").read_text(encoding="utf-8"), "new_content"
        )

        # Verify traversal OUT of internal junction is blocked
        with self.assertRaises(PermissionError):
            self.reader.read_file("internal_junction/../../outside.txt")

    def test_circular_junction_loop_contained(self):
        """Verify that a circular junction pointing back to workspace root does not escape."""
        if not HAS_WINAPI_JUNCTION:
            self.skipTest("_winapi.CreateJunction not available on this platform")

        loop_link = self.workspace_root / "self_loop"
        _winapi.CreateJunction(str(self.workspace_root), str(loop_link))

        self.writer.write_file("data.txt", "loop_data")
        # Reading through loop resolves back inside workspace root
        content = self.reader.read_file("self_loop/data.txt")
        self.assertEqual(content, "loop_data")

        # self_loop/.. resolves to workspace root itself (stays safe inside root)
        content_up1 = self.reader.read_file("self_loop/../data.txt")
        self.assertEqual(content_up1, "loop_data")

        # Attempting escape via loop + 2 levels of parent (escaping workspace root) is blocked
        with self.assertRaises(PermissionError):
            self.reader.read_file("self_loop/../../outside.txt")


class TestAdversarialExtremeFileOperations(AdversarialTestBase):
    """Empirically test large payloads, line slicing boundaries, and overwrite protection."""

    def test_massive_payload_write_read_sha256(self):
        """Write and read a ~10 MB file with mixed unicode and verify SHA-256 integrity."""
        # Construct ~10 MB text payload with unicode characters
        line_unit = "UTF8_TEST_λ_π_€_🚀_0123456789_ABCDEF_abcdef_Line_\n"
        repetitions = 180000
        full_content = line_unit * repetitions
        expected_bytes = len(full_content.encode("utf-8"))
        expected_hash = hashlib.sha256(full_content.encode("utf-8")).hexdigest()

        # Write large file
        result_msg = self.writer.write_file("massive_file.txt", full_content)
        self.assertIn(f"Successfully written {expected_bytes} bytes", result_msg)

        # Read entire large file back and verify SHA-256
        read_content = self.reader.read_file("massive_file.txt")
        read_hash = hashlib.sha256(read_content.encode("utf-8")).hexdigest()
        self.assertEqual(
            read_hash,
            expected_hash,
            "SHA-256 checksum mismatch on large file round-trip!",
        )

        # Verify line slicing on large file
        slice_content = self.reader.read_file(
            "massive_file.txt", start_line=1000, end_line=1005
        )
        sliced_lines = slice_content.splitlines(keepends=True)
        self.assertEqual(len(sliced_lines), 6)
        for line in sliced_lines:
            self.assertEqual(line, line_unit)

    def test_line_slicing_boundary_validation(self):
        """Test boundary conditions and invalid line ranges in WorkspaceFileReadTool."""
        content = "line1\nline2\nline3\nline4\nline5\n"
        self.writer.write_file("lines.txt", content)

        # Invalid: start_line < 1
        with self.assertRaises(ValueError) as ctx:
            self.reader.read_file("lines.txt", start_line=0)
        self.assertIn("start_line must be >= 1", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            self.reader.read_file("lines.txt", start_line=-3)
        self.assertIn("start_line must be >= 1", str(ctx.exception))

        # Invalid: end_line < start_line
        with self.assertRaises(ValueError) as ctx:
            self.reader.read_file("lines.txt", start_line=4, end_line=2)
        self.assertIn("cannot be less than start_line", str(ctx.exception))

        # Valid slices
        self.assertEqual(self.reader.read_file("lines.txt", start_line=1, end_line=1), "line1\n")
        self.assertEqual(self.reader.read_file("lines.txt", start_line=2, end_line=3), "line2\nline3\n")
        # end_line beyond file length returns up to end of file
        self.assertEqual(self.reader.read_file("lines.txt", start_line=4, end_line=100), "line4\nline5\n")

    def test_overwrite_protection(self):
        """Verify that overwrite=False prevents overwriting existing files."""
        self.writer.write_file("protected.txt", "original_secret")

        with self.assertRaises(FileExistsError) as ctx:
            self.writer.write_file("protected.txt", "new_overwrite", overwrite=False)
        self.assertIn("already exists and overwrite=False", str(ctx.exception))

        # Verify content remained unchanged
        self.assertEqual(self.reader.read_file("protected.txt"), "original_secret")

        # Overwrite=True succeeds
        self.writer.write_file("protected.txt", "overwritten_content", overwrite=True)
        self.assertEqual(self.reader.read_file("protected.txt"), "overwritten_content")

    def test_atomic_write_cleans_up_on_failure(self):
        """Verify that temp files are cleaned up and original file intact when write fails."""
        # Attempt to write to a path where parent is a file (invalid directory structure)
        self.writer.write_file("conflict_file", "im a file not a directory")

        with self.assertRaises((FileExistsError, NotADirectoryError, OSError)):
            self.writer.write_file("conflict_file/sub/file.txt", "content")

        # Verify no orphan tmp files created in workspace root
        for entry in self.workspace_root.iterdir():
            self.assertFalse(
                entry.name.startswith("tmp") and entry.is_file(),
                f"Leaked temporary file detected: {entry.name}",
            )


class TestAdversarialConcurrentFileOperations(AdversarialTestBase):
    """Empirically stress-test multi-threaded concurrency and atomicity."""

    def test_concurrent_writes_to_distinct_files(self):
        """25 worker threads writing and reading distinct files concurrently."""
        num_threads = 25
        errors: List[str] = []

        def worker(idx: int):
            try:
                fname = f"thread_file_{idx:02d}.txt"
                payload = f"Payload-Thread-{idx}-" + ("Z" * 2000)
                self.writer.write_file(fname, payload)
                read_back = self.reader.read_file(fname)
                if read_back != payload:
                    errors.append(f"Thread {idx}: Payload mismatch")
            except Exception as e:
                errors.append(f"Thread {idx}: Exception {type(e).__name__}: {e}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Concurrent distinct writes encountered errors: {errors}")

    def test_concurrent_writes_to_same_file_atomicity_and_isolation(self):
        """Verify that concurrent writes to the same file never cause file corruption or torn states."""
        target_file = "shared_concurrent.txt"
        self.writer.write_file(target_file, "INITIAL_CONTENT\n")

        num_threads = 12
        iterations_per_thread = 10
        lock_exceptions = 0
        success_writes = 0
        corrupted_reads = 0

        def write_worker(thread_id: int):
            nonlocal lock_exceptions, success_writes
            for i in range(iterations_per_thread):
                body = f"TID={thread_id:02d}|ITER={i:02d}|" + ("X" * 300) + "\n"
                try:
                    self.writer.write_file(target_file, body)
                    success_writes += 1
                except (PermissionError, OSError):
                    # Expected on Windows when NTFS locks file during os.replace contention
                    lock_exceptions += 1
                time.sleep(0.0005)

        def read_worker():
            nonlocal corrupted_reads
            for _ in range(iterations_per_thread * 2):
                try:
                    content = self.reader.read_file(target_file)
                    # Check that read content is either initial or fully structured
                    if not (
                        content == "INITIAL_CONTENT\n"
                        or (content.startswith("TID=") and content.endswith("X\n"))
                    ):
                        corrupted_reads += 1
                except (PermissionError, OSError):
                    pass
                time.sleep(0.0005)

        threads = [
            threading.Thread(target=write_worker, args=(t,)) for t in range(num_threads)
        ]
        threads.extend([threading.Thread(target=read_worker) for _ in range(4)])

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Zero corrupted or torn reads allowed
        self.assertEqual(
            corrupted_reads,
            0,
            "SECURITY CRITICAL: Torn read or data corruption detected during concurrent writes!",
        )

        # Final state must be intact
        final_content = self.reader.read_file(target_file)
        self.assertTrue(
            final_content == "INITIAL_CONTENT\n"
            or (final_content.startswith("TID=") and final_content.endswith("X\n")),
            f"Final content is malformed: {final_content[:60]!r}",
        )

        # Verify no orphan temp files leaked in workspace root
        for entry in self.workspace_root.iterdir():
            self.assertFalse(
                entry.name.startswith("tmp") and entry.is_file(),
                f"Leaked temporary file detected: {entry.name}",
            )


class TestAdversarialPatchSemantics(AdversarialTestBase):
    """Empirically test exact-block patching edge cases and failure modes."""

    def test_nonexistent_search_block_raises_value_error(self):
        """Searching for non-existent content raises ValueError and preserves file intact."""
        initial = "def main():\n    return 42\n"
        self.writer.write_file("app.py", initial)

        with self.assertRaises(ValueError) as ctx:
            self.patcher.patch_file(
                "app.py",
                search_block="def missing_function():",
                replace_block="def new_func():",
            )
        self.assertIn("Search block not found", str(ctx.exception))
        self.assertEqual(self.reader.read_file("app.py"), initial)

    def test_ambiguous_patch_raises_value_error(self):
        """Searching for block with >1 occurrence raises ValueError and preserves file intact."""
        initial = "x = 100\nx = 100\nx = 100\n"
        self.writer.write_file("ambiguous.py", initial)

        with self.assertRaises(ValueError) as ctx:
            self.patcher.patch_file(
                "ambiguous.py",
                search_block="x = 100",
                replace_block="x = 200",
            )
        self.assertIn("Ambiguous patch: 3 occurrences found", str(ctx.exception))
        self.assertEqual(self.reader.read_file("ambiguous.py"), initial)

    def test_exact_unique_patch_succeeds(self):
        """Single occurrence patch with special characters and multiline formatting succeeds."""
        initial = (
            "class Engine:\n"
            "    def __init__(self, key: str = 'v1$'):\n"
            "        self.key = key\n"
            "        self.factor = 3.14\n"
        )
        self.writer.write_file("engine.py", initial)

        search = (
            "    def __init__(self, key: str = 'v1$'):\n"
            "        self.key = key\n"
            "        self.factor = 3.14\n"
        )
        replace = (
            "    def __init__(self, key: str = 'v2$$', factor: float = 6.28):\n"
            "        self.key = key\n"
            "        self.factor = factor\n"
        )

        res = self.patcher.patch_file("engine.py", search_block=search, replace_block=replace)
        self.assertIn("Successfully patched", res)

        expected = (
            "class Engine:\n"
            "    def __init__(self, key: str = 'v2$$', factor: float = 6.28):\n"
            "        self.key = key\n"
            "        self.factor = factor\n"
        )
        self.assertEqual(self.reader.read_file("engine.py"), expected)

    def test_patch_empty_search_block_on_nonempty_file_rejected(self):
        """Empty search block on non-empty file has >1 occurrences and is rejected."""
        self.writer.write_file("data.txt", "hello")
        with self.assertRaises(ValueError) as ctx:
            self.patcher.patch_file("data.txt", search_block="", replace_block="world")
        self.assertIn("Ambiguous patch", str(ctx.exception))

    def test_patch_missing_file_raises_file_not_found(self):
        """Patching non-existent file raises FileNotFoundError."""
        with self.assertRaises(FileNotFoundError):
            self.patcher.patch_file(
                "does_not_exist.py",
                search_block="foo",
                replace_block="bar",
            )

    def test_patch_missing_arguments_raises_value_error(self):
        """Patching with None parameters raises ValueError."""
        with self.assertRaises(ValueError):
            self.patcher.patch_file(file_path=None, search_block="a", replace_block="b")
        with self.assertRaises(ValueError):
            self.patcher.patch_file("file.py", search_block=None, replace_block="b")
        with self.assertRaises(ValueError):
            self.patcher.patch_file("file.py", search_block="a", replace_block=None)


class TestAdversarialCrewAIToolWrappers(AdversarialTestBase):
    """Empirically test BaseTool execution wrappers: _run(), run(), __call__()."""

    def test_read_tool_run_traps_security_violations_gracefully(self):
        """BaseTool._run() returns formatted Error string without unhandled crash."""
        res = self.reader._run(file_path="../../etc/shadow")
        self.assertTrue(res.startswith("Error: Security violation: Access outside workspace root"))

        res = self.reader.run(file_path=r"C:\Windows\System32\cmd.exe")
        self.assertTrue(res.startswith("Error: Security violation: Access outside workspace root"))

        res = self.reader(file_path="file\x00null")
        self.assertTrue(res.startswith("Error: Target path cannot contain null bytes"))

    def test_write_tool_run_traps_security_violations_gracefully(self):
        """WorkspaceFileWriteTool._run() returns formatted Error string."""
        res = self.writer._run(file_path="../../malicious.txt", content="payload")
        self.assertTrue(res.startswith("Error: Security violation: Access outside workspace root"))

        res = self.writer.run(file_path=r"C:\Windows\win.ini", content="payload")
        self.assertTrue(res.startswith("Error: Security violation: Access outside workspace root"))

    def test_list_tool_run_traps_security_violations_gracefully(self):
        """WorkspaceFileListTool._run() returns formatted Error string."""
        res = self.lister._run(directory_path="../../")
        self.assertTrue(res.startswith("Error: Security violation: Access outside workspace root"))

        # Valid listing returns valid JSON
        self.writer.write_file("item1.txt", "val")
        json_str = self.lister.run(directory_path=".")
        parsed = json.loads(json_str)
        self.assertIsInstance(parsed, list)
        self.assertTrue(any(item["name"] == "item1.txt" for item in parsed))

    def test_patch_tool_run_traps_security_violations_gracefully(self):
        """WorkspaceFilePatchTool._run() returns formatted Error string."""
        res = self.patcher._run(
            file_path="../../secret.py",
            search_block="a",
            replace_block="b",
        )
        self.assertTrue(res.startswith("Error: Security violation: Access outside workspace root"))

    def test_audit_event_recording_telemetry(self):
        """Verify state_manager.record_event is called on successful operations."""
        self.writer.write_file("audit_test.txt", "audited content")
        self.reader.read_file("audit_test.txt")
        self.lister.list_files(".")
        self.patcher.patch_file(
            "audit_test.txt",
            search_block="audited content",
            replace_block="audited v2",
        )

        calls = self.mock_sm.record_event.call_args_list
        event_names = [call[0][0] for call in calls]
        self.assertIn("workspace_file_write", event_names)
        self.assertIn("workspace_file_read", event_names)
        self.assertIn("workspace_file_list", event_names)
        self.assertIn("workspace_file_patch", event_names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
