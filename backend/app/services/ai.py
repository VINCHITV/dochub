"""
backend/app/services/ai.py

All Pydantic section/story models and instructor-patched AsyncOpenAI wrappers.

Design notes:
- instructor.from_openai with AsyncOpenAI() is used for structured output via
  tool-calling on the OpenAI API.
- AsyncOpenAI() is required — never use the sync OpenAI() in async routes.
- Every generate_section() call logs an llm_call structlog event with token counts
  and latency for full observability.
- Model strings always come from services.versions — never hardcoded here.
"""

from __future__ import annotations

import time
from typing import Any, Literal, Optional, Type, TypeVar

import instructor
import structlog
from openai import AsyncOpenAI
from pydantic import BaseModel

from app.services.versions import GENERATOR_MODEL, PRD_PROMPT_VERSION

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# instructor-patched async client — module-level singleton
# ---------------------------------------------------------------------------

instructor_client = instructor.from_openai(AsyncOpenAI())

# ---------------------------------------------------------------------------
# PRD Section Pydantic models
# ---------------------------------------------------------------------------


class TitleSection(BaseModel):
    """Project title and optional subtitle."""

    title: str
    subtitle: Optional[str] = None


class DescriptionSection(BaseModel):
    """High-level product overview with KB source references."""

    overview: str
    source_doc_ids: list[str] = []


class ProblemSection(BaseModel):
    """Problem statement with enumerated pain points."""

    problem_statement: str
    pain_points: list[str]
    source_doc_ids: list[str] = []


class WhySection(BaseModel):
    """Rationale and business value for building this product."""

    rationale: str
    business_value: str
    source_doc_ids: list[str] = []


class SuccessSection(BaseModel):
    """Success metrics and KPIs — must be measurable."""

    metrics: list[str]
    kpis: list[str]
    source_doc_ids: list[str] = []


class AudienceSection(BaseModel):
    """Primary/secondary audience and persona descriptions."""

    primary_audience: str
    secondary_audience: Optional[str] = None
    personas: list[str] = []
    source_doc_ids: list[str] = []


class ConflictEntry(BaseModel):
    """
    A single conflict or risk identified between the current PRD and the KB.

    severity levels:
    - blocking: must be resolved before any implementation can begin
    - needs_discussion: stakeholder alignment required, not a hard blocker
    - minor: informational; track but don't block

    kb_excerpt: verbatim text from the KB chunk that motivated this conflict.
    Populated by the LLM from the rag_context. Empty string if unavailable.
    """

    source_prd_id: str
    conflicting_statement: str
    proposed_change: str
    severity: Literal["blocking", "needs_discussion", "minor"]
    kb_excerpt: str = ""


class GapEntry(BaseModel):
    """
    A single transcript gap or ambiguity (Type 2 open question).

    question: the gap or ambiguity as a clear, answerable question.
    transcript_excerpt: 1-2 verbatim sentences from the transcript that reveal
    the gap (e.g. the statement that prompted this question). Empty if the gap
    comes from the absence of information rather than a specific statement.
    """

    question: str
    transcript_excerpt: str = ""


class OpenQuestionsSection(BaseModel):
    """
    Type 1 conflicts (KB contradictions) + Type 2 gaps (transcript ambiguities).

    type1_conflicts: structured ConflictEntry objects derived from RAG retrieval.
    type2_gaps: structured GapEntry objects with question + transcript context.
    """

    type1_conflicts: list[ConflictEntry]
    type2_gaps: list[GapEntry]
    source_doc_ids: list[str] = []


class PRDSections(BaseModel):
    """Container model for all 7 PRD sections (used for full-doc extraction)."""

    title: TitleSection
    description: DescriptionSection
    problem: ProblemSection
    why: WhySection
    success: SuccessSection
    audience: AudienceSection
    open_questions: OpenQuestionsSection


# ---------------------------------------------------------------------------
# User Story Pydantic models
# ---------------------------------------------------------------------------


class CapabilityList(BaseModel):
    """Step 1 of the 3-step user story pipeline: extracted product capabilities."""

    capabilities: list[str]


class SlicePlan(BaseModel):
    """
    Step 2: vertical slices derived from capabilities.

    Max 7 slices enforced at the prompt level; Pydantic validates the list length
    if needed but the LLM is instructed to cap at 7.
    """

    slices: list[str]


class ValidationRow(BaseModel):
    """One row in a user story's validation/acceptance table."""

    field: str
    rule: str
    error_message: str


class TranscriptRef(BaseModel):
    """
    A speaker-attributed reference to a verbatim transcript or KB excerpt.

    speaker: the speaker's name as it appears in the transcript (e.g. "Alice", "PM").
             Use empty string if transcript has no named speakers.
    excerpt: 1-2 verbatim sentences from the transcript that support this story.
    source:  "transcript" for direct transcript quotes, "kb" for KB-sourced context.
    """

    speaker: str = ""
    excerpt: str
    source: Literal["transcript", "kb"] = "transcript"


class UserStoryModel(BaseModel):
    """
    A single vertically-sliced user story with full acceptance criteria.

    description must follow: "As a <persona>, I want <action>, so that <value>."
    acceptance_criteria should cover happy path, alternative paths, and error paths.
    validations is the field-level validation table (e.g. email format, required fields).
    priority reflects business value: 'high' for must-have, 'medium' for should-have, 'low' for nice-to-have.
    size is the T-shirt effort estimate: XS (<1 day), S (1-2 days), M (3-5 days), L (6-10 days), XL (>2 weeks).
    dependencies lists story titles that must be completed before this one.
    reference_links lists relevant documentation URLs or knowledge base references.
    transcript_references lists 1-3 verbatim speaker quotes from the transcript that justify this story.
    """

    title: str
    description: str
    acceptance_criteria: list[str]
    validations: list[ValidationRow]
    priority: Literal["high", "medium", "low"] = "medium"
    size: Literal["XS", "S", "M", "L", "XL"] = "M"
    dependencies: list[str] = []
    reference_links: list[str] = []
    transcript_references: list[TranscriptRef] = []


# ---------------------------------------------------------------------------
# Generic type variable for generate_section return type
# ---------------------------------------------------------------------------

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Core AI call wrapper
# ---------------------------------------------------------------------------


async def generate_section(
    section_name: str,
    section_model: Type[T],
    system_prompt: str,
    user_prompt: str,
    project_id: str,
    retry_count: int = 0,
) -> tuple[T, int, int]:
    """
    Call the Anthropic API via instructor to generate and validate one PRD section.

    Uses create_with_completion() to get both the structured Pydantic model and
    the raw completion object (needed for token usage logging).

    Parameters
    ----------
    section_name : str
        Human-readable section identifier (e.g. "description") — used in log events.
    section_model : Type[T]
        The Pydantic model class that instructor should extract into.
    system_prompt : str
        System prompt providing context and instructions for this section.
    user_prompt : str
        User-facing prompt with the transcript excerpt and retrieved KB context.
    project_id : str
        Project identifier for structured logging.
    retry_count : int
        Number of retries already attempted — logged for observability.

    Returns
    -------
    tuple[T, int, int]
        (pydantic_model, input_tokens, output_tokens)

    Raises
    ------
    instructor.exceptions.InstructorRetryException
        If all retries (max_retries=3) are exhausted without a valid extraction.
    Exception
        Any Anthropic API error is propagated to the caller.
    """
    start_ts = time.monotonic()
    input_tokens = 0
    output_tokens = 0

    try:
        pydantic_model, completion = await instructor_client.chat.completions.create_with_completion(
            model=GENERATOR_MODEL,
            max_retries=3,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_model=section_model,
        )

        # Extract token usage from the raw completion object
        if hasattr(completion, "usage") and completion.usage is not None:
            input_tokens = completion.usage.prompt_tokens or 0
            output_tokens = completion.usage.completion_tokens or 0

    except Exception:
        latency_ms = (time.monotonic() - start_ts) * 1000
        _log_llm_call(
            project_id=project_id,
            section=section_name,
            input_tokens=0,
            output_tokens=0,
            latency_ms=latency_ms,
            retry_count=retry_count,
        )
        raise

    latency_ms = (time.monotonic() - start_ts) * 1000
    _log_llm_call(
        project_id=project_id,
        section=section_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        retry_count=retry_count,
    )

    return pydantic_model, input_tokens, output_tokens


def _log_llm_call(
    project_id: str,
    section: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: float,
    retry_count: int,
) -> None:
    """
    Emit a structured llm_call log event via structlog.

    All model version constants are imported from services.versions — never
    hardcoded in the log event payload.
    """
    logger.info(
        "llm_call",
        project_id=project_id,
        section=section,
        generator_model=GENERATOR_MODEL,
        prompt_version=PRD_PROMPT_VERSION,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=round(latency_ms, 2),
        retry_count=retry_count,
    )
