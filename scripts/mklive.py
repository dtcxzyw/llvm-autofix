#!/usr/bin/env python3
"""Create a live benchmark subset from bench/full/.

Selects issues whose knowledge_cutoff falls within the one-year window
ending on the given date, and creates a directory of symlinks under bench/.

Usage:
    python scripts/mklive.py YYMMDD
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
FULL_DIR = ROOT_DIR / "bench" / "full"
BENCH_DIR = ROOT_DIR / "bench"


def parse_timestamp(ts: str) -> datetime:
  """Parse a YYMMDD timestamp into a datetime (date only)."""
  if len(ts) != 6 or not ts.isdigit():
    raise ValueError(f"Invalid timestamp {ts!r}, expected YYMMDD format")
  return datetime.strptime(ts, "%y%m%d")


def main():
  parser = argparse.ArgumentParser(description="Create a live benchmark subset.")
  parser.add_argument("timestamp", help="End date in YYMMDD format (e.g. 250826)")
  args = parser.parse_args()

  end_date = parse_timestamp(args.timestamp).date()
  start_date = (
    datetime.combine(end_date, datetime.min.time()) - timedelta(days=365)
  ).date()

  out_dir = BENCH_DIR / f"live-{args.timestamp}"
  if out_dir.exists():
    print(f"Error: {out_dir} already exists", file=sys.stderr)
    sys.exit(1)

  # Collect matching cases
  cases = sorted(FULL_DIR.glob("*.json"))
  if not cases:
    print(f"Error: no cases found in {FULL_DIR}", file=sys.stderr)
    sys.exit(1)

  selected = []
  for case in cases:
    with open(case) as f:
      data = json.load(f)
    cutoff = datetime.fromisoformat(data["knowledge_cutoff"].replace("Z", "")).date()
    if start_date <= cutoff <= end_date:
      selected.append(case.name)

  if not selected:
    print(f"No cases found in range [{start_date}, {end_date}]", file=sys.stderr)
    sys.exit(1)

  # Create output directory and symlinks
  out_dir.mkdir(parents=True)
  for name in selected:
    (out_dir / name).symlink_to(os.path.relpath(FULL_DIR / name, out_dir))

  print(f"Created {out_dir} with {len(selected)} cases ({start_date} to {end_date})")


if __name__ == "__main__":
  main()
