"""LangChain tools for the Blockchain IQ x402 API.

Three tools:

``blockchainiq_brief``
    The $0.01 market brief — BTC/ETH/SOL/XRP prices and moves, macro and
    crypto events in the next 48 hours, and risk flags. The natural opening
    call of a trading or research agent loop, and the cheapest paid call in
    the catalogue.

``blockchainiq_resource``
    Any other catalogue resource by id or path — macro indicators, the
    economic calendar, sector indices, per-chain network activity, analytics
    dashboards and charts, stablecoin supply and blacklist events, and more.
    Prices run from free to $19; the client's per-call cap applies.

``blockchainiq_sample``
    The free sample (Bitcoin network activity). No payment, ever — useful as
    a connectivity smoke test.

Every tool returns a JSON string. On a paid call it includes ``price_usd``
and, when the API returns one, the ``settlement`` proof (including the
on-chain ``transaction`` hash) decoded from the PAYMENT-RESPONSE header. If a
paid call is attempted without a configured signer, the tool returns a JSON
object with ``error: "payment_required"`` and the exact price and pay-to
address instead of raising, so an agent can reason about the cost.
"""

from __future__ import annotations

import json
from typing import Any, Optional, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from .client import (
    BlockchainIQClient,
    BlockchainIQError,
    PaymentRequiredError,
    ResourceResult,
)
from .payments import EIP3009Signer

#: Catalogue id -> API path tail (paths verified against the live catalogue).
RESOURCE_PATHS = {
    "brief": "brief",
    "macro": "macro",
    "calendar": "calendar",
    "indices": "indices",
    "event-risk": "event-risk",
    "networks": "networks",
    "network": "network/{chain}",
    "stablecoins": "stablecoins",
    "stablecoins-events": "stablecoins/events",
    "stablecoins-history": "stablecoins/history",
    "dashboards": "dashboards/{slug}",
    "dashboard-panel": "dashboards/{slug}/{panel}",
    "sections": "sections/{section}",
    "x402-market": "x402-market",
    "x402-seller-history": "x402-seller-history",
    "cardano": "cardano",
    "cardano-epochs": "cardano/epochs",
    "sample": "sample",
}


def _result_json(result: ResourceResult) -> str:
    out: dict[str, Any] = {"data": result.data}
    if result.price_usd is not None:
        out["price_usd"] = result.price_usd
    if result.settlement is not None:
        out["settlement"] = {
            "success": result.settlement.success,
            "transaction": result.settlement.transaction,
            "network": result.settlement.network,
            "payer": result.settlement.payer,
        }
    return json.dumps(out)


def _call(client: BlockchainIQClient, path: str, params: Optional[dict]) -> str:
    try:
        return _result_json(client.get(path, params=params))
    except PaymentRequiredError as exc:
        accepted = exc.accepted or {}
        return json.dumps(
            {
                "error": "payment_required",
                "detail": str(exc),
                "price_usd": exc.price_usd,
                "pay_to": accepted.get("payTo"),
                "network": accepted.get("network"),
                "asset": accepted.get("asset"),
            }
        )
    except BlockchainIQError as exc:
        return json.dumps({"error": "api_error", "detail": str(exc)})


class _EmptyInput(BaseModel):
    pass


class BlockchainIQBriefTool(BaseTool):
    """Buy the Blockchain IQ market brief ($0.01 via x402)."""

    name: str = "blockchainiq_brief"
    description: str = (
        "Buy the Blockchain IQ crypto market brief for $0.01 (paid in USDC "
        "on Base via x402, no API key): BTC, ETH, SOL and XRP prices with "
        "24h/7d/30d moves and market-cap rank, scheduled macro and crypto "
        "events in the next 48 hours, and short risk flags. Refreshed every "
        "minute. Use as the opening call of a trading, portfolio or research "
        "loop."
    )
    args_schema: Type[BaseModel] = _EmptyInput
    client: BlockchainIQClient

    def _run(self, **kwargs: Any) -> str:
        return _call(self.client, "brief", None)


class ResourceInput(BaseModel):
    resource: str = Field(
        description=(
            "Catalogue resource id (e.g. 'brief', 'macro', 'calendar', "
            "'indices', 'event-risk', 'networks', 'stablecoins', "
            "'x402-market') or a full path tail such as "
            "'network/bitcoin-network' or 'dashboards/bitcoin-network'. "
            "Template ids: 'network' needs param chain, 'dashboards' needs "
            "slug, 'dashboard-panel' needs slug and panel, 'sections' "
            "needs section."
        )
    )
    params: Optional[dict] = Field(
        default=None,
        description=(
            "Optional query/path parameters as a JSON object, e.g. "
            '{"chain": "ethereum-network"} or {"days": 7}.'
        ),
    )


class BlockchainIQResourceTool(BaseTool):
    """Buy any Blockchain IQ catalogue resource via x402."""

    name: str = "blockchainiq_resource"
    description: str = (
        "Buy one resource from the Blockchain IQ x402 catalogue (paid in "
        "USDC on Base, no API key; each resource has a fixed per-call price "
        "from $0.01 to $19 and the client's per-call cap applies). Resources "
        "include US macro indicators ('macro'), the economic and crypto "
        "calendar ('calendar'), sector indices ('indices'), per-chain "
        "network activity ('network' with param chain), all-chains snapshot "
        "('networks'), analytics dashboards ('dashboards' with slug) and "
        "single charts ('dashboard-panel'), USDC/EURC supply and blacklist "
        "events ('stablecoins', 'stablecoins-events'), and the x402 seller "
        "conformance roster ('x402-market'). Returns the resource JSON plus "
        "the x402 settlement proof (transaction hash) when a payment was "
        "made."
    )
    args_schema: Type[BaseModel] = ResourceInput
    client: BlockchainIQClient

    def _run(self, resource: str, params: Optional[dict] = None) -> str:
        params = dict(params or {})
        path = RESOURCE_PATHS.get(resource, resource)
        # Fill {placeholders} in template paths from params.
        for key in list(params):
            token = "{" + key + "}"
            if token in path:
                path = path.replace(token, str(params.pop(key)))
        if "{" in path:
            return json.dumps(
                {
                    "error": "missing_path_parameter",
                    "detail": f"Resource '{resource}' needs parameters to "
                    f"fill its path template '{path}'.",
                }
            )
        return _call(self.client, path, params or None)


class BlockchainIQSampleTool(BaseTool):
    """Fetch the free Blockchain IQ sample (no payment)."""

    name: str = "blockchainiq_sample"
    description: str = (
        "Fetch Blockchain IQ's free sample: Bitcoin network activity "
        "(transactions, active addresses, fees) for its latest complete UTC "
        "day. Free, no payment and no API key. Useful to verify connectivity "
        "and response parsing before buying paid resources."
    )
    args_schema: Type[BaseModel] = _EmptyInput
    client: BlockchainIQClient

    def _run(self, **kwargs: Any) -> str:
        return _call(self.client, "sample", None)


def create_tools(
    client: Optional[BlockchainIQClient] = None,
    *,
    private_key: Optional[str] = None,
    max_price_usd: float = 2.00,
) -> list[BaseTool]:
    """Build the three tools, sharing one client.

    If ``client`` is omitted, one is created. When ``private_key`` is given
    (or the ``BLOCKCHAINIQ_PRIVATE_KEY`` environment variable is set), paid
    calls are enabled through :class:`EIP3009Signer`; otherwise paid tools
    report the price instead of paying.
    """
    if client is None:
        signer = None
        if private_key:
            signer = EIP3009Signer(private_key)
        else:
            try:
                signer = EIP3009Signer.from_env()
            except RuntimeError:
                signer = None
        client = BlockchainIQClient(
            payment_signer=signer, max_price_usd=max_price_usd
        )
    return [
        BlockchainIQBriefTool(client=client),
        BlockchainIQResourceTool(client=client),
        BlockchainIQSampleTool(client=client),
    ]
