# Operational definitions

## Replay and information timing

Blocks are processed in increasing number; historical swaps retain log order.
Within each block, all organic swaps run first, followed by the manipulator and
one arbitrage correction. Empty blocks are included so strategy scheduling is not
conditioned on organic activity. Each historical input is replayed against current
sandbox reserves; historical output is used only to qualify baseline fidelity.

Path B imports a frozen Pinax archive. The initial reserves are the last Sync
strictly before **start_block**, with complete pool-log coverage from
`coverage_start` through `end_block`. This represents end-of-start_block-minus-one
state even when that block contains no pool events. PancakeSwap V2 emits **Sync before Swap**;
each Swap is paired with the preceding Sync in the same transaction. Standalone
Sync, unsupported multi-input/output swaps, removed logs, incomplete block chains,
and reserve/output mismatches are rejected. Pinax imports require exact raw
reserve transitions, a contiguous parent-hash chain, and a later block whose
`lib_num` covers the interval. These checks trust provider completeness and are
not independent consensus or receipt-inclusion proofs. Import, simulation, and
analysis are offline. Block hashes are retained in
`blocks.parquet`. Raw uint112/uint256 quantities are decimal strings in Parquet,
not lossy floats or overflowing int64 values.

The arbitrage reference is the latest observed historical reserve ratio, not an
exchange price for the meme token. The trigger compares
`abs(log(sandbox_price / reference_price)) * 10000` with the configured band.
Integer binary search finds one corrective input. Arbitrage trades stay in the
price path (including reversion calculations) but are excluded from entities and
volume-share denominators.

USDT is assumed to equal USD. WBNB is converted using causal BNBUSDT hourly
**opens**, because the upstream adapter provides hourly candles. Using a candle's
close at its opening timestamp would leak future information. An external causal
minute-level series can replace this approximation. The data manifest records the
choice; stablecoin basis and hourly conversion error remain limitations.

## Strategy preservation and the ladder

The source subset is byte-for-byte unchanged. Importing its `backend/` through
`sys.path` avoids importing its web application, databases, settlement
orchestration, or signer components.
The pool invokes `quote_v2_exact_in` for primary 25-bps pricing. Only explicitly
labelled fee sensitivity scenarios use generalized integer fee arithmetic.

The original `ActivityController`, `BundlePlanner`, and `SelfTradeGuard` are called.
The guard sees recent accepted orders **and** proposed orders; checking only legs
of one same-direction bundle would not enforce the requested reversal interval.
The original nominal inventory state is separate from physical balances. NoSafeSize,
RiskLimitExceeded, NotDueYet, and SelfTradeRisk are non-fills, recorded with reasons.
Runtime patching of only the two inventory constants implements M7 and is restored
immediately after preview. M7 maps to S1 and branches from M5 at 10%.

The same manipulator accepts a second, non-cumulative ladder encoding. When
`ladder.yaml` carries a `specs:` list, each entry is an explicit vendor-control
profile (`max_amount_usdt`, organic-participation cap, interval chain, wallet
count, bundle threshold, inventory band) rather than an inherited level. This
encoding is used by the supplementary experiment below; the cumulative M0–M7
encoding is otherwise translated into the identical schema before execution.

The original nominal inventory-recovery threshold remains active for every level.
Therefore, M0 is not an unconditional strict-alternation policy, and widening M7's
soft/hard bands may have little effect when another original recovery rule binds.
The original organic cap is disabled when measured organic volume is exactly zero.
These are findings about the supplied implementation, not changes made by this
harness. Funding seeds change only random funding amounts; they do not represent
independent strategy or market realizations.

Physical endowments are consolidated and identical across levels. Sandbox fills
cannot spend more quote/token than those balances contain. The five synthetic
funding edges are attribution evidence, not constraints on individual wallet
balances; gas, treasury transfer costs, borrowing, and wallet-specific settlement
are outside the experiment. Authors must freeze endowments before historical runs.

## Entities and windows

V1 merges all controlled wallets into E_manip. V0 merges wallets with a shared
funding ancestor at up to k directed upstream transfer hops. Random funding amounts
do not remove the common treasury ancestor. Organic funding histories are not
retrieved, so each organic address remains separate. G1 therefore cannot identify
organic funding relationships in these data; this baseline limitation is explicit.

Windows are half-open `[start,end)`. Only complete windows are evaluated. Standard
indicators use the configured daily-width sliding windows; c' has its own width
and both sliding and consecutive fixed windows anchored at replay start. Hourly
sliding evaluation is a sampled implementation, not the continuous family of
windows assumed in Proposition 3(ii). Fixed windows are not implicitly UTC midnight.
Inactive controlled entities remain in the evaluation grid, so low activity does
not remove silent windows from an alarm-rate denominator. Inactive organic
entities are not treated as extra calibration observations.

- **a:** entity input USD notional / pool input USD notional, excluding arbitrage.
- **b:** absolute sum of the entity's attributable log-price increments.
- **c':** `1 - abs(sum(signed_token)) / sum(abs(signed_token))`, gated on a minimum
  entity volume share. An ineligible window is legitimately silent; coverage is
  reported separately. No inventory dollar proxy substitutes for actual token flow.
- **d:** opposite-direction dollar notionals matched FIFO within T seconds.
  Both matched legs contribute once to the numerator. V0 matches within each
  wallet then aggregates; V1 matches at controlled-entity level. This follows the
  manuscript's dollar-notional definition, not token matching.
- **e:** inter-arrival burstiness and the impact-weighted fraction of each trade's
  absolute log-price impact subsequently recovered within H. Recovery is the
  maximum opposite-direction excursion in the subsequent complete price path,
  capped at that trade's impact. Both components must exceed their independently
  calibrated thresholds. Fewer than three trades gives undefined burstiness;
  an all-simultaneous group with at least three trades is assigned burstiness 1.
  Trades without a complete forward H are censored; active windows with unavailable
  e statistics produce an unidentified run-level p_e, never a false success.
- **G1:** at least two active wallets with supplied funding edges in the entity.
- **G2:** at least one inter-trade interval strictly below g2_seconds.
- **G3:** the same minimum-share-gated net-position statistic as c'.

G1–G3 are transparent operational proxies from the supplied description, not a
claim of independently reproducing an unavailable Gan et al. reference codebase.
MAR (f) is analytically void; MAR (g), oracle manipulation, is outside scope.
Figure 3(a) uses V1, where the controlled group is fully attributed and the
noncontrolled denominator is organic volume; this avoids incorrectly applying
the group-level bound to an incomplete V0 wallet cluster.

The complete panel definitions, visual encodings, missing-evidence conventions
and manuscript insertion gate are fixed in [FIGURES.md](FIGURES.md). In
particular, color is never the sole status channel, and synthetic-validation
figures are visibly marked and cannot substitute for historical evidence.

## Calibration and inference

Only organic-only simulations of calibration pools determine quantiles. Each
organic entity-window is a calibration observation. The clean variant removes
pool/entity/view groups flagged above the raw calibration 95th quantile by any
G1–G3 proxy, and recalibrates. G1 exclusion is limited by missing organic funding
edges. e uses two separate component quantiles and a conjunction, so its joint
organic load is not nominally 5% or 1%. All comparisons are strictly greater than
the threshold; tied or discrete distributions also need not yield exactly the
nominal alert load. Organic traders are unlabelled: report **alert load**, not FPR.

All runs, percentiles, risk weights, information views, and robust calibrations
remain separate. Evasion minima are computed **within each pool**, over all tested
level/parameter/seed settings. Both minima require V >= V*. A baseline unable to
reach V* produces unidentified cost, rather than infinity. Infinity is reserved
for a valid target-reaching baseline with no tested quiet feasible configuration.
Maximum quiet volume is exported separately. A target-reaching configuration with
an unknown alarm rate makes the finite-grid minimum unidentified, even if other
configurations are evaluated. Lack of a calibrated statistic
produces missing evidence, not an infeasibility conclusion.

Organic-only test replay is also evaluated separately and exported to
`organic_only_alert_load.csv`; `organic_alert_load.csv` describes organic alerts
on intervention paths. Neither dataset is used to adjust thresholds.

Uncertainty resamples entire pools with replacement after averaging seed replicates.
The reported estimand is the mean of per-pool minima/differences, not a minimum
across heterogeneous pools. With fewer than two pools, intervals are unavailable.
Nearest-rank bootstrap quantiles preserve infinity. Finite-grid findings are
labelled cosmetic, costly-on-grid, grid-infeasible, or unresolved; only the analytical
propositions can support a statement applying to every strategy.

## Costs and additional diagnostics

V is input asset amount valued in USD at the **pre-trade** marginal pool price.
F converts integer fee amounts at that same price. Marked wealth is
`(quote_balance + token_balance * sandbox_price) * quote_usd`. S is initial wealth
minus terminal wealth minus F. Unrealized token and quote appreciation is therefore
included, and S and total realized cost may be negative.

The primary R follows the development document: sample standard deviation (ddof=1)
of wealth changes over equally spaced, nonoverlapping risk windows. The manuscript's
phrase "standard deviation of wealth across windows" differs from this convention
and must be aligned before submission. Risk-window endpoints use state before trades
at the right boundary. c is `(F + S + lambda*R)/V * 10000` bps.

Starting token endowments create passive market exposure. `marks.parquet` therefore
also exports the passive buy-and-hold benchmark and incremental trading P&L, and
summary exports their terminal changes. Primary c retains the literal prescribed
wealth definition. `max_abs_inventory_usd` includes the full physical inventory;
`max_abs_inventory_change_usd` separately records deviation from initial holdings.
Authors should not interpret an endowment-driven change as solely manipulation risk.

Proposition 1 defines volume as the sum of the two **quote legs**, whereas the
empirical metric values the token input at its pre-sell marginal price. The test
checks the proposition using its own denominator and separately validates the
empirical definition. The distinction is small for tiny trades but mathematically
real. Fee floor rounding can also make F differ slightly from f*V at small raw sizes.

## Sensitivity and scope

Risk weights 0, 1, and 2 are recomputed from the same paths. Optional additional
scenarios independently change arbitrage, fee, initial depth, and window length.
Each scenario recalibrates using its own calibration-only replay. The depth scenarios
scale initial reserves but retain observed organic input amounts and reference
prices: they are explicit counterfactuals, not chain fidelity checks. Classification
flips are exported, with unavailable evidence preserved.

The suite does not provide a real-time trading interface, strategy optimization
against live venues, live execution, transaction construction, or account credentials.
It does not establish empirical H1–H4 without a preregistered real sample. Synthetic
fixtures test mechanics only; two synthetic test pools cannot substantiate an H3
volatility-tercile claim. Historical exclusions and endogenous organic responses,
MEV, transfer costs, concentrated liquidity, and nonstandard token mechanics remain
outside the current study.

## Supplementary experiment: General Player vs Top Player

The primary grid is cumulative: M1–M7 each add a control on top of the previous
level, so the marginal effect of a single control is not isolated. The
supplementary experiment replaces that ladder with a directed, one-factor-at-a-time
grid declared under `specs:` in a separate frozen configuration. `G0` is the
honest baseline (identical to M0); every `TP_*` spec deviates a single declared
control toward one MAR statistic while holding the other controls at their honest
defaults. This keeps each effect attributable and directly tests whether a
threshold-aware trader can dodge a statistic without an exhaustive search.

The supplementary configuration amends the primary cohort to a
**10-calibration / 20-test design (30 pools)**. The re-fetched historical
snapshot (`pinax_20261024`) left four pools (`pool_15`, `pool_17`, `pool_20`,
`pool_33`) failing the fixed eligibility rule for fewer than 72 organic swaps;
rather than invoking the reserve-replacement stage, those four pools were dropped
together with two pre-flagged vacancy pools (`pool_14`, `pool_24`) to preserve the
1:2 calibration:test ratio. Calibration and indicator thresholds are recomputed
on the retained ten calibration pools.

The distinction is behavioral, not a relabeling. A **General Player** follows the
preregistered cumulative ladder, adding controls one after another as if evasion
were a monotonic hardening exercise. A **Top Player** reasons from the indicator
definition and adjusts only the knob the statistic is actually sensitive to. The
contrast is therefore between *strategy complexity* (general) and *strategy
targeting* (top).

`TP_b` and `TP_c`/`TP_d` are deliberately antagonistic. Indicator (b) is silent
for symmetric wash trading because positive and negative log-price increments
cancel, but that symmetric structure exposes the net-position (c′) and reversal
(d) statistics. A one-sided run dodges (c′)/(d) by never reversing within the
matching window, at the cost of the accumulating price impact that (b) detects.
No single spec can therefore silence every wash-trading statistic simultaneously;
the grid exposes the trade-off rather than asserting a universal bypass.

The comparison (`experiments/analysis/supplementary.py`) reads the completed main
and supplementary `summary.csv` and `evasion.csv` files and writes, for each
indicator, the General Player M0 rate, the General Player in-grid minimum, the
targeted Top Player rate, and the Top Player in-grid minimum, together with the
evasion-classification contrast. A Top Player rate below the general baseline, or
a classification shift from grid-infeasible to cosmetic/feasible, is the
operational signature of a dodge. The comparison is table-based and mints no new
figures.

## Reproducible figure regeneration

Reporting is separable from block replay. After a run has completed and its final
manifest contains the output inventory, `redteam plot --input SOURCE --output DEST`
reconstructs the report inputs from `summary.csv`, `thresholds.csv`, `evasion.csv`
and each run's indicator/trade Parquet files. It then invokes the same report code
used at the end of `redteam run`. `DEST` must be new or empty and must differ from
`SOURCE`; this prevents regenerated presentation artifacts from invalidating the
experiment's original provenance manifest. The plot manifest binds the regenerated
artifacts to the source manifest and protocol lock. Replotting changes neither
measurements nor classifications except through explicitly changed reporting code.
