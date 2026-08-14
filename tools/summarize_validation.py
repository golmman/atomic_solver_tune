#!/usr/bin/env python3
"""Print a human-readable summary of a benchmark JSON file."""

import argparse
import json
import sys


def main():
    parser = argparse.ArgumentParser(description="Summarize a benchmark JSON file.")
    parser.add_argument("path", help="benchmark JSON file (use '-' for stdin)")
    args = parser.parse_args()

    try:
        if args.path == "-":
            data = json.load(sys.stdin)
        else:
            with open(args.path) as f:
                data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    agg = data.get("aggregates", {})
    results = data.get("results", [])
    total_positions = len(results)

    print(f"suite:       {data.get('suite', 'unknown')}")
    print(f"config:      {data.get('config_path', 'unknown')}")
    print(f"timeout:     {data.get('timeout', 'unknown')}s")
    print(f"runs:        {data.get('runs', 'unknown')}")
    print(f"positions:   {total_positions}")
    print(f"solved:      {agg.get('solved', 0)}/{total_positions}")
    print(f"wrong:       {agg.get('wrong', 0)}")
    print(f"timeouts:    {agg.get('timeouts', 0)}")
    print(f"child_evals: {agg.get('total_child_evals', 0):,}")
    print(f"total_time:  {agg.get('total_time', 0.0):.2f}s")
    print(f"mean_pv_len: {agg.get('mean_pv_len', 0.0):.2f}")


if __name__ == "__main__":
    main()
