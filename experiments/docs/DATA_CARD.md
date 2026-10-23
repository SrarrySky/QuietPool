# Dataset card

Each historical dataset is documented with the following fields.

| Field | Required entry |
| --- | --- |
| Chain / venue | BSC (56), PancakeSwap V2 |
| Pair address and assets | Pair, token0, token1, quote side, decimals |
| Selection rule | Preregistered volume range, period, volatility strata |
| Split assignment | Calibration or test, assigned before strategy outcomes |
| Block range and timestamps | Inclusive downloaded range; enough coverage for replay horizon |
| Initial state | Reserves at end of start_block - 1 |
| Finality | Pinax last contiguous header has end_block <= lib_num <= block_num; provider-trusted evidence |
| Ordering and continuity | block/log order, contiguous blocks, matching parent hashes |
| Trader attribution | transaction.from; never router sender/to |
| Unsupported events | Number and reasons for excluded intervals/pools |
| Price conversion | USDT=USD or causal WBNB/USD source and resolution |
| Funding evidence | Synthetic controlled-wallet treasury edges; no organic funding histories |
| Integrity | SHA256 of each explicit source file, import configuration, prices and frozen replay outputs; import_manifest.json |
| Access restrictions | Public demo coverage can reset; retain provider terms; never put credentials in import YAML |
| Ethical scope | Historical reads and local simulation only |

The Pinax importer qualifies swap-only histories. Initial state comes from the
last pre-start Sync; the export must cover all pool logs from coverage_start.
Pair/token decimals and venue membership require separately archived metadata;
the three event tables alone do not establish them. The importer refuses to silently discard
unsupported liquidity events. Report this restriction and all exclusions in the
paper; choosing pools based on whether a hypothesis holds is not permitted by the
experimental protocol.

Pools that fail the fixed historical eligibility rule (at least 72 organic swaps
and positive finite baseline volatility) cannot enter the main experiment. They
are handled by the formal data-eligibility-and-supplementation stage
([REPLACEMENT_PROTOCOL.md](REPLACEMENT_PROTOCOL.md)): only those slots are replaced
from a pre-fixed reserve order using the same rule, and the amended cohort is
re-audited before freezing. Eligibility thresholds are never relaxed and no
strategy outcome is consulted.

Figures regenerated with `redteam plot --input SOURCE --output DEST` are derived
artifacts rather than a new dataset or experiment. Retain `plot_manifest.json`
with the source experiment's `manifest.json` and `protocol.lock.json`; the command
does not contact data providers or modify the source result directory.
