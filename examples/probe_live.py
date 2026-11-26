"""Free live probe of the Blockchain IQ API — no key, no money.

Run:  python examples/probe_live.py

Does two real calls:
  1. GET /api/x402/v1/sample  — free, returns Bitcoin network activity.
  2. GET /api/x402/v1/brief   — paid ($0.01); with no signer configured the
     client stops at the 402 and prints the decoded payment requirements:
     the exact amount, asset, network and pay-to address a paid call needs.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blockchainiq_langchain import BlockchainIQClient, PaymentRequiredError


def main() -> None:
    with BlockchainIQClient() as client:
        sample = client.sample()
        data = sample.data["data"]
        print("FREE SAMPLE  GET /api/x402/v1/sample")
        print(
            f"  {data['title']} as of {data['asOf']}: "
            f"{data['transactions']['value']:,} transactions, "
            f"{data['activeAddresses']['value']:,} active addresses, "
            f"{data['fees']['value']} BTC in fees"
        )

        print("\nPAID PROBE   GET /api/x402/v1/brief (unpaid — stops at the 402)")
        try:
            client.get("brief")
            print("  Unexpectedly free today.")
        except PaymentRequiredError as exc:
            accepted = exc.accepted or {}
            print(f"  price:    ${exc.price_usd:.2f}")
            print(f"  network:  {accepted.get('network')}")
            print(f"  asset:    {accepted.get('asset')} (USDC)")
            print(f"  pay to:   {accepted.get('payTo')}")
            print(
                "  -> configure EIP3009Signer with a funded Base wallet "
                "to complete this call for real."
            )


if __name__ == "__main__":
    main()
