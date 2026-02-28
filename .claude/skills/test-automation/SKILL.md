# Test Automation Skill

## Purpose
Create deterministic tests for AI-assisted system.

## Requirements
- Provide LLM mock mode (LLM_MODE=mock).
- Provide Jira mock mode (JIRA_MODE=mock).
- Use pytest.
- Mock httpx using respx or equivalent.
- Test rollback behavior explicitly.

## Guardrails
- No real API keys in tests.
- No external network dependency.
- Tests must be reproducible.