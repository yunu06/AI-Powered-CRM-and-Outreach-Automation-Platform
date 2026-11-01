"""
ContactOut Enrichment Integration Client.
Handles contact lookups by name and company domain with budget/credit protection
and sandbox execution support.
"""

import logging
import os
from typing import Any, Dict, Optional
import requests

logger = logging.getLogger(__name__)


# --- 1. Custom Exceptions ---
class ContactOutError(Exception):
    """Base exception for ContactOut integration failures."""
    pass


class CreditExhaustedError(ContactOutError):
    """Raised when credit budget is exceeded or provider returns credit exhaustion."""
    pass


class ContactNotFoundError(ContactOutError):
    """Raised when the requested contact profile cannot be found."""
    pass


class ProviderAPIError(ContactOutError):
    """Raised when ContactOut returns a 5xx or unhandled API error."""
    pass


# --- 2. Fallback / Mock Credit Service Resolver ---
try:
    from backend.app.services.credit_service import CreditService  # type: ignore
except ImportError:
    class CreditService:
        @staticmethod
        def check_and_deduct_credits(feature: str = "enrichment", amount: int = 1) -> bool:
            return True


# --- 3. ContactOut Client ---
class ContactOutClient:
    BASE_URL = "https://api.contactout.com/v1"

    def __init__(
        self,
        api_key: Optional[str] = None,
        credit_service: Optional[Any] = None,
        sandbox_mode: Optional[bool] = None,
    ):
        self.api_key = api_key or os.getenv("CONTACTOUT_API_KEY", "")
        self.credit_service = credit_service or CreditService()

        if sandbox_mode is not None:
            self.sandbox_mode = sandbox_mode
        else:
            env_val = os.getenv("CONTACTOUT_SANDBOX", "false").lower()
            self.sandbox_mode = env_val in ("true", "1", "yes")

    def _get_mock_contact(self, name: str, domain: str) -> Dict[str, Any]:
        clean_name = name.strip().lower().replace(" ", ".")
        return {
            "status": "success",
            "source": "sandbox_mock",
            "data": {
                "full_name": name,
                "domain": domain,
                "work_email": f"{clean_name}@{domain}",
                "personal_email": f"{clean_name}@gmail.com",
                "phone": "+1-555-0199",
                "linkedin_url": f"https://www.linkedin.com/in/{clean_name}",
                "title": "Software Engineer",
                "company": domain.split(".")[0].capitalize(),
            },
        }

    def lookup_contact(self, name: str, domain: str) -> Dict[str, Any]:
        if not name or not domain:
            raise ValueError("Both 'name' and 'domain' must be non-empty strings.")

        # Step 1: Pre-flight Credit/Budget Check
        has_credit = False
        if hasattr(self.credit_service, "check_and_deduct_credits"):
            has_credit = self.credit_service.check_and_deduct_credits(feature="enrichment", amount=1)
        elif hasattr(self.credit_service, "has_sufficient_credits"):
            has_credit = self.credit_service.has_sufficient_credits()
        elif callable(self.credit_service):
            has_credit = self.credit_service()
        else:
            has_credit = True

        if not has_credit:
            logger.error("Credit check failed: Budget exceeded or zero credits remaining.")
            raise CreditExhaustedError("Insufficient credits or budget exceeded to perform enrichment.")

        # Step 2: Sandbox Mode Fallback
        if self.sandbox_mode:
            logger.info("Sandbox mode active: Returning simulated enrichment profile for %s (%s).", name, domain)
            return self._get_mock_contact(name, domain)

        # Step 3: Production API Call
        if not self.api_key:
            raise ValueError("CONTACTOUT_API_KEY is not configured.")

        url = f"{self.BASE_URL}/people/enrich"
        auth_header = f"Bearer {self.api_key}"
        headers = {
            "Authorization": auth_header,
            "Accept": "application/json",
        }
        params = {
            "name": name,
            "domain": domain,
        }

        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
        except requests.RequestException as exc:
            logger.error("Network error during ContactOut lookup: %s", exc)
            raise ProviderAPIError(f"Network error communicating with ContactOut: {exc}") from exc

        if response.status_code == 200:
            data = response.json()
            if not data or not data.get("data"):
                raise ContactNotFoundError(f"No profile found for '{name}' at domain '{domain}'.")
            return data

        if response.status_code == 404:
            raise ContactNotFoundError(f"Contact '{name}' at domain '{domain}' not found.")

        if response.status_code in (402, 429):
            logger.critical("ContactOut provider reported credit exhaustion (Status %s). Never retrying.", response.status_code)
            raise CreditExhaustedError(f"Provider credits exhausted or payment required (HTTP {response.status_code}).")

        logger.error("ContactOut provider error: HTTP %s - %s", response.status_code, response.text)
        raise ProviderAPIError(f"ContactOut API failed with HTTP {response.status_code}: {response.text}")
