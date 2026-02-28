# Backend Design Skill

## Purpose
Design and modify backend architecture safely.

## Scope
- FastAPI routes
- SQLModel models
- Workflow state machine
- Docker configuration
- Logging strategy

## Standards
- No business logic inside routes.
- All state transitions must be idempotent.
- Use transactions for DB writes.
- Keep services modular and testable.

## Guardrails
- Prefer backward-compatible schema changes.
- Avoid large refactors in Iteration 1.