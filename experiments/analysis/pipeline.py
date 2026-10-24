"""Vector Figure 1 describing the implemented experimental pipeline.

Renders the offline constrained-evaluation workflow as two non-communicating
lanes: organic-only threshold calibration and strategy-grid test replay.
"""

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

from .paper_figures import BLUE, GREEN, GRID, INK, MUTED, ORANGE, PALE, PURPLE


def pipeline_figure(synthetic):
    """Draw the pipeline diagram with qualification, calibration and test replay.

    Args:
        synthetic (bool): If True, label qualified inputs as synthetic fixtures;
            otherwise as historical Pinax blocks, logs and transactions.

    Returns:
        matplotlib.figure.Figure: The assembled vector pipeline diagram.
    """
    fig, ax = plt.subplots(figsize=(12.0, 5.1), constrained_layout=True)
    ax.set(xlim=(0, 1), ylim=(0, 1))
    ax.axis("off")

    def lane(x, y, width, label, color):
        ax.add_patch(
            FancyBboxPatch(
                (x, y - 0.025),
                width,
                0.26,
                boxstyle="round,pad=0.004,rounding_size=0.015",
                facecolor=color,
                edgecolor="none",
                alpha=0.055,
                zorder=0,
            )
        )
        ax.text(
            x + 0.008,
            y + 0.252,
            label,
            ha="left",
            va="center",
            color=color,
            fontsize=9,
            fontweight="bold",
        )

    lane(0.405, 0.61, 0.57, "CALIBRATION", PURPLE)
    lane(0.195, 0.25, 0.78, "TEST REPLAY", BLUE)

    def box(x, y, width, height, heading, body, accent):
        ax.add_patch(
            FancyBboxPatch(
                (x, y),
                width,
                height,
                boxstyle="round,pad=0.012,rounding_size=0.018",
                facecolor="white",
                edgecolor=GRID,
                linewidth=0.9,
                zorder=2,
            )
        )
        ax.add_patch(
            FancyBboxPatch(
                (x, y),
                0.008,
                height,
                boxstyle="round,pad=0,rounding_size=0.008",
                facecolor=accent,
                edgecolor="none",
                zorder=3,
            )
        )
        ax.text(
            x + 0.018,
            y + height * 0.68,
            heading,
            ha="left",
            va="center",
            color=INK,
            fontsize=9,
            fontweight="bold",
        )
        ax.text(
            x + 0.018,
            y + height * 0.30,
            body,
            ha="left",
            va="center",
            color=MUTED,
            fontsize=7.3,
            linespacing=1.35,
        )

    def arrow(start, end, color=MUTED, connection="arc3"):
        ax.add_patch(
            FancyArrowPatch(
                start,
                end,
                arrowstyle="-|>",
                mutation_scale=11,
                color=color,
                linewidth=1.05,
                connectionstyle=connection,
                shrinkA=2,
                shrinkB=3,
                zorder=1,
            )
        )

    box(
        0.01,
        0.72,
        0.18,
        0.17,
        "Qualified inputs",
        ("Synthetic fixtures" if synthetic else "Pinax blocks · logs · txs")
        + "\nExact accounting · fixed hashes",
        BLUE,
    )
    box(
        0.225,
        0.72,
        0.16,
        0.17,
        "Frozen protocol",
        "12 calibration · 24 test\nParameters · budgets · split",
        ORANGE,
    )
    arrow((0.19, 0.805), (0.225, 0.805))

    box(
        0.43,
        0.67,
        0.17,
        0.16,
        "Organic-only replay",
        "Separate calibration pools\nNo intervention outcomes",
        PURPLE,
    )
    box(
        0.66,
        0.67,
        0.16,
        0.16,
        "Thresholds",
        "95th / 99th percentiles\nRaw + robust variants",
        PURPLE,
    )
    arrow((0.385, 0.79), (0.43, 0.75), PURPLE, "arc3,rad=-0.08")
    arrow((0.60, 0.75), (0.66, 0.75), PURPLE)

    box(
        0.225,
        0.27,
        0.16,
        0.17,
        "Strategy grid",
        "M0–M7 / S1 · 15 settings\nPinned preview · bundle · guard",
        BLUE,
    )
    box(
        0.43,
        0.27,
        0.17,
        0.17,
        "CPMM sandbox",
        "Integer V2 execution\nOrganic → strategy → arbitrage",
        GREEN,
    )
    box(
        0.66,
        0.27,
        0.16,
        0.17,
        "Indicator battery",
        "MAR (a)–(e) · G1–G3\nV0 / V1 attribution",
        GREEN,
    )
    box(
        0.855,
        0.27,
        0.13,
        0.17,
        "Constrained cost",
        "V · F · S · R · c\nΔĉ · coverage · status",
        ORANGE,
    )
    arrow((0.385, 0.77), (0.30, 0.45), BLUE, "arc3,rad=0.18")
    arrow((0.385, 0.355), (0.43, 0.355), BLUE)
    arrow((0.60, 0.355), (0.66, 0.355), GREEN)
    arrow((0.82, 0.355), (0.855, 0.355), GREEN)
    arrow((0.74, 0.67), (0.74, 0.45), PURPLE)

    ax.add_patch(
        FancyBboxPatch(
            (0.17, 0.055),
            0.77,
            0.09,
            boxstyle="round,pad=0.01,rounding_size=0.012",
            facecolor=PALE,
            edgecolor=GRID,
            linewidth=0.7,
        )
    )
    ax.text(
        0.555,
        0.100,
        "Resource admission  ·  deterministic seeds  ·  path diagnostics  ·  pool-cluster inference  ·  provenance manifests",
        ha="center",
        va="center",
        color=MUTED,
        fontsize=7.6,
    )
    ax.text(
        0.99,
        0.965,
        "No signing · no broadcasting",
        ha="right",
        va="top",
        fontsize=8,
        color=MUTED,
    )
    return fig
