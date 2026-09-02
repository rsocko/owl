from __future__ import annotations

import random

from doc_intelligence_hub.modules.ocr_quality.sampling import (
    SampleCandidate,
    select_stratified_sample,
    stratum_key,
)


def _candidate(doc_id: int, *, score: int = 60, doc_type: str = "invoice") -> SampleCandidate:
    return SampleCandidate(
        document_id=doc_id,
        preliminary_score=score,
        document_type=doc_type,
        correspondent="Acme",
        created="2024-01-01T00:00:00",
        downstream_outcome="reviewed",
        content_length=500,
    )


class TestStratumKey:
    def test_deterministic_for_same_input(self):
        c = _candidate(1)
        assert stratum_key(c) == stratum_key(c)

    def test_unknown_fields_bucketed_safely(self):
        c = SampleCandidate(
            document_id=1,
            preliminary_score=50,
            document_type=None,
            correspondent=None,
            created=None,
            downstream_outcome=None,
            content_length=0,
        )
        key = stratum_key(c)
        assert "unknown" in key
        assert "age_unknown" in key


class TestSelectStratifiedSample:
    def test_empty_candidates_returns_empty(self):
        assert select_stratified_sample([], seed="s", target_size=10) == []

    def test_target_size_zero_returns_empty(self):
        assert select_stratified_sample([_candidate(1)], seed="s", target_size=0) == []

    def test_returns_all_when_fewer_than_target(self):
        candidates = [_candidate(i) for i in range(1, 6)]
        decisions = select_stratified_sample(candidates, seed="s", target_size=100)
        assert len(decisions) == 5
        assert {d.document_id for d in decisions} == {1, 2, 3, 4, 5}

    def test_deterministic_regardless_of_input_order(self):
        candidates = [_candidate(i, score=i % 100, doc_type=f"type{i % 4}") for i in range(1, 200)]
        shuffled = list(candidates)
        random.Random(42).shuffle(shuffled)

        d1 = select_stratified_sample(candidates, seed="seed-a", target_size=30)
        d2 = select_stratified_sample(shuffled, seed="seed-a", target_size=30)

        assert [d.document_id for d in d1] == [d.document_id for d in d2]

    def test_different_seeds_can_produce_different_samples(self):
        candidates = [_candidate(i, score=i % 100, doc_type=f"type{i % 5}") for i in range(1, 500)]
        d1 = select_stratified_sample(candidates, seed="seed-a", target_size=50)
        d2 = select_stratified_sample(candidates, seed="seed-b", target_size=50)
        assert {d.document_id for d in d1} != {d.document_id for d in d2}

    def test_never_exceeds_target_size(self):
        candidates = [
            _candidate(i, score=i % 100, doc_type=f"type{i % 10}") for i in range(1, 1000)
        ]
        decisions = select_stratified_sample(
            candidates, seed="s", target_size=40, min_per_stratum=2
        )
        assert len(decisions) <= 40

    def test_min_per_stratum_honored_when_budget_allows(self):
        candidates = [_candidate(i, doc_type=f"type{i % 5}") for i in range(1, 100)]
        decisions = select_stratified_sample(
            candidates, seed="s", target_size=50, min_per_stratum=3
        )
        by_stratum: dict[str, int] = {}
        for d in decisions:
            by_stratum[d.stratum_key] = by_stratum.get(d.stratum_key, 0) + 1
        assert all(count >= 1 for count in by_stratum.values())

    def test_more_strata_than_budget_trims_deterministically(self):
        # 1000 distinct correspondents (so 1000 strata), min_per_stratum=2,
        # but target_size only 100 -> minimums alone (2000) exceed target.
        candidates = [
            SampleCandidate(
                document_id=i,
                preliminary_score=60,
                document_type="invoice",
                correspondent=f"corr-{i}",
                created="2024-01-01T00:00:00",
                downstream_outcome="reviewed",
                content_length=500,
            )
            for i in range(1, 1001)
        ]
        d1 = select_stratified_sample(candidates, seed="s", target_size=100, min_per_stratum=2)
        d2 = select_stratified_sample(candidates, seed="s", target_size=100, min_per_stratum=2)
        assert len(d1) <= 100
        assert [d.document_id for d in d1] == [d.document_id for d in d2]

    def test_no_duplicate_document_ids_in_result(self):
        candidates = [_candidate(i, score=i % 100, doc_type=f"type{i % 7}") for i in range(1, 300)]
        decisions = select_stratified_sample(candidates, seed="s", target_size=60)
        ids = [d.document_id for d in decisions]
        assert len(ids) == len(set(ids))
