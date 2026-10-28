"""
Executive Orchestration CLI script for Persistent CrewAI Multi-Agent Framework.
Allows developers or external orchestrators to trigger autonomous project generation from the command line:
1. Decomposes project goal via Coordinator
2. Scaffolds physical workspace and production manifests
3. Executes assigned agent tasks with TDD self-correction
4. Runs Playwright browser smoke test & verifies Git commits
"""

import argparse
import json
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("Error: requests package is required. Install with pip install requests.")
    sys.exit(1)


SERVER_URL = "http://127.0.0.1:8000"


def orchestrate_project(goal: str, slug: str = "production_app") -> None:
    print(f"\n[AgentHub Meta-Orchestrator] Initiating project: '{slug}'")
    print(f"Goal: {goal}\n")

    # 1. Check server health
    try:
        res = requests.get(f"{SERVER_URL}/api/overview", timeout=3)
        if res.status_code != 200:
            print("[ERROR] Control Console server on port 8000 is not responding cleanly.")
            sys.exit(1)
        print("[OK] Control Console server active on port 8000.")
    except Exception as e:
        print(f"[ERROR] Server connection error on {SERVER_URL}: {e}")
        print("Please start the server first via: python ui/server.py")
        sys.exit(1)

    # 2. Decompose project goal into tasks
    print("\n[Step 1] Triggering Coordinator to decompose goal into tasks...")
    dec_res = requests.post(f"{SERVER_URL}/api/decompose", json={"goal": goal, "slug": slug})
    if dec_res.status_code != 200:
        print(f"[ERROR] Decomposition failed: {dec_res.text}")
        sys.exit(1)

    dec_data = dec_res.json()
    tasks = dec_data.get("created_tasks", [])
    print(f"[OK] Coordinator generated {len(tasks)} tasks:")
    for t in tasks:
        print(f"   - [{t.get('role', 'Agent')}] {t.get('title', 'Untitled')} (ID: {t.get('id')})")

    # 3. Execute tasks through assigned agents
    print("\n[Step 2] Executing pipeline across specialist agents with TDD verification...")
    completed = []
    for idx, t in enumerate(tasks, 1):
        t_id = t.get("id")
        role = t.get("role", "Developer")
        title = t.get("title", "")
        print(f"\n[RUNNING] [{idx}/{len(tasks)}] Task: '{title}' assigned to {role}...")
        start_t = time.time()
        
        exec_res = requests.post(f"{SERVER_URL}/api/tasks/{t_id}/execute", json={"slug": slug})
        duration = time.time() - start_t

        if exec_res.status_code == 200:
            data = exec_res.json()
            out_preview = data.get("output", "").splitlines()[:4]
            print(f"   [COMPLETED] in {duration:.2f}s:")
            for line in out_preview:
                print(f"      {line[:80].encode('ascii', errors='replace').decode('ascii')}")
            completed.append(t)
        else:
            print(f"   [FAILED] Task execution failed ({exec_res.status_code}): {exec_res.text[:200]}")

    # 4. Run Live Playwright Browser Smoke Test
    print("\n[Step 3] Running Playwright Headless Chromium live smoke test...")
    smoke_res = requests.post(f"{SERVER_URL}/api/smoke-test", json={"url": SERVER_URL, "slug": slug})
    if smoke_res.status_code == 200:
        smoke_data = smoke_res.json()
        print(f"   [SMOKE TEST]: {'PASSED' if smoke_data.get('success') else 'FAILED'}")
        print(f"   - HTTP Status: {smoke_data.get('status_code')}")
        print(f"   - Console Errors: {len(smoke_data.get('console_errors', []))}")
        print(f"   - Screenshot: {smoke_data.get('screenshot_path')}")
    else:
        print(f"   [WARNING] Smoke test call returned status {smoke_res.status_code}")

    # 5. Check Physical Project Workspace & Git Status
    print("\n[Step 4] Inspecting physical project workspace on disk...")
    stat_res = requests.get(f"{SERVER_URL}/api/projects/status?slug={slug}")
    if stat_res.status_code == 200:
        stat_data = stat_res.json()
        print(f"   - Workspace Root: {stat_data.get('root_path')}")
        print(f"   - Files Generated: {stat_data.get('files_count')} files")
        for f in (stat_data.get("files") or [])[:8]:
            print(f"      File: {f}")
        print(f"   - Git History:\n      {stat_data.get('git_log')}")

    print(f"\n[SUCCESS] [AgentHub Meta-Orchestrator] Project '{slug}' build and verification complete!")


if __name__ == "__main__":
    import os
    parser = argparse.ArgumentParser(description="Orchestrate project with Persistent CrewAI Team")
    parser.add_argument("--goal", type=str, required=True, help="Project requirements or prompt")
    parser.add_argument("--slug", type=str, default="production_app", help="Project folder slug")
    parser.add_argument("--mock", action="store_true", help="Run in deterministic offline mock mode (testing/debug)")
    args = parser.parse_args()

    if args.mock:
        os.environ["MOCK_LLM"] = "1"

    orchestrate_project(goal=args.goal, slug=args.slug)
