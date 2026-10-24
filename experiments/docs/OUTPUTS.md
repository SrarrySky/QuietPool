# Outputs

Each run writes a self-contained set of artifacts into the output directory. The
table below lists every artifact and what it records.

| Artifact | Purpose |
| --- | --- |
| Raw `download_plan.json`, `.download/` | Projected column traffic, verified range caches and extraction checkpoints, persistent traffic accounting; resume state, not experimental results |
| Raw `acquisition.json` | Completion gate for all 74 raw data files, SHA256s, source ETags/footer hashes and price provenance; required by generated cohort imports |
| Study `qualification.csv/.json`, `baseline_strata.csv` (on successful baseline measurement) | Per-pool admission failures, exact history hashes, historical volume/volatility, split coverage; required before freezing the selected cohort |
| Supplementation `replacement_plan.json/.sha256`, `reserve_config/`, `import_configs/`, `candidates/`, `screening.csv/.json`, `amendment_manifest.json`, `final_config/`, `qualification/` | Data-eligibility-and-supplementation stage: fixed reserve order, candidate acquisition/import/screening, slot mappings, and the final re-qualified 36-pool configuration |
| `config/study_evidence/` | Discovery records, inclusion/exclusion audit, fixed cohort addresses, Pinax active-file coverage and source hashes; not empirical outcomes |
| Imported `meta.json`, `swaps.parquet`, `blocks.parquet` | Qualified Pinax replay input, exact raw quantities, tx.from, complete replay blocks |
| `meta.json` fields `liquidity_events`, `liquidity_model` (when present) | Ordered Mint/Burn with decimal-string raw quantities and observed post-reserves; fixed external token-flow model, included in protocol hashes |
| Imported `import_manifest.json` | Source file sizes/SHA256, import configuration, reserve anchor, LIB witness and completeness limitation; included in protocol freeze |
| `summary.csv` | V, F, S, R, cost bps, alarm rates, coverage, inventory, pool volume; separate view, percentile, calibration, risk weight, scenario |
| `thresholds.csv`, `calibration_*.parquet` | Auditable calibration evidence |
| `<run>/trades.parquet` | Full organic/manipulator/arbitrage trace with integer amounts stored losslessly as decimal strings |
| `<run>/marks.parquet` | Physical balances, wealth, passive-endowment benchmark, incremental trading P&L |
| `<run>/indicator_windows.parquet` | Window statistics, thresholds, eligibility, alarms, and entity attribution |
| `<run>/decisions.json.gz`, `parameters.json`, `funding_edges.json` | Losslessly compressed original strategy rationale, effective settings, and funding graph |
| `evasion.csv` | Per-pool, target-conditioned finite-grid evasion costs and maximum quiet volume |
| `table5_classification.csv/.tex` | Empirical classifications and pool-cluster confidence intervals |
| `figures/fig1_alarm_heatmap_p95/p99.*` | Alarm-rate matrices, both views and cost annotations |
| `figures/fig2_binding_indicators.*` | Binding diagnostics: volume bound and inventory-band sweep |
| `figures/h1_attribution_distributions.*`, `h1_auc.csv` | Net-position distributions and descriptive AUC |
| `figures/h3_timing_cost.*`, `h3_timing.csv` | Timing cost, volatility, and Proposition 2 overlay where identifiable |
| `organic_only_alert_load.csv`, `organic_only_<pool>_<scenario>.parquet` | Held-out organic-only baseline, separate from intervention paths |
| `h1_auc_ci.csv` | Pool-bootstrap intervals for descriptive AUC |
| `organic_alert_load*.csv` | Organic alert load and coverage, not a false-positive rate |
| `pool_cluster_intervals.csv` | Pool-bootstrap intervals for costs and alarm rates |
| `classification_sensitivity.csv` | Classification changes across sensitivity scenarios |
| `volume_bound_windows.csv` | Window-level Proposition 3(i) diagnostics |
| `ablation_activity.csv` | Accepted decisions, binding caps, guard blocks, bundles, and inventory recovery |
| `study_readiness.json`, `h3_association_diagnostics.csv` | Evidence gaps and finite-only association diagnostics, with missing/infinite counts |
| `plan.json`, `INCOMPLETE.json` (on failure) | Resource admission plan and explicit failed-run marker |
| `manifest.json`, `protocol.lock.json` | Inputs, dependencies, and runtime environment |
| Standalone plot `plot_manifest.json` | Hashes of the completed source experiment and every regenerated figure/table |

Plots are exported to **PDF, SVG, and PNG**. `NaN` means evidence is unavailable;
`Infinity` means the target was reached by the tested grid but no tested setting
satisfied the alarm constraint. It is not a proof against all possible strategies.
The software never invents a cost, AUC, or confidence interval when it is undefined.

See the [paper figure specification](FIGURES.md), [methodology](METHODOLOGY.md),
[requirements coverage](REQUIREMENTS.md), and [review evidence](REVIEWS.md) for
visual encodings, definitions and limits.

## Standalone figure regeneration

Figures and report-derived CSV/TeX files can be regenerated without replaying
blocks or rerunning the strategy:

```bash
redteam plot --input output/completed_run --output output/completed_run_plots
```

The command reads immutable path-level evidence from the completed input directory
and writes only to the separate output directory. It recreates `figures/`, Table 5,
H1/H3 summaries, alert-load intervals, classification sensitivities, volume-bound
diagnostics and `REPORT.md`. It never updates the source `manifest.json`. The new
`plot_manifest.json` records the source manifest/protocol hashes and hashes every
regenerated artifact. A source marked incomplete or lacking its final output
inventory is rejected.
