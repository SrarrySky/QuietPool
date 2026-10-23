"""Stamp the current snapshot date into the frozen config's data paths.

Only "pinax_<8-digit-date>" directory segments are rewritten; the fixed
deterministic seed ("42") is left untouched.
"""

import argparse
import re
from pathlib import Path

from .. import ROOT, pinax_date

PATH_DATE = re.compile(r"pinax_(\d{8})")


def relabel(path, date):
    """Rewrite "pinax_<8-digit-date>" segments in "path" to "date".

    Args:
        path: Filesystem location of the text file to rewrite in place.
        date: Target "YYYYMMDD" suffix to substitute.

    Returns:
        "True" if the file changed, "False" if no segment matched.
    """
    path = Path(path)
    text = path.read_text()
    updated = PATH_DATE.sub(f"pinax_{date}", text)
    if updated == text:
        return False
    path.write_text(updated)
    return True


def main():
    """Relabel "pools.yaml" and "cohort.csv" to the target date in place."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(ROOT / "config"))
    parser.add_argument(
        "--date",
        default=pinax_date(),
        help="Target YYYYMMDD suffix (default: today)",
    )
    args = parser.parse_args()
    config = Path(args.config)
    changed = [
        name
        for name in ("pools.yaml", "study_evidence/cohort.csv")
        if relabel(config / name, args.date)
    ]
    if not changed:
        print(f"Paths already use pinax_{args.date}; nothing changed.")
    else:
        print(f"Relabeled to pinax_{args.date}: {', '.join(changed)}")


if __name__ == "__main__":
    main()
