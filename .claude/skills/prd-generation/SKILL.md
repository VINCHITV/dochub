# PRD Generation Skill

## Purpose
Generate a structured 7-section PRD from a meeting transcript and retrieved context.

## Inputs
- transcript_text (string)
- retrieved_context (optional list of past PRD snippets)

## Output
A JSON object matching PRDDocument schema:
- title
- description
- problem
- why
- success
- audience
- open_questions

## Rules
1. Generate sections in this exact order.
2. Never fabricate metrics or numbers.
3. If something is inferred, explicitly label with "(Inferred)".
4. Open Questions must include:
    - Type 1: Conflicts with retrieved PRDs
    - Type 2: Missing transcript gaps
5. Keep content concise and formal.

## Guardrails
- No marketing language.
- No hallucinated statistics.
- No sections outside the defined 7.