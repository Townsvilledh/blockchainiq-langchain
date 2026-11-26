"""The EIP-3009 signer must produce signatures that actually verify against
the USDC EIP-712 domain — tested cryptographically, with a test key, and
never sent anywhere."""

import base64
import json

from eth_account import Account
from eth_account.messages import encode_typed_data

from blockchainiq_langchain.client import _b64_json_decode, _select_accepted
from blockchainiq_langchain.payments import EIP3009Signer

from .conftest import FIXTURE_HEADER, TEST_ADDRESS, TEST_PRIVATE_KEY


def _accepted():
    requirements = _b64_json_decode(FIXTURE_HEADER)
    accepted = _select_accepted(requirements)
    assert accepted is not None
    return requirements, accepted


def test_signer_address_matches_test_key():
    signer = EIP3009Signer(TEST_PRIVATE_KEY)
    assert signer.address == TEST_ADDRESS


def test_payment_header_signature_recovers_to_signer():
    requirements, accepted = _accepted()
    signer = EIP3009Signer(TEST_PRIVATE_KEY)

    header = signer.create_payment_header(requirements, accepted)
    payload = json.loads(base64.b64decode(header))

    assert payload["x402Version"] == 2
    auth = payload["payload"]["authorization"]
    assert auth["from"] == TEST_ADDRESS
    assert auth["to"] == accepted["payTo"] == "0x617dA9383fbA67e86137BC4924b8b7E400fB7C9e"
    assert auth["value"] == "10000"
    assert int(auth["validBefore"]) > int(auth["validAfter"])

    # Rebuild the exact typed data from the payload and recover the signer
    # from the signature: this is the same check the USDC contract's
    # transferWithAuthorization performs (via the facilitator).
    typed_data = signer.build_typed_data(
        accepted,
        valid_after=int(auth["validAfter"]),
        valid_before=int(auth["validBefore"]),
        nonce=bytes.fromhex(auth["nonce"][2:]),
    )
    signable = encode_typed_data(full_message=typed_data)
    recovered = Account.recover_message(
        signable, signature=bytes.fromhex(payload["payload"]["signature"][2:])
    )
    assert recovered == TEST_ADDRESS
    assert typed_data["domain"]["chainId"] == 8453
    assert typed_data["domain"]["name"] == "USD Coin"
    assert typed_data["domain"]["verifyingContract"] == accepted["asset"]
