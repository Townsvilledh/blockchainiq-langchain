# blockchainiq-langchain

LangChain tools for the [Blockchain IQ](https://www.blockchainiq.co) x402 API —
crypto market data your agent buys one call at a time, paid in USDC on Base
over the open [x402](https://www.x402.org) standard. No account, no API key:
an unpaid request returns HTTP 402 with the price, the tool pays, and the
data comes back as JSON together with the settlement proof (including the
on-chain transaction hash) from the `PAYMENT-RESPONSE` header.

Built for the Blockchain IQ x402 build bounties (LangChain slot).

## The tools

| Tool | What it does | Price |
| --- | --- | --- |
| `blockchainiq_brief` | Market brief: BTC/ETH/SOL/XRP prices with 24h/7d/30d moves and rank, macro & crypto events in the next 48h, risk flags. Refreshed every minute. | $0.01 |
| `blockchainiq_resource` | Any other catalogue resource: US macro indicators, economic calendar, sector indices, per-chain network activity, analytics dashboards and charts, USDC/EURC supply and blacklist events, the x402 seller conformance roster, and more. | $0.01–$19 per call (cap applies) |
| `blockchainiq_sample` | Free sample: Bitcoin network activity for its latest complete UTC day. A connectivity smoke test. | Free |

The full catalogue (42 resources, each with a fixed per-call price) is free
to read at <https://www.blockchainiq.co/api/x402/v1>.

## Install

```bash
pip install .            # core tools (free resources + payment reporting)
pip install ".[evm]"     # + eth-account, enables real paid calls
pip install ".[dev]"     # + pytest, to run the tests
```

## Quickstart

```python
from blockchainiq_langchain import create_tools

tools = {t.name: t for t in create_tools()}

# Free — no wallet needed:
print(tools["blockchainiq_sample"].invoke({}))

# Paid — without a wallet key this reports the exact price instead of paying:
print(tools["blockchainiq_brief"].invoke({}))
```

To make paid calls for real, give the tools a dedicated, small-balance Base
wallet key (environment variable only — never commit it):

```bash
BLOCKCHAINIQ_PRIVATE_KEY=0x... python examples/langchain_quickstart.py
```

With the key set, the brief call pays $0.01 in USDC on Base through x402 and
the tool output includes the settlement proof:

```json
{ "data": { "...": "the brief" },
  "price_usd": 0.01,
  "settlement": { "success": true, "transaction": "0x…", "network": "eip155:8453", "payer": "0x…" } }
```

Or use the client directly:

```python
from blockchainiq_langchain import BlockchainIQClient, EIP3009Signer

client = BlockchainIQClient(
    payment_signer=EIP3009Signer.from_env(),  # reads BLOCKCHAINIQ_PRIVATE_KEY
    max_price_usd=2.00,                        # per-call safety cap
)
result = client.get("network/bitcoin-network")   # $0.03
print(result.data, result.settlement.transaction)
```

## How payment works

1. The client GETs the resource. Free resources answer 200 immediately.
2. A paid resource answers **402** with a base64 `PAYMENT-REQUIRED` header
   (x402 v2) listing payment options. The client picks the Base `exact`
   option and checks its price against the cap — **over-cap prices are
   refused before anything is signed**.
3. The signer builds an EIP-3009 `transferWithAuthorization` for the exact
   amount to the API's `payTo` address and signs it as EIP-712 typed data
   under the USDC domain on Base (`USD Coin`, version `2`, chain id 8453).
   The signature goes in the `PAYMENT-SIGNATURE` header on the retry.
4. The server settles on-chain and answers 200 with the data plus a
   `PAYMENT-RESPONSE` header; the client decodes it into the `settlement`
   proof returned alongside the data.

Safety posture: use a dedicated wallet with a small balance, keep the
per-call cap low (default $2.00), and treat the key like cash. Signing is
implemented in `blockchainiq_langchain/payments.py` and kept separate from
the HTTP client, so the wallet never has to be shared with tool code.

## Verified behaviour (2026-11-26)

Free paths were exercised against the live API, and the paid path against a
mock transport using the API's own genuine 402 challenge:

- `examples/probe_live.py` (no key, no money) prints:

  ```
  FREE SAMPLE  GET /api/x402/v1/sample
    Bitcoin Network as of 2026-11-24: 502,260 transactions, 690,760 active addresses, 2.91517103 BTC in fees

  PAID PROBE   GET /api/x402/v1/brief (unpaid — stops at the 402)
    price:    $0.01
    network:  eip155:8453
    asset:    0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913 (USDC)
    pay to:   0x617dA9383fbA67e86137BC4924b8b7E400fB7C9e
  ```

- The test-suite (14 tests, `pytest`) replays the **real** `PAYMENT-REQUIRED`
  header captured from the live `/brief` endpoint
  (`tests/fixtures/payment_required_brief.txt`), drives the full
  402 → sign → retry → settlement flow through a mock transport, and
  **cryptographically verifies** the EIP-3009 signature by recovering the
  signer address from it — the same check `transferWithAuthorization`
  performs on-chain. The price-cap refusal and the no-signer price report
  are covered too.

- One honest limitation: the author did not execute a live *paid* call.
  Everything up to the payment is proven live, the signing is proven
  cryptographically, and the paid retry is proven against the mock
  transport. The single remaining step is running
  `examples/langchain_quickstart.py` once with a funded key: it performs a
  real **$0.01** paid call to `GET /api/x402/v1/brief` (USDC on Base to
  `0x617dA9383fbA67e86137BC4924b8b7E400fB7C9e`) and prints the settlement
  transaction hash.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev,evm]"
.venv/bin/python -m pytest
.venv/bin/python examples/probe_live.py
```

## Licence

MIT. Market data and analysis from Blockchain IQ are not investment advice;
see their terms at <https://www.blockchainiq.co/terms>.
