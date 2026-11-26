"""Shared fixtures: the genuine PAYMENT-REQUIRED header captured from the
live Blockchain IQ API on 2026-11-26 for GET /api/x402/v1/brief, plus a mock
transport that plays the server side of the x402 flow."""

import base64
import json
from pathlib import Path

import httpx
import pytest

FIXTURE_HEADER = (
    Path(__file__).parent / "fixtures" / "payment_required_brief.txt"
).read_text().strip()

#: A throwaway, publicly known test key (Hardhat account #0). Never funded,
#: never used outside tests.
TEST_PRIVATE_KEY = (
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
)
TEST_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"

FAKE_TX = "0x" + "ab" * 32

SETTLEMENT_HEADER = base64.b64encode(
    json.dumps(
        {
            "success": True,
            "transaction": FAKE_TX,
            "network": "eip155:8453",
            "payer": TEST_ADDRESS,
        }
    ).encode()
).decode()


def make_paid_handler(calls: list, data: dict | None = None):
    """Mock server: 402 until a PAYMENT-SIGNATURE header shows up, then 200
    with a settlement proof — the real API's behaviour, minus the money."""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if "PAYMENT-SIGNATURE" not in request.headers:
            return httpx.Response(
                402,
                headers={"PAYMENT-REQUIRED": FIXTURE_HEADER},
                json={"error": "Payment required.", "price": "$0.01"},
            )
        return httpx.Response(
            200,
            headers={"PAYMENT-RESPONSE": SETTLEMENT_HEADER},
            json=data if data is not None else {"data": {"brief": "fake"}},
        )

    return handler


@pytest.fixture
def transport_factory():
    def _make(handler):
        return httpx.MockTransport(handler)

    return _make
