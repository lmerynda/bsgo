"""Reproduce reviewed notification-only fixtures from retained native frames.

Does not capture the game, infer labels, modify originals or copy full screens.
The source-run split was fixed before fitting the notification reader.
"""
import hashlib
import json
from pathlib import Path

from PIL import Image

from bsgo_offline.reward_notice import NOTICE_BOX


ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "test-data/vision/v3-reward-notices"
SOURCES = (("009", range(49, 55), "development", 53, "Water", 1237),
           ("010", range(69, 75), "development", 73, "Tylium", 5936),
           ("008", range(99, 104), "evaluation", 102, "Tylium", 7597))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    CORPUS.mkdir(parents=True, exist_ok=True)
    rows = []
    for number, sequences, split, first_notice, resource, amount in SOURCES:
        run = f"continuous-fire-{number}"
        for sequence in sequences:
            source = ROOT / "logs/mining/live-20261003" / run / "failed-recognition" / f"observation-{sequence:08}.png"
            sidecar = source.with_suffix(".json")
            metadata = json.loads(sidecar.read_text(encoding="utf-8"))
            if digest(source) != metadata["sha256"] or metadata["acquisition_sequence"] != sequence:
                raise ValueError("native_source_identity_mismatch")
            with Image.open(source) as image:
                if image.size != (2560, 1440):
                    raise ValueError("unsupported_native_source_geometry")
                crop = image.crop(NOTICE_BOX).convert("RGB")
                destination = CORPUS / f"fire{number}-{sequence:03}.png"
                if destination.exists():
                    with Image.open(destination) as previous:
                        if previous.size != crop.size or previous.convert("RGB").tobytes() != crop.tobytes():
                            raise ValueError("existing_fixture_differs")
                else:
                    crop.save(destination)
            rows.append({"id": destination.stem, "file": destination.name,
                "sha256": digest(destination), "source": source.relative_to(ROOT).as_posix(),
                "source_sha256": metadata["sha256"], "source_sidecar_sha256": digest(sidecar),
                "source_run": run, "run_id": metadata["run_id"],
                "acquisition_sequence": sequence, "acquired_at": metadata["snapshot"]["acquired_at"],
                "clock": "source_process_monotonic", "layout": metadata["context"]["layout"],
                "source_size": [2560,1440], "crop_box": list(NOTICE_BOX), "split": split,
                "expected": {"resource": resource, "amount": amount} if sequence >= first_notice else None,
                "annotation_basis": "Direct visual review of original notification pixels; no reader predictions",
                "attribution": "not_labeled", "inventory_total": "not_labeled"})
    manifest = {"version": 3, "field": "reward_notice", "scope": "Visible notification text only",
        "privacy": "Notification-only crops; no chat, player identity, account or cargo UI",
        "evaluation_scope": "Retrospective separate source run; same session/layout; no prospective holdout",
        "rows": rows}
    (CORPUS / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8", newline="\n")
    templates = ROOT / "tools/offline-bot/bsgo_offline/data/reward-notice"
    templates.mkdir(parents=True, exist_ok=True)
    for name, fixture in (("water", "fire009-053"), ("tylium", "fire010-073")):
        (templates / f"{name}.png").write_bytes((CORPUS / f"{fixture}.png").read_bytes())
    print(f"Prepared {len(rows)} source-verified, reviewed notification crops")


if __name__ == "__main__":
    main()
