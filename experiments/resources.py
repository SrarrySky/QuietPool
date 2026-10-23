"""Admission control and bounded memory, time, disk, and output checkpoints."""

import json
import sys
import time
from pathlib import Path

import pyarrow.parquet as pq

DEFAULTS = dict(
    memory_mb=2048,
    disk_mb=2048,
    wall_seconds=3600,
    max_runs=256,
    max_swaps_per_pool=200000,
    max_blocks_per_pool=400000,
    max_indicator_rows=250000,
)


def _peak_rss_mb():
    """Return the process peak resident set size in MiB (best effort).

    Returns:
        Peak RSS in MiB as a float, sampled from platform-specific APIs.
    """
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class _ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(_ProcessMemoryCounters)
        ctypes.windll.psapi.GetProcessMemoryInfo(
            ctypes.windll.kernel32.GetCurrentProcess(),
            ctypes.byref(counters),
            ctypes.sizeof(counters),
        )
        return counters.PeakWorkingSetSize / (1024**2)

    import resource

    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / (1024**2 if sys.platform == "darwin" else 1024)


class BudgetExceeded(RuntimeError):
    """A declared resource budget was reached; partial output is not a result."""


def plan(config, cfg, ladder, pools, sensitivities=False):
    """Admission-check the run count and data budgets without executing anything.

    Args:
        config: Path to the experiment config directory.
        cfg: Frozen configuration mapping.
        ladder: Strategy ladder settings.
        pools: Pool cohort list.
        sensitivities: Whether sensitivity scenarios are included.

    Returns:
        Dict with run count, limits, per-pool history sizes, and a note.

    Raises:
        BudgetExceeded: If the planned run count or any history exceeds limits.
        ValueError: If any resource limit is non-positive.
    """
    from .agents.manipulator import ladder_grid

    limits = dict(DEFAULTS, **cfg.get("resources", {}))
    if any(not isinstance(v, (int, float)) or v <= 0 for v in limits.values()):
        raise ValueError("All resource limits must be positive")
    count = (
        sum(p["split"] == "test" for p in pools["pools"])
        * len(ladder_grid(ladder))
        * len(cfg["seeds"])
        * (7 if sensitivities else 1)
    )
    if count > limits["max_runs"]:
        raise BudgetExceeded(
            f"{count} runs exceed max_runs={limits['max_runs']}; plan a smaller preregistered study or explicitly raise its budget"
        )
    histories = []
    for p in pools["pools"]:
        path = Path(config) / p["history"]
        swaps = pq.read_metadata(path / "swaps.parquet").num_rows
        blocks = pq.read_metadata(path / "blocks.parquet").num_rows
        if (
            swaps > limits["max_swaps_per_pool"]
            or blocks > limits["max_blocks_per_pool"]
        ):
            raise BudgetExceeded(f"History for {p['id']} exceeds its admission budget")
        histories.append(dict(pool=p["id"], swaps=swaps, blocks=blocks))
    return dict(
        runs=count,
        limits=limits,
        histories=histories,
        note="No strategy run was executed. Lambda sensitivities reuse paths; funding seeds are not independent market samples.",
    )


class Budget:
    """Cooperative resource budget enforcing wall, memory, row, and disk limits.

    Instances are intended to be used as context managers. On POSIX the
    virtual-address limit is tightened during the "with" block so that large
    temporary pandas/Arrow allocations are bounded between cooperative
    "check" calls.
    """

    def __init__(self, limits, output, *, started=None, enforce_disk=True):
        self.limits = dict(DEFAULTS, **limits)
        self.output = Path(output)
        self.started = time.monotonic() if started is None else started
        self.enforce_disk = enforce_disk
        self.old_limit = None
        self.peak_rss_mb = 0.0

    def __enter__(self):
        # POSIX virtual-address cap is stricter than an RSS cap, and bounds large
        # temporary pandas/Arrow allocations between cooperative checkpoints.
        if sys.platform == "win32":
            # RLIMIT_AS is unavailable on Windows; memory is tracked via peak RSS.
            return self
        import resource

        self.old_limit = resource.getrlimit(resource.RLIMIT_AS)
        requested = int(self.limits["memory_mb"] * 1024**2)
        soft, hard = self.old_limit
        if soft != resource.RLIM_INFINITY:
            requested = min(requested, soft)
        resource.setrlimit(resource.RLIMIT_AS, (requested, hard))
        return self

    def __exit__(self, kind, value, traceback):
        if self.old_limit is not None:
            import resource

            resource.setrlimit(resource.RLIMIT_AS, self.old_limit)
        if kind is not None and self.output.exists():
            (self.output / "INCOMPLETE.json").write_text(
                json.dumps(
                    dict(
                        error=kind.__name__,
                        message=str(value),
                        elapsed_seconds=time.monotonic() - self.started,
                    ),
                    indent=2,
                )
                + "\n"
            )
        return False

    def check(self, *, disk=False, rows=None):
        """Enforce the wall, memory, row, and optional disk budgets.

        Args:
            disk: If True, also check the output directory's disk usage.
            rows: If provided, enforce the indicator row budget.

        Returns:
            Dict with "elapsed_seconds" and "peak_rss_mb".

        Raises:
            BudgetExceeded: If any budget is exceeded.
        """
        self.peak_rss_mb = max(self.peak_rss_mb, _peak_rss_mb())
        if time.monotonic() - self.started > self.limits["wall_seconds"]:
            raise BudgetExceeded("Wall-time budget exceeded")
        if self.peak_rss_mb > self.limits["memory_mb"]:
            raise BudgetExceeded("Memory budget exceeded")
        if rows is not None and rows > self.limits["max_indicator_rows"]:
            raise BudgetExceeded(
                "Indicator row budget exceeded; use a prespecified smaller sample"
            )
        if disk and self.enforce_disk and self.output.exists():
            size = sum(p.stat().st_size for p in self.output.rglob("*") if p.is_file())
            if size > self.limits["disk_mb"] * 1024**2:
                raise BudgetExceeded("Output disk budget exceeded")
        return dict(
            elapsed_seconds=time.monotonic() - self.started,
            peak_rss_mb=self.peak_rss_mb,
        )

    def reserve(self, bytes_to_write):
        """Check the disk budget before writing an artifact of the given size.

        Args:
            bytes_to_write: Estimated size of the next artifact in bytes.

        Raises:
            BudgetExceeded: If writing would exceed the disk budget.
        """
        self.check(disk=True)
        if not self.enforce_disk:
            return
        size = sum(p.stat().st_size for p in self.output.rglob("*") if p.is_file())
        if size + bytes_to_write > self.limits["disk_mb"] * 1024**2:
            raise BudgetExceeded("Insufficient output budget for the next artifact")
