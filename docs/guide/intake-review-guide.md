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
loaded from the Action Queue's configured intake tags (normally `Inbox`). Each
row includes a Paperless thumbnail, resolved correspondent and document type,
and an **Action needed** indicator where applicable. Search and filters narrow
the queue without changing the selected document.

## Review workflow

The workspace keeps the live document date, correspondent, and document type
visible above the preview. The right panel contains:

- **Metadata** — current Paperless metadata, field-local Paperless ML/AI
  suggestions, removable Paperless-colored tags in one searchable entry
  control, storage path, and relevant
  Paperless custom fields. Operational Action/Obligation projections are not
  presented as editable document metadata.
- **Actions** — all inferred and user-added actions for the source document.
  Completed actions remain visible as history but are excluded from pending
  counts and confirmation.
- **OCR** — the searchable text and quality score. Green is 90–100, amber is
  75–89, and red is below 75. Poor OCR is flagged on the tab before it opens.

Arrow keys move focus through the document list without changing the active
document. Press **Enter** or click a row to select it.

The selected document is stored in the `document` query parameter, so a review
can be bookmarked or shared, for example
`#/intake-review?document=123&tab=actions`.

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

Saving in place keeps the current document selected and visible, even when an
edit means it no longer matches the active queue filter. The item leaves that
filtered list after the reviewer selects another document, defers, or files it.

Tags, correspondents, document types, and storage paths use one searchable
combobox for selection and creation. Typing a value that does not exist offers
an explicit create action when Paperless permits it. OWL prevents
case-insensitive duplicates and shows Paperless permission or validation errors
instead of assuming success. AI suggestions never create taxonomy values
automatically, so no Paperless setting change is required to prevent unreviewed
tags or document types from being added.

Tag options retain Paperless colors. Names containing hierarchy separators
such as `Parent / Child` or `Parent::Child` are displayed with parent context
while preserving the original Paperless tag name.

Tag suggestions are restricted to tags that already exist in Paperless. Novel
AI-generated tag names are not offered as suggestions; reviewers can still
create a new tag explicitly by typing it into the tag control. This is enforced
by OWL and does not require a Paperless setting change.

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

The preview and core Paperless metadata load first. Suggestions, actions, and
OCR text then load independently with local progress and error states, so a
slow analysis service does not blank the entire workspace. Suggestions are
cached by document ID and Paperless `modified` revision for up to six hours.
Changing the source revision or choosing **Refresh analysis** bypasses the
snapshot.

**Refresh analysis** reloads Paperless suggestions and forces action analysis
for the current source revision. It is blocked while metadata edits are
unsaved, and a changed Paperless revision returns a conflict.

OCR remediation continues through OWL's OCR Quality workflow. After accepting
new OCR, return to Intake Review and refresh analysis so metadata suggestions
and inferred actions are evaluated against the new searchable text.

Hover the document preview to reveal **Expand** and **New tab** controls.
Expanded preview uses the full browser viewport and closes with Escape. When a
browser-facing Paperless URL is configured, **Open in Paperless** is also
available in the document header and expanded viewer.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/intake/documents` | List configured intake documents |
| `GET /api/intake/documents/{id}` | Load the review contract |
| `GET /api/intake/documents/{id}/core` | Load source metadata and taxonomies |
| `GET /api/intake/documents/{id}/suggestions` | Load revision-cached suggestions |
| `GET /api/intake/documents/{id}/actions` | Load actions and analysis state |
| `GET /api/intake/documents/{id}/ocr` | Load searchable text and OCR quality |
| `PUT /api/intake/documents/{id}` | Save metadata and disposition |
| `POST /api/intake/documents/{id}/refresh` | Refresh suggestions and analysis |
| `POST /api/intake/taxonomies/{kind}` | Create a Paperless taxonomy value |
| `POST /api/intake/documents/{id}/actions` | Add a user action |
| `DELETE /api/intake/actions/{id}` | Delete a user-added action |

The API never returns an empty success response for unauthorized or malformed
Paperless source access. Suggestion capability failures are reported per source
so the rest of the document remains reviewable without hiding the outage.
