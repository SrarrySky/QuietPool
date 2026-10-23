"""Public funding ancestry versus known controlled ownership."""

from collections import defaultdict
from decimal import ROUND_DOWN, Decimal

import numpy as np


def funding_edges(wallets, randomized, seed, settings):
    """Generate treasury-to-wallet funding edges with reproducible amounts.

    Args:
        wallets: Iterable of wallet identifiers to fund.
        randomized: If True, draw each amount uniformly from the configured
            range; otherwise use the fixed amount.
        seed: Integer seed for the NumPy random generator.
        settings: Funding range/fixed-amount settings mapping.

    Returns:
        List of "(source, target, amount)" triples; every source is
        "treasury".

    Raises:
        ValueError: If the configured funding range is invalid.
    """
    rng = np.random.default_rng(seed)
    low, high = (
        Decimal(str(settings["min_amount"])),
        Decimal(str(settings["max_amount"])),
    )
    if not 0 < low <= high:
        raise ValueError("Invalid funding range")
    amounts = [
        (low + (high - low) * Decimal(str(rng.random()))).quantize(
            Decimal("0.00000001"), rounding=ROUND_DOWN
        )
        if randomized
        else Decimal(str(settings["fixed_amount"]))
        for _ in wallets
    ]
    return [("treasury", w, str(a)) for w, a in zip(wallets, amounts, strict=False)]


def entity_map(wallets, controlled, edges, view, hops=1):
    """Map wallets to entities under the V0 or V1 attribution view.

    V1 assigns every controlled wallet to a single "E_manip" entity. V0
    clusters wallets by shared upstream funding ancestry up to "hops" levels
    deep, collapsing ancestors that are already owned by another wallet.

    Args:
        wallets: Iterable of all wallet identifiers.
        controlled: Iterable of known controlled wallet identifiers.
        edges: Iterable of "(source, target, amount)" funding edges.
        view: Either "V0" (funding ancestry) or "V1" (known ownership).
        hops: Ancestry depth for V0 clustering (default 1).

    Returns:
        Dict mapping each wallet to its entity label.

    Raises:
        ValueError: If view is not "V0"/"V1" or "hops" is negative.
    """
    if view not in {"V0", "V1"} or hops < 0:
        raise ValueError("Invalid entity view or ancestry depth")
    wallets = sorted(set(wallets) | set(controlled))
    parent = {w: w for w in wallets}

    def root(w):
        while parent[w] != w:
            parent[w] = parent[parent[w]]
            w = parent[w]
        return w

    if view == "V1":
        return {w: "E_manip" if w in controlled else w for w in wallets}
    incoming = defaultdict(set)
    for source, target, _ in edges:
        incoming[target].add(source)
    owners = {}
    for w in wallets:
        ancestors, frontier = {w}, {w}
        for _ in range(hops):
            frontier = {a for node in frontier for a in incoming[node]} - ancestors
            ancestors.update(frontier)
        for a in ancestors:
            if a in owners:
                parent[root(w)] = root(owners[a])
            else:
                owners[a] = w
    return {w: "E_" + root(w) for w in wallets}
