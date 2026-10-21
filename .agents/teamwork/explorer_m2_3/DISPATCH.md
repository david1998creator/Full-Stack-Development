## 2026-10-13T21:22:43Z

You are Explorer M2-3 (Milestone 2 Real GitHub Adapter & Tool Wrappers Explorer).
Your working directory is: C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.agents\teamwork\explorer_m2_3
The project root directory is: C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team
The authoritative user request is located at: C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.agents\teamwork\ORIGINAL_REQUEST.md
The project specification document is at: C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\PROJECT.md
The spec miner reference is at: C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.agents\teamwork\spec_miner_survey_3\specifications.md

MANDATORY FIRST STEP: Read ORIGINAL_REQUEST.md and PROJECT.md completely.

Your task:
Analyze and formulate the exact implementation strategy for Real GitHub Adapter, Factory, and CrewAI Tools:
1. `src/persistent_crewai/tools/real_github.py`:
   - `RealGitHubAdapter` using `urllib.request` / `requests` calling GitHub REST API v3 (`https://api.github.com/repos/{owner}/{repo}`).
   - Authentication via `GITHUB_TOKEN` header (`Authorization: Bearer <token>`).
   - Graceful error handling (401, 404, rate limits).
2. Adapter Factory `create_github_adapter()`:
   - Returns `RealGitHubAdapter` if `GITHUB_TOKEN` and repo are configured; otherwise defaults safely to `MockGitHubAdapter`.
3. Tool Classes / Functions for Agents (`src/persistent_crewai/tools/__init__.py`):
   - Export tools usable by CrewAI agents (`IssueCreateTool`, `PRCreateTool`, `WorkspaceReadTool`, `WorkspaceWriteTool`, etc.) as standard callables or BaseTool subclasses.
4. Comprehensive unit test plan in `tests/test_tools.py`.

Write your report to:
- C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.agents\teamwork\explorer_m2_3\analysis.md
- C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.agents\teamwork\explorer_m2_3\handoff.md
When done, send a message to the orchestrator (parent) with the path to handoff.md.
