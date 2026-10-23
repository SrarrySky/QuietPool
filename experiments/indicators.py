"""Explicit operational definitions of MAR a-e and descriptive G1-G3 proxies."""

from collections import deque

import numpy as np
import pandas as pd

from .entities import entity_map

INDICATORS = ["a", "b", "c_sliding", "c_fixed", "d", "e", "g1", "g2", "g3"]


def volume_share(entity, all_trades):
    """Return the entity's share of total window notional volume."""
    total = all_trades.notional_usd.sum()
    return float(entity.notional_usd.sum() / total) if total else 0.0


def attributable_impact(entity):
    """Return the absolute net signed log-price change across the entity's swaps."""
    return float(abs(np.log(entity.price_after / entity.price_before).sum()))


def net_position_ratio(entity):
    """Return the within-window flow-netting proxy, or NaN if no gross volume."""
    gross = entity.signed_token.abs().sum()
    return (
        float(np.clip(1 - abs(entity.signed_token.sum()) / gross, 0, 1))
        if gross
        else np.nan
    )


def reversal_share(entity, horizon, by_wallet=False):
    """Compute the dollar-weighted FIFO reversal share within a horizon.

    Matches opposite-side trades in chronological order, counting matched
    notional on both legs and never matching trades farther than "horizon"
    seconds apart.

    Args:
        entity: DataFrame of one entity's swaps (sorted by time).
        horizon: Maximum matching gap in seconds.
        by_wallet: If True, match within each wallet before aggregating.

    Returns:
        Fraction in "[0, 1]", or NaN if there is no notional to match.
    """
    total = entity.notional_usd.sum()
    if total <= 0:
        return np.nan
    matched = 0.0
    groups = (
        [g for _, g in entity.groupby("wallet", sort=False)] if by_wallet else [entity]
    )
    for group in groups:
        queue = deque()
        for row in group.itertuples():
            while queue and row.ts - queue[0][0] > horizon:
                queue.popleft()
            left = row.notional_usd
            while queue and queue[0][1] != row.side and left > 0:
                t, side, amount = queue.popleft()
                take = min(left, amount)
                matched += 2 * take
                left -= take
                if amount > take:
                    queue.appendleft((t, side, amount - take))
            if left > 0:
                queue.append((row.ts, row.side, left))
    return min(1.0, matched / total)


def burst_revert(entity, path, horizon, observation_end):
    """Return the (burstiness, recovery-ratio) pair for one entity-window.

    Burstiness is a normalized dispersion of inter-swap gaps; recovery is the
    fraction of absolute log impact recovered within "horizon" seconds.
    Either value may be NaN when evidence is insufficient.

    Args:
        entity: DataFrame of one entity's swaps within the window.
        path: DataFrame of the full replay's swaps (for recovery weights).
        horizon: Forward recovery horizon in seconds.
        observation_end: Replay end timestamp (observations past this are
            censored).

    Returns:
        Tuple "(burst, recovery)".
    """
    gaps = np.diff(entity.ts.to_numpy())
    if len(gaps) < 2:
        burst = np.nan
    else:
        mean, std = float(gaps.mean()), float(gaps.std())
        burst = (std - mean) / (std + mean) if std + mean else 1.0
    if "recovered_impact" in entity:
        numerator = entity.recovered_impact.sum()
        denominator = entity.observed_impact.sum()
    else:
        recovered, weights = reversion_weights(path, horizon, observation_end)
        lookup = pd.DataFrame(
            {"sequence": path.sequence, "recovered": recovered, "weight": weights}
        ).set_index("sequence")
        selected = lookup.reindex(entity.sequence)
        numerator, denominator = selected.recovered.sum(), selected.weight.sum()
    return burst, numerator / denominator if denominator else np.nan


def reversion_weights(path, horizon, observation_end):
    """Compute per-swap recovery and impact weights in O(n).

    For each swap, the recovered impact is the largest subsequent movement back
    toward the pre-swap price within "horizon" seconds, capped at the swap's
    own absolute impact and floored at zero. The result is shared across views
    and windows.

    Args:
        path: DataFrame of the full replay's swaps.
        horizon: Forward recovery horizon in seconds.
        observation_end: Replay end timestamp.

    Returns:
        Tuple "(recovered, weights)" of arrays aligned to "path".
    """
    times = path.ts.to_numpy()
    prices = np.log(path.price_after.to_numpy())
    impacts = np.log(path.price_after.to_numpy() / path.price_before.to_numpy())
    recovered = np.zeros(len(path))
    weights = np.zeros(len(path))
    minimum, maximum = deque(), deque()
    right = 0
    for i in range(len(path)):
        while minimum and minimum[0] <= i:
            minimum.popleft()
        while maximum and maximum[0] <= i:
            maximum.popleft()
        right = max(right, i)
        while right + 1 < len(path) and times[right + 1] <= times[i] + horizon:
            right += 1
            while minimum and prices[minimum[-1]] >= prices[right]:
                minimum.pop()
            while maximum and prices[maximum[-1]] <= prices[right]:
                maximum.pop()
            minimum.append(right)
            maximum.append(right)
        impact = impacts[i]
        if times[i] + horizon > observation_end or abs(impact) < 1e-15:
            continue
        weights[i] = abs(impact)
        if minimum:
            recovery = (
                prices[i] - prices[minimum[0]]
                if impact > 0
                else prices[maximum[0]] - prices[i]
            )
            recovered[i] = min(abs(impact), max(0.0, recovery))
    return recovered, weights


def evaluate(trades, controlled, edges, cfg, start, end, run_id, budget=None):
    """Score all indicator windows for both V0 and V1 attribution views.

    Computes indicator values over sliding and fixed windows for every entity,
    always including silent controlled entities in firing-rate denominators.
    Arbitrage swaps are excluded from volume denominators.

    Args:
        trades: DataFrame of all replay fills.
        controlled: List of controlled wallet identifiers.
        edges: Funding edges for V0 clustering.
        cfg: Frozen configuration mapping.
        start: Replay start timestamp in seconds.
        end: Replay end timestamp in seconds.
        run_id: Identifier attached to every output row.
        budget: Optional :class:`Budget` for cooperative row checks.

    Returns:
        DataFrame with one row per entity/window/indicator.
    """
    rows = []
    trades = trades.copy()
    trades["recovered_impact"], trades["observed_impact"] = reversion_weights(
        trades, cfg["H_seconds"], end
    )
    for view in ("V0", "V1"):
        mapping = entity_map(trades.wallet, controlled, edges, view, cfg["v0_hops"])
        eligible = trades[trades.actor != "arb"].copy()
        eligible["entity"] = eligible.wallet.map(mapping)
        manip_entities = {mapping[w] for w in controlled}
        for kind, width, step in [
            ("sliding", cfg["window_hours"] * 3600, cfg["step_hours"] * 3600),
            ("c_sliding", cfg["w_c_hours"] * 3600, cfg["step_hours"] * 3600),
            ("fixed", cfg["w_c_hours"] * 3600, cfg["w_c_hours"] * 3600),
        ]:
            for left in range(start, int(end - width) + 1, int(step)):
                window = eligible[(eligible.ts >= left) & (eligible.ts < left + width)]
                groups = {
                    name: group for name, group in window.groupby("entity", sort=False)
                }
                organic_volume = float(
                    window.loc[window.actor == "organic", "notional_usd"].sum()
                )
                empty = window.iloc[:0]
                if budget:
                    budget.check(rows=len(rows))
                for entity in sorted(set(groups) | manip_entities):
                    e = groups.get(entity, empty)
                    # Include silent controlled windows in firing-rate denominators.
                    if e.empty and entity not in manip_entities:
                        continue
                    a = volume_share(e, window)
                    c = net_position_ratio(e) if a >= cfg["c_min_share"] else np.nan
                    if kind in ("fixed", "c_sliding"):
                        values = {
                            "c_fixed" if kind == "fixed" else "c_sliding": (c, np.nan)
                        }
                    else:
                        burst, revert = burst_revert(e, trades, cfg["H_seconds"], end)
                        linked = {target for _, target, _ in edges}
                        g1 = float(sum(w in linked for w in e.wallet.unique()) >= 2)
                        gaps = np.diff(e.ts.to_numpy())
                        values = {
                            "a": (a, np.nan),
                            "b": (attributable_impact(e), np.nan),
                            "d": (
                                reversal_share(e, cfg["T_seconds"], view == "V0"),
                                np.nan,
                            ),
                            "e": (burst, revert),
                            "g1": (g1, np.nan),
                            "g2": (float(np.any(gaps < cfg["g2_seconds"])), np.nan),
                            "g3": (c, np.nan),
                        }
                    for name, (value, aux) in values.items():
                        rows.append(
                            dict(
                                run_id=run_id,
                                view=view,
                                entity=entity,
                                actor="manip"
                                if entity in manip_entities
                                else "organic",
                                indicator=name,
                                window_start=left,
                                window_end=left + int(width),
                                window_kind="fixed" if kind == "fixed" else "sliding",
                                value=value,
                                aux_value=aux,
                                eligible=bool(np.isfinite(value)),
                                active=not e.empty,
                                entity_volume=float(e.notional_usd.sum()),
                                organic_volume=organic_volume,
                            )
                        )
    if budget:
        budget.check(rows=len(rows))
    return pd.DataFrame(rows)


def calibrate(windows, pcts):
    """Calibrate thresholds from organic windows at the given percentiles.

    The robust variant excludes entity-pool pairs flagged by any descriptive
    proxy at the raw 95th percentile before recalibrating.

    Args:
        windows: Organic-only indicator windows.
        pcts: List of threshold percentiles (e.g. "[95, 99]").

    Returns:
        DataFrame with one threshold per view/indicator/percentile/robustness.
    """
    organic = windows[windows.actor == "organic"]
    # Exclude entity-pool pairs flagged by ANY G proxy at the raw 95th cut.
    flags = organic[organic.indicator.isin(["g1", "g2", "g3"])].copy()
    flagged = set()
    for (_, _indicator), group in flags.groupby(["view", "indicator"]):
        cut = group.value.quantile(0.95)
        hits = group[group.value > cut]
        flagged.update(zip(hits.pool, hits.entity, hits.view, strict=False))
    rows = []
    for robust in (False, True):
        data = organic
        if robust:
            keep = [
                (p, e, v) not in flagged
                for p, e, v in zip(data.pool, data.entity, data.view, strict=False)
            ]
            data = data.loc[keep]
        for (view, indicator), group in data.groupby(["view", "indicator"]):
            for pct in pcts:
                rows.append(
                    dict(
                        view=view,
                        indicator=indicator,
                        threshold_pct=pct,
                        robust=robust,
                        threshold=group.value.quantile(pct / 100),
                        aux_threshold=group.aux_value.quantile(pct / 100)
                        if indicator == "e"
                        else np.nan,
                        n_calibration=int(group.value.notna().sum()),
                    )
                )
    return pd.DataFrame(rows)


def apply_thresholds(windows, thresholds):
    """Merge thresholds onto windows and mark evaluable/fired status.

    Args:
        windows: Indicator windows.
        thresholds: Calibrated thresholds.

    Returns:
        Input windows with "evaluable" and "fired" boolean columns added.
    """
    result = windows.merge(
        thresholds, on=["view", "indicator"], how="left", validate="many_to_many"
    )
    result["evaluable"] = result.eligible & result.threshold.notna()
    e = result.indicator == "e"
    result.loc[e, "evaluable"] &= (
        result.loc[e, "aux_value"].notna() & result.loc[e, "aux_threshold"].notna()
    )
    result["fired"] = result.evaluable & (result.value > result.threshold)
    result.loc[e, "fired"] &= (
        result.loc[e, "aux_value"] > result.loc[e, "aux_threshold"]
    )
    return result
