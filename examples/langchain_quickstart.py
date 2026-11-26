"""LangChain quickstart for the Blockchain IQ tools.

Run:  python examples/langchain_quickstart.py

Without a wallet key this runs the free sample tool for real and shows the
brief tool reporting its $0.01 price. To make paid calls for real, set a
dedicated small-balance Base wallet key first:

    BLOCKCHAINIQ_PRIVATE_KEY=0x... python examples/langchain_quickstart.py

With the key set, the brief call pays $0.01 in USDC on Base through x402 and
prints the settlement proof, including the on-chain transaction hash.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blockchainiq_langchain import create_tools


def show(title: str, raw: str) -> None:
    print(f"\n== {title} ==")
    print(json.dumps(json.loads(raw), indent=2)[:1200])


def main() -> None:
    tools = {tool.name: tool for tool in create_tools()}
    print("Tools:", ", ".join(tools))

    show("blockchainiq_sample (free)", tools["blockchainiq_sample"].invoke({}))
    show(
        "blockchainiq_brief ($0.01 via x402)",
        tools["blockchainiq_brief"].invoke({}),
    )


if __name__ == "__main__":
    main()
