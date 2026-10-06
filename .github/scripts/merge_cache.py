"""Conservative merge for persist_cache.yml; no network or Git commands."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cache_safety import atomic_json, merge_cache


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("current")
    parser.add_argument("candidate")
    parser.add_argument("output")
    args = parser.parse_args()
    with open(args.current, encoding="utf-8") as handle:
        current = json.load(handle)
    with open(args.candidate, encoding="utf-8") as handle:
        candidate = json.load(handle)
    merged, stats = merge_cache(current, candidate)
    if merged != current or Path(args.output).resolve() != Path(args.current).resolve():
        atomic_json(args.output, merged)
    print(json.dumps(stats, sort_keys=True))
    if stats["invalid"] or stats["stale"] or stats["conflict"]:
        print("::warning::Versioni incomplete, vecchie o ambigue conservate dalla cache corrente.")


if __name__ == "__main__":
    main()
