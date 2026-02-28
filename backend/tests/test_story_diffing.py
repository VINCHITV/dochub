"""
backend/tests/test_story_diffing.py

Unit tests for the deterministic story diffing algorithm in services/story_diff.py.

Coverage:
- jaccard_title_similarity: identical, partial overlap, disjoint, empty inputs
- classify_stories: new, modified, kept (done), obsolete classifications
- Invariant: stories with story_status='done' are never modified or obsoleted
- Idempotency: re-generation on same PRD keeps all stories
- Edge cases: empty existing list, empty new list

Zero network calls — all Pydantic models and DB models constructed in-memory.
"""

from __future__ import annotations

import pytest

from app.services.ai import UserStoryModel, ValidationRow
from app.services.story_diff import (
    JACCARD_THRESHOLD,
    DiffResult,
    StoryDiffEntry,
    classify_stories,
    jaccard_title_similarity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_story_model(title: str, priority: str = "medium") -> UserStoryModel:
    return UserStoryModel(
        title=title,
        description=f"As a user, I want {title.lower()}, so that value.",
        acceptance_criteria=["Happy path: ok."],
        validations=[],
        priority=priority,  # type: ignore[arg-type]
    )


class _FakeStoryRow:
    """Minimal substitute for UserStory SQLModel row (no DB needed)."""
    def __init__(self, id: str, title: str, story_status: str = "open"):
        self.id = id
        self.title = title
        self.story_status = story_status


# ---------------------------------------------------------------------------
# jaccard_title_similarity
# ---------------------------------------------------------------------------


class TestJaccardTitleSimilarity:
    def test_identical_titles_return_1(self):
        assert jaccard_title_similarity("User can log in", "User can log in") == pytest.approx(1.0)

    def test_completely_different_titles_return_0(self):
        assert jaccard_title_similarity("Reset password", "View dashboard") == pytest.approx(0.0)

    def test_partial_overlap(self):
        # "user can log in" vs "user can log out" → 3 shared / 5 union
        sim = jaccard_title_similarity("User can log in", "User can log out")
        assert 0.5 < sim < 1.0

    def test_empty_both_returns_0(self):
        assert jaccard_title_similarity("", "") == pytest.approx(0.0)

    def test_one_empty_returns_0(self):
        assert jaccard_title_similarity("Reset password", "") == pytest.approx(0.0)
        assert jaccard_title_similarity("", "Reset password") == pytest.approx(0.0)

    def test_case_insensitive(self):
        assert jaccard_title_similarity("RESET PASSWORD", "reset password") == pytest.approx(1.0)

    def test_single_word_match(self):
        sim = jaccard_title_similarity("login", "login")
        assert sim == pytest.approx(1.0)

    def test_threshold_boundary(self):
        # 5-word title, 4 common words → 4/6 ≈ 0.667 — below default threshold
        sim = jaccard_title_similarity("a b c d e", "a b c d f")
        assert sim == pytest.approx(4 / 6)
        assert sim < JACCARD_THRESHOLD


# ---------------------------------------------------------------------------
# classify_stories — new stories
# ---------------------------------------------------------------------------


class TestClassifyStoriesNew:
    def test_all_new_when_no_existing_rows(self):
        new_models = [
            _make_story_model("User can log in"),
            _make_story_model("User can reset password"),
        ]
        result = classify_stories(existing=[], new_models=new_models)
        assert len(result.new_stories) == 2
        assert len(result.modified_stories) == 0
        assert len(result.kept_stories) == 0
        assert len(result.obsolete_stories) == 0

    def test_no_match_produces_new(self):
        existing = [_FakeStoryRow("ex1", "View user profile")]
        new_models = [_make_story_model("Reset password via SMS")]
        result = classify_stories(existing=existing, new_models=new_models)
        assert len(result.new_stories) == 1
        assert result.new_stories[0].new_model.title == "Reset password via SMS"

    def test_new_entry_has_no_existing_id(self):
        result = classify_stories(existing=[], new_models=[_make_story_model("New story")])
        assert result.new_stories[0].existing_id is None


# ---------------------------------------------------------------------------
# classify_stories — modified stories
# ---------------------------------------------------------------------------


class TestClassifyStoriesModified:
    def test_high_similarity_open_story_produces_modified(self):
        existing = [_FakeStoryRow("ex1", "User can log in via email")]
        new_models = [_make_story_model("User can log in via email")]  # exact match
        result = classify_stories(existing=existing, new_models=new_models)
        assert len(result.modified_stories) == 1
        entry = result.modified_stories[0]
        assert entry.existing_id == "ex1"
        assert entry.new_model is not None

    def test_modified_entry_has_new_model(self):
        existing = [_FakeStoryRow("ex1", "User can save payment method")]
        new_models = [_make_story_model("User can save payment method")]
        result = classify_stories(existing=existing, new_models=new_models)
        assert result.modified_stories[0].new_model.title == "User can save payment method"

    def test_similarity_above_threshold_required_for_modified(self):
        # Titles share only 1 of 5 words each → Jaccard = 1/9 ≈ 0.11 — too low
        existing = [_FakeStoryRow("ex1", "Alpha beta gamma delta epsilon")]
        new_models = [_make_story_model("Zeta eta theta iota kappa")]
        result = classify_stories(existing=existing, new_models=new_models)
        assert len(result.new_stories) == 1  # no match → new
        assert len(result.modified_stories) == 0


# ---------------------------------------------------------------------------
# classify_stories — kept (done) stories
# ---------------------------------------------------------------------------


class TestClassifyStoriesKept:
    def test_matching_done_story_produces_kept(self):
        existing = [_FakeStoryRow("ex1", "User can log in", story_status="done")]
        new_models = [_make_story_model("User can log in")]
        result = classify_stories(existing=existing, new_models=new_models)
        assert len(result.kept_stories) == 1
        assert len(result.modified_stories) == 0

    def test_kept_entry_preserves_existing_id(self):
        existing = [_FakeStoryRow("ex1", "User can log in", story_status="done")]
        new_models = [_make_story_model("User can log in")]
        result = classify_stories(existing=existing, new_models=new_models)
        assert result.kept_stories[0].existing_id == "ex1"

    def test_done_story_never_modified(self):
        existing = [_FakeStoryRow("ex1", "Reset password via email", story_status="done")]
        new_models = [_make_story_model("Reset password via email")]
        result = classify_stories(existing=existing, new_models=new_models)
        assert len(result.modified_stories) == 0
        assert len(result.kept_stories) == 1


# ---------------------------------------------------------------------------
# classify_stories — obsolete stories
# ---------------------------------------------------------------------------


class TestClassifyStoriesObsolete:
    def test_unmatched_open_story_is_obsolete(self):
        existing = [_FakeStoryRow("ex1", "Old feature nobody needs")]
        new_models = [_make_story_model("Brand new capability")]
        result = classify_stories(existing=existing, new_models=new_models)
        assert len(result.obsolete_stories) == 1
        assert result.obsolete_stories[0].existing_id == "ex1"

    def test_done_story_not_obsoleted(self):
        # Invariant: done stories are never classified as obsolete
        existing = [_FakeStoryRow("ex1", "Old done story", story_status="done")]
        new_models = [_make_story_model("Completely different story")]
        result = classify_stories(existing=existing, new_models=new_models)
        assert len(result.obsolete_stories) == 0

    def test_obsolete_entries_have_no_new_model(self):
        existing = [_FakeStoryRow("ex1", "Obsolete feature")]
        new_models = [_make_story_model("Different story")]
        result = classify_stories(existing=existing, new_models=new_models)
        assert result.obsolete_stories[0].new_model is None


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestClassifyStoriesIdempotent:
    def test_regeneration_on_identical_titles_produces_all_modified(self):
        titles = ["User can log in", "User can reset password", "User can view profile"]
        existing = [_FakeStoryRow(f"ex{i}", t) for i, t in enumerate(titles)]
        new_models = [_make_story_model(t) for t in titles]
        result = classify_stories(existing=existing, new_models=new_models)
        assert len(result.modified_stories) == 3
        assert len(result.new_stories) == 0
        assert len(result.obsolete_stories) == 0

    def test_empty_new_list_makes_all_open_stories_obsolete(self):
        existing = [
            _FakeStoryRow("ex1", "Story A", story_status="open"),
            _FakeStoryRow("ex2", "Story B", story_status="done"),
        ]
        result = classify_stories(existing=existing, new_models=[])
        # Only the open story becomes obsolete; done story is protected
        assert len(result.obsolete_stories) == 1
        assert result.obsolete_stories[0].existing_id == "ex1"

    def test_empty_existing_and_new_produces_empty_result(self):
        result = classify_stories(existing=[], new_models=[])
        assert len(result.entries) == 0


# ---------------------------------------------------------------------------
# Mixed scenarios
# ---------------------------------------------------------------------------


class TestClassifyStoriesMixed:
    def test_mixed_new_modified_kept_obsolete(self):
        existing = [
            _FakeStoryRow("ex1", "User can log in", story_status="open"),
            _FakeStoryRow("ex2", "User can reset password", story_status="done"),
            _FakeStoryRow("ex3", "Old feature to remove", story_status="open"),
        ]
        new_models = [
            _make_story_model("User can log in"),             # → modified (ex1 open)
            _make_story_model("User can reset password"),     # → kept (ex2 done)
            _make_story_model("Brand new checkout flow"),     # → new
            # ex3 "Old feature to remove" has no match → obsolete
        ]
        result = classify_stories(existing=existing, new_models=new_models)
        assert len(result.modified_stories) == 1
        assert len(result.kept_stories) == 1
        assert len(result.new_stories) == 1
        assert len(result.obsolete_stories) == 1
        assert result.obsolete_stories[0].existing_id == "ex3"

    def test_threshold_parameter_overrides_default(self):
        # With threshold=0.5, a lower-similarity match should trigger modified
        existing = [_FakeStoryRow("ex1", "User can log in via email and password")]
        # Jaccard of "User can log" vs "User can log in via email and password"
        # tokens: {user,can,log} vs {user,can,log,in,via,email,and,password} = 3/8 = 0.375
        new_models = [_make_story_model("User can log")]
        result_default = classify_stories(existing=existing, new_models=new_models)
        result_lower = classify_stories(existing=existing, new_models=new_models, threshold=0.3)
        assert len(result_default.new_stories) == 1   # below 0.85
        assert len(result_lower.modified_stories) == 1  # above 0.3
