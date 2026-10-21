import subprocess
import tempfile
import os
import sys

print("Python executable:", sys.executable)

# Test 1: Python-generated explicit UTF-8 BOM file
f_bom = tempfile.NamedTemporaryFile('wb', delete=False, suffix='.crew')
f_bom.write(b'\xef\xbb\xbfstatus\nexit\n')
f_bom.close()

try:
    res_bom = subprocess.run(
        [sys.executable, "-m", "persistent_crewai.cli.main", "repl", "--batch", f_bom.name],
        capture_output=True,
        text=True
    )
    print("Test 1 (Explicit UTF-8 BOM via python module):")
    print(f"  Returncode: {res_bom.returncode}")
    print(f"  Stdout length: {len(res_bom.stdout)}")
    print(f"  Stderr: '{res_bom.stderr.strip()}'")
    assert res_bom.returncode == 0, f"Expected 0, got {res_bom.returncode}"
    assert "UnicodeEncodeError" not in res_bom.stderr
    assert len(res_bom.stdout) > 0
finally:
    os.unlink(f_bom.name)

# Test 2: Using the installed CLI entrypoint 'crewai-team'
f_bom2 = tempfile.NamedTemporaryFile('wb', delete=False, suffix='.crew')
f_bom2.write(b'\xef\xbb\xbfstatus\nexit\n')
f_bom2.close()

try:
    res_cli = subprocess.run(
        ["crewai-team", "repl", "--batch", f_bom2.name],
        capture_output=True,
        text=True,
        shell=True
    )
    print("Test 2 (Explicit UTF-8 BOM via 'crewai-team' CLI):")
    print(f"  Returncode: {res_cli.returncode}")
    print(f"  Stdout length: {len(res_cli.stdout)}")
    print(f"  Stderr: '{res_cli.stderr.strip()}'")
    assert res_cli.returncode == 0, f"Expected 0, got {res_cli.returncode}"
    assert "UnicodeEncodeError" not in res_cli.stderr
    assert len(res_cli.stdout) > 0
finally:
    os.unlink(f_bom2.name)

# Test 3: Standard UTF-8 without BOM
f_nobom = tempfile.NamedTemporaryFile('wb', delete=False, suffix='.crew')
f_nobom.write(b'status\nexit\n')
f_nobom.close()

try:
    res_nobom = subprocess.run(
        ["crewai-team", "repl", "--batch", f_nobom.name],
        capture_output=True,
        text=True,
        shell=True
    )
    print("Test 3 (Non-BOM UTF-8 via 'crewai-team' CLI):")
    print(f"  Returncode: {res_nobom.returncode}")
    print(f"  Stdout length: {len(res_nobom.stdout)}")
    print(f"  Stderr: '{res_nobom.stderr.strip()}'")
    assert res_nobom.returncode == 0, f"Expected 0, got {res_nobom.returncode}"
    assert "UnicodeEncodeError" not in res_nobom.stderr
    assert len(res_nobom.stdout) > 0
finally:
    os.unlink(f_nobom.name)

print("\nALL UTF-8 BOM TESTS PASSED CLEANLY!")
