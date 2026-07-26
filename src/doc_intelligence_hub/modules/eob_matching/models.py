from __future__ import annotations

from datetime import UTC, date, datetime
from enum import Enum

from pydantic import BaseModel, Field


class DocumentType(str, Enum):
    EOB = "EOB"
    BILL = "BILL"
    UNKNOWN = "UNKNOWN"


class MatchConfidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class PaymentStatus(str, Enum):
    UNPAID = "unpaid"
    PARTIAL = "partial"
    PAID = "paid"
    OVERPAID = "overpaid"


class ClassificationResult(BaseModel):
    type: DocumentType
    confidence_score: float = 0.0
    indicators_matched: list[str] = Field(default_factory=list)


class ServiceLine(BaseModel):
    description: str = ""
    cpt_code: str | None = None
    amount: float | None = None
    billed_amount: float | None = None
    allowed_amount: float | None = None
    plan_pays: float | None = None
    patient_responsibility: float | None = None


class ExtractedEOB(BaseModel):
    insurance_company: str | None = None
    policy_number: str | None = None
    patient_name: str | None = None
    claim_number: str | None = None
    date_of_service: date | None = None
    provider_name: str | None = None
    services: list[ServiceLine] = Field(default_factory=list)
    total_billed: float | None = None
    total_allowed: float | None = None
    total_plan_pays: float | None = None
    total_patient_responsibility: float | None = None
    document_id: str
    extraction_confidence: float = 0.0


class ExtractedBill(BaseModel):
    provider_name: str | None = None
    patient_name: str | None = None
    invoice_number: str | None = None
    date_of_service: date | None = None
    due_date: date | None = None
    services: list[ServiceLine] = Field(default_factory=list)
    total_amount: float | None = None
    balance_due: float | None = None
    payment_status: str | None = None
    document_id: str
    extraction_confidence: float = 0.0


class MatchBreakdown(BaseModel):
    date: float
    provider: float
    patient: float
    amount: float
    procedures: float


class MatchResult(BaseModel):
    eob_id: str
    bill_id: str
    score: float
    confidence: MatchConfidence
    breakdown: MatchBreakdown
    matched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payment_status: PaymentStatus = PaymentStatus.UNPAID
    paid_amount: float = 0.0
    paid_date: datetime | None = None


class PaymentRequest(BaseModel):
    amount: float = Field(..., gt=0, description="Payment amount in dollars")
    paid_date: str | None = Field(default=None, description="Payment date (ISO format). Defaults to now.")
    method: str | None = Field(default=None, description="Payment method (e.g. check, online, insurance)")
    notes: str | None = Field(default=None, description="Optional notes about this payment")


class PaymentSummaryResponse(BaseModel):
    total_billed: float = 0.0
    total_due: float = 0.0
    total_paid: float = 0.0
    unpaid_count: int = 0
    partial_count: int = 0
    paid_count: int = 0
    overpaid_count: int = 0
