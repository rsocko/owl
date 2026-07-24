# Triage & Correction UI — Quick Reference

## What It Is

A power-user admin interface at `:8001/admin/triage` for reviewing and correcting automated document relationship decisions made by the Document Intelligence system.

## Two Core Workflows

### 1. EOB ↔ Bill Match Review
- **See**: Queue of matches below confidence threshold (default: 75%)
- **Do**: Confirm correct matches, reject wrong ones, re-link to the right document
- **Why flagged**: Amount mismatch, provider name variation, multiple similar candidates, first-time provider

### 2. Statement Grouping Correction
- **See**: Series where the system detected anomalies (multiple accounts, irregular gaps, name collisions)
- **Do**: Split one series into two (different accounts), merge incorrectly-split series, reassign individual documents
- **Why flagged**: Multiple account numbers in same series, similar correspondent names, title variation creating false splits

## Where It Lives

| Component | Surface | Who Uses It |
|-----------|---------|-------------|
| Queue counts / alerts | Mission Control | Daily glance — "12 items need attention" |
| Triage correction UI | DI Admin (`:8001/admin/triage`) | When you sit down to fix things |
| Paperless document links | Paperless-ngx | After corrections are written back |

## Key Design Decisions

1. **Queue-based** — Process items one at a time, like email triage
2. **Keyboard shortcuts** — Y/N/S for confirm/reject/skip
3. **Non-destructive** — All corrections recorded as events; original decisions preserved
4. **Corrections feed learning** — Over time, scoring weights adjust based on patterns of corrections

## Files

- **Design**: `docs/triage-correction/DESIGN.md`
- **Mockups**:
  - `mockups/triage-correction/triage-queue.html` — Main queue with list + detail panel
  - `mockups/triage-correction/eob-match-review.html` — Full EOB match review page
  - `mockups/triage-correction/statement-series-detail.html` — Series split/merge/reassign

## API Surface

```
GET  /api/triage/queue          — List queue items
POST /api/triage/queue/:id/resolve
POST /api/eob/matches/:id/confirm
POST /api/eob/matches/:id/reject
POST /api/statements/series/:id/split
POST /api/statements/series/merge
POST /api/statements/series/:id/reassign
```

## Implementation Priority

1. Queue infrastructure + auto-flagging rules
2. EOB match review (most common triage action)
3. Statement grouping correction (less frequent but higher impact)
4. Smart flagging + learning from corrections
