"""Payment signing for the x402 "exact" scheme on Base (EIP-3009).

The Blockchain IQ API's Base payment option is the x402 ``exact`` scheme:
the client signs an ERC-3009 ``transferWithAuthorization`` for the USDC
amount with EIP-712 typed data, wraps the signature in a payment payload,
base64-encodes it, and sends it as the ``PAYMENT-SIGNATURE`` header. The
server (or its facilitator) then submits the authorization on-chain and
returns the data plus a settlement proof.

The signer here is a *real* implementation of that scheme: given a private
key it produces signatures that verify against the USDC contract's EIP-712
domain on Base (``USD Coin``, version ``2``, chain id 8453). It is only ever
invoked by the client after the price-cap check passes. Unit tests exercise
it with a throwaway test key against a mock transport — nothing is signed
for, or sent to, the live API by the test-suite.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
from typing import Optional, Protocol

#: Environment variable the default signer reads its key from.
PRIVATE_KEY_ENV = "BLOCKCHAINIQ_PRIVATE_KEY"


class PaymentSigner(Protocol):
    """Anything that can turn decoded 402 requirements into a header value."""

    def create_payment_header(self, requirements: dict, accepted: dict) -> str:
        """Return the base64 ``PAYMENT-SIGNATURE`` header value."""
        ...


def _parse_chain_id(network: str) -> int:
    # Networks are CAIP-2, e.g. "eip155:8453".
    return int(network.split(":")[-1])


class EIP3009Signer:
    """Signs x402 ``exact`` payments for EVM networks with a local key.

    Parameters
    ----------
    private_key:
        Hex private key of a wallet holding USDC on Base (and a little ETH
        is *not* needed — the facilitator submits the transfer). Use a
        dedicated, small-balance wallet: this key authorizes real payments
        whenever the client decides a price is within its cap.
    """

    def __init__(self, private_key: str):
        try:
            from eth_account import Account
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ImportError(
                "EIP3009Signer needs the 'eth-account' package: "
                "pip install 'blockchainiq-langchain[evm]'"
            ) from exc
        self._account = Account.from_key(private_key)

    @property
    def address(self) -> str:
        return self._account.address

    @classmethod
    def from_env(cls, env_var: str = PRIVATE_KEY_ENV) -> "EIP3009Signer":
        key = os.environ.get(env_var)
        if not key:
            raise RuntimeError(
                f"{env_var} is not set. Paid calls need a dedicated wallet "
                f"key, e.g. {env_var}=0x... (never commit this key)."
            )
        return cls(key)

    # -- signing ------------------------------------------------------------

    def build_typed_data(self, accepted: dict, *, valid_after: int, valid_before: int, nonce: bytes) -> dict:
        """The EIP-712 message for one ``accepts`` entry (exposed for tests)."""
        extra = accepted.get("extra") or {}
        authorization = {
            "from": self.address,
            "to": accepted["payTo"],
            "value": int(accepted["amount"]),
            "validAfter": valid_after,
            "validBefore": valid_before,
            "nonce": nonce,
        }
        return {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "TransferWithAuthorization": [
                    {"name": "from", "type": "address"},
                    {"name": "to", "type": "address"},
                    {"name": "value", "type": "uint256"},
                    {"name": "validAfter", "type": "uint256"},
                    {"name": "validBefore", "type": "uint256"},
                    {"name": "nonce", "type": "bytes32"},
                ],
            },
            "primaryType": "TransferWithAuthorization",
            "domain": {
                "name": extra.get("name", "USD Coin"),
                "version": extra.get("version", "2"),
                "chainId": _parse_chain_id(accepted["network"]),
                "verifyingContract": accepted["asset"],
            },
            "message": authorization,
        }

    def create_payment_header(self, requirements: dict, accepted: dict) -> str:
        from eth_account import Account
        from eth_account.messages import encode_typed_data

        now = int(time.time())
        valid_before = now + int(accepted.get("maxTimeoutSeconds", 300))
        typed_data = self.build_typed_data(
            accepted,
            valid_after=0,
            valid_before=valid_before,
            nonce=secrets.token_bytes(32),
        )
        signable = encode_typed_data(full_message=typed_data)
        signed = Account.sign_message(signable, private_key=self._account.key)
        signature = "0x" + signed.signature.hex()

        auth = typed_data["message"]
        payload = {
            "x402Version": int(requirements.get("x402Version", 2)),
            # v2 carries the chosen requirements; v1 fields kept alongside so
            # either parser generation can read the header.
            "accepted": accepted,
            "scheme": accepted["scheme"],
            "network": accepted["network"],
            "payload": {
                "signature": signature,
                "authorization": {
                    "from": auth["from"],
                    "to": auth["to"],
                    "value": str(auth["value"]),
                    "validAfter": str(auth["validAfter"]),
                    "validBefore": str(auth["validBefore"]),
                    "nonce": "0x" + auth["nonce"].hex(),
                },
            },
        }
        return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


class UnconfiguredSigner:
    """Placeholder signer that always refuses, with the reason attached."""

    def create_payment_header(self, requirements: dict, accepted: dict) -> str:
        raise RuntimeError(
            "No payment signer configured. Create one with "
            "EIP3009Signer.from_env() (env "
            f"{PRIVATE_KEY_ENV}) to enable paid calls."
        )
