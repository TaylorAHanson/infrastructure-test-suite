"""Service seam: the NL→Test compiler (Assistant write-path, ADR-0006/0017).

Deterministic assertions over a non-deterministic dependency: the model is a
stub returning canned JSON, so these assert the *harness's* grounding + validator
+ guardrails, not the LLM's wording (spec #22 Testing Decisions).
"""
from __future__ import annotations

import json

from app.core.enums import SafetyClass
from app.models import Target
from app.services import authoring
from app.services.roles import validate_roles_for_class


def _model(plan: dict):
    """A stub pinned-model caller returning a fixed authoring plan as JSON."""
    def call(messages):
        return json.dumps(plan)
    return call


def test_plain_sentence_yields_a_valid_draft(seeded_session):
    plan = {
        "name": "SQL warehouse returns rows",
        "operation": {"service": "statement_execution", "method": "execute_statement",
                      "params": {"statement": "SELECT 1", "demo_row_count": 1}},
        "assertion": "the query returns at least one row",
        "handler_key": "sql_warehouse",
    }
    draft = authoring.draft_from_intent(
        seeded_session, "run a SQL query and confirm it returns rows",
        model_call=_model(plan))

    assert draft.clarifying_question is None
    assert len(draft.checks) == 1
    check = draft.checks[0]
    assert check.operation["service"] == "statement_execution"
    assert check.compilable and check.evaluator == "deterministic"
    # execute_statement is POST-shaped (it can mutate), so the coarse verb-derived
    # tier is R1 — the author/model would set verb=GET for a pure read.
    assert check.risk_tier == "R1"
    assert draft.derivation["chosen_operation"]["method"] == "execute_statement"


def test_grounds_on_sdk_surface_operation_never_pre_declared(seeded_session):
    # jobs.list is on the open SDK surface but no seeded handler "manifest"
    # declares it — the compiler can still author it (ADR-0017).
    plan = {"operation": {"service": "jobs", "method": "list", "params": {}},
            "assertion": "the operation succeeds", "name": "List jobs"}
    draft = authoring.draft_from_intent(seeded_session, "list the jobs",
                                        model_call=_model(plan))
    assert draft.checks and draft.checks[0].operation["service"] == "jobs"
    assert draft.checks[0].operation["verb"] == "GET"


def test_blocklisted_r3_operation_is_refused_not_drafted(seeded_session):
    plan = {"operation": {"service": "workspaces", "method": "delete", "params": {}},
            "assertion": "the workspace is deleted", "name": "delete ws"}
    draft = authoring.draft_from_intent(seeded_session, "delete the workspace",
                                        model_call=_model(plan))
    assert draft.checks == []          # nothing dangerous drafted
    assert draft.refusals              # refusal recorded loudly
    assert draft.refusals[0]["tier"] == "R3"


def test_counterfactual_suggested_from_surface_hint(seeded_session):
    plan = {"operation": {"service": "clusters", "method": "create", "params": {}},
            "assertion": "the operation succeeds", "name": "cluster create"}
    draft = authoring.draft_from_intent(
        seeded_session, "start a cluster under the compute policy",
        model_call=_model(plan))
    assert draft.suggested_counterfactual is not None
    assert "denied" in draft.suggested_counterfactual["assertion"].lower()
    # Suggested, never applied: the draft still has only the positive check.
    assert all(c.kind == "assertion" for c in draft.checks)


def test_run_as_role_only_offered_when_target_registers_it(session):
    from app.services.seed import seed_all
    seed_all(session, with_demo_targets=False)

    roles = validate_roles_for_class(
        [{"group_id": "g-analyst", "label": "analyst", "safety_class": "read-only"}],
        SafetyClass.SANDBOX)
    target = Target(name="sbx", workspace_name="sbx-ws", safety_class=SafetyClass.SANDBOX,
                    assumable_roles=roles)
    session.add(target)
    session.commit()
    session.refresh(target)

    base = {"operation": {"service": "clusters", "method": "create", "params": {}},
            "assertion": "the operation succeeds", "name": "c"}

    # A role the target actually registered → offered.
    d1 = authoring.draft_from_intent(session, "start a cluster as the analyst", target=target,
                                     model_call=_model({**base, "run_as_role": "analyst"}))
    assert d1.suggested_run_as_role is not None
    assert d1.suggested_run_as_role["role"]["label"] == "analyst"

    # A role the model invented (not registered) → never offered.
    d2 = authoring.draft_from_intent(session, "start a cluster as admin", target=target,
                                     model_call=_model({**base, "run_as_role": "ghost-admin"}))
    assert d2.suggested_run_as_role is None


def test_no_registered_roles_means_no_role_suggestion(seeded_session):
    plan = {"operation": {"service": "clusters", "method": "create", "params": {}},
            "assertion": "the operation succeeds", "run_as_role": "analyst"}
    # Seeded demo targets carry no assumable roles; with no page target either,
    # a run-as-role can never be suggested.
    draft = authoring.draft_from_intent(seeded_session, "start a cluster",
                                        model_call=_model(plan))
    assert draft.suggested_run_as_role is None


def test_missing_operation_slot_asks_one_clarifying_question(seeded_session):
    # Model gives nothing usable and the intent matches no surface entry.
    draft = authoring.draft_from_intent(seeded_session, "zzzz qwerty",
                                        model_call=_model({}))
    assert draft.clarifying_question
    assert draft.checks == []


def test_weak_surface_match_abstains_instead_of_guessing(seeded_session):
    # "are" fuzzy-matches a substring of the "warehouse" keyword, so the surface
    # isn't empty — but it covers only 1 of 6 query terms, well below the
    # confidence bar. The fallback must abstain rather than silently draft a
    # check from a greeting (issue #111).
    draft = authoring.draft_from_intent(
        seeded_session, "hi there, how are you doing today?", model_call=_model({}))
    assert draft.clarifying_question
    assert draft.checks == []


def test_works_without_a_model_using_deterministic_fallback(seeded_session):
    # No model at all: grounds on the surface and still produces a draft.
    draft = authoring.draft_from_intent(seeded_session, "run a sql query on the warehouse",
                                        model_call=None)
    assert draft.checks
    assert draft.derivation["model_available"] is False


# ------------------ conversational-turn abstention guard (issue #112) ------------------
def test_is_conversational_turn_recognises_greetings_and_meta_questions():
    for turn in ("hi", "Hello!", "hey there", "Test are you working?",
                 "what can you do?", "who are you?", "thanks!"):
        assert authoring.is_conversational_turn(turn), turn


def test_is_conversational_turn_leaves_real_intents_alone():
    for turn in ("run a sql query on the warehouse", "list the jobs",
                 "create a cluster and confirm it starts", "zzzz qwerty", ""):
        assert not authoring.is_conversational_turn(turn), turn
