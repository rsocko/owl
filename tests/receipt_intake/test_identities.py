from __future__ import annotations

import pytest

from doc_intelligence_hub.modules.receipt_intake.identities import (
    email_attachment_occurrence,
    email_body_occurrence,
    monarch_recovery_occurrence,
    provider_occurrence,
    scanner_job_occurrence,
    upload_occurrence,
)
from doc_intelligence_hub.modules.receipt_intake.models import SourceChannel


def test_supported_ingress_identities_are_deterministic_and_channel_scoped():
    fixtures = [
        email_attachment_occurrence(
            mailbox_ref="mailbox-fixture",
            message_ref="message-fixture",
            attachment_index=0,
        ),
        email_body_occurrence(
            mailbox_ref="mailbox-fixture",
            message_ref="message-fixture",
            transform_version="email-body-v1",
        ),
        scanner_job_occurrence(scanner_ref="scanner-fixture", job_ref="job-fixture"),
        upload_occurrence(channel=SourceChannel.PHONE_UPLOAD, upload_ref="phone-fixture"),
        upload_occurrence(channel=SourceChannel.MANUAL_UPLOAD, upload_ref="manual-fixture"),
        provider_occurrence(
            connector_ref="provider-fixture",
            object_ref="object-fixture",
            object_version="1",
        ),
        monarch_recovery_occurrence(
            connector_ref="monarch-fixture",
            receipt_ref="receipt-fixture",
            receipt_version="1",
        ),
    ]

    assert len(set(fixtures)) == len(fixtures)
    assert all(len(identity) == 64 for identity in fixtures)
    assert fixtures[0] == email_attachment_occurrence(
        mailbox_ref="mailbox-fixture",
        message_ref="message-fixture",
        attachment_index=0,
    )


def test_upload_identity_rejects_unsupported_channel():
    with pytest.raises(ValueError, match="phone_upload or manual_upload"):
        upload_occurrence(channel=SourceChannel.PROVIDER_API, upload_ref="fixture")
