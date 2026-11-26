"""A small, dependency-light client for the Blockchain IQ x402 API.

Blockchain IQ (https://www.blockchainiq.co) sells crypto market data one call
at a time over the x402 protocol: no account, no API key. An unpaid request to
a paid resource returns HTTP 402 with a base64 ``PAYMENT-REQUIRED`` header
describing the price; the client pays in USDC on Base (x402 "exact" scheme,
an EIP-3009 ``transferWithAuthorization`` signature) and retries with a
``PAYMENT-SIGNATURE`` header. A successful paid response carries a
``PAYMENT-RESPONSE`` header whose decoded JSON is the settlement proof,
including the on-chain transaction hash.

This client owns the HTTP and protocol handling. Signing is delegated to a
pluggable ``PaymentSigner`` (see :mod:`blockchainiq_langchain.payments`), so
the wallet stays outside the client and tests can substitute a test signer.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

BASE_URL = "https://www.blockchainiq.co"
CATALOG_PATH = "/api/x402/v1"

#: USDC on Base (the asset in every Base ``accepts`` entry of this API).
BASE_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
BASE_NETWORK = "eip155:8453"

#: Atomic-unit decimals for assets we know how to price for the safety cap.
_ASSET_DECIMALS = {BASE_USDC.lower(): 6}


class BlockchainIQError(Exception):
    """Any non-payment failure talking to the API."""


class PaymentRequiredError(BlockchainIQError):
    """A paid resource was requested but no payment could be made.

    Carries the decoded payment requirements so callers (and agents) can see
    exactly what the call would cost and where the money would go.
    """

    def __init__(self, message: str, *, requirements: dict, accepted: Optional[dict]):
        super().__init__(message)
        self.requirements = requirements
        self.accepted = accepted

    @property
    def price_usd(self) -> Optional[float]:
        if not self.accepted:
            return None
        return atomic_to_usd(self.accepted)


class PriceCapExceededError(PaymentRequiredError):
    """The asked price is above the client's configured per-call cap."""


def atomic_to_usd(accepted: dict) -> Optional[float]:
    """Convert an ``accepts`` entry's atomic amount to a USD figure.

    Returns ``None`` for assets whose decimals we do not know, so the caller
    can refuse to guess at a price.
    """
    decimals = _ASSET_DECIMALS.get(str(accepted.get("asset", "")).lower())
    if decimals is None:
        return None
    try:
        return int(accepted["amount"]) / (10**decimals)
    except (KeyError, TypeError, ValueError):
        return None


def _b64_json_decode(value: str) -> dict:
    return json.loads(base64.b64decode(value).decode("utf-8"))


def _env_proxy_url() -> Optional[str]:
    """The first proxy URL from the environment, scheme added if missing."""
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
                "ALL_PROXY", "all_proxy"):
        value = os.environ.get(var)
        if value:
            return value if "://" in value else f"http://{value}"
    return None


def _build_http_client(
    base_url: str, transport: Optional[httpx.BaseTransport], timeout: float
) -> httpx.Client:
    """Build the httpx client, tolerating malformed proxy environments.

    httpx parses NO_PROXY entries into URL patterns at construction time and
    crashes on some real-world values (e.g. truncated IPv6 literals). If the
    default construction fails, fall back to an explicit proxy taken from the
    *_PROXY variables with trust_env off.
    """
    if transport is not None:
        return httpx.Client(base_url=base_url, transport=transport, timeout=timeout)
    try:
        return httpx.Client(base_url=base_url, timeout=timeout)
    except Exception:
        # trust_env=False also stops httpx honouring SSL_CERT_FILE, so pass
        # the CA bundle explicitly to keep certificate verification intact.
        verify = os.environ.get("SSL_CERT_FILE") or True
        proxy = _env_proxy_url()
        if proxy:
            return httpx.Client(
                base_url=base_url,
                timeout=timeout,
                proxy=proxy,
                trust_env=False,
                verify=verify,
            )
        return httpx.Client(
            base_url=base_url, timeout=timeout, trust_env=False, verify=verify
        )


def _select_accepted(requirements: dict, network: str = BASE_NETWORK) -> Optional[dict]:
    """Pick the Base ``exact`` payment option out of a 402's ``accepts`` list."""
    accepts = requirements.get("accepts") or []
    for entry in accepts:
        if entry.get("network") == network and entry.get("scheme") == "exact":
            return entry
    return accepts[0] if accepts else None


@dataclass
class Settlement:
    """Settlement proof decoded from a paid response's PAYMENT-RESPONSE header."""

    success: bool
    transaction: Optional[str] = None
    network: Optional[str] = None
    payer: Optional[str] = None
    raw: dict = field(default_factory=dict)


@dataclass
class ResourceResult:
    """The JSON data of one API call, plus what it cost and how it settled."""

    data: Any
    price_usd: Optional[float] = None
    settlement: Optional[Settlement] = None
    status_code: int = 200


class BlockchainIQClient:
    """Client for the Blockchain IQ x402 API.

    Parameters
    ----------
    payment_signer:
        Object with ``create_payment_header(requirements, accepted) -> str``
        (see :mod:`blockchainiq_langchain.payments`). ``None`` means the client
        can read free resources and will raise :class:`PaymentRequiredError`
        on paid ones.
    max_price_usd:
        Safety cap. A 402 asking more than this is refused *before* any
        signing happens. Defaults to $2.00; set it deliberately.
    base_url:
        API origin. Override only for testing.
    transport:
        An ``httpx`` transport, e.g. ``httpx.MockTransport`` in tests.
    """

    def __init__(
        self,
        payment_signer: Optional[Any] = None,
        *,
        max_price_usd: float = 2.00,
        base_url: str = BASE_URL,
        transport: Optional[httpx.BaseTransport] = None,
        timeout: float = 30.0,
    ):
        self.payment_signer = payment_signer
        self.max_price_usd = max_price_usd
        self.base_url = base_url.rstrip("/")
        self._http = _build_http_client(self.base_url, transport, timeout)

    # -- public API ---------------------------------------------------------

    def get(self, path: str, params: Optional[dict] = None) -> ResourceResult:
        """GET one resource, paying through x402 if the resource is priced.

        ``path`` may be a full API path (``/api/x402/v1/brief``) or just the
        resource tail (``brief``, ``network/bitcoin-network``).
        """
        if not path.startswith("/"):
            path = f"{CATALOG_PATH}/{path}"
        response = self._http.get(path, params=params)
        if response.status_code == 200:
            return ResourceResult(data=response.json(), status_code=200)
        if response.status_code == 402:
            return self._pay_and_retry(path, params, response)
        raise BlockchainIQError(
            f"GET {path} failed with HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )

    def catalog(self) -> ResourceResult:
        """The free catalogue: every resource, price and response shape."""
        return self.get(CATALOG_PATH)

    def sample(self) -> ResourceResult:
        """The free sample resource (Bitcoin network activity, unpaid)."""
        return self.get(f"{CATALOG_PATH}/sample")

    # -- x402 payment flow --------------------------------------------------

    def _pay_and_retry(
        self, path: str, params: Optional[dict], first_response: httpx.Response
    ) -> ResourceResult:
        header = first_response.headers.get("PAYMENT-REQUIRED") or first_response.headers.get(
            "X-PAYMENT-REQUIRED"
        )
        if not header:
            raise BlockchainIQError(
                f"GET {path} returned 402 without a PAYMENT-REQUIRED header"
            )
        try:
            requirements = _b64_json_decode(header)
        except Exception as exc:  # pragma: no cover - defensive
            raise BlockchainIQError(
                f"GET {path} returned an undecodable PAYMENT-REQUIRED header"
            ) from exc

        accepted = _select_accepted(requirements)
        if accepted is None:
            raise PaymentRequiredError(
                f"GET {path} returned 402 with no usable payment option",
                requirements=requirements,
                accepted=None,
            )

        price = atomic_to_usd(accepted)
        if price is not None and price > self.max_price_usd:
            raise PriceCapExceededError(
                f"GET {path} costs ${price:.2f}, above the configured "
                f"per-call cap of ${self.max_price_usd:.2f}; refusing to pay",
                requirements=requirements,
                accepted=accepted,
            )
        if self.payment_signer is None:
            raise PaymentRequiredError(
                f"GET {path} costs "
                + (f"${price:.2f}" if price is not None else "an unknown amount")
                + " via x402, but no payment signer is configured. Pass a "
                "payment_signer (e.g. EIP3009Signer.from_env()) to enable "
                "paid calls.",
                requirements=requirements,
                accepted=accepted,
            )

        payment_header = self.payment_signer.create_payment_header(
            requirements, accepted
        )
        paid_response = self._http.get(
            path, params=params, headers={"PAYMENT-SIGNATURE": payment_header}
        )
        if paid_response.status_code != 200:
            raise BlockchainIQError(
                f"Paid retry of GET {path} failed with HTTP "
                f"{paid_response.status_code}: {paid_response.text[:300]}"
            )

        settlement = None
        response_header = paid_response.headers.get(
            "PAYMENT-RESPONSE"
        ) or paid_response.headers.get("X-PAYMENT-RESPONSE")
        if response_header:
            try:
                raw = _b64_json_decode(response_header)
                settlement = Settlement(
                    success=bool(raw.get("success")),
                    transaction=raw.get("transaction"),
                    network=raw.get("network"),
                    payer=raw.get("payer"),
                    raw=raw,
                )
            except Exception:  # pragma: no cover - never fail a paid call on proof parsing
                settlement = None

        return ResourceResult(
            data=paid_response.json(),
            price_usd=price,
            settlement=settlement,
            status_code=200,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "BlockchainIQClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
