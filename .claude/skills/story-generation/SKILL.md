# Story Generation Skill

## Purpose
Convert a PRD into vertically sliced, independently shippable user stories (max 7).

## Pipeline
1. Extract capabilities
2. Plan slices (3–7 max)
3. Expand each slice into a full user story

## Output Schema (UserStory)
- title
- description (As a / I want / So that)
- acceptance_criteria (3–6 bullet points)
- validations (input/expected table entries)

## Rules
- Stories must be vertically sliced.
- Each story must include happy path and error cases.
- No cross-story dependencies.
- Maximum 7 stories.

## Guardrails
- Avoid generic acceptance criteria.
- Avoid epics disguised as stories.