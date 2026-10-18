# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

OWL is used by people managing personal or household documents in Paperless-ngx. In Action Review, the primary job is fast sequential triage: understand the source evidence, complete the real-world work, and move immediately to the next item without losing document context. In Needs Review, the job is to supply judgment where OWL cannot yet safely decide what a document or relationship means.

## Product Purpose

OWL turns imported documents into organized, reviewable metadata and actionable work. It keeps Paperless authoritative for documents while maintaining a separate, auditable lifecycle for inferred and user-created actions.

## Positioning

OWL connects document evidence, metadata quality, inferred obligations, related documents, and action lifecycle in one review workflow rather than treating documents and tasks as disconnected records.

## Operating Context

- Documents originate in Paperless-ngx and may enter through configured intake tags such as Inbox.
- Inbox Review is document-focused: review metadata, actions, relationships, OCR quality, and filing disposition while the source remains visible.
- Action Review is work-focused: review pending actions, use document-derived links or contact details, edit classifications, complete, defer, decline, or mark false positives, and preserve history.
- Needs Review is trust-focused: accept, correct, reject, re-evaluate, match, or defer uncertain classifications and relationships before they become trusted work or durable system beliefs.
- Filing a document and resolving an action are independent decisions.
- Resolving Needs Review does not necessarily create an action. Confirmed actionable classifications enter Action Review; confirmed no-action decisions and relationship corrections follow their own destinations.

## Capabilities and Constraints

- The shared Document Workspace uses a thumbnail queue, persistent source preview, and Metadata, Actions, Related, and OCR tabs.
- Action Review should open the Actions tab by default and optimize for rapid sequential triage with the document always visible.
- Needs Review should use the same workspace shell but open a Decision surface by default. Its queue must expose review type, reason, confidence, age, and priority rather than action deadline and amount.
- Action lifecycle includes pending, completed, snoozed, dismissed, and not-an-action states, with reopening or undo where supported.
- Needs Review lifecycle includes pending, deferred, and resolved states. Outcomes vary by review type and include confirm, correct, reject, no action, re-evaluate, match, and review later.
- Actions may expose document-derived payment links, phone numbers, email addresses, references, confidence, urgency, due dates, related documents, and recommended calls to action.
- Paperless remains authoritative for document metadata and source files. OWL action history remains auditable.
- Existing light/dark theme tokens and accessible web interaction patterns should be preserved.

## Evidence on Hand

- Current implementations: `frontend/src/pages/IntakeReview.tsx` and `frontend/src/pages/ActionQueue.tsx`.
- Current visual system: `frontend/src/styles/theme.css`, `frontend/src/styles/intake-review.css`, and shared UI components.
- Existing interactive proposal: `mockups/intake-review/`.
- Migration scope: GitHub issue #293 and subtasks #294–#298.

## Product Principles

- Keep source evidence visible while decisions are made.
- Keep Action Review and Needs Review visually connected but semantically distinct.
- Separate document filing from action lifecycle.
- Treat Needs Review confirmation as trust establishment, not task completion.
- Make the next correct action obvious without hiding correction and audit tools.
- Preserve expert queue controls without letting list density consume the workspace.
- Treat classifier corrections as durable feedback, not silent deletion.

## Accessibility & Inclusion

Keyboard navigation, visible focus, semantic controls, responsive layouts, and status information that does not rely on color alone are required.
