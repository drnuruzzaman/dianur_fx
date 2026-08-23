"""Command-line entry point for the registered trendline research run.

The CLI intentionally requires adapters for project-specific data/strategy
execution rather than inventing data paths or silently changing the existing
backtester. `--contract-test` is useful in CI; the full run is wired once the
project's existing data loader is selected explicitly.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .tl_pipeline import Pipeline, persist_json


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="DiaNurFx automated trendline research")
    p.add_argument("--config", default="configs/tl_research.yaml")
    p.add_argument("--root", default=".")
    p.add_argument("--contract-test", action="store_true",
                   help="write a contract manifest without touching market data")
    args = p.parse_args(argv)

    root = Path(args.root).resolve()
    config_path = root / args.config
    if not config_path.exists():
        raise SystemExit(f"config not found: {config_path}")

    # YAML is deliberately not imported here until it is an explicit dependency.
    # The repository currently does not pin PyYAML, so fail with an actionable
    # message instead of a hidden dependency error.
    try:
        import yaml
    except ImportError as exc:
        raise SystemExit("Install PyYAML to run --config YAML: pip install pyyaml") from exc

    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    pipeline = Pipeline(root, config)
    pipeline.persist_manifest()

    if args.contract_test:
        payload = {
            "run_id": pipeline.result.run_id,
            "config_hash": pipeline.result.run_id,
            "status": "CONTRACT_READY",
            "message": "Research adapters are intentionally required before an authoritative market-data run.",
        }
        persist_json(payload, pipeline.run_dir / "contract.json")
        print(json.dumps(payload, indent=2))
        return 0

    raise SystemExit(
        "The orchestration layer is installed, but no authoritative data/strategy adapter "
        "was selected. This is intentional: do not guess the project's data loader or "
        "reinterpret existing strategy semantics. Implement the adapters and then run again."
    )


if __name__ == "__main__":
    raise SystemExit(main())
