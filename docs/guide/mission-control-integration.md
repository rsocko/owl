---
title: Mission Control Integration
sidebar_label: Mission Control Integration
---

# Mission Control Integration

Mission Control (MC) reconciles OWL Action Queue items through the flat-array
connector API. OWL remains the source of truth for document actions and applies
every lifecycle mutation to its database before writing the Action Status back
to Paperless-ngx. Successful Paperless writes update `last_synced_status`;
resolved statuses also remove configured intake tags when that behavior is
enabled.

## List and reconcile actions

```http
GET /api/action-queue/actions?status=all&limit=100&offset=0&updated_since=2026-08-22T12:00:00Z
```

| Parameter | Default | Behavior |
|-----------|---------|----------|
| `status` | all | One of `pending`, `acknowledged`, `completed`, `snoozed`, `dismissed`, `not_an_action`, or `all` |
| `limit` | `100` | Page size from 1 through 500 |
| `offset` | `0` | Deterministic page offset |
| `updated_since` | none | Inclusive ISO-8601 lower bound on `updated_at` |

The response remains a flat JSON array for backward compatibility. Each item
includes `id`, normalized lowercase `action_type` and `urgency`, normalized OWL
`status`, `created_at`, `updated_at`, `completed_at`, `snoozed_until`, and the
existing document/action metadata. Results preserve the legacy newest-first
order by `created_at`, then `id`, so unpaginated clients continue to receive the
newest actions.

## Change lifecycle status

```http
PATCH /api/action-queue/actions/{id}
Content-Type: application/json

{"status": "completed"}
```

Accepted status values are:

- `pending` or `reopen` to reopen the action and clear completion,
  acknowledgement, and snooze timestamps
- `completed` or legacy `done` to complete the action
- `dismissed` to resolve without completion

Unsupported values return `422` and are never persisted. A successful response
is:

```json
{"status": "ok", "id": "42", "new_status": "completed"}
```

## Snooze at the source

```http
POST /api/action-queue/actions/{id}/snooze
Content-Type: application/json

{"until": "2026-09-01T09:00:00Z"}
```

OWL stores the `snoozed` lifecycle status and `snoozed_until`, writes the status
to Paperless when write-back is enabled, and returns both values.

## Submit classifier feedback

```http
POST /api/action-queue/actions/{id}/feedback
Content-Type: application/json

{"feedback_type": "wrong_urgency", "corrected_urgency": "low"}
```

Supported payloads are:

| `feedback_type` | Optional correction |
|-----------------|---------------------|
| `not_an_action` | `reason` |
| `misclassified` | `corrected_action_type` |
| `wrong_urgency` | `corrected_urgency` |
| `wrong_amount` | `corrected_amount` |

Corrections are recorded in Action Queue feedback history and update the source
action when a corrected value is supplied. `not_an_action` always records
feedback, changes the OWL lifecycle status, and writes that status to Paperless.
