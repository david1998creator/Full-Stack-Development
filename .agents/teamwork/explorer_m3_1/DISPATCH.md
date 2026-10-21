## 2026-10-13T21:54:55Z
From: 16296ebf-4c98-41ac-a0f3-1f8ac4690a22
Priority: MESSAGE_PRIORITY_HIGH

You are Explorer M3-1 (Milestone 3 LLM Factory & DeterministicMockLLM Explorer).
Your working directory is: C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.agents\teamwork\explorer_m3_1
The project root directory is: C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team
The authoritative user request is located at: C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.agents\teamwork\ORIGINAL_REQUEST.md
The project specification document is at: C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\PROJECT.md
The spec miner survey is at: C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.agents\teamwork\spec_miner_survey_3\specifications.md
The agent architecture survey is at: C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.agents\teamwork\explorer_survey_2\agent_architecture.md

MANDATORY FIRST STEP: Read ORIGINAL_REQUEST.md and PROJECT.md completely.

Your task:
Analyze and formulate the exact architecture and implementation plan for `src/persistent_crewai/agents/llm_factory.py`:
1. `DeterministicMockLLM`:
   - Must provide deterministic, role-grounded, and context-aware responses when offline or testing without API keys.
   - Compatible with CrewAI Agent expectations (implements BaseChatModel or Custom LLM interface expected by CrewAI / LangChain, e.g. `invoke()`, `generate()`, `_generate()`, `predict()`, `call()`, Pydantic BaseLLM compatibility).
   - Dynamic prompt parsing: recognize user prompt topics, role instructions, and tool call triggers.
   - Reproducible responses for Coordinator, Architect, Designer, Developer, Tester, Reviewer, GitHub Manager, Memory Manager.
2. `LLMFactory`:
   - `get_llm(provider=None, model_name=None, temperature=0.7)` factory function.
   - Support auto-detection of environment variables: `OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`.
   - If keys are missing or provider is "mock" or "deterministic", return `DeterministicMockLLM`.
3. Unit test plan for `tests/test_agents.py` testing LLM generation, role-specific prompts, and factory fallback.

Write your report to:
- C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.agents\teamwork\explorer_m3_1\analysis.md
- C:\Users\User\.gemini\antigravity\scratch\persistent_crewai_team\.agents\teamwork\explorer_m3_1\handoff.md
When done, send a message to the orchestrator (parent) with the path to handoff.md.
