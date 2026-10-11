---
title: Send a Paperless document to Monarch
---

# Paperless document delivery to Monarch

OWL adds a document-level **Send to Monarch** action to its shared document
viewer. Paperless-ngx remains the canonical document store and continues to own
email, scanner/folder, and web-upload ingestion. The action does not add another
inbox, fork Paperless, or require n8n.

## User flow

1. Open an existing document from any OWL view that uses the shared document
   viewer.
2. Select **Send to Monarch** and confirm the protected write.
3. OWL downloads the canonical file from Paperless into a bounded, transient
   in-memory buffer and sends its raw bytes to the fixed private Tyrion
   authority. OWL does not persist the bytes.
4. The action shows `processing`, `sent`, `unknown`, `retryable`, or blocked
   recovery state from OWL's durable ledger.
5. An unknown outcome offers reconciliation first. A retry is available only
   after Tyrion reports that no delivery exists, and the retry reuses the same
   deterministic opaque idempotency key.

Documents ingested through the `monarch_recovery` source channel are
structurally blocked from delivery to prevent a recovery loop.

## Safety and authorization

`OWL_MONARCH_DELIVERY_WRITE_ENABLED` defaults to `false`. The operator enables
it only for an OWL deployment whose existing private access boundary authorizes
the document users. The browser never receives the Tyrion bearer token; OWL
authorizes the user action and authenticates server-to-server with
`OWL_MONARCH_DELIVERY_TYRION_API_TOKEN`.

The ledger stores only opaque hashes, state, bounded reason codes, counters, and
timestamps. It does not store document bytes, OCR, filenames, signed URLs, raw
Paperless or Tyrion identifiers, or credentials.

An optional existing Paperless tag can make acknowledged state visible:

```dotenv
OWL_MONARCH_DELIVERY_SENT_TAG=Sent to Monarch
```

The tag is applied only after authoritative Tyrion acknowledgement. Tag failure
sets a visible pending state and can be retried, but tags are never consulted
for idempotency or delivery truth.

## Configuration

```dotenv
OWL_MONARCH_DELIVERY_TYRION_AUTHORITY=http://tyrion-operations-ui:3000
OWL_MONARCH_DELIVERY_TYRION_API_TOKEN=replace-with-secret
OWL_MONARCH_DELIVERY_IDENTITY_SECRET=replace-with-stable-random-secret
OWL_MONARCH_DELIVERY_WRITE_ENABLED=false
OWL_MONARCH_DELIVERY_SENT_TAG=
OWL_MONARCH_DELIVERY_DEPLOYMENT_ID=default
```

The authority is fixed to the private `tyrion-operations-ui:3000` service and is
process configuration, never request input. `DEPLOYMENT_ID`
names the Paperless deployment and `IDENTITY_SECRET` keys the HMAC used for
deterministic opaque identities. Keep both stable across restarts.

OWL sends only PDF, JPEG, or PNG documents up to 2 MiB. The Tyrion request body
is the raw document, never multipart or base64. Submit timeout is 100 seconds and
reconcile timeout is 60 seconds to exceed Tyrion's bounded Bridge operations.

## Recovery behavior

- `unknown`: reconcile only; never recreate. If submit timed out before returning
  a revision, OWL omits the reconcile revision header as required by the broker
  recovery contract.
- `retryable`: explicit retry reconciles first and submits only after a
  definitive not-found result.
- `processing`: status checks reconcile the existing delivery.
- `acknowledged`: repeated sends return the existing result.
- `blocked_recovery`: no send or retry is allowed.

The optional Paperless status tag is display state only. Removing or changing it
cannot trigger another delivery.
