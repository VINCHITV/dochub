# backend/app/services/story_diff.py
"""
Deterministic story diffing for DocHub story re-generation.

When a project already has UserStory rows and the user triggers story
re-generation, this service classifies each new LLM-generated story
against the existing rows to avoid duplicate creation.

Algorithm:
  - Jaccard similarity on lowercased title token sets (threshold 0.85).
  - Exact O(N*M) comparison — both N (existing) and M (new) are ≤ 7.

Classification rules:
  new       — no existing story matches (Jaccard < threshold)
  modified  — match found AND existing story_status is not 'done'
  kept      — match found AND existing story_status is 'done' (never touch)
  obsolete  — existing story has no match in new list AND story_status is not 'done'

Invariant: Stories with story_status='done' are NEVER modified or marked obsolete.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.models import UserStory
    from app.services.ai import UserStoryModel

StoryClassification = Literal["new", "modified", "kept", "obsolete"]

JACCARD_THRESHOLD: float = 0.85


# ---------------------------------------------------------------------------
# Core similarity function
# ---------------------------------------------------------------------------


def jaccard_title_similarity(a: str, b: str) -> float:
    """
    Compute Jaccard similarity between two story titles using token sets.

    Tokens are whitespace-split lowercased words. Punctuation is not stripped
    because story titles rarely include punctuation in practice, and keeping
    the function simple avoids false negatives from aggressive normalisation.

    Returns 0.0 if both token sets are empty.

    Examples:
        >>> jaccard_title_similarity("User can log in", "User can log in")
        1.0
        >>> jaccard_title_similarity("User can log in", "Admin can log in")
        0.6
        >>> jaccard_title_similarity("", "")
        0.0
        >>> jaccard_title_similarity("Reset password", "View profile")
        0.0
    """
    set_a = set(a.lower().split())
    set_b = set(b.lower().split())
    if not set_a and not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


# ---------------------------------------------------------------------------
# Diff result dataclass
# ---------------------------------------------------------------------------


@dataclass
class StoryDiffEntry:
    """
    The classification result for a single story.

    For 'new' and 'modified': new_model contains the LLM-generated data.
    For 'modified': existing_id is the ID of the row to update in-place.
    For 'kept':    existing_id is the ID of the protected done-story.
    For 'obsolete': existing_id is the ID to mark as story_status='obsolete'.
    """

    classification: StoryClassification
    new_model: "UserStoryModel | None" = None
    existing_id: str | None = None
    existing_title: str | None = None
    similarity: float = 0.0


@dataclass
class DiffResult:
    """
    Full diff result for a story re-generation pass.

    entries: one entry per new story (new/modified/kept) PLUS one entry per
    obsolete story (stories in existing that had no match in new).
    """

    entries: list[StoryDiffEntry] = field(default_factory=list)

    @property
    def new_stories(self) -> list[StoryDiffEntry]:
        return [e for e in self.entries if e.classification == "new"]

    @property
    def modified_stories(self) -> list[StoryDiffEntry]:
        return [e for e in self.entries if e.classification == "modified"]

    @property
    def kept_stories(self) -> list[StoryDiffEntry]:
        return [e for e in self.entries if e.classification == "kept"]

    @property
    def obsolete_stories(self) -> list[StoryDiffEntry]:
        return [e for e in self.entries if e.classification == "obsolete"]


# ---------------------------------------------------------------------------
# Main diff function
# ---------------------------------------------------------------------------


def classify_stories(
    existing: "list[UserStory]",
    new_models: "list[UserStoryModel]",
    threshold: float = JACCARD_THRESHOLD,
) -> DiffResult:
    """
    Classify new_models against existing UserStory rows.

    Parameters
    ----------
    existing : list[UserStory]
        The current UserStory rows from the database for this project.
    new_models : list[UserStoryModel]
        The newly LLM-generated stories from this generation pass.
    threshold : float
        Jaccard similarity threshold for considering two stories a match.
        Default 0.85.

    Returns
    -------
    DiffResult
        Classification for every new story PLUS obsolete entries for any
        existing stories that had no match in new_models.
    """
    result = DiffResult()

    # Track which existing story IDs were matched to classify the rest as obsolete
    matched_existing_ids: set[str] = set()

    for new_model in new_models:
        best_similarity = 0.0
        best_match: "UserStory | None" = None

        for ex in existing:
            sim = jaccard_title_similarity(new_model.title, ex.title)
            if sim > best_similarity:
                best_similarity = sim
                best_match = ex

        if best_match is not None and best_similarity >= threshold:
            matched_existing_ids.add(best_match.id)
            if best_match.story_status == "done":
                # INVARIANT: never touch done stories
                result.entries.append(StoryDiffEntry(
                    classification="kept",
                    new_model=new_model,
                    existing_id=best_match.id,
                    existing_title=best_match.title,
                    similarity=best_similarity,
                ))
            else:
                result.entries.append(StoryDiffEntry(
                    classification="modified",
                    new_model=new_model,
                    existing_id=best_match.id,
                    existing_title=best_match.title,
                    similarity=best_similarity,
                ))
        else:
            result.entries.append(StoryDiffEntry(
                classification="new",
                new_model=new_model,
                similarity=best_similarity,
            ))

    # Any existing story not matched → obsolete (unless done)
    for ex in existing:
        if ex.id not in matched_existing_ids and ex.story_status != "done":
            result.entries.append(StoryDiffEntry(
                classification="obsolete",
                existing_id=ex.id,
                existing_title=ex.title,
            ))

    return result
