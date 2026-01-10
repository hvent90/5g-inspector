"""Service terms documentation service."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from ..config import get_settings

log = structlog.get_logger()


class ServiceTermsService:
    """Service for managing service terms documentation."""

    DEFAULT_TERMS = {
        "plan_name": "",
        "monthly_cost": None,
        "advertised_download_min": 133,
        "advertised_download_max": 415,
        "advertised_upload_min": 12,
        "advertised_upload_max": 55,
        "advertised_latency_min": 16,
        "advertised_latency_max": 28,
        "service_start_date": None,
        "service_address": "",
        "account_number": "",
        "contract_terms": "",
        "deprioritization_policy": "",
        "data_cap_policy": "",
        "throttling_terms": "",
        "typical_language": "",
        "promotional_claims": "",
        "website_screenshot_date": None,
        "screenshot_url": "",
        "terms_of_service_date": None,
        "notes": "",
        "updated_at": None,
    }

    ALLOWED_FIELDS = [
        "plan_name",
        "monthly_cost",
        "advertised_download_min",
        "advertised_download_max",
        "advertised_upload_min",
        "advertised_upload_max",
        "advertised_latency_min",
        "advertised_latency_max",
        "service_start_date",
        "service_address",
        "account_number",
        "contract_terms",
        "deprioritization_policy",
        "data_cap_policy",
        "throttling_terms",
        "typical_language",
        "promotional_claims",
        "website_screenshot_date",
        "screenshot_url",
        "terms_of_service_date",
        "notes",
    ]

    def __init__(self) -> None:
        settings = get_settings()
        self._storage_path = Path(settings.database.path).parent / "service_terms.json"
        self._terms: dict[str, Any] = dict(self.DEFAULT_TERMS)
        self._load_terms()

    def _load_terms(self) -> None:
        """Load service terms from file."""
        if self._storage_path.exists():
            try:
                with open(self._storage_path) as f:
                    loaded = json.load(f)
                self._terms.update(loaded)
                log.info("service_terms_loaded")
            except Exception as e:
                log.error("service_terms_load_failed", error=str(e))

    def _save_terms(self) -> None:
        """Save service terms to file."""
        try:
            with open(self._storage_path, "w") as f:
                json.dump(self._terms, f, indent=2)
        except Exception as e:
            log.error("service_terms_save_failed", error=str(e))

    def get_terms(self) -> dict:
        """Get current service terms."""
        return dict(self._terms)

    def update_terms(self, updates: dict[str, Any]) -> dict:
        """Update service terms documentation."""
        for field in self.ALLOWED_FIELDS:
            if field in updates:
                self._terms[field] = updates[field]

        self._terms["updated_at"] = datetime.utcnow().isoformat()
        self._save_terms()
        log.info("service_terms_updated")
        return self.get_terms()

    def get_summary(self) -> dict:
        """Get summary for FCC complaint."""
        terms = self._terms

        return {
            "terms": terms,
            "advertised_speeds": {
                "download": f"{terms.get('advertised_download_min', 133)}-{terms.get('advertised_download_max', 415)} Mbps",
                "upload": f"{terms.get('advertised_upload_min', 12)}-{terms.get('advertised_upload_max', 55)} Mbps",
                "latency": f"{terms.get('advertised_latency_min', 16)}-{terms.get('advertised_latency_max', 28)} ms",
            },
            "documentation_complete": bool(
                terms.get("plan_name")
                and terms.get("monthly_cost")
                and terms.get("service_start_date")
            ),
            "has_policy_documentation": bool(
                terms.get("deprioritization_policy")
                or terms.get("data_cap_policy")
                or terms.get("throttling_terms")
            ),
            "has_evidence": bool(
                terms.get("website_screenshot_date") or terms.get("terms_of_service_date")
            ),
        }

    def get_fcc_export(self) -> dict:
        """Export service terms in FCC complaint format."""
        terms = self._terms

        return {
            "service_information": {
                "provider": "T-Mobile",
                "service_type": "Home Internet",
                "plan_name": terms.get("plan_name") or "Not documented",
                "monthly_cost": terms.get("monthly_cost"),
                "service_start_date": terms.get("service_start_date"),
                "service_address": terms.get("service_address") or "Not documented",
                "account_number": terms.get("account_number") or "Not documented",
            },
            "advertised_performance": {
                "download_speed": f"{terms.get('advertised_download_min', 133)}-{terms.get('advertised_download_max', 415)} Mbps",
                "upload_speed": f"{terms.get('advertised_upload_min', 12)}-{terms.get('advertised_upload_max', 55)} Mbps",
                "latency": f"{terms.get('advertised_latency_min', 16)}-{terms.get('advertised_latency_max', 28)} ms",
                "typical_language_used": terms.get("typical_language") or "Not documented",
                "promotional_claims": terms.get("promotional_claims") or "Not documented",
            },
            "policies": {
                "deprioritization": terms.get("deprioritization_policy") or "Not documented",
                "data_cap": terms.get("data_cap_policy") or "Not documented",
                "throttling": terms.get("throttling_terms") or "Not documented",
                "contract_terms": terms.get("contract_terms") or "Not documented",
            },
            "evidence_documentation": {
                "website_screenshot_date": terms.get("website_screenshot_date"),
                "screenshot_url": terms.get("screenshot_url"),
                "terms_of_service_date": terms.get("terms_of_service_date"),
            },
            "notes": terms.get("notes") or "",
            "last_updated": terms.get("updated_at"),
        }


# Global service instance
_service_terms_service: ServiceTermsService | None = None


def get_service_terms_service() -> ServiceTermsService:
    """Get the global service terms service instance."""
    global _service_terms_service
    if _service_terms_service is None:
        _service_terms_service = ServiceTermsService()
    return _service_terms_service
