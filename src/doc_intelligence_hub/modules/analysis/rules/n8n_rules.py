"""n8n webhook-based analysis rules.

These rules call external n8n workflows via webhook, enabling multi-step
analysis that can cross-reference Monarch, external APIs, or other systems.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

from doc_intelligence_hub.modules.analysis.models import (
    ContextData,
    InsightSeverity,
    InsightType,
    RuleExecutionResult,
)
from doc_intelligence_hub.modules.analysis.rules.base import AnalysisRule, register_rule

logger = logging.getLogger(__name__)

# Max response size from webhooks (1 MB)
_MAX_RESPONSE_BYTES = 1_048_576


def _validate_webhook_url(url: str) -> str | None:
    """Validate a webhook URL against SSRF risks.

    Returns an error message if the URL is unsafe, or None if it's OK.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return "Invalid URL format"

    # Only allow http/https schemes
    if parsed.scheme not in ("http", "https"):
        return f"Unsupported scheme '{parsed.scheme}' — only http/https allowed"

    hostname = parsed.hostname
    if not hostname:
        return "URL has no hostname"

    # Resolve hostname and check for private/loopback IPs
    try:
        addrs = socket.getaddrinfo(hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
        for _family, _, _, _, sockaddr in addrs:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved:
                return f"Webhook URL resolves to private/loopback address ({ip})"
    except socket.gaierror:
        return f"Could not resolve hostname '{hostname}'"

    return None


@register_rule("n8n-webhook")
class N8nWebhookRule(AnalysisRule):
    """Generic n8n webhook rule — calls an external workflow and maps the response."""

    async def execute(self, context: ContextData) -> RuleExecutionResult:
        webhook_url = self.get_param("webhook_url")
        if not webhook_url:
            return RuleExecutionResult(
                rule_id=self.config.id, success=False, error="No webhook_url configured"
            )

        # SSRF protection
        url_error = _validate_webhook_url(webhook_url)
        if url_error:
            return RuleExecutionResult(
                rule_id=self.config.id, success=False, error=f"Unsafe webhook URL: {url_error}"
            )

        timeout = min(self.get_param("timeout", 30), 120)  # Cap at 2 minutes

        # Build payload from context
        payload = self._build_payload(context)

        try:
            async with httpx.AsyncClient(timeout=timeout, max_redirects=3) as client:
                resp = await client.post(webhook_url, json=payload)

            if resp.status_code >= 400:
                return RuleExecutionResult(
                    rule_id=self.config.id,
                    success=False,
                    error=f"Webhook returned HTTP {resp.status_code}",
                )

            # Guard against oversized responses
            if len(resp.content) > _MAX_RESPONSE_BYTES:
                return RuleExecutionResult(
                    rule_id=self.config.id,
                    success=False,
                    error=f"Webhook response too large ({len(resp.content)} bytes)",
                )

            result_data = resp.json()
            return self._map_response(result_data, context)

        except httpx.TimeoutException:
            return RuleExecutionResult(
                rule_id=self.config.id, success=False, error=f"Webhook timed out after {timeout}s"
            )
        except Exception as exc:
            logger.error("n8n webhook call failed: %s", exc)
            return RuleExecutionResult(
                rule_id=self.config.id, success=False, error=f"Webhook call failed: {exc}"
            )

    def _build_payload(self, context: ContextData) -> dict[str, Any]:
        """Build the webhook payload from context and configured payload template."""
        payload_template = self.get_param("payload_template", {})

        payload: dict[str, Any] = {
            "rule_id": self.config.id,
            "rule_name": self.config.name,
        }

        if context.current_document:
            payload["document"] = {
                "id": context.current_document.get("id"),
                "title": context.current_document.get("title"),
                "correspondent": context.current_document.get("correspondent"),
                "tags": context.current_document.get("tags", []),
            }

        if context.series_info:
            payload["series"] = context.series_info

        if context.extracted_fields:
            payload["fields"] = context.extracted_fields

        # Merge with template overrides
        payload.update(payload_template)

        return payload

    def _map_response(self, data: dict[str, Any], context: ContextData) -> RuleExecutionResult:
        """Map webhook response to a RuleExecutionResult."""
        response_mapping = self.get_param("response_mapping", {})

        # Allow the webhook to return a structured result directly
        title = data.get(
            response_mapping.get("title", "title"),
            data.get("title", f"n8n result: {self.config.name}"),
        )
        summary = data.get(response_mapping.get("summary", "summary"), data.get("summary", ""))
        severity_str = data.get(response_mapping.get("severity", "severity"), "info")
        insight_type_str = data.get(
            response_mapping.get("insight_type", "insight_type"), "extraction"
        )

        try:
            severity = InsightSeverity(severity_str)
        except ValueError:
            severity = InsightSeverity.INFO

        try:
            insight_type = InsightType(insight_type_str)
        except ValueError:
            insight_type = InsightType.EXTRACTION

        doc_ids = []
        if context.current_document and "id" in context.current_document:
            doc_ids = [context.current_document["id"]]

        return RuleExecutionResult(
            rule_id=self.config.id,
            success=True,
            insight_type=insight_type,
            title=str(title),
            summary=str(summary),
            detail=data,
            suggested_severity=severity,
            metric_values={k: float(v) for k, v in data.items() if isinstance(v, (int, float))},
            document_ids=doc_ids,
        )


@register_rule("cross-reference-check")
class CrossReferenceCheck(AnalysisRule):
    """Cross-reference documents against external systems via n8n webhook."""

    async def execute(self, context: ContextData) -> RuleExecutionResult:
        webhook_url = self.get_param("webhook_url")
        if not webhook_url:
            return RuleExecutionResult(
                rule_id=self.config.id,
                success=False,
                error="No webhook_url configured for cross-reference check",
            )

        # SSRF protection
        url_error = _validate_webhook_url(webhook_url)
        if url_error:
            return RuleExecutionResult(
                rule_id=self.config.id,
                success=False,
                error=f"Unsafe webhook URL: {url_error}",
            )

        doc = context.current_document
        if not doc:
            return RuleExecutionResult(
                rule_id=self.config.id, success=False, error="No current document"
            )

        timeout = min(self.get_param("timeout", 60), 120)

        payload = {
            "rule_id": self.config.id,
            "action": "cross_reference",
            "document": {
                "id": doc.get("id"),
                "title": doc.get("title"),
                "correspondent": doc.get("correspondent"),
                "amount": doc.get("total_amount") or doc.get("amount"),
                "date": doc.get("created") or doc.get("added"),
            },
            "check_systems": self.get_param("check_systems", ["monarch"]),
        }

        try:
            async with httpx.AsyncClient(timeout=timeout, max_redirects=3) as client:
                resp = await client.post(webhook_url, json=payload)

            if resp.status_code >= 400:
                return RuleExecutionResult(
                    rule_id=self.config.id,
                    success=False,
                    error=f"Cross-reference webhook returned HTTP {resp.status_code}",
                )

            data = resp.json()
            matches_found = data.get("matches_found", 0)
            discrepancies = data.get("discrepancies", [])

            if not matches_found and not discrepancies:
                return RuleExecutionResult(
                    rule_id=self.config.id, success=False, error="No cross-reference results"
                )

            severity = InsightSeverity.WARNING if discrepancies else InsightSeverity.INFO

            return RuleExecutionResult(
                rule_id=self.config.id,
                success=True,
                insight_type=InsightType.COMPLIANCE,
                title=f"Cross-reference: {matches_found} match(es), {len(discrepancies)} discrepanc{'y' if len(discrepancies) == 1 else 'ies'}",
                summary=data.get(
                    "summary", f"Found {matches_found} matches across external systems"
                ),
                detail=data,
                suggested_severity=severity,
                document_ids=[doc["id"]] if "id" in doc else [],
            )

        except httpx.TimeoutException:
            return RuleExecutionResult(
                rule_id=self.config.id,
                success=False,
                error=f"Cross-reference timed out after {timeout}s",
            )
        except Exception as exc:
            logger.error("Cross-reference check failed: %s", exc)
            return RuleExecutionResult(
                rule_id=self.config.id, success=False, error=f"Cross-reference failed: {exc}"
            )
