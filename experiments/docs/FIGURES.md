# Paper figure specification

The reporting layer is designed around the figures requested in `Write/main.tex`.
All figures are generated from completed, hash-bound experiment outputs; no figure
re-runs a strategy or changes a calibrated threshold. Use:

```bash
redteam plot --input output/completed_run --output output/completed_run_plots
```

The command exports every figure as PDF, SVG and 600-dpi PNG. PDF or SVG is the
preferred manuscript source. PNG is intended for review and visual QA. The
standalone output contains `plot_manifest.json`, which binds every rendered file
to its source experiment.

## Visual system

- A color-vision-deficiency-safe palette, restrained grids and publication-sized
  typography are shared by every statistical figure.
- Color is paired with text, marker shape, line style or hatching; it is never the
  only status channel.
- Panel letters, labels, units and legends are explicit. Missing evidence is
  shown rather than silently dropped.
- `NaN` is unidentified or unavailable evidence. `Infinity` is grid-infeasible:
  a valid target-reaching baseline exists, but no tested configuration is both
  target-reaching and quiet. Neither state is converted to an arbitrary number.
- A synthetic run is visibly marked `SYNTHETIC VALIDATION` and must not be used as
  paper evidence.

## Figure 1 — alarm-rate matrix

Files: `figures/fig1_alarm_heatmap_p95.*` and
`figures/fig1_alarm_heatmap_p99.*`.

The two panels show V0 public-data grouping and V1 known-control grouping. Columns
cover all 15 M0–M7 configurations; rows cover (a), (b), (c′ sliding), (c′ fixed),
(d), (e), G1, G2 and G3. Each cell is the pool-mean firing rate and includes its
numeric value. White group separators distinguish M0–M4, M5, M6 and M7.

Hatching marks evidence coverage below 95%; `×` marks unavailable evidence. The
lower labels report `C`, the mean complete-path cost in basis points, and `T`, the
share of pools that reach the target. The view comparison for (d) must be read
with the manuscript caveat that V0 and V1 also change the matching granularity.
The p95 plot is primary; p99 is the preregistered threshold sensitivity.

## Figure 2 — binding constraints

File: `figures/fig2_binding_indicators.*`.

Panel A plots the calibrated (a) threshold against the maximum manufactured-to-
organic volume ratio in a V1 quiet window. The dashed curve is the same-window
analytical bound. Marker color encodes the complete-path cost only when that cost
exists; window selection itself does not require a quiet, target-reaching full
path.

Panels B–D show the M7 inventory-band sweep for (c′ sliding), (c′ fixed) and (d).
Because all three alarm rates are constant at one in the completed experiment,
the panels use the soft/hard inventory band as a categorical horizontal axis
rather than compressing every point at the right edge of a 0–1 alarm-rate axis.
Thin lines trace pool-specific risk `R`, small coloured points show pool summaries,
and diamonds show the band medians. The compact header reports the alarm rate,
quietness threshold and target coverage; exact duplicate-path counts are stated
in the manuscript text and caption. Hollow markers are reserved for target
failures. Missing evidence remains absent from the path and is reported in the
underlying tables, not imputed.

## H1 — attribution distributions

File: `figures/h1_attribution_distributions.*`; data:
`h1_auc.csv` and `h1_auc_ci.csv`.

The empirical CDF compares eligible injected activity with unmarked background
entities under V0 and V1. Direct distribution curves avoid bin sensitivity;
descriptive AUC and eligible-evidence coverage are printed with the panels. AUC
is not a legal classification rate and unavailable entity evidence is retained in
the coverage denominator.

## H3 — timing, volatility and identification

File: `figures/h3_timing_cost.*`; data: `h3_timing.csv` and
`h3_association_diagnostics.csv`.

The 2×2 small multiples show both timing proxies, (e) burst/recovery and G2 short
gaps, against continuous baseline volatility under V0 and V1. Finite costs are
split at the preregistered `δ`; triangles at the upper boundary denote
grid-infeasible results and crosses at the lower boundary denote unidentified
results. The single-holding formula appears only for (e), as a descriptive
reference rather than a fitted prediction. Each panel reports finite, infinite
and unavailable counts so threshold saturation cannot disappear from the plot.

## Manuscript use

Do not replace Figure 2 or Figure 3 placeholders with synthetic-validation
outputs. After the final historical run passes `study_readiness.json`, regenerate
figures into a separate directory, inspect the vector files at final column width,
and include the PDF/SVG outputs in `Write/main.tex`. Captions must state the pool
aggregation, threshold percentile, view, target conditioning and the meanings of
hatching, `×`, hollow markers, `Infinity` and `NaN` where applicable.
