import json

import httpx

from blockchainiq_langchain.client import BlockchainIQClient
from blockchainiq_langchain.payments import EIP3009Signer
from blockchainiq_langchain.tools import create_tools

from .conftest import FAKE_TX, TEST_PRIVATE_KEY, make_paid_handler


def _tools(handler, signer=None):
    client = BlockchainIQClient(
        payment_signer=signer, transport=httpx.MockTransport(handler)
    )
    tools = create_tools(client)
    return {tool.name: tool for tool in tools}


def test_create_tools_returns_three_tools():
    tools = create_tools(BlockchainIQClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))))
    assert [t.name for t in tools] == [
        "blockchainiq_brief",
        "blockchainiq_resource",
        "blockchainiq_sample",
    ]


def test_brief_tool_paid_round_trip():
    calls: list = []
    tools = _tools(make_paid_handler(calls), signer=EIP3009Signer(TEST_PRIVATE_KEY))
    out = json.loads(tools["blockchainiq_brief"].invoke({}))
    assert out["data"] == {"data": {"brief": "fake"}}
    assert out["price_usd"] == 0.01
    assert out["settlement"]["transaction"] == FAKE_TX
    assert out["settlement"]["success"] is True


def test_brief_tool_without_signer_reports_price():
    calls: list = []
    tools = _tools(make_paid_handler(calls))
    out = json.loads(tools["blockchainiq_brief"].invoke({}))
    assert out["error"] == "payment_required"
    assert out["price_usd"] == 0.01
    assert out["pay_to"] == "0x617dA9383fbA67e86137BC4924b8b7E400fB7C9e"
    assert out["network"] == "eip155:8453"


def test_resource_tool_fills_path_template():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        return httpx.Response(200, json={"data": {"chain": "eth"}})

    tools = _tools(handler)
    out = json.loads(
        tools["blockchainiq_resource"].invoke(
            {"resource": "network", "params": {"chain": "ethereum-network"}}
        )
    )
    assert seen["path"] == "/api/x402/v1/network/ethereum-network"
    assert out["data"]["data"]["chain"] == "eth"


def test_resource_tool_missing_template_param():
    tools = _tools(lambda r: httpx.Response(200, json={}))
    out = json.loads(tools["blockchainiq_resource"].invoke({"resource": "network"}))
    assert out["error"] == "missing_path_parameter"


def test_sample_tool_is_free():
    def handler(request):
        assert "PAYMENT-SIGNATURE" not in request.headers
        return httpx.Response(200, json={"data": {"chain": "btc"}})

    tools = _tools(handler)
    out = json.loads(tools["blockchainiq_sample"].invoke({}))
    assert out["data"]["data"]["chain"] == "btc"
    assert "settlement" not in out
