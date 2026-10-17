---
title: Intake Review User Guide
sidebar_label: Intake Review
sidebar_position: 2
---

# Intake Review

Intake Review is OWL's document-focused Paperless Inbox workspace. It keeps the
source document visible while metadata, action assessment, and OCR quality are
reviewed together. Filing the document and confirming its actions remain
independent decisions.

Open **Workflow → Intake Review** in the OWL web interface. The document list is
loaded from the Action Queue's configured intake tags (normally `Inbox`).

## Review workflow

The workspace keeps the live document date, correspondent, and document type
visible above the preview. The right panel contains:

- **Metadata** — current Paperless metadata, unapplied Paperless ML/AI
  suggestions, removable tags, storage path, and Paperless custom fields.
- **Actions** — all inferred and user-added actions for the source document.
  Completed actions remain visible as history but are excluded from pending
  counts and confirmation.
- **OCR** — the searchable text and quality score. Green is 90–100, amber is
  75–89, and red is below 75. Poor OCR is flagged on the tab before it opens.

Arrow keys move focus through the document list without changing the active
document. Press **Enter** or click a row to select it.

## Metadata and filing decisions

Paperless remains authoritative. OWL loads the current `modified` revision and
rejects a save if Paperless changed while the document was open.

| Control | Result |
|---|---|
| **Save changes** | Persist metadata without changing Inbox disposition |
| **Defer & next** | Persist metadata, keep the intake tag, and advance |
| **Save & file** | Persist metadata, remove configured intake tags, and advance |
| **File only** | File the document while leaving proposed actions pending |
| **Confirm action(s) & file** | Confirm active actions and file the document |

Tags, correspondents, document types, and storage paths can be created inline
when Paperless permits it. OWL prevents case-insensitive duplicates and shows
Paperless permission or validation errors instead of assuming success.

## Action editing and audit history

Actions can be edited directly. Fields include type, title, payee or owner,
amount, due date, and reference. Inferred actions can be marked **Not an
action**; user-added actions can be deleted.

**Mark done** records completion but leaves the action visible. **Undo
completion** restores the pending state and appends a corrective lifecycle
event. Completion history is not erased. Every action update uses the Action
Queue version for optimistic concurrency.

Action Queue exposes **Review document**, which opens this same workspace on
the Actions tab rather than maintaining a second document editor.

## Analysis and OCR freshness

**Refresh analysis** reloads Paperless suggestions and forces action analysis
for the current source revision. It is blocked while metadata edits are
unsaved, and a changed Paperless revision returns a conflict.

OCR remediation continues through OWL's OCR Quality workflow. After accepting
new OCR, return to Intake Review and refresh analysis so metadata suggestions
and inferred actions are evaluated against the new searchable text.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/intake/documents` | List configured intake documents |
| `GET /api/intake/documents/{id}` | Load the review contract |
| `PUT /api/intake/documents/{id}` | Save metadata and disposition |
| `POST /api/intake/documents/{id}/refresh` | Refresh suggestions and analysis |
| `POST /api/intake/taxonomies/{kind}` | Create a Paperless taxonomy value |
| `POST /api/intake/documents/{id}/actions` | Add a user action |
| `DELETE /api/intake/actions/{id}` | Delete a user-added action |

The API never returns an empty success response for unauthorized or malformed
Paperless source access. Suggestion capability failures are reported per source
so the rest of the document remains reviewable without hiding the outage.
