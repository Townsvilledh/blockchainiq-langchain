import base64
import json

import httpx
import pytest

from blockchainiq_langchain.client import (
    BlockchainIQClient,
    PaymentRequiredError,
    PriceCapExceededError,
)
from blockchainiq_langchain.payments import EIP3009Signer

from .conftest import FAKE_TX, FIXTURE_HEADER, TEST_PRIVATE_KEY, make_paid_handler


def test_free_resource_needs_no_signer(transport_factory):
    def handler(request):
        assert "PAYMENT-SIGNATURE" not in request.headers
        return httpx.Response(200, json={"data": {"chain": "btc"}})

    client = BlockchainIQClient(transport=transport_factory(handler))
    result = client.sample()
    assert result.data["data"]["chain"] == "btc"
    assert result.settlement is None
    assert result.price_usd is None


def test_paid_flow_signs_and_returns_settlement(transport_factory):
    calls: list = []
    signer = EIP3009Signer(TEST_PRIVATE_KEY)
    client = BlockchainIQClient(
        payment_signer=signer,
        transport=transport_factory(make_paid_handler(calls)),
    )
    result = client.get("brief")

    assert result.data == {"data": {"brief": "fake"}}
    assert result.price_usd == pytest.approx(0.01)
    assert result.settlement is not None
    assert result.settlement.success is True
    assert result.settlement.transaction == FAKE_TX

    # Exactly two requests: the unpaid probe and the paid retry, and the
    # retry carried a well-formed payment payload for the right amount.
    assert len(calls) == 2
    payload = json.loads(
        base64.b64decode(calls[1].headers["PAYMENT-SIGNATURE"])
    )
    assert payload["payload"]["authorization"]["value"] == "10000"
    assert (
        payload["payload"]["authorization"]["to"]
        == "0x617dA9383fbA67e86137BC4924b8b7E400fB7C9e"
    )


def test_paid_resource_without_signer_reports_price(transport_factory):
    calls: list = []
    client = BlockchainIQClient(
        transport=transport_factory(make_paid_handler(calls))
    )
    with pytest.raises(PaymentRequiredError) as excinfo:
        client.get("brief")
    assert excinfo.value.price_usd == pytest.approx(0.01)
    assert len(calls) == 1  # no retry was attempted


def test_price_cap_refuses_before_signing(transport_factory):
    expensive = json.loads(base64.b64decode(FIXTURE_HEADER))
    for entry in expensive["accepts"]:
        entry["amount"] = "19000000"  # $19.00
    header = base64.b64encode(json.dumps(expensive).encode()).decode()

    signed = []

    class SpySigner:
        def create_payment_header(self, requirements, accepted):
            signed.append(True)
            return "unused"

    def handler(request):
        return httpx.Response(
            402, headers={"PAYMENT-REQUIRED": header}, json={"error": "pay"}
        )

    client = BlockchainIQClient(
        payment_signer=SpySigner(),
        max_price_usd=2.00,
        transport=transport_factory(handler),
    )
    with pytest.raises(PriceCapExceededError):
        client.get("brief")
    assert signed == []  # the cap fired before any signature was produced


def test_client_constructs_despite_malformed_no_proxy(monkeypatch):
    # httpx crashes at construction on some real-world NO_PROXY values
    # (truncated IPv6 literals); the client must survive them.
    monkeypatch.setenv("NO_PROXY", "localhost,::1,[::1],fd8b:4f8")
    monkeypatch.setenv("no_proxy", "localhost,::1,[::1],fd8b:4f8")
    client = BlockchainIQClient()
    client.close()


def test_http_error_raises(transport_factory):
    def handler(request):
        return httpx.Response(404, text="not here")

    client = BlockchainIQClient(transport=transport_factory(handler))
    with pytest.raises(Exception) as excinfo:
        client.get("brief")
    assert "404" in str(excinfo.value)
