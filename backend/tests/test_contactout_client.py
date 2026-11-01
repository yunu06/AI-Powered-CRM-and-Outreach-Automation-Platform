import os
import pytest
from unittest.mock import Mock, patch

from backend.app.integrations.contactout_client import (
    ContactOutClient,
    CreditExhaustedError,
    ContactNotFoundError,
    ProviderAPIError,
)


def test_preflight_credit_exhausted_aborts_without_request():
    mock_credit_service = Mock()
    mock_credit_service.check_and_deduct_credits.return_value = False

    client = ContactOutClient(
        api_key="test_api_key",
        credit_service=mock_credit_service,
        sandbox_mode=False,
    )

    with patch("requests.get") as mock_get:
        with pytest.raises(CreditExhaustedError, match="Insufficient credits"):
            client.lookup_contact("John Doe", "example.com")
        mock_get.assert_not_called()


def test_sandbox_mode_returns_mock_data_without_request():
    mock_credit_service = Mock()
    mock_credit_service.check_and_deduct_credits.return_value = True

    client = ContactOutClient(
        api_key="test_api_key",
        credit_service=mock_credit_service,
        sandbox_mode=True,
    )

    with patch("requests.get") as mock_get:
        result = client.lookup_contact("Alice Smith", "techcorp.io")
        assert result["status"] == "success"
        assert result["source"] == "sandbox_mock"
        assert result["data"]["full_name"] == "Alice Smith"
        assert result["data"]["domain"] == "techcorp.io"
        assert "work_email" in result["data"]
        mock_get.assert_not_called()


def test_sandbox_mode_from_environment_variable(monkeypatch):
    monkeypatch.setenv("CONTACTOUT_SANDBOX", "true")
    mock_credit_service = Mock()
    mock_credit_service.check_and_deduct_credits.return_value = True

    client = ContactOutClient(
        api_key="test_api_key",
        credit_service=mock_credit_service,
    )
    assert client.sandbox_mode is True

    with patch("requests.get") as mock_get:
        result = client.lookup_contact("Bob Lee", "sandboxcompany.com")
        assert result["status"] == "success"
        assert result["source"] == "sandbox_mock"
        mock_get.assert_not_called()


def test_lookup_contact_found_and_verifies_auth_header():
    mock_credit_service = Mock()
    mock_credit_service.check_and_deduct_credits.return_value = True
    test_key = "my_secret_token_123"

    client = ContactOutClient(
        api_key=test_key,
        credit_service=mock_credit_service,
        sandbox_mode=False,
    )

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "status": "success",
        "data": {
            "full_name": "Jane Doe",
            "work_email": "jane@company.com",
            "phone": "+1234567890",
        },
    }

    with patch("requests.get", return_value=mock_response) as mock_get:
        result = client.lookup_contact("Jane Doe", "company.com")
        assert result["data"]["work_email"] == "jane@company.com"
        mock_get.assert_called_once()

        called_kwargs = mock_get.call_args.kwargs
        assert "headers" in called_kwargs
        assert called_kwargs["headers"].get("Authorization") == f"Bearer {test_key}"


def test_lookup_contact_not_found():
    mock_credit_service = Mock()
    mock_credit_service.check_and_deduct_credits.return_value = True

    client = ContactOutClient(
        api_key="valid_token",
        credit_service=mock_credit_service,
        sandbox_mode=False,
    )

    mock_response = Mock()
    mock_response.status_code = 404
    mock_response.text = "Not Found"

    with patch("requests.get", return_value=mock_response):
        with pytest.raises(ContactNotFoundError):
            client.lookup_contact("Unknown Person", "nonexistent.com")


def test_lookup_provider_api_error():
    mock_credit_service = Mock()
    mock_credit_service.check_and_deduct_credits.return_value = True

    client = ContactOutClient(
        api_key="valid_token",
        credit_service=mock_credit_service,
        sandbox_mode=False,
    )

    mock_response = Mock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"

    with patch("requests.get", return_value=mock_response):
        with pytest.raises(ProviderAPIError, match="ContactOut API failed with HTTP 500"):
            client.lookup_contact("Jane Doe", "company.com")


@pytest.mark.parametrize("status_code", [402, 429])
def test_provider_credit_exhausted_never_retried(status_code):
    mock_credit_service = Mock()
    mock_credit_service.check_and_deduct_credits.return_value = True

    client = ContactOutClient(
        api_key="valid_token",
        credit_service=mock_credit_service,
        sandbox_mode=False,
    )

    mock_response = Mock()
    mock_response.status_code = status_code
    mock_response.text = "Payment Required / Out of Credits"

    with patch("requests.get", return_value=mock_response) as mock_get:
        with pytest.raises(CreditExhaustedError):
            client.lookup_contact("Jane Doe", "company.com")
        assert mock_get.call_count == 1
