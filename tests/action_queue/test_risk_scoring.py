"""Tests for the composite risk scoring module."""

from datetime import date, timedelta

import pytest

from doc_intelligence_hub.modules.action_queue.risk_scoring import (
    compute_risk_score,
    recalculate_risk_scores,
)


class TestComputeRiskScore:
    """Test compute_risk_score with various factor combinations."""

    def test_minimal_action_low_risk(self):
        """A LOW urgency action with no due date or amount should score low."""
        score = compute_risk_score(urgency="LOW", action_type="FILE")
        assert 0 <= score <= 20

    def test_critical_urgency_high_base(self):
        """CRITICAL urgency should produce a high base score."""
        score = compute_risk_score(urgency="CRITICAL", action_type="REVIEW", as_of=date(2026, 1, 15))
        assert score >= 40

    def test_overdue_increases_score(self):
        """An overdue action should score higher than one due in the future."""
        as_of = date(2026, 7, 20)
        overdue = compute_risk_score(
            urgency="MEDIUM", due_date=date(2026, 7, 10), as_of=as_of
        )
        future = compute_risk_score(
            urgency="MEDIUM", due_date=date(2026, 8, 20), as_of=as_of
        )
        assert overdue > future

    def test_due_today_scores_high(self):
        """An action due today should score significantly."""
        as_of = date(2026, 7, 20)
        score = compute_risk_score(urgency="MEDIUM", due_date=as_of, as_of=as_of)
        assert score >= 40

    def test_high_amount_increases_score(self):
        """A high financial amount should boost the score."""
        base = compute_risk_score(urgency="MEDIUM", amount=None)
        with_amount = compute_risk_score(urgency="MEDIUM", amount=5000)
        assert with_amount > base

    def test_pay_action_type_multiplier(self):
        """PAY action type should score higher than FILE for same urgency."""
        pay = compute_risk_score(urgency="HIGH", action_type="PAY", as_of=date(2026, 1, 1))
        file = compute_risk_score(urgency="HIGH", action_type="FILE", as_of=date(2026, 1, 1))
        assert pay > file

    def test_high_confidence_boosts_score(self):
        """High confidence should add to the score."""
        low_conf = compute_risk_score(urgency="MEDIUM", confidence=20)
        high_conf = compute_risk_score(urgency="MEDIUM", confidence=90)
        assert high_conf > low_conf

    def test_score_clamped_to_100(self):
        """Score should never exceed 100 even with all max factors."""
        score = compute_risk_score(
            urgency="CRITICAL",
            due_date=date(2026, 1, 1),  # Very overdue
            amount=10000,
            confidence=100,
            action_type="PAY",
            as_of=date(2026, 7, 20),
        )
        assert score <= 100

    def test_score_minimum_zero(self):
        """Score should never go below 0."""
        score = compute_risk_score(
            urgency="LOW",
            confidence=10,  # Low confidence penalty
            action_type="SCHEDULE",
            as_of=date(2026, 1, 1),
        )
        assert score >= 0

    def test_string_due_date_parsed(self):
        """Due date as ISO string should work correctly."""
        as_of = date(2026, 7, 20)
        score = compute_risk_score(urgency="HIGH", due_date="2026-07-15", as_of=as_of)
        assert score > 30  # Overdue by 5 days

    def test_invalid_date_string_handled(self):
        """Invalid date string should not raise."""
        score = compute_risk_score(urgency="MEDIUM", due_date="not-a-date")
        assert score >= 0

    def test_none_due_date_gives_small_base(self):
        """None due date should give a small base score (undated could be urgent)."""
        score_no_date = compute_risk_score(urgency="MEDIUM", due_date=None)
        score_far_future = compute_risk_score(
            urgency="MEDIUM", due_date=date(2027, 12, 31), as_of=date(2026, 7, 20)
        )
        # No-date gets a small base (5), far future gets 3
        assert score_no_date >= score_far_future


class TestRecalculateRiskScores:
    """Test the batch recalculation helper."""

    def test_recalculates_changed_scores(self):
        """Should update actions whose scores differ from current value."""

        class FakeAction:
            def __init__(self, urgency, due_date, amount, confidence, action_type, risk_score):
                self.urgency = urgency
                self.due_date = due_date
                self.amount = amount
                self.confidence = confidence
                self.action_type = action_type
                self.risk_score = risk_score

        # Action with stale score of 0
        action = FakeAction(
            urgency="CRITICAL",
            due_date=date.today() - timedelta(days=5),
            amount=1000,
            confidence=85,
            action_type="PAY",
            risk_score=0,
        )
        changed = recalculate_risk_scores([action])
        assert changed == 1
        assert action.risk_score > 0

    def test_no_change_when_score_matches(self):
        """Should return 0 changed when score already matches."""

        class FakeAction:
            def __init__(self):
                self.urgency = "LOW"
                self.due_date = None
                self.amount = None
                self.confidence = 0
                self.action_type = "FILE"
                self.risk_score = compute_risk_score(
                    urgency="LOW", action_type="FILE", confidence=0
                )

        action = FakeAction()
        changed = recalculate_risk_scores([action])
        assert changed == 0
