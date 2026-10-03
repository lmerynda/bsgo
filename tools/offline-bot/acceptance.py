"""Recording-only mining acceptance scenarios. Does not import native drivers."""
import argparse
import json
from pathlib import Path
import sys

from bsgo_offline.mining_harness import simulate_mining


SCENARIOS = (
    ("nominal-no-planner", {}, True, "reward_observed", "stopped"),
    ("delayed-advice", {"planner_delay": .4}, True, "reward_observed", "stopped"),
    ("slow-advice", {"planner_delay": 10}, True, "reward_observed", "stopped"),
    ("ignored-brake", {"ignore_brake": True}, False, "approach_evidence_rejected", "stop_unverified"),
    ("stalled-recognition", {"stall_after": 15}, False, "observation_unavailable", "stop_unverified"),
    ("capture-fault", {"fault_after": 8}, False, "observation_unavailable", "control_unavailable"),
    ("missing-scan", {"scan_missing": True}, False, "scan_timeout", "already_stopped"),
    ("ineligible-resource", {"eligible": False}, False, "resource_ineligible", "already_stopped"),
    ("mining-ignored", {"mining_ignored": True}, False, "mine_timeout", "stopped"),
    ("missing-reward", {"reward_missing": True}, False, "confirm_outcome_timeout", "stopped"),
    ("inventory-conflict", {"inventory_conflict": True}, False, "reward_attribution_uncertain", "stopped"),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("logs/offline-bot/improvement/acceptance"))
    args = parser.parse_args()
    rows = []
    for name, options, completed, reason, cleanup in SCENARIOS:
        report = simulate_mining(args.output / name, **options)
        result = report["result"]
        passed = (result.completed == completed and result.reason == reason and result.cleanup == cleanup
                  and all(report["shutdown"].values()) and report["complete_event_trace"])
        if name == "delayed-advice":
            passed = passed and any(e["kind"] == "planner_result" and e["accepted"]
                                    for e in report["transitions"])
        row = {"scenario": name, "passed": passed, "completed": result.completed,
               "reason": result.reason, "cleanup": result.cleanup,
               "recognition_processed": report["recognition_processed"],
               "all_workers_joined": all(report["shutdown"].values()),
               "complete_event_trace": report["complete_event_trace"],
               "report": str(args.output / name / "report.json"),
               "runtime_source_sha256": report["runtime_source_sha256"]}
        rows.append(row)
        print(json.dumps(row), flush=True)
    payload = {"scope": "Synthetic receiver only; no BSGO capture/input", "passed": all(r["passed"] for r in rows),
               "scenarios": rows}
    (args.output / "acceptance.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0 if payload["passed"] else 1


if __name__ == "__main__": sys.exit(main())
