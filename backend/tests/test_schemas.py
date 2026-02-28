"""
backend/tests/test_schemas.py

Unit tests for all Pydantic models defined in app/services/ai.py.

Coverage:
- TitleSection: valid construction, missing required field raises ValidationError
- DescriptionSection: source_doc_ids defaults to empty list
- ProblemSection: pain_points must be a list of strings
- WhySection: both fields required
- SuccessSection: metrics and kpis must be lists
- AudienceSection: secondary_audience is optional; personas defaults to []
- ConflictEntry: severity is a strict Literal — rejects unknown values
- OpenQuestionsSection: type1_conflicts is list[ConflictEntry], type2_gaps is list[str]
- PRDSections: container model validates all 7 sections together
- CapabilityList: capabilities is a required list
- SlicePlan: slices is a required list
- ValidationRow: all three fields required
- UserStoryModel: all fields present and typed correctly

Zero external dependencies — no network, no DB, runs in milliseconds.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.ai import (
    AudienceSection,
    CapabilityList,
    ConflictEntry,
    DescriptionSection,
    OpenQuestionsSection,
    PRDSections,
    ProblemSection,
    SlicePlan,
    SuccessSection,
    TitleSection,
    UserStoryModel,
    ValidationRow,
    WhySection,
)


# ---------------------------------------------------------------------------
# TitleSection
# ---------------------------------------------------------------------------

class TestTitleSection:
    def test_valid_with_title_only(self):
        # Arrange / Act
        section = TitleSection(title="Payments v2")

        # Assert
        assert section.title == "Payments v2"
        assert section.subtitle is None

    def test_valid_with_subtitle(self):
        section = TitleSection(title="Payments v2", subtitle="1-click checkout")
        assert section.subtitle == "1-click checkout"

    def test_missing_title_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            TitleSection()  # type: ignore[call-arg]
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("title",) for e in errors)

    def test_title_must_be_string(self):
        with pytest.raises(ValidationError):
            TitleSection(title=123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# DescriptionSection
# ---------------------------------------------------------------------------

class TestDescriptionSection:
    def test_valid_minimal(self):
        section = DescriptionSection(overview="High-level overview text.")
        assert section.overview == "High-level overview text."

    def test_source_doc_ids_defaults_to_empty_list(self):
        section = DescriptionSection(overview="Overview")
        assert section.source_doc_ids == []

    def test_source_doc_ids_accepts_list_of_strings(self):
        section = DescriptionSection(
            overview="Overview", source_doc_ids=["payments-v1", "auth-2024-01-01"]
        )
        assert len(section.source_doc_ids) == 2
        assert "payments-v1" in section.source_doc_ids

    def test_missing_overview_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            DescriptionSection()  # type: ignore[call-arg]
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("overview",) for e in errors)


# ---------------------------------------------------------------------------
# ProblemSection
# ---------------------------------------------------------------------------

class TestProblemSection:
    def test_valid_minimal(self):
        section = ProblemSection(
            problem_statement="Checkout drop-off is too high.",
            pain_points=["Slow load", "Too many steps"],
        )
        assert section.problem_statement == "Checkout drop-off is too high."
        assert len(section.pain_points) == 2

    def test_pain_points_must_be_list(self):
        # A plain string should be rejected (not coerceable to list[str] in Pydantic v2)
        with pytest.raises(ValidationError):
            ProblemSection(
                problem_statement="Some problem",
                pain_points="not a list",  # type: ignore[arg-type]
            )

    def test_pain_points_empty_list_is_valid(self):
        section = ProblemSection(
            problem_statement="Some problem", pain_points=[]
        )
        assert section.pain_points == []

    def test_source_doc_ids_defaults_to_empty_list(self):
        section = ProblemSection(
            problem_statement="Problem", pain_points=["Pain"]
        )
        assert section.source_doc_ids == []

    def test_missing_problem_statement_raises(self):
        with pytest.raises(ValidationError):
            ProblemSection(pain_points=["some pain"])  # type: ignore[call-arg]

    def test_missing_pain_points_raises(self):
        with pytest.raises(ValidationError):
            ProblemSection(problem_statement="Problem")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# WhySection
# ---------------------------------------------------------------------------

class TestWhySection:
    def test_valid(self):
        section = WhySection(
            rationale="Market timing is right.",
            business_value="$5M ARR uplift expected.",
        )
        assert section.rationale == "Market timing is right."
        assert section.business_value == "$5M ARR uplift expected."

    def test_missing_rationale_raises(self):
        with pytest.raises(ValidationError):
            WhySection(business_value="Some value")  # type: ignore[call-arg]

    def test_missing_business_value_raises(self):
        with pytest.raises(ValidationError):
            WhySection(rationale="Some rationale")  # type: ignore[call-arg]

    def test_source_doc_ids_defaults_to_empty_list(self):
        section = WhySection(rationale="r", business_value="bv")
        assert section.source_doc_ids == []


# ---------------------------------------------------------------------------
# SuccessSection
# ---------------------------------------------------------------------------

class TestSuccessSection:
    def test_valid(self):
        section = SuccessSection(
            metrics=["Drop-off rate < 30%"],
            kpis=["Conversion rate", "Time to checkout"],
        )
        assert len(section.metrics) == 1
        assert len(section.kpis) == 2

    def test_metrics_must_be_list(self):
        with pytest.raises(ValidationError):
            SuccessSection(metrics="not a list", kpis=["kpi"])  # type: ignore[arg-type]

    def test_kpis_must_be_list(self):
        with pytest.raises(ValidationError):
            SuccessSection(metrics=["metric"], kpis="not a list")  # type: ignore[arg-type]

    def test_empty_lists_are_valid(self):
        section = SuccessSection(metrics=[], kpis=[])
        assert section.metrics == []
        assert section.kpis == []


# ---------------------------------------------------------------------------
# AudienceSection
# ---------------------------------------------------------------------------

class TestAudienceSection:
    def test_valid_minimal(self):
        section = AudienceSection(primary_audience="Shoppers")
        assert section.primary_audience == "Shoppers"
        assert section.secondary_audience is None
        assert section.personas == []

    def test_valid_full(self):
        section = AudienceSection(
            primary_audience="Enterprise IT Admins",
            secondary_audience="End users",
            personas=["Alice the Admin", "Bob the Developer"],
        )
        assert section.secondary_audience == "End users"
        assert len(section.personas) == 2

    def test_missing_primary_audience_raises(self):
        with pytest.raises(ValidationError):
            AudienceSection()  # type: ignore[call-arg]

    def test_source_doc_ids_defaults_to_empty_list(self):
        section = AudienceSection(primary_audience="Users")
        assert section.source_doc_ids == []


# ---------------------------------------------------------------------------
# ConflictEntry
# ---------------------------------------------------------------------------

class TestConflictEntry:
    def test_valid_blocking(self):
        entry = ConflictEntry(
            source_prd_id="payments-v1",
            conflicting_statement="Old system used batch processing.",
            proposed_change="Switch to real-time processing.",
            severity="blocking",
        )
        assert entry.severity == "blocking"

    def test_valid_needs_discussion(self):
        entry = ConflictEntry(
            source_prd_id="auth-v1",
            conflicting_statement="SAML was optional.",
            proposed_change="Make SAML mandatory.",
            severity="needs_discussion",
        )
        assert entry.severity == "needs_discussion"

    def test_valid_minor(self):
        entry = ConflictEntry(
            source_prd_id="notif-v2",
            conflicting_statement="Notification limit was 5/day.",
            proposed_change="Change to 3/day.",
            severity="minor",
        )
        assert entry.severity == "minor"

    def test_invalid_severity_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            ConflictEntry(
                source_prd_id="payments-v1",
                conflicting_statement="Statement",
                proposed_change="Change",
                severity="critical",  # not in Literal
            )
        errors = exc_info.value.errors()
        assert any("severity" in str(e["loc"]) for e in errors)

    def test_missing_source_prd_id_raises(self):
        with pytest.raises(ValidationError):
            ConflictEntry(
                conflicting_statement="Statement",
                proposed_change="Change",
                severity="minor",
            )  # type: ignore[call-arg]

    def test_all_fields_required(self):
        with pytest.raises(ValidationError):
            ConflictEntry()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# OpenQuestionsSection
# ---------------------------------------------------------------------------

class TestOpenQuestionsSection:
    def _make_conflict(self) -> ConflictEntry:
        return ConflictEntry(
            source_prd_id="payments-v1",
            conflicting_statement="Old flow had 3 steps.",
            proposed_change="New flow should have 1 step.",
            severity="needs_discussion",
        )

    def test_valid_with_conflicts_and_gaps(self):
        section = OpenQuestionsSection(
            type1_conflicts=[self._make_conflict()],
            type2_gaps=["What is the fallback if Apple Pay fails?"],
        )
        assert len(section.type1_conflicts) == 1
        assert isinstance(section.type1_conflicts[0], ConflictEntry)
        assert len(section.type2_gaps) == 1

    def test_empty_lists_are_valid(self):
        section = OpenQuestionsSection(type1_conflicts=[], type2_gaps=[])
        assert section.type1_conflicts == []
        assert section.type2_gaps == []

    def test_type1_conflicts_must_be_list_of_conflict_entries(self):
        with pytest.raises(ValidationError):
            OpenQuestionsSection(
                type1_conflicts=["not a ConflictEntry"],  # type: ignore[list-item]
                type2_gaps=[],
            )

    def test_type2_gaps_must_be_list_of_strings(self):
        with pytest.raises(ValidationError):
            OpenQuestionsSection(
                type1_conflicts=[],
                type2_gaps=[{"not": "a string"}],  # type: ignore[list-item]
            )

    def test_missing_type1_conflicts_raises(self):
        with pytest.raises(ValidationError):
            OpenQuestionsSection(type2_gaps=["gap"])  # type: ignore[call-arg]

    def test_missing_type2_gaps_raises(self):
        with pytest.raises(ValidationError):
            OpenQuestionsSection(type1_conflicts=[])  # type: ignore[call-arg]

    def test_source_doc_ids_defaults_to_empty_list(self):
        section = OpenQuestionsSection(type1_conflicts=[], type2_gaps=[])
        assert section.source_doc_ids == []


# ---------------------------------------------------------------------------
# PRDSections (container model)
# ---------------------------------------------------------------------------

class TestPRDSections:
    def _make_all_sections(self) -> dict:
        return {
            "title": TitleSection(title="Test PRD"),
            "description": DescriptionSection(overview="Overview"),
            "problem": ProblemSection(
                problem_statement="The problem", pain_points=["Pain 1"]
            ),
            "why": WhySection(rationale="Because", business_value="Value"),
            "success": SuccessSection(metrics=["Metric 1"], kpis=["KPI 1"]),
            "audience": AudienceSection(primary_audience="Users"),
            "open_questions": OpenQuestionsSection(
                type1_conflicts=[], type2_gaps=["Gap 1"]
            ),
        }

    def test_valid_all_7_sections(self):
        sections = PRDSections(**self._make_all_sections())
        assert sections.title.title == "Test PRD"
        assert sections.description.overview == "Overview"
        assert sections.problem.problem_statement == "The problem"
        assert sections.why.rationale == "Because"
        assert sections.success.metrics == ["Metric 1"]
        assert sections.audience.primary_audience == "Users"
        assert sections.open_questions.type2_gaps == ["Gap 1"]

    def test_missing_any_section_raises(self):
        data = self._make_all_sections()
        for field_name in data:
            partial = {k: v for k, v in data.items() if k != field_name}
            with pytest.raises(ValidationError):
                PRDSections(**partial)

    def test_has_exactly_7_section_fields(self):
        sections = PRDSections(**self._make_all_sections())
        expected_fields = {
            "title", "description", "problem", "why",
            "success", "audience", "open_questions",
        }
        actual_fields = set(sections.model_fields.keys())
        assert expected_fields == actual_fields


# ---------------------------------------------------------------------------
# CapabilityList
# ---------------------------------------------------------------------------

class TestCapabilityList:
    def test_valid(self):
        cap_list = CapabilityList(capabilities=["Saved cards", "Apple Pay", "Order summary"])
        assert len(cap_list.capabilities) == 3

    def test_empty_capabilities_valid(self):
        cap_list = CapabilityList(capabilities=[])
        assert cap_list.capabilities == []

    def test_missing_capabilities_raises(self):
        with pytest.raises(ValidationError):
            CapabilityList()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# SlicePlan
# ---------------------------------------------------------------------------

class TestSlicePlan:
    def test_valid(self):
        plan = SlicePlan(slices=["Slice 1", "Slice 2", "Slice 3"])
        assert len(plan.slices) == 3

    def test_empty_slices_valid(self):
        plan = SlicePlan(slices=[])
        assert plan.slices == []

    def test_missing_slices_raises(self):
        with pytest.raises(ValidationError):
            SlicePlan()  # type: ignore[call-arg]

    def test_slices_must_be_list(self):
        with pytest.raises(ValidationError):
            SlicePlan(slices="not a list")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ValidationRow
# ---------------------------------------------------------------------------

class TestValidationRow:
    def test_valid(self):
        row = ValidationRow(
            field="email",
            rule="Must be valid RFC 5322 format",
            error_message="Please enter a valid email address.",
        )
        assert row.field == "email"
        assert row.rule == "Must be valid RFC 5322 format"
        assert row.error_message == "Please enter a valid email address."

    def test_missing_field_raises(self):
        with pytest.raises(ValidationError):
            ValidationRow(rule="rule", error_message="err")  # type: ignore[call-arg]

    def test_missing_rule_raises(self):
        with pytest.raises(ValidationError):
            ValidationRow(field="email", error_message="err")  # type: ignore[call-arg]

    def test_missing_error_message_raises(self):
        with pytest.raises(ValidationError):
            ValidationRow(field="email", rule="rule")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# UserStoryModel
# ---------------------------------------------------------------------------

class TestUserStoryModel:
    def _make_story(self) -> dict:
        return {
            "title": "User can save payment method",
            "description": (
                "As a shopper, I want to save my card details, "
                "so that I can check out faster next time."
            ),
            "acceptance_criteria": [
                "Happy path: Card is saved and shown in profile.",
                "Alt path: User can skip saving.",
                "Error path: Invalid card number shows validation error.",
            ],
            "validations": [
                ValidationRow(
                    field="card_number",
                    rule="Must be 16 digits",
                    error_message="Card number must be 16 digits.",
                )
            ],
        }

    def test_valid_full_story(self):
        story = UserStoryModel(**self._make_story())
        assert story.title == "User can save payment method"
        assert "As a shopper" in story.description
        assert len(story.acceptance_criteria) == 3
        assert len(story.validations) == 1
        assert isinstance(story.validations[0], ValidationRow)

    def test_missing_title_raises(self):
        data = self._make_story()
        del data["title"]
        with pytest.raises(ValidationError):
            UserStoryModel(**data)

    def test_missing_description_raises(self):
        data = self._make_story()
        del data["description"]
        with pytest.raises(ValidationError):
            UserStoryModel(**data)

    def test_missing_acceptance_criteria_raises(self):
        data = self._make_story()
        del data["acceptance_criteria"]
        with pytest.raises(ValidationError):
            UserStoryModel(**data)

    def test_missing_validations_raises(self):
        data = self._make_story()
        del data["validations"]
        with pytest.raises(ValidationError):
            UserStoryModel(**data)

    def test_acceptance_criteria_empty_list_is_valid(self):
        data = self._make_story()
        data["acceptance_criteria"] = []
        story = UserStoryModel(**data)
        assert story.acceptance_criteria == []

    def test_validations_empty_list_is_valid(self):
        data = self._make_story()
        data["validations"] = []
        story = UserStoryModel(**data)
        assert story.validations == []
