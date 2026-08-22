"""Tests for the expanded rule-based fallback analyzer."""

import pytest

from doc_intelligence_hub.modules.action_queue.fallback_analyzer import RuleBasedAnalyzer


@pytest.fixture()
def analyzer():
    return RuleBasedAnalyzer()


class TestPayDetection:
    def test_invoice_detected_as_pay(self, analyzer):
        result = analyzer.analyze_document(
            {
                "title": "Electric Bill - July 2024",
                "content": "Amount Due: $142.50\nPayment Due By: 08/15/2025\nAccount Balance: $142.50",
                "tag_names": ["bill"],
            }
        )
        action = result["actions"][0]
        assert action["action_type"] == "PAY"
        assert action["amount"] == 142.50
        assert result["document_assessment"]["requires_action"] is True

    def test_medical_copay_detected(self, analyzer):
        result = analyzer.analyze_document(
            {
                "title": "Statement from City Hospital",
                "content": "Copay: $50.00\nBalance Due: $50.00\nPay by: 09/01/2025",
                "tag_names": [],
            }
        )
        action = result["actions"][0]
        assert action["action_type"] == "PAY"

    def test_mortgage_payment(self, analyzer):
        result = analyzer.analyze_document(
            {
                "title": "Monthly Mortgage Statement",
                "content": "Mortgage payment due: $1,850.00\nDue date: 08/01/2025",
                "tag_names": [],
            }
        )
        action = result["actions"][0]
        assert action["action_type"] == "PAY"
        assert action["amount"] == 1850.00

    def test_payment_url_becomes_quick_action(self, analyzer):
        result = analyzer.analyze_document(
            {
                "title": "Electric bill",
                "content": "Amount due: $42.00. Pay online at https://billing.example.com/pay?id=7",
                "tag_names": ["bill"],
            }
        )

        action = result["actions"][0]
        extracted = result["document_assessment"]["extracted_data"]
        assert action["recommended_cta"]["url"] == "https://billing.example.com/pay?id=7"
        assert extracted["links"] == [
            {
                "url": "https://billing.example.com/pay?id=7",
                "label": "Pay online",
                "purpose": "payment",
            }
        ]

    def test_extracts_non_payment_links_and_contact_email(self, analyzer):
        result = analyzer.analyze_document(
            {
                "title": "Appointment reminder",
                "content": (
                    "Confirm at www.example.com/schedule or contact care@example.com. "
                    "Your appointment is scheduled soon."
                ),
                "tag_names": [],
            }
        )

        extracted = result["document_assessment"]["extracted_data"]
        assert extracted["email"] == "care@example.com"
        assert extracted["links"][0]["url"] == "https://www.example.com/schedule"
        assert extracted["links"][0]["purpose"] == "other"


class TestRespondDetection:
    def test_action_required_letter(self, analyzer):
        result = analyzer.analyze_document(
            {
                "title": "Important: Action Required",
                "content": "Please respond within 30 days. Verification needed for your account.",
                "tag_names": [],
            }
        )
        action = result["actions"][0]
        assert action["action_type"] == "RESPOND"

    def test_jury_duty(self, analyzer):
        result = analyzer.analyze_document(
            {
                "title": "Jury Duty Summons",
                "content": "You are summoned to appear for jury duty. Please confirm your attendance.",
                "tag_names": [],
            }
        )
        action = result["actions"][0]
        assert action["action_type"] in ("RESPOND", "SIGN")


class TestScheduleDetection:
    def test_appointment_reminder(self, analyzer):
        result = analyzer.analyze_document(
            {
                "title": "Appointment Reminder",
                "content": "Your appointment is scheduled for 09/15/2025. Please confirm.",
                "tag_names": [],
            }
        )
        action = result["actions"][0]
        assert action["action_type"] in ("SCHEDULE", "RESPOND")

    def test_license_renewal(self, analyzer):
        result = analyzer.analyze_document(
            {
                "title": "License Renewal Notice",
                "content": "Your license expires 12/31/2025. Renew by the deadline to avoid late fees.",
                "tag_names": [],
            }
        )
        action = result["actions"][0]
        assert action["action_type"] == "SCHEDULE"


class TestReviewDetection:
    def test_eob_document(self, analyzer):
        result = analyzer.analyze_document(
            {
                "title": "Explanation of Benefits",
                "content": "This is not a bill. Review the claim summary for your recent visit.",
                "tag_names": ["medical", "eob"],
            }
        )
        action = result["actions"][0]
        assert action["action_type"] == "REVIEW"

    def test_insurance_policy_change(self, analyzer):
        result = analyzer.analyze_document(
            {
                "title": "Policy Update Notice",
                "content": "Important information about your coverage change effective January 1.",
                "tag_names": ["insurance"],
            }
        )
        action = result["actions"][0]
        assert action["action_type"] == "REVIEW"


class TestFileDetection:
    def test_tax_form(self, analyzer):
        result = analyzer.analyze_document(
            {
                "title": "2024 W-2 Wage and Tax Statement",
                "content": "W-2 form for tax year 2024. Keep for your records.",
                "tag_names": ["tax"],
            }
        )
        action = result["actions"][0]
        assert action["action_type"] == "FILE"

    def test_1099_form(self, analyzer):
        result = analyzer.analyze_document(
            {
                "title": "1099-INT Interest Income",
                "content": "1099 form showing interest income for tax year 2024.",
                "tag_names": ["tax", "financial"],
            }
        )
        action = result["actions"][0]
        assert action["action_type"] == "FILE"

    def test_lab_results(self, analyzer):
        result = analyzer.analyze_document(
            {
                "title": "Lab Results - Blood Work",
                "content": "Lab results from your recent blood work. Diagnosis: normal ranges.",
                "tag_names": ["medical"],
            }
        )
        action = result["actions"][0]
        assert action["action_type"] in ("FILE", "REVIEW")


class TestSignDetection:
    def test_consent_form(self, analyzer):
        result = analyzer.analyze_document(
            {
                "title": "Consent Form",
                "content": "Please sign here to authorize the procedure. Signature required.",
                "tag_names": [],
            }
        )
        action = result["actions"][0]
        assert action["action_type"] == "SIGN"


class TestArchiveDetection:
    def test_informational_notice(self, analyzer):
        result = analyzer.analyze_document(
            {
                "title": "Account Update Notification",
                "content": "For your records. No action needed. This is informational only.",
                "tag_names": [],
            }
        )
        # Either ARCHIVE or low-confidence FILE is acceptable
        action = result["actions"][0] if result["actions"] else None
        if action:
            assert action["action_type"] in ("ARCHIVE", "FILE", "REVIEW")


class TestEdgeCases:
    def test_empty_document(self, analyzer):
        result = analyzer.analyze_document(
            {
                "title": "",
                "content": "",
                "tag_names": [],
            }
        )
        assert result["document_assessment"]["requires_action"] is False

    def test_tag_boost_with_bill_tag(self, analyzer):
        result = analyzer.analyze_document(
            {
                "title": "Document",
                "content": "Some generic content with amount $50.00",
                "tag_names": ["bill"],
            }
        )
        action = result["actions"][0]
        assert action["action_type"] == "PAY"

    def test_urgency_critical_for_overdue(self, analyzer):
        result = analyzer.analyze_document(
            {
                "title": "Final Notice - Past Due",
                "content": "Your account is overdue. Final notice before collection action.",
                "tag_names": [],
            }
        )
        action = result["actions"][0]
        assert action["urgency"] == "CRITICAL"
