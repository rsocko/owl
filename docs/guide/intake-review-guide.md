---
title: Inbox Review User Guide
sidebar_label: Inbox Review
sidebar_position: 2
---

# Inbox Review

Inbox Review is OWL's document-focused Paperless Inbox workspace. It keeps the
source document visible while metadata, action assessment, and OCR quality are
reviewed together. Filing the document and confirming its actions remain
independent decisions.

Open **Workflow → Inbox Review** in the OWL web interface. The document list is
loaded specifically from the Paperless tag named `Inbox`. Action Queue scan
mode, monitor tags, and saved views do not change Inbox Review's scope. Each row
includes a Paperless thumbnail, resolved correspondent and document type, and
an **Action needed** indicator where applicable. Search and filters apply to
the full Paperless Inbox without changing the selected document. OWL initially
loads 100 matching documents and shows the full matching count; use **Load
more** at the end of the list to retrieve the next page.

Paperless must contain a tag named `Inbox`. If that tag is missing, OWL reports
the configuration problem instead of showing an empty or broader queue.

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
- **OCR** — the multidimensional OCR assessment, candidate state, and a
  collapsed preview of the current searchable text. Review-worthy assessments
  are flagged on the tab before it opens.

Arrow keys move focus through the document list without changing the active
document. Press **Enter** or click a row to select it.

The selected document is stored in the `document` query parameter, so a review
can be bookmarked or shared, for example
`#/inbox?document=123&tab=actions`.

## Metadata and filing decisions

Paperless remains authoritative. OWL loads the current `modified` revision and
compares it again immediately before writeback. OWL sends only fields changed
in the workspace, so unrelated Paperless edits are preserved.

If the same field changed in both places, OWL shows the loaded, current
Paperless, and reviewed OWL values. Choose **Keep Paperless** or **Apply OWL**
for each conflict, or choose **Continue editing** without discarding the draft.
If a prior request succeeded but its response was lost, matching values are
treated as an idempotent replay rather than written again.

| Control | Result |
|---|---|
| **Save changes** | Persist metadata without changing Inbox disposition |
| **Defer & next** | Discard unsaved metadata changes, keep the `Inbox` tag, and advance |
| **Save & file** | Persist metadata, remove the `Inbox` tag, and advance |
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

Durable review fields such as Document Amount, Document Due Date, and Account
Identifier are editable in the metadata panel. Account Identifier shows the
exact Paperless value in this trusted review flow and requires an identifier
type when changed so OWL can validate the value before writing it back.

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
OCR data then load independently with local progress and error states, so a
slow analysis service does not blank the entire workspace. The OCR tab uses
OWL's multidimensional assessment as the authority: overall status, overlay
and machine-extraction scores, scorer freshness, candidate state, top reasons,
page profile, and searchable text. The older action-pipeline text-quality
score is not presented as the overall OCR score.

If the corpus inventory has not reached the selected document yet, opening the
OCR tab automatically creates the inexpensive text-only assessment from the
searchable OCR already stored in Paperless. Deeper PDF geometry and overlay
analysis remain explicit through **Inspect OCR**. The searchable-text preview
is collapsed by default so assessment evidence and actions stay above the fold.

Suggestions are cached by document ID and Paperless `modified` revision for up
to six hours. Changing the source revision or choosing **Refresh analysis**
bypasses the snapshot.

**Refresh analysis** reloads Paperless suggestions and forces action analysis
for the current source revision. It is blocked while metadata edits are
unsaved, and a changed Paperless revision returns a conflict.

**Inspect OCR** enters a focus inspector without leaving Inbox Review. The
originating queue becomes a compact thumbnail rail, and **Return to review**
restores the same document and OCR tab. Current-only inspection includes the
page image, word boxes, heatmap and flag tools, word details, synchronized
page navigation and zoom, annotations, and region-driven metadata correction.
Selecting a candidate changes the canvas to synchronized current-versus-
candidate views with Visual, Text diff, and Page findings modes.

The persistent evidence rail shows current and candidate scores and deltas,
blocking findings, candidate provenance and state, assessment freshness, and
downstream invalidation state. Candidate generation, cancellation, per-
document accept/reject, invalidation retry, rollback, and Stage 2 analysis are
available only when supported by the candidate state. Technical reason codes,
the full page profile, candidate history, and actor identity are progressively
disclosed.

The summary calls out accepted candidates whose downstream invalidation is
still pending and assessments that predate the accepted OCR. After accepting
new OCR, return to Inbox Review and refresh analysis so metadata suggestions
and inferred actions are evaluated against the new searchable text. OCR
resolution never files the document or completes an action.

Documents whose OCR assessment needs a dedicated daily decision also appear as
the **OCR** review type in **Needs Review**. Selecting one opens this same focus
inspector. The **OCR Quality** page is reserved for corpus analytics and
administration rather than serving as a second operational queue.

Hover the document preview to reveal **Expand** and **New tab** controls.
Expanded preview uses the full browser viewport and closes with Escape. When a
browser-facing Paperless URL is configured, **Open in Paperless** is also
available in the document header and expanded viewer.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/intake/documents` | List documents tagged `Inbox` |
| `GET /api/intake/documents/{id}` | Load the review contract |
| `GET /api/intake/documents/{id}/core` | Load source metadata and taxonomies |
| `GET /api/intake/documents/{id}/suggestions` | Load revision-cached suggestions |
| `GET /api/intake/documents/{id}/actions` | Load actions and analysis state |
| `GET /api/intake/documents/{id}/ocr` | Load current searchable text (legacy quality fields remain compatibility-only) |
| `GET /api/ocr-quality/documents/{id}` | Load the authoritative multidimensional OCR assessment |
| `POST /api/ocr-quality/documents/{id}/assess` | Idempotently create a missing text-only assessment for one document |
| `GET /api/ocr-quality/candidates?document_id={id}` | Load OCR candidate and resolution state |
| `PUT /api/intake/documents/{id}` | Save metadata and disposition |
| `POST /api/intake/documents/{id}/refresh` | Refresh suggestions and analysis |
| `POST /api/intake/taxonomies/{kind}` | Create a Paperless taxonomy value |
| `POST /api/intake/documents/{id}/actions` | Add a user action |
| `DELETE /api/intake/actions/{id}` | Delete a user-added action |

The API never returns an empty success response for unauthorized or malformed
Paperless source access. Suggestion capability failures are reported per source
so the rest of the document remains reviewable without hiding the outage.
