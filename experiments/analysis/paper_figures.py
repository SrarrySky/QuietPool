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
            "axes.labelcolor": INK,
            "axes.edgecolor": MUTED,
            "axes.linewidth": 0.7,
            "xtick.labelsize": 7.5,
            "ytick.labelsize": 7.5,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
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
    folder.mkdir(exist_ok=True)
    for ext in ("pdf", "svg", "png"):
        kwargs = {"dpi": 600} if ext == "png" else {}
        fig.savefig(
            folder / f"{name}.{ext}",
            metadata={"Title": name, "Creator": "Indicators as Constraints"},
            **kwargs,
        )
    plt.close(fig)


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


def alarm_heatmap_figure(main, cfg, percentile, title_suffix=""):
    """Build Figure 2: alarms, evidence coverage, cost and target attainment.

    Args:
        main (pandas.DataFrame): Primary-scenario summary rows for the selected
            "lambda" and non-robust calibration.
        cfg (dict): Frozen configuration supplying "V_star_usd".
        percentile (int): Calibration percentile (95 or 99) to display.
        title_suffix (str): Extra title text, e.g. the synthetic-validation mark.

    Returns:
        matplotlib.figure.Figure: The two-panel alarm-rate heatmap.
    """
    levels = natural_level_order(main.level.dropna().unique())
    cmap = mcolors.LinearSegmentedColormap.from_list(
        "quiet_to_alarm", ["#F6F8FA", "#C6E4ED", SKY, BLUE, "#173A5E"]
    )
    fig = plt.figure(figsize=(11.4, 7.7))
    grid = fig.add_gridspec(2, 2, width_ratios=[1, 0.025], hspace=0.42, wspace=0.03)
    axes = [fig.add_subplot(grid[i, 0]) for i in range(2)]
    cax = fig.add_subplot(grid[:, 1])
    last_image = None

    for panel, (ax, view) in enumerate(zip(axes, ("V0", "V1"), strict=True)):
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
        last_image = ax.imshow(
            np.ma.masked_invalid(values),
            cmap=cmap,
            vmin=0,
            vmax=1,
            interpolation="nearest",
            aspect="auto",
        )
        ax.set_yticks(range(len(INDICATORS)), [INDICATOR_LABELS[x] for x in INDICATORS])
        labels = []
        for level in levels:
            cost = costs.get(level, np.nan)
            target = targets.get(level, np.nan)
            cost_text = f"{cost:.1f}" if np.isfinite(cost) else "NA"
            target_text = f"{100 * target:.0f}%" if np.isfinite(target) else "NA"
            labels.append(f"{level}\nC {cost_text}\nT {target_text}")
        if panel == 0:
            ax.set_xticks(range(len(levels)), levels, rotation=35, ha="left")
            ax.tick_params(axis="x", labeltop=True, labelbottom=False, pad=2)
        else:
            ax.set_xticks(
                range(len(levels)),
                labels,
                rotation=52,
                ha="right",
                rotation_mode="anchor",
            )
        ax.tick_params(axis="both", length=0)
        ax.set_title(
            (
                "Public-data grouping (V0)"
                if view == "V0"
                else "Known-control grouping (V1)"
            ),
            loc="left",
            pad=8,
        )
        panel_label(ax, chr(ord("A") + panel))
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
                    fontsize=6.8,
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
    colorbar = fig.colorbar(last_image, cax=cax)
    colorbar.set_label("Pool-mean alarm rate", color=INK)
    colorbar.outline.set_linewidth(0.5)
    fig.legend(
        handles=[
            Patch(
                facecolor="white", edgecolor=GRAY, hatch="////", label="Coverage < 95%"
            ),
            Line2D(
                [], [], linestyle="none", marker="$×$", color=MUTED, label="Unavailable"
            ),
        ],
        loc="lower left",
        bbox_to_anchor=(0.005, 0.005),
        ncol=2,
        handlelength=1.6,
    )
    fig.suptitle(
        f"Indicator firing rates across the strategy grid · calibration p{percentile}"
        + title_suffix,
        x=0.01,
        y=0.995,
        ha="left",
        fontsize=12,
        fontweight="bold",
        color=INK,
    )
    fig.text(
        0.73,
        0.02,
        "C = mean complete-path cost [bps]   ·   T = target-reaching pools",
        ha="center",
        va="bottom",
        fontsize=7.2,
        color=MUTED,
    )
    fig.subplots_adjust(left=0.17, right=0.93, top=0.82, bottom=0.16)
    return fig


def binding_indicators_figure(main, window_rows, cfg, title_suffix=""):
    """Build Figure 3: window-level volume bound and M7 inventory-risk sweep.

    Args:
        main (pandas.DataFrame): Primary-scenario summary rows.
        window_rows (pandas.DataFrame): V1 window-level volume diagnostics.
        cfg (dict): Frozen configuration supplying "V_star_usd" and
            "epsilon".
        title_suffix (str): Extra title text, e.g. the synthetic-validation mark.

    Returns:
        matplotlib.figure.Figure: The binding-indicator panel figure.
    """
    fig = plt.figure(figsize=(12.0, 5.5), constrained_layout=True)
    gs = fig.add_gridspec(1, 4, width_ratios=[1.35, 1, 1, 1], wspace=0.08)
    ax_bound = fig.add_subplot(gs[0, 0])

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
        ylabel="Maximum quiet manufactured / organic notional",
        title="Observed window maxima",
    )
    ax_bound.grid(True)
    ax_bound.legend(loc="upper left")
    panel_label(ax_bound, "A")
    if scatter is not None and norm is not None:
        cbar = fig.colorbar(
            scatter, ax=ax_bound, location="bottom", pad=0.16, shrink=0.80, aspect=28
        )
        cbar.set_label("Complete-path cost of selected window [bps]")
        cbar.outline.set_linewidth(0.5)

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
    levels = natural_level_order(sweep.level.unique())
    band_colors = dict(zip(levels, [BLUE, GREEN, ORANGE, PURPLE], strict=False))
    metrics = [
        ("p_c_sliding", "(c′) sliding"),
        ("p_c_fixed", "(c′) fixed"),
        ("p_d", "(d) reversals"),
    ]
    for index, (metric, title) in enumerate(metrics, start=1):
        ax = fig.add_subplot(gs[0, index])
        for _, pool in sweep.groupby("pool"):
            ordered = pool.set_index("level").reindex(levels).reset_index()
            valid = ordered[[metric, "R"]].notna().all(axis=1)
            ax.plot(
                ordered.loc[valid, metric],
                ordered.loc[valid, "R"],
                color=GRAY,
                linewidth=0.55,
                alpha=0.32,
                zorder=1,
            )
        for level in levels:
            group = sweep[sweep.level == level]
            reached = group.V >= cfg["V_star_usd"]
            ax.scatter(
                group.loc[reached, metric],
                group.loc[reached, "R"],
                s=22,
                color=band_colors[level],
                alpha=0.55,
                linewidth=0,
                zorder=2,
            )
            ax.scatter(
                group.loc[~reached, metric],
                group.loc[~reached, "R"],
                s=27,
                facecolor="white",
                edgecolor=band_colors[level],
                linewidth=0.9,
                zorder=2,
            )
            duplicated = group[group.duplicate_path & group[metric].notna()]
            if len(duplicated):
                ax.scatter(
                    duplicated[metric],
                    duplicated.R,
                    s=50,
                    marker="x",
                    color=INK,
                    linewidth=0.8,
                    zorder=3,
                )
            finite = group[[metric, "R"]].dropna()
            if len(finite):
                ax.scatter(
                    [finite[metric].median()],
                    [finite.R.median()],
                    s=68,
                    marker="D",
                    color=band_colors[level],
                    edgecolor=INK,
                    linewidth=0.55,
                    zorder=4,
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
                color=MUTED,
            )
        ax.axvline(cfg["epsilon"], color=VERMILLION, linestyle="--", linewidth=1.0)
        ax.set(
            xlim=(-0.025, 1.025),
            xlabel="Alarm rate",
            title=title,
        )
        if index == 1:
            ax.set_ylabel("Hourly wealth-change risk R [USD]")
        else:
            ax.tick_params(labelleft=False)
        ax.grid(True)
        panel_label(ax, chr(ord("A") + index))
    handles = [
        Line2D(
            [],
            [],
            marker="D",
            linestyle="none",
            color=color,
            markeredgecolor=INK,
            label=level,
        )
        for level, color in band_colors.items()
    ]
    handles += [
        Line2D(
            [],
            [],
            marker="o",
            linestyle="none",
            markerfacecolor="white",
            markeredgecolor=INK,
            label="Target not reached",
        ),
        Line2D(
            [],
            [],
            marker="x",
            linestyle="none",
            color=INK,
            label="Duplicate summary path",
        ),
        Line2D(
            [],
            [],
            color=VERMILLION,
            linestyle="--",
            label=f"Quietness ε = {cfg['epsilon']:.2f}",
        ),
    ]
    fig.legend(
        handles=handles, loc="lower center", bbox_to_anchor=(0.67, -0.04), ncol=3
    )
    fig.suptitle(
        "Binding indicators and realised inventory risk" + title_suffix,
        x=0.01,
        ha="left",
        fontsize=12,
        fontweight="bold",
        color=INK,
    )
    return fig


def attribution_figure(windows, auc_table, title_suffix=""):
    """Build the H1 evidence figure with comparable ECDFs, coverage and AUC.

    Args:
        windows (pandas.DataFrame): Window-level "c_sliding" values for both
            organic and injected actors.
        auc_table (pandas.DataFrame): Per-pool, per-view AUC estimates.
        title_suffix (str): Extra title text, e.g. the synthetic-validation mark.

    Returns:
        matplotlib.figure.Figure: The two-view attribution distributions.
    """
    fig, axes = plt.subplots(
        1, 2, figsize=(10.2, 3.8), sharex=True, sharey=True, constrained_layout=True
    )
    actor_style = {
        "organic": (MUTED, "--", "Observed background"),
        "manip": (BLUE, "-", "Injected controlled"),
    }
    for index, (ax, view) in enumerate(zip(axes, ("V0", "V1"), strict=True)):
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
            color=INK,
            bbox=dict(boxstyle="round,pad=0.25", facecolor=PALE, edgecolor="none"),
        )
        ax.set(
            xlim=(-0.02, 1.02),
            ylim=(0, 1.01),
            xlabel="Net-position proxy c′",
            title="Public-data grouping (V0)"
            if view == "V0"
            else "Known-control grouping (V1)",
        )
        ax.grid(True)
        panel_label(ax, chr(ord("A") + index))
    axes[0].set_ylabel("Empirical cumulative probability")
    axes[1].legend(loc="lower right")
    fig.suptitle(
        "Attribution design and score discrimination" + title_suffix,
        x=0.01,
        ha="left",
        fontsize=12,
        fontweight="bold",
        color=INK,
    )
    return fig


def timing_cost_figure(h3, cfg, title_suffix=""):
    """Build the H3 timing-cost figure, preserving non-finite outcomes.

    Args:
        h3 (pandas.DataFrame): Pool-level timing diagnostics with volatility,
            cost and prediction columns for indicators "e" and "g2".
        cfg (dict): Frozen configuration supplying "delta_bps".
        title_suffix (str): Extra title text, e.g. the synthetic-validation mark.

    Returns:
        matplotlib.figure.Figure: The 2x2 timing-cost small multiples.
    """
    fig, axes = plt.subplots(
        2, 2, figsize=(10.4, 7.2), sharex=True, sharey="row", constrained_layout=True
    )
    color_map = {"cosmetic": GREEN, "costly": ORANGE}
    for row, indicator in enumerate(("e", "g2")):
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
            low, high = np.nanquantile(domain_values, [0.02, 0.98])
            span = max(high - low, 1.0)
            y_limits = (min(low - 0.15 * span, 0), high + 0.20 * span)
        else:
            y_limits = (-1, max(5, cfg["delta_bps"] * 2))
        for col, view in enumerate(("V0", "V1")):
            ax = axes[row, col]
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
            ax.set_title(
                ("(e) Burst + recovery" if indicator == "e" else "G2 · Short gaps")
                + (" · V0" if view == "V0" else " · V1"),
                loc="left",
            )
            ax.text(
                0.98,
                0.03,
                f"finite {len(finite)} · ∞ {len(infeasible)} · NA {len(unknown)}",
                transform=ax.transAxes,
                ha="right",
                va="bottom",
                color=MUTED,
                fontsize=7.2,
            )
            if row == 1:
                ax.set_xlabel("Baseline volatility σ [s⁻½]")
                ax.ticklabel_format(axis="x", style="sci", scilimits=(-2, 2))
            if col == 0:
                ax.set_ylabel("Grid-relative evasion cost Δĉ [bps]")
            panel_label(ax, chr(ord("A") + row * 2 + col))
    handles, labels = [], []
    for ax in axes.flat:
        current_handles, current_labels = ax.get_legend_handles_labels()
        handles.extend(current_handles)
        labels.extend(current_labels)
    unique = dict(zip(labels, handles, strict=False))
    fig.legend(
        unique.values(),
        unique.keys(),
        loc="lower center",
        bbox_to_anchor=(0.5, -0.035),
        ncol=5,
    )
    fig.suptitle(
        "Timing constraints, volatility and identification" + title_suffix,
        x=0.01,
        ha="left",
        fontsize=12,
        fontweight="bold",
        color=INK,
    )
    return fig
