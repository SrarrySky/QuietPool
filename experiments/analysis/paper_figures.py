"""Publication-grade figures matched to the manuscript's evidential claims.

Implements the shared visual system, colour palette and the four manuscript
figures: alarm heatmap, binding indicators, attribution and timing cost.
"""

import re

import matplotlib as mpl
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle

from ..indicators import INDICATORS

# Okabe-Ito-derived, colour-vision-safe palette with restrained neutrals.
INK = "#17212B"
MUTED = "#5B6773"
GRID = "#D7DEE5"
PALE = "#F4F7F9"
BLUE = "#0072B2"
SKY = "#56B4E9"
GREEN = "#009E73"
ORANGE = "#E69F00"
VERMILLION = "#D55E00"
PURPLE = "#7A5195"
GRAY = "#8A949E"

INDICATOR_LABELS = {
    "a": "(a) Volume share",
    "b": "(b) Net price impact",
    "c_sliding": "(c′) Net position · sliding",
    "c_fixed": "(c′) Net position · fixed",
    "d": "(d) Short-horizon reversal",
    "e": "(e) Burst + recovery",
    "g1": "G1 · Funding links",
    "g2": "G2 · Short gaps",
    "g3": "G3 · Net position",
}


def apply_publication_style():
    """Apply the compact journal style that stays legible after LNCS down-scaling.

    Returns:
        None
    """
    mpl.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8.5,
            "axes.titlesize": 10,
            "axes.labelsize": 9,
            "axes.titleweight": "bold",
            "axes.labelcolor": "black",
            "axes.labelweight": "bold",
            "axes.edgecolor": MUTED,
            "axes.linewidth": 0.7,
            "xtick.labelsize": 7.5,
            "ytick.labelsize": 7.5,
            "xtick.color": "black",
            "ytick.color": "black",
            "xtick.major.width": 0.6,
            "ytick.major.width": 0.6,
            "grid.color": GRID,
            "grid.linewidth": 0.55,
            "grid.alpha": 0.75,
            "legend.fontsize": 7.5,
            "legend.frameon": False,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "savefig.facecolor": "white",
            "savefig.bbox": "tight",
            "savefig.pad_inches": 0.04,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "none",
            "lines.linewidth": 1.4,
        }
    )


def save_figure(fig, folder, name):
    """Save identical vector/raster editions with publication-safe metadata.

    Args:
        fig (matplotlib.figure.Figure): Figure to render.
        folder (pathlib.Path): Output directory, created if needed.
        name (str): Base filename; the format extension is appended per edition.

    Returns:
        None
    """
    style_axis_text(fig)
    folder.mkdir(exist_ok=True)
    for ext in ("pdf", "svg", "png"):
        kwargs = {"dpi": 600} if ext == "png" else {}
        fig.savefig(
            folder / f"{name}.{ext}",
            metadata={"Title": name, "Creator": "Indicators as Constraints"},
            **kwargs,
        )
    plt.close(fig)


def style_axis_text(fig):
    """Render every Cartesian axis label and tick in bold black.

    Args:
        fig (matplotlib.figure.Figure): Figure whose axes should be normalised.

    Returns:
        None
    """
    for ax in fig.axes:
        ax.xaxis.label.set(color="black", fontweight="bold")
        ax.yaxis.label.set(color="black", fontweight="bold")
        ax.tick_params(axis="both", colors="black")
        ax.xaxis.get_offset_text().set(color="black", fontweight="bold")
        ax.yaxis.get_offset_text().set(color="black", fontweight="bold")
        for label in [*ax.get_xticklabels(), *ax.get_yticklabels()]:
            label.set_color("black")
            label.set_fontweight("bold")


def panel_label(ax, label):
    """Place a bold panel letter above and to the left of an axes.

    Args:
        ax (matplotlib.axes.Axes): Axes to annotate.
        label (str): Panel letter (e.g. "A").

    Returns:
        None
    """
    ax.text(
        -0.075,
        1.035,
        label,
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=11,
        fontweight="bold",
        color=INK,
        clip_on=False,
    )


def natural_level_order(labels):
    """Sort level labels by numeric token segments rather than lexicographically.

    Args:
        labels (iterable): Level identifiers containing embedded integers.

    Returns:
        list: Labels ordered so "M2" precedes "M10", with non-numeric
        segments compared as strings.
    """
    return sorted(
        labels,
        key=lambda label: [
            int(token) if token.isdigit() else token
            for token in re.split(r"(\d+)", str(label))
        ],
    )


def _group_boundaries(levels):
    """Return x-axis separators where the leading level-group token changes.

    Args:
        levels (list): Ordered level labels.

    Returns:
        list[float]: Half-integer column positions at each group transition.
    """
    groups = [str(level).split("_")[0] for level in levels]
    return [i - 0.5 for i in range(1, len(groups)) if groups[i] != groups[i - 1]]


def alarm_heatmap_figure(main, cfg, percentile, view, title_suffix=""):
    """Build one Figure 2 panel: alarms, coverage, cost and target attainment.

    The panel is drawn for a single attribution view so that the manuscript can
    stack the V0 and V1 panels as subfigures. Each panel carries its own
    colourbar and legend, and its x-axis level labels are staggered (alternating
    up and down) instead of rotated.

    Args:
        main (pandas.DataFrame): Primary-scenario summary rows for the selected
            "lambda" and non-robust calibration.
        cfg (dict): Frozen configuration supplying "V_star_usd".
        percentile (int): Calibration percentile (95 or 99) to display.
        view (str): Attribution view ("V0" or "V1") to render.
        title_suffix (str): Extra title text, e.g. the synthetic-validation mark.

    Returns:
        matplotlib.figure.Figure: The single-view alarm-rate heatmap.
    """
    levels = natural_level_order(main.level.dropna().unique())
    cmap = mcolors.LinearSegmentedColormap.from_list(
        "quiet_to_alarm", ["#F6F8FA", "#C6E4ED", SKY, BLUE, "#173A5E"]
    )
    fig = plt.figure(figsize=(11.4, 4.55))
    grid = fig.add_gridspec(1, 2, width_ratios=[1, 0.025], wspace=0.03)
    ax = fig.add_subplot(grid[0, 0])
    cax = fig.add_subplot(grid[0, 1])

    selected = main[(main.view == view) & (main.threshold_pct == percentile)]
    pooled = selected.groupby(["pool", "level"], as_index=False).agg(
        **{
            **{f"p_{name}": (f"p_{name}", "mean") for name in INDICATORS},
            **{
                f"coverage_{name}": (f"coverage_{name}", "mean")
                for name in INDICATORS
            },
            "c_bps": ("c_bps", "mean"),
            "V": ("V", "mean"),
        }
    )
    alarms = pooled.groupby("level")[[f"p_{x}" for x in INDICATORS]].mean()
    coverage = pooled.groupby("level")[[f"coverage_{x}" for x in INDICATORS]].mean()
    costs = pooled.groupby("level").c_bps.mean()
    targets = (
        pooled.assign(reached=pooled.V >= cfg["V_star_usd"])
        .groupby("level")
        .reached.mean()
    )
    alarms = alarms.reindex(levels)
    coverage = coverage.reindex(levels)
    values = alarms.to_numpy().T
    image = ax.imshow(
        np.ma.masked_invalid(values),
        cmap=cmap,
        vmin=0,
        vmax=1,
        interpolation="nearest",
        aspect="auto",
    )
    ax.set_yticks(range(len(INDICATORS)), [INDICATOR_LABELS[x] for x in INDICATORS])
    ax.set_xticks(range(len(levels)))
    ax.set_xticklabels([])
    ax.tick_params(axis="both", length=0)

    # Staggered x-axis level labels: alternate two vertical offsets so adjacent
    # labels never collide and rotation is unnecessary.
    label_transform = ax.get_xaxis_transform()
    for i, level in enumerate(levels):
        ax.text(
            i,
            -0.03 if i % 2 == 0 else -0.11,
            str(level),
            transform=label_transform,
            ha="center",
            va="top",
            fontsize=7.5,
            color="black",
            fontweight="bold",
            clip_on=False,
        )

    for boundary in _group_boundaries(levels):
        ax.axvline(boundary, color="white", linewidth=2.0)
        ax.axvline(boundary, color=MUTED, linewidth=0.45, alpha=0.55)
    for i, indicator in enumerate(INDICATORS):
        for j, level in enumerate(levels):
            value = values[i, j]
            cov = coverage.loc[level, f"coverage_{indicator}"]
            if not np.isfinite(value):
                ax.text(
                    j, i, "×", ha="center", va="center", color=MUTED, fontsize=10
                )
                continue
            ax.text(
                j,
                i,
                f"{value:.2f}",
                ha="center",
                va="center",
                color="white" if value >= 0.52 else INK,
                fontsize=7.8,
                fontweight="bold" if value >= 0.5 else "normal",
            )
            if not np.isfinite(cov) or cov < 0.95:
                ax.add_patch(
                    Rectangle(
                        (j - 0.5, i - 0.5),
                        1,
                        1,
                        fill=False,
                        hatch="////",
                        edgecolor=(0.25, 0.25, 0.25, 0.42),
                        linewidth=0,
                    )
                )

    # Complete-path cost and target-attainment strip below the level labels.
    for j, level in enumerate(levels):
        cost = costs.get(level, np.nan)
        target = targets.get(level, np.nan)
        cost_text = f"{cost:.1f}" if np.isfinite(cost) else "NA"
        target_text = f"{100 * target:.0f}%" if np.isfinite(target) else "NA"
        ax.text(
            j,
            -0.16,
            f"C {cost_text}",
            transform=label_transform,
            ha="center",
            va="top",
            fontsize=6.6,
            color="black",
            fontweight="bold",
            clip_on=False,
        )
        ax.text(
            j,
            -0.22,
            f"T {target_text}",
            transform=label_transform,
            ha="center",
            va="top",
            fontsize=6.6,
            color="black",
            fontweight="bold",
            clip_on=False,
        )

    colorbar = fig.colorbar(image, cax=cax)
    colorbar.set_label("Pool-mean alarm rate", color="black", fontweight="bold")
    colorbar.ax.tick_params(labelsize=8.5)
    colorbar.outline.set_linewidth(0.5)
    # Explain exceptional cell states only when they actually occur in the
    # rendered panel. The primary figures contain neither state, so no unused
    # legend is shown.
    legend_handles = []
    coverage_values = coverage.to_numpy(dtype=float)
    if np.any(np.isfinite(coverage_values) & (coverage_values < 0.95)):
        legend_handles.append(
            Patch(
                facecolor="white",
                edgecolor=GRAY,
                hatch="////",
                label="Hatched cell: coverage < 95%",
            )
        )
    if np.any(~np.isfinite(values)):
        legend_handles.append(
            Line2D(
                [],
                [],
                linestyle="none",
                marker="$×$",
                color=MUTED,
                label="×: score unavailable",
            )
        )
    if legend_handles:
        fig.legend(
            handles=legend_handles,
            loc="lower left",
            bbox_to_anchor=(0.005, 0.02),
            ncol=len(legend_handles),
            handlelength=1.6,
            prop={"weight": "bold"},
        )
    fig.text(
        0.73,
        0.07,
        "C = mean complete-path cost [bps]   ·   T = target-reaching pools",
        ha="center",
        va="bottom",
        fontsize=7.2,
        color="black",
        fontweight="bold",
    )
    fig.subplots_adjust(left=0.17, right=0.93, top=0.95, bottom=0.27)
    return fig


def binding_indicators_volume_figure(main, window_rows, cfg, title_suffix=""):
    """Build Figure 2 panel (a): window-level volume bound with cost colourbar.

    Args:
        main (pandas.DataFrame): Primary-scenario summary rows.
        window_rows (pandas.DataFrame): V1 window-level volume diagnostics.
        cfg (dict): Frozen configuration (retained for signature parity).
        title_suffix (str): Extra title text, e.g. the synthetic-validation mark.

    Returns:
        matplotlib.figure.Figure: The single volume-bound panel.
    """
    fig, ax_bound = plt.subplots(figsize=(6.6, 4.4))

    quiet = window_rows[(~window_rows.fired) & (window_rows.threshold < 1)].copy()
    quiet = quiet.dropna(subset=["manufactured_over_organic"])
    if len(quiet):
        idx = quiet.groupby(["pool", "threshold_pct", "threshold"])[
            "manufactured_over_organic"
        ].idxmax()
        points = quiet.loc[idx].copy()
        costs = main[["run_id", "view", "threshold_pct", "c_bps"]].drop_duplicates(
            ["run_id", "view", "threshold_pct"]
        )
        points = points.merge(costs, on=["run_id", "view", "threshold_pct"], how="left")
    else:
        points = quiet.assign(c_bps=np.nan)

    thresholds = quiet.threshold[np.isfinite(quiet.threshold)]
    upper = (
        min(0.95, max(0.12, float(thresholds.max()) * 1.25))
        if len(thresholds)
        else 0.95
    )
    x = np.linspace(0.001, upper, 300)
    ax_bound.plot(
        x, x / (1 - x), color=VERMILLION, linewidth=1.7, label="Same-window bound"
    )
    finite_cost = points.c_bps[np.isfinite(points.c_bps)]
    norm = None
    if len(finite_cost):
        low, high = np.nanquantile(finite_cost, [0.05, 0.95])
        if low == high:
            high = low + 1
        norm = mcolors.Normalize(low, high)
    markers = {95: "o", 99: "s"}
    scatter = None
    for pct, group in points.groupby("threshold_pct"):
        scatter = ax_bound.scatter(
            group.threshold,
            group.manufactured_over_organic,
            c=group.c_bps if norm else BLUE,
            cmap="viridis" if norm else None,
            norm=norm,
            marker=markers.get(int(pct), "o"),
            s=35,
            linewidth=0.55,
            edgecolor="white",
            alpha=0.82,
            label=f"p{int(pct)} quiet-window maxima",
            zorder=3,
        )
    ax_bound.set(
        xlabel="Calibrated volume-share threshold",
        ylabel="Max. quiet manufactured / organic ratio",
    )
    ax_bound.xaxis.label.set_fontsize(11)
    ax_bound.yaxis.label.set_fontsize(11)
    ax_bound.tick_params(axis="both", labelsize=9.5)
    ax_bound.grid(True)
    ax_bound.legend(
        loc="upper left",
        prop={"size": 8.5, "weight": "bold"},
        labelcolor="black",
    )
    if not len(points):
        ax_bound.text(
            0.5,
            0.48,
            "No eligible quiet windows\n(threshold < 1)",
            transform=ax_bound.transAxes,
            ha="center",
            va="center",
            color="black",
            fontweight="bold",
        )
    if scatter is not None and norm is not None:
        cbar = fig.colorbar(
            scatter, ax=ax_bound, location="bottom", pad=0.16, shrink=0.80, aspect=28
        )
        cbar.set_label(
            "Complete-path cost of selected window [bps]",
            color="black",
            fontweight="bold",
        )
        cbar.outline.set_linewidth(0.5)
    fig.subplots_adjust(left=0.13, right=0.97, top=0.94, bottom=0.26)
    return fig


def _m7_sweep(main):
    """Aggregate M7 inventory-band rows into per-pool alarm-risk summaries.

    Args:
        main (pandas.DataFrame): Primary-scenario summary rows.

    Returns:
        pandas.DataFrame: One row per (pool, M7 level) with mean R, V and the
        three alarm rates, plus a boolean "duplicate_path" flag for pools whose
        levels share an identical summary signature.
    """
    sweep = main[
        (main.level.astype(str).str.startswith("M7"))
        & (main.view == "V1")
        & (main.threshold_pct == 95)
    ].copy()
    sweep = sweep.groupby(["pool", "level"], as_index=False).agg(
        R=("R", "mean"),
        V=("V", "mean"),
        p_c_sliding=("p_c_sliding", "mean"),
        p_c_fixed=("p_c_fixed", "mean"),
        p_d=("p_d", "mean"),
    )
    signature = ["R", "V", "p_c_sliding", "p_c_fixed", "p_d"]
    sweep["duplicate_path"] = False
    for indices in sweep.groupby("pool").groups.values():
        sweep.loc[indices, "duplicate_path"] = sweep.loc[indices].duplicated(
            signature, keep=False
        )
    return sweep


def _m7_band_tick(level):
    """Format an M7 level as a compact soft/hard kUSD axis label."""
    match = re.fullmatch(r"M7_i(\d+)_(\d+)", str(level))
    if not match:
        return str(level)
    soft, hard = (int(value) / 1000 for value in match.groups())
    return f"{soft:g} / {hard:g}"


def binding_indicators_sweep_figure(main, cfg, metric, title_suffix=""):
    """Build one compact Figure 2 M7 inventory-band sweep panel.

    Args:
        main (pandas.DataFrame): Primary-scenario summary rows.
        cfg (dict): Frozen configuration supplying "V_star_usd" and "epsilon".
        metric (str): Alarm-rate column to plot ("p_c_sliding", "p_c_fixed" or
            "p_d").
        title_suffix (str): Extra title text, e.g. the synthetic-validation mark.

    Returns:
        matplotlib.figure.Figure: The single M7 sweep panel.
    """
    sweep = _m7_sweep(main)
    levels = natural_level_order(sweep.level.unique())
    band_colors = dict(zip(levels, [BLUE, GREEN, ORANGE, PURPLE], strict=False))

    # Alarm rates are often degenerate in this diagnostic (all equal to one).
    # A categorical band axis preserves the actual values while using the panel
    # area for the risk variation that remains scientifically informative.
    positions = np.arange(len(levels))
    fig, ax = plt.subplots(figsize=(3.35, 2.10))
    for position in positions[::2]:
        ax.axvspan(position - 0.5, position + 0.5, color=PALE, zorder=0)
    for _, pool in sweep.groupby("pool"):
        ordered = pool.set_index("level").reindex(levels).reset_index()
        valid = ordered.R.notna()
        ax.plot(
            positions[valid.to_numpy()],
            ordered.loc[valid, "R"],
            color=GRAY,
            linewidth=0.6,
            alpha=0.30,
            zorder=1,
        )
    for position, level in zip(positions, levels, strict=True):
        group = sweep[sweep.level == level]
        reached = group.V >= cfg["V_star_usd"]
        ax.scatter(
            np.full(reached.sum(), position),
            group.loc[reached, "R"],
            s=13,
            color=band_colors[level],
            alpha=0.42,
            linewidth=0,
            zorder=2,
        )
        ax.scatter(
            np.full((~reached).sum(), position),
            group.loc[~reached, "R"],
            s=18,
            facecolor="white",
            edgecolor=band_colors[level],
            linewidth=0.8,
            zorder=2,
        )
        finite = group.R.dropna()
        if len(finite):
            ax.scatter(
                [position],
                [finite.median()],
                s=46,
                marker="D",
                color=band_colors[level],
                edgecolor=INK,
                linewidth=0.7,
                zorder=3,
            )
    missing = sweep[metric].isna().sum()
    if missing:
        ax.text(
            0.98,
            0.02,
            f"{missing} unavailable",
            transform=ax.transAxes,
            ha="right",
            va="bottom",
            color="black",
            fontweight="bold",
        )

    finite_alarm = sweep[metric].dropna()
    alarm_summary = (
        f"{100 * finite_alarm.iloc[0]:.0f}%"
        if len(finite_alarm) and finite_alarm.nunique() == 1
        else f"{100 * finite_alarm.min():.0f}-{100 * finite_alarm.max():.0f}%"
    )
    reached_count = int((sweep.V >= cfg["V_star_usd"]).sum())
    path_count = len(sweep)
    fig.text(
        0.57,
        0.955,
        f"Alarm = {alarm_summary} (> ε = {100 * cfg['epsilon']:.0f}%)"
        f"  ·  target = {reached_count}/{path_count}",
        ha="center",
        va="top",
        fontsize=7.8,
        fontweight="bold",
        color="black",
    )
    ax.set(
        xlim=(-0.45, len(levels) - 0.55),
        xlabel="Soft / hard inventory bands [kUSD]",
        ylabel="Hourly risk R [USD]",
        xticks=positions,
        xticklabels=[_m7_band_tick(level) for level in levels],
    )
    ax.set_ylim(0, sweep.R.max() * 1.06)
    ax.yaxis.set_major_formatter(
        mpl.ticker.FuncFormatter(lambda value, _position: f"{value / 1000:g}k")
    )
    ax.xaxis.label.set_fontsize(8.2)
    ax.yaxis.label.set_fontsize(8.2)
    ax.tick_params(axis="both", labelsize=7.2)
    ax.grid(True, axis="y")
    ax.grid(False, axis="x")
    fig.subplots_adjust(left=0.20, right=0.975, top=0.82, bottom=0.27)
    return fig


def attribution_figure(windows, auc_table, view, title_suffix=""):
    """Build the H1 evidence figure for a single attribution view.

    Each view is emitted as its own panel so the manuscript can stack the V0
    and V1 panels as subfigures (a) and (b). The panel carries its own ECDFs
    and a bold, unboxed annotation summarising mean AUC and scored coverage.

    Args:
        windows (pandas.DataFrame): Window-level "c_sliding" values for both
            organic and injected actors.
        auc_table (pandas.DataFrame): Per-pool, per-view AUC estimates.
        view (str): Attribution view ("V0" or "V1") to render.
        title_suffix (str): Extra title text, e.g. the synthetic-validation mark.

    Returns:
        matplotlib.figure.Figure: The single-view attribution distributions.
    """
    fig, ax = plt.subplots(figsize=(6.4, 4.0), constrained_layout=True)
    actor_style = {
        "organic": (MUTED, "--", "Observed background"),
        "manip": (BLUE, "-", "Injected controlled"),
    }
    selected = windows[windows.view == view]
    for actor, (color, linestyle, label) in actor_style.items():
        values = np.sort(
            selected.loc[selected.actor == actor, "value"].dropna().to_numpy()
        )
        if len(values):
            y = np.arange(1, len(values) + 1) / len(values)
            ax.step(
                values,
                y,
                where="post",
                color=color,
                linestyle=linestyle,
                label=label,
            )
    valid = selected.value.notna()
    coverage = valid.mean() if len(selected) else np.nan
    auc_values = auc_table.loc[auc_table.view == view, "auc"].dropna()
    auc_value = auc_values.mean() if len(auc_values) else np.nan
    annotation = (
        f"Mean AUC {auc_value:.2f}\nScored {coverage:.1%}"
        if np.isfinite(auc_value)
        else f"AUC unavailable\nScored {coverage:.1%}"
    )
    ax.text(
        0.03,
        0.97,
        annotation,
        transform=ax.transAxes,
        va="top",
        ha="left",
        color="black",
        fontweight="bold",
    )
    ax.set(
        xlim=(-0.02, 1.02),
        ylim=(0, 1.01),
        xlabel="Net-position proxy c′",
        ylabel="Empirical cumulative probability",
    )
    ax.xaxis.label.set_fontsize(10)
    ax.yaxis.label.set_fontsize(10)
    ax.tick_params(axis="both", labelsize=8.5)
    ax.grid(True)
    ax.legend(loc="lower right")
    return fig


def timing_cost_figure(h3, cfg, indicator, view, title_suffix=""):
    """Build a single H3 timing-cost panel for one indicator and view.

    Each (indicator, view) panel is emitted separately so the manuscript can
    assemble a 2x2 grid of subfigures (a)--(d). The y-limits are shared across
    the two views of an indicator so V0 and V1 remain directly comparable.

    Args:
        h3 (pandas.DataFrame): Pool-level timing diagnostics with volatility,
            cost and prediction columns for indicators "e" and "g2".
        cfg (dict): Frozen configuration supplying "delta_bps".
        indicator (str): Timing proxy ("e" or "g2") to render.
        view (str): Attribution view ("V0" or "V1") to render.
        title_suffix (str): Extra title text, e.g. the synthetic-validation mark.

    Returns:
        matplotlib.figure.Figure: The single-panel timing-cost figure.
    """
    fig, ax = plt.subplots(figsize=(5.2, 4.0), constrained_layout=True)
    color_map = {"cosmetic": GREEN, "costly": ORANGE}

    indicator_finite = h3[
        (h3.indicator == indicator) & np.isfinite(h3.delta_c_bps)
    ].delta_c_bps
    domain_values = indicator_finite.to_numpy()
    if indicator == "e" and "prediction_bps" in h3:
        finite_prediction = h3.loc[
            (h3.indicator == indicator) & np.isfinite(h3.prediction_bps),
            "prediction_bps",
        ].to_numpy()
        domain_values = np.concatenate([domain_values, finite_prediction])
    if len(domain_values):
        low, high = np.nanmin(domain_values), np.nanmax(domain_values)
        span = max(high - low, 1.0)
        y_limits = (min(low - 0.08 * span, 0), high + 0.10 * span)
    else:
        y_limits = (-1, max(5, cfg["delta_bps"] * 2))

    data = h3[
        (h3.indicator == indicator)
        & (h3.view == view)
        & (h3.threshold_pct == 95)
    ].copy()
    finite = data[np.isfinite(data.delta_c_bps)].copy()
    finite["class"] = np.where(
        finite.delta_c_bps <= cfg["delta_bps"], "cosmetic", "costly"
    )
    for category, group in finite.groupby("class"):
        ax.scatter(
            group.volatility,
            group.delta_c_bps,
            s=40,
            color=color_map[category],
            edgecolor="white",
            linewidth=0.6,
            alpha=0.82,
            label="≤ δ cosmetic" if category == "cosmetic" else "> δ costly",
            zorder=3,
        )
    infeasible = data[np.isposinf(data.delta_c_bps)]
    unknown = data[data.delta_c_bps.isna()]
    if len(infeasible):
        ax.scatter(
            infeasible.volatility,
            np.full(
                len(infeasible),
                y_limits[1] - 0.035 * (y_limits[1] - y_limits[0]),
            ),
            marker="^",
            s=43,
            color=VERMILLION,
            label="Grid-infeasible",
            zorder=4,
        )
    if len(unknown):
        ax.scatter(
            unknown.volatility,
            np.full(
                len(unknown), y_limits[0] + 0.035 * (y_limits[1] - y_limits[0])
            ),
            marker="x",
            s=38,
            color=MUTED,
            linewidth=1.2,
            label="Unidentified",
            zorder=4,
        )
    if indicator == "e":
        predicted = data[np.isfinite(data.prediction_bps)].sort_values(
            "volatility"
        )
        if len(predicted):
            ax.plot(
                predicted.volatility,
                predicted.prediction_bps,
                color=PURPLE,
                linestyle=":",
                marker="+",
                markersize=5,
                linewidth=1.0,
                label="Single-holding reference",
            )
    ax.axhline(
        cfg["delta_bps"], color=VERMILLION, linestyle="--", linewidth=1.0
    )
    ax.set_ylim(*y_limits)
    ax.grid(True)
    ax.set_xlabel("Baseline volatility σ [s⁻½]")
    ax.set_ylabel("Grid-relative evasion cost Δĉ [bps]")
    ax.ticklabel_format(axis="x", style="sci", scilimits=(-2, 2))
    ax.xaxis.label.set_fontsize(10)
    ax.yaxis.label.set_fontsize(10)
    ax.tick_params(axis="both", labelsize=8.5)
    ax.legend(
        loc="upper left",
        prop={"size": 8.5, "weight": "bold"},
        labelcolor="black",
    )
    return fig
