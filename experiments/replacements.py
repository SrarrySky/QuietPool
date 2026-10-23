"""Data eligibility and supplementation: bounded reserve screening and an auditable 36-pool cohort."""

import argparse
import copy
import json
import os
import shutil
from pathlib import Path

import pandas as pd
import yaml

from . import ROOT, pinax_date
from .data.download_pinax import download, write_json
from .data.import_pinax import import_history
from .protocol import freeze, load_config, sha256, verify_frozen
from .resources import Budget
from .resources import plan as resource_plan
from .study import (
    END,
    START,
    USDT,
    audit_histories,
    prepare_imports,
    qualify_pool,
    rank,
    select_cohort,
)

VACANCIES = ("pool_14", "pool_15", "pool_20", "pool_24", "pool_34")
RULE = "reserve_replacement_v1_first_eligible_fixed_order"


def reserve_candidates(discovery, n_vacancies=len(VACANCIES)):
    """Build the fixed-order reserve candidate pool list from discovery.

    Args:
        discovery: Path to the discovery CSV.
        n_vacancies: Number of reserve slots to prepare.

    Returns:
        List of reserve pool dicts.

    Raises:
        ValueError: If the reserve inventory is insufficient or oversized.
    """
    _, audit = select_cohort(discovery)
    reserves = [
        r
        for r in audit
        if r["discovery_decision"] == "unselected_reserve_no_automatic_replacement"
    ]
    reserves.sort(key=lambda r: (rank(r["pair"], RULE), r["pair"]))
    if len(reserves) < n_vacancies or len(reserves) > 36:
        raise ValueError("Insufficient or oversized reserve inventory")
    pools = []
    for index, r in enumerate(reserves, 37):
        token0, token1 = sorted([r["token"], r["quote"]])
        pools.append(
            dict(
                id=f"pool_{index:02}",
                label=r["symbol"],
                pair=r["pair"],
                token0=token0,
                token1=token1,
                quote_index=int(token1 == r["quote"]),
                token_decimals=int(r["decimals"]),
                quote_decimals=18,
                quote_symbol="USDT" if r["quote"] == USDT else "WBNB",
                split="calibration",
                start_block=START,
                end_block=END,
                history=f"../candidates/pool_{index:02}",
            )
        )
    return pools


def verify_prepared(work):
    """Verify a prepared replacement plan and its inputs are unchanged.

    Args:
        work: Working directory containing "replacement_plan.json".

    Returns:
        The parsed replacement plan record.

    Raises:
        ValueError: If the plan or any prepared input changed.
    """
    path = work / "replacement_plan.json"
    if sha256(path) != (work / "replacement_plan.sha256").read_text().strip():
        raise ValueError("Replacement plan changed")
    record = json.loads(path.read_text())
    if record["rule"] != RULE:
        raise ValueError("Replacement rule changed; create a new amendment")
    for name, digest in record["files"].items():
        if sha256(work / name) != digest:
            raise ValueError(f"Prepared input changed: {name}")
    return record


def prepare(config, work):
    """Prepare the reserve-screening workspace and bind its provenance.

    Args:
        config: Path to the original (preregistered) config directory.
        work: Working directory for the replacement amendment.

    Returns:
        The replacement plan record.
    """
    config, work = Path(config).resolve(), Path(work).resolve()
    if (work / "replacement_plan.json").exists():
        record = verify_prepared(work)
        if (work / record["source_config"]).resolve() != config:
            raise ValueError("Resume source differs")
        return record
    if work.exists() and any(work.iterdir()):
        raise ValueError(
            "Use a fresh work directory; incomplete preparation must be inspected"
        )
    source = yaml.safe_load((config / "pools.yaml").read_text())
    if (
        sha256(config / source["selection"]["discovery_evidence"])
        != source["selection"]["discovery_sha256"]
    ):
        raise ValueError("Original discovery evidence changed")
    if (
        len(source["pools"]) != 36
        or sum(p["split"] == "calibration" for p in source["pools"]) != 12
    ):
        raise ValueError("Expected the original 36-pool, 12/24 cohort")
    work.mkdir(parents=True, exist_ok=True)
    print(
        "Verifying all original histories and baseline exclusions", flush=True
    )
    audit_histories(config, work / "original_qualification")
    baseline = pd.read_csv(work / "original_qualification/qualification.csv").fillna("")
    rejected = baseline[baseline.status != "qualified"]
    if not all(
        reason
        == "Insufficient positive-volatility organic observations (at least 72 swaps)"
        for reason in rejected.reason
    ):
        raise ValueError(
            "Original failures include reasons other than low organic observations; see original_qualification"
        )
    vacancies = tuple(sorted(rejected.id))
    reserves = reserve_candidates(config / "study_evidence/discovery.csv", len(vacancies))
    reserve_config = work / "reserve_config"
    reserve_config.mkdir()
    shutil.copytree(config / "study_evidence", reserve_config / "study_evidence")
    for name in ("frozen.yaml", "ladder.yaml"):
        shutil.copyfile(config / name, reserve_config / name)
    (reserve_config / "pools.yaml").write_text(
        yaml.safe_dump(
            dict(
                selection=dict(purpose="reserve_screening", rule=RULE),
                pools=reserves,
            ),
            sort_keys=False,
        )
    )
    prepare_imports(reserve_config, work / "import_configs")
    # Bind the original records (including failed pools), baseline evidence,
    # candidate order, source provenance and import configuration before network IO.
    paths = list(reserve_config.rglob("*")) + list(
        (work / "import_configs").glob("*.yaml")
    )
    paths += list((work / "original_qualification").glob("*"))
    paths += [config / n for n in ("pools.yaml", "frozen.yaml", "ladder.yaml")]
    for pool in source["pools"]:
        paths += [
            (config / pool["history"] / n).resolve()
            for n in (
                "meta.json",
                "swaps.parquet",
                "blocks.parquet",
                "import_manifest.json",
            )
        ]
    record = dict(
        rule=RULE,
        source_config=os.path.relpath(config, work),
        vacancies=list(vacancies),
        reserve_order=[dict(id=p["id"], pair=p["pair"]) for p in reserves],
        assignment=(
            f"First {len(vacancies)} eligible reserves map in order to "
            f"{','.join(vacancies)}; original splits retained"
        ),
        eligibility="Same importer and qualify_pool rule; final volatility coverage gate unchanged; no intervention outcomes used",
        files={os.path.relpath(p, work): sha256(p) for p in paths if p.is_file()},
    )
    write_json(work / "replacement_plan.json", record)
    (work / "replacement_plan.sha256").write_text(
        sha256(work / "replacement_plan.json") + "\n"
    )
    return record


def choose_replacements(candidates, evaluate, vacancies=VACANCIES):
    """Select first-eligible candidates only; never optimize using outcomes.

    Args:
        candidates: Ordered list of reserve candidate dicts.
        evaluate: Callable that returns eligibility evidence for a candidate.
        vacancies: Tuple of vacancy slot identifiers.

    Returns:
        Tuple "(selected, screening)" of candidate lists.
    """
    selected, screening = [], []
    for candidate in candidates:
        if len(selected) == len(vacancies):
            screening.append(
                dict(
                    candidate=candidate["id"],
                    pair=candidate["pair"],
                    status="not_evaluated_quota_filled",
                )
            )
            continue
        try:
            evidence = evaluate(candidate)
        except ValueError as error:
            # Resource/input availability failures are not scientific exclusions.
            if any(
                word in str(error).lower()
                for word in (
                    "budget",
                    "changed",
                    "differs",
                    "incomplete",
                    "new directory",
                )
            ):
                raise
            screening.append(
                dict(
                    candidate=candidate["id"],
                    pair=candidate["pair"],
                    status="ineligible",
                    reason=str(error),
                )
            )
            print(f"Reserve {candidate['id']} ineligible: {error}", flush=True)
        else:
            selected.append(candidate)
            print(
                f"Reserve {candidate['id']} eligible ({len(selected)}/{len(vacancies)})",
                flush=True,
            )
            screening.append(
                dict(
                    candidate=candidate["id"],
                    pair=candidate["pair"],
                    slot=vacancies[len(selected) - 1],
                    **(evidence | {"status": "selected"}),
                )
            )
    return selected, screening


def consistent_write(path, text):
    """Write "text" to "path", refusing to silently overwrite different content.

    Raises:
        ValueError: If "path" exists with content different from "text".
    """
    if path.exists() and path.read_text() != text:
        raise ValueError(f"Amendment artifact changed: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(text)
        temporary.replace(path)


def complete(work, *, network_gib=32, download_seconds=14400, plan_only=False):
    """Run reserve screening, amend the cohort, and freeze the protocol.

    Args:
        work: Prepared working directory.
        network_gib: Network download budget in GiB.
        download_seconds: Wall-time budget for downloads.
        plan_only: If True, stop after planning without executing.

    Returns:
        Path to the final amended config directory, or "None" if "plan_only".
    """
    work = Path(work).resolve()
    record = verify_prepared(work)
    config = (work / record["source_config"]).resolve()
    vacancies = tuple(record["vacancies"])
    reserve_config = work / "reserve_config"
    if vacancies:
        download(
            reserve_config,
            work / "candidates/raw",
            network_gib=network_gib,
            wall_seconds=download_seconds,
            plan_only=plan_only,
        )
    elif not plan_only:
        print("All 36 original pools qualified; no supplementation required", flush=True)
    if plan_only:
        return None
    reserves = yaml.safe_load((reserve_config / "pools.yaml").read_text())["pools"]
    with Budget(dict(memory_mb=8192, disk_mb=65536, wall_seconds=10800), work) as budget:

        def evaluate(candidate):
            budget.check(disk=True)
            history = (reserve_config / candidate["history"]).resolve()
            cfg = work / "import_configs" / (candidate["id"] + ".yaml")
            if (history / "import_manifest.json").is_file():
                manifest = json.loads((history / "import_manifest.json").read_text())
                if manifest["config_sha256"] != sha256(cfg):
                    raise ValueError("Candidate import config changed")
            else:
                print(
                    f"Importing reserve {candidate['id']}: {candidate['label']}",
                    flush=True,
                )
                import_history(cfg, history)
            result = qualify_pool(reserve_config, candidate)
            result["history_hashes"] = {
                n: sha256(history / n)
                for n in (
                    "meta.json",
                    "swaps.parquet",
                    "blocks.parquet",
                    "import_manifest.json",
                )
            }
            return result

        selected, screening = choose_replacements(reserves, evaluate, vacancies)
        write_json(work / "screening.json", screening)
        pd.DataFrame(screening).drop(
            columns=["history_hashes"], errors="ignore"
        ).to_csv(work / "screening.csv", index=False)
        if len(selected) != len(vacancies):
            raise ValueError(
                "Reserve inventory exhausted before eligible pools; see screening.csv. No threshold was relaxed"
            )
        final = work / "final_config"
        source = yaml.safe_load((config / "pools.yaml").read_text())
        amended = copy.deepcopy(source)
        by_slot = dict(zip(vacancies, selected, strict=True))
        mappings = []
        for pool in amended["pools"]:
            if pool["id"] in by_slot:
                candidate = by_slot[pool["id"]]
                old_id, old_split, old_pair = pool["id"], pool["split"], pool["pair"]
                pool.clear()
                pool.update(
                    candidate,
                    id=old_id,
                    split=old_split,
                    history=os.path.relpath(
                        (reserve_config / candidate["history"]).resolve(), final
                    ),
                )
                mappings.append(
                    dict(
                        slot=old_id,
                        split=old_split,
                        excluded_pair=old_pair,
                        replacement_pair=pool["pair"],
                        candidate=candidate["id"],
                    )
                )
            else:
                pool["history"] = os.path.relpath(
                    (config / pool["history"]).resolve(), final
                )
        amended["selection"].update(
            design_id="reserve-amendment",
            qualification_status="see_bound_qualification_report",
            qualification_report="../qualification/qualification.json",
            amendment_manifest="../amendment_manifest.json",
            split_ratio=[1, 2],
        )
        consistent_write(final / "pools.yaml", yaml.safe_dump(amended, sort_keys=False))
        for name in ("frozen.yaml", "ladder.yaml"):
            consistent_write(final / name, (config / name).read_text())
        discovery = Path(source["selection"]["discovery_evidence"])
        consistent_write(final / discovery, (config / discovery).read_text())
        amendment = dict(
            rule=RULE,
            original_config_sha256=sha256(config / "pools.yaml"),
            plan_sha256=sha256(work / "replacement_plan.json"),
            screening_sha256=sha256(work / "screening.json"),
            mappings=mappings,
            n_pools=36,
            n_calibration=12,
            n_test=24,
            limitation="Baseline-informed amendment after data inspection, not original preregistration; fixed reserve order; no pool substitution after intervention outcomes",
            evidence_files={
                "replacement_plan.json": sha256(work / "replacement_plan.json"),
                "screening.json": sha256(work / "screening.json"),
                "original_qualification/qualification.csv": sha256(
                    work / "original_qualification/qualification.csv"
                ),
            },
        )
        consistent_write(
            work / "amendment_manifest.json", json.dumps(amendment, indent=2) + "\n"
        )
    if not (work / "qualification").exists():
        print(
            "Five reserves selected; auditing all 36 pools and volatility coverage",
            flush=True,
        )
        report = audit_histories(final, work / "qualification")
    else:
        report = json.loads((work / "qualification/qualification.json").read_text())
    if not report["qualified"]:
        raise ValueError(
            "Amended cohort failed final qualification/volatility coverage; see qualification/. No automatic reshuffling"
        )
    cfg, ladder, pools = load_config(final)
    planned = resource_plan(final, cfg, ladder, pools)
    write_json(work / "execution_plan.json", planned)
    if (final / "protocol.lock.json").exists():
        verify_frozen(final)
    else:
        freeze(final)
    (work / "INCOMPLETE.json").unlink(missing_ok=True)
    print(
        f"36-pool protocol frozen; planned paths={planned['runs']}; config={final}",
        flush=True,
    )
    return final


def main():
    """CLI entry point for the prepare/plan/complete/run amendment workflow."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "plan", "complete", "run"])
    parser.add_argument("--config", default=str(ROOT / "config"))
    parser.add_argument("--work", default=f"data/pinax_{pinax_date()}/replacement_v1")
    parser.add_argument("--output", default="output/pinax36_v2")
    parser.add_argument("--network-gib", type=float, default=32)
    parser.add_argument("--download-seconds", type=int, default=14400)
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Worker processes for the primary experiment (default 1 = serial)",
    )
    args = parser.parse_args()
    if (
        args.command == "run"
        and Path(args.output).exists()
        and any(Path(args.output).iterdir())
    ):
        raise ValueError(
            "Experiment output already exists; inspect it and choose a new --output for a new run"
        )
    prepare(args.config, args.work)
    if args.command == "prepare":
        print("Reserve order fixed. Run plan, complete, or run with the same --work.")
        return
    final = complete(
        args.work,
        network_gib=args.network_gib,
        download_seconds=args.download_seconds,
        plan_only=args.command == "plan",
    )
    if final and args.command == "run":
        from .run import run

        run(final, args.output, jobs=args.jobs)
    elif final:
        print(f"Amended 36-pool protocol qualified and frozen: {final}")


if __name__ == "__main__":
    main()
