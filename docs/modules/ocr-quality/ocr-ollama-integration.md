---
title: "OCR Secondary Review"
sidebar_label: OCR Secondary Review
sidebar_position: 4
---

# OCR Secondary Review

**Status:** Deferred, optional advisory capability
**Revised:** 2026-08-24

## Decision

An LLM is not part of the initial scoring or acceptance gate.

The previous design used `phi3:mini` to classify a short text sample and compare
two OCR snippets. That approach can miss page-level failures, tables, incorrect
numbers, overlay misalignment, or regressions outside the sample. It can also
prefer fluent but incorrect text.

Initial quality decisions instead use deterministic, page-aware signals,
candidate geometry/confidence, downstream extraction evidence, and human review.

## Possible future role

After corpus calibration, issue
[#17](https://github.com/rsocko/owl/issues/17) may add provider-neutral
secondary review for genuinely uncertain cases.

Allowed uses:

- explain why deterministic signals disagree;
- identify regions that deserve human attention;
- classify whether a region is prose, table, code-heavy, or mixed; and
- provide an advisory comparison reason.

Disallowed uses:

- silently changing deterministic scores;
- accepting or replacing a candidate;
- treating fluency as proof of OCR accuracy;
- comparing only one short sample when the decision affects a whole document;
- sending document content outside the configured privacy boundary; or
- succeeding with a plausible fallback when the provider fails.

## Contract

Any future provider must:

- operate behind a provider-neutral interface;
- receive only the minimum authorized evidence;
- return a schema-validated advisory result with model/version provenance;
- preserve deterministic results when unavailable or invalid;
- expose disagreement rather than hide it;
- record timeout and invalid-response outcomes explicitly; and
- be evaluated against the human-labeled calibration set.

The comparison UI labels LLM output as advisory and keeps it visually separate
from deterministic blocking checks.

## Activation gate

Do not implement secondary review until:

1. the baseline and human calibration set exist;
2. deterministic false positives/negatives are measured;
3. uncertain cases form a meaningful population;
4. the proposed model improves review precision on held-out labels; and
5. privacy, latency, and cost are acceptable.
