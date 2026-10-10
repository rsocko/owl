"""Deterministic, channel-scoped source occurrence identities.

Callers should use these helpers before crossing the OWL boundary so raw mailbox,
provider, scanner, upload, and Monarch identifiers are never persisted.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .models import SourceChannel


def source_occurrence_id(channel: SourceChannel, **parts: str | int) -> str:
    if not parts or any(value is None or str(value).strip() == "" for value in parts.values()):
        raise ValueError("source occurrence identity parts must be non-empty")
    payload: dict[str, Any] = {
        "version": 1,
        "channel": channel.value,
        "parts": {key: str(parts[key]) for key in sorted(parts)},
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def email_attachment_occurrence(
    *, mailbox_ref: str, message_ref: str, attachment_index: int
) -> str:
    if attachment_index < 0:
        raise ValueError("attachment_index must be non-negative")
    return source_occurrence_id(
        SourceChannel.EMAIL_ATTACHMENT,
        mailbox_ref=mailbox_ref,
        message_ref=message_ref,
        attachment_index=attachment_index,
    )


def email_body_occurrence(*, mailbox_ref: str, message_ref: str, transform_version: str) -> str:
    return source_occurrence_id(
        SourceChannel.EMAIL_BODY,
        mailbox_ref=mailbox_ref,
        message_ref=message_ref,
        transform_version=transform_version,
    )


def scanner_job_occurrence(*, scanner_ref: str, job_ref: str) -> str:
    return source_occurrence_id(SourceChannel.SCANNER_JOB, scanner_ref=scanner_ref, job_ref=job_ref)


def upload_occurrence(*, channel: SourceChannel, upload_ref: str) -> str:
    if channel not in {SourceChannel.PHONE_UPLOAD, SourceChannel.MANUAL_UPLOAD}:
        raise ValueError("upload occurrence channel must be phone_upload or manual_upload")
    return source_occurrence_id(channel, upload_ref=upload_ref)


def provider_occurrence(*, connector_ref: str, object_ref: str, object_version: str) -> str:
    return source_occurrence_id(
        SourceChannel.PROVIDER_API,
        connector_ref=connector_ref,
        object_ref=object_ref,
        object_version=object_version,
    )


def monarch_recovery_occurrence(
    *, connector_ref: str, receipt_ref: str, receipt_version: str
) -> str:
    return source_occurrence_id(
        SourceChannel.MONARCH_RECOVERY,
        connector_ref=connector_ref,
        receipt_ref=receipt_ref,
        receipt_version=receipt_version,
    )
