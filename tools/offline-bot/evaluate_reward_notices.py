"""Verify and score reviewed native notification crops without game access."""
import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image

from bsgo_offline.reward_notice import NOTICE_BOX, read_reward_notice_crop


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluate(manifest_path, *, source_root=None):
    manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != 3 or manifest.get("field") != "reward_notice":
        raise ValueError("unsupported_reward_evidence_manifest")
    groups, hashes, ids, rows = {}, {}, set(), []
    counts = {split: {key: 0 for key in ("accepted_correct", "accepted_wrong", "rejected_present",
        "rejected_absent", "total")} for split in ("development", "evaluation")}
    for row in manifest["rows"]:
        split = row["split"]
        if split not in counts or row["id"] in ids:
            raise ValueError("invalid_split_or_duplicate_id")
        ids.add(row["id"])
        for table, identity in ((groups, row["source_run"]), (hashes, row["source_sha256"]),
                                 (hashes, row["sha256"])):
            if identity in table and table[identity] != split:
                raise ValueError("source_or_image_split_leakage")
            table[identity] = split
        path = (manifest_path.parent / row["file"]).resolve()
        if not path.is_relative_to(manifest_path.parent) or digest(path) != row["sha256"]:
            raise ValueError("fixture_path_or_hash_mismatch")
        if (row["source_size"] != [2560,1440] or row["crop_box"] != list(NOTICE_BOX)
                or row["layout"] != "largehud-20261003"):
            raise ValueError("unsupported_fixture_provenance")
        expected = row["expected"]
        if expected is not None and (set(expected) != {"resource", "amount"}
                or expected["resource"] not in ("Water", "Tylium")
                or type(expected["amount"]) is not int or expected["amount"] <= 0):
            raise ValueError("invalid_notification_label")
        with Image.open(path) as image:
            if image.size != (440,80):
                raise ValueError("fixture_geometry_mismatch")
            if source_root is not None:
                source_root = Path(source_root).resolve()
                original = (source_root / row["source"]).resolve()
                if not original.is_relative_to(source_root) or digest(original) != row["source_sha256"]:
                    raise ValueError("original_path_or_hash_mismatch")
                sidecar = original.with_suffix(".json")
                if digest(sidecar) != row["source_sidecar_sha256"]:
                    raise ValueError("original_sidecar_hash_mismatch")
                metadata = json.loads(sidecar.read_text(encoding="utf-8"))
                if (metadata["run_id"] != row["run_id"]
                        or metadata["acquisition_sequence"] != row["acquisition_sequence"]
                        or metadata["snapshot"]["acquired_at"] != row["acquired_at"]):
                    raise ValueError("original_observation_identity_mismatch")
                with Image.open(original) as full:
                    if full.size != tuple(row["source_size"]) or full.crop(NOTICE_BOX).convert("RGB").tobytes() != image.convert("RGB").tobytes():
                        raise ValueError("original_crop_mismatch")
            notice = read_reward_notice_crop(image)
        actual = {"resource": notice.resource, "amount": notice.amount} if notice.resource is not None else None
        outcome = ("accepted_correct" if actual == expected else "accepted_wrong") if actual is not None else (
            "rejected_absent" if expected is None else "rejected_present")
        counts[split][outcome] += 1
        counts[split]["total"] += 1
        rows.append({"id": row["id"], "split": split, "expected": expected,
            "actual": actual, "outcome": outcome, "reason": notice.reason})
    return {"field": "reward_notice", "manifest_sha256": digest(manifest_path),
        "originals_verified": source_root is not None, "counts": counts, "rows": rows,
        "limitations": ["Retrospective separate runs in one session/layout; no prospective evaluation",
            "Four-digit amounts; digits 0,4,8 unsupported", "No Water evaluation positives",
            "Notice text is not inventory, a unique event, or causal mining attribution",
            "Default runtime and mission event detector do not consume this field"]}


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=root / "test-data/vision/v3-reward-notices/manifest.json")
    parser.add_argument("--verify-originals", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate(args.manifest, source_root=root if args.verify_originals else None)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report["counts"], indent=2))
    return int(any(value["accepted_wrong"] for value in report["counts"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
