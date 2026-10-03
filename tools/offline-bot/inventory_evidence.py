"""Inventory frozen saved evidence and make sheets for manual field labeling."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=root / "logs/offline-bot/improvement/evidence-inventory")
    args = parser.parse_args()
    source = root / "test-data/vision/v1"
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    args.output.mkdir(parents=True, exist_ok=True)
    groups = defaultdict(list)
    for row in manifest["rows"]:
        path = (source / row["file"]).resolve()
        if not path.is_relative_to(source):
            raise ValueError("corpus_path_escape")
        if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError("corpus_hash_mismatch:" + row["id"])
        if row["kind"] == "real_capture":
            groups[row["source_run"]].append(row)
    selected = []
    for group, rows in sorted(groups.items()):
        # Preserve manual source identities, never infer labels from run names.
        rows.sort(key=lambda r: r["source"])
        for row in (rows[0], rows[-1]) if len(rows) > 1 else (rows[0],):
            selected.append(row)
    records = []
    for offset in range(0, len(selected), 12):
        sheet = Image.new("RGB", (1920, 1400), "#161d29")
        draw = ImageDraw.Draw(sheet)
        for index, row in enumerate(selected[offset:offset+12]):
            x, y = (index % 3) * 640, (index // 3) * 350
            with Image.open(source / row["file"]) as opened:
                preview = opened.convert("RGB")
                preview.thumbnail((630, 305))
            sheet.paste(preview, (x, y))
            draw.text((x+5, y+307), f'{offset+index}: {row["source_run"]}', fill="white")
            draw.text((x+5, y+324), row["id"], fill="white")
            records.append({"sheet_index": offset+index, "id": row["id"], "source": row["source"],
                            "file": row["file"], "sha256": row["sha256"], "size": row["size"],
                            "source_run": row["source_run"], "labels": "not_yet_manually_reviewed"})
        sheet.save(args.output / f"sheet-{offset//12:02d}.png")
    report = {"manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
              "all_input_hashes_verified": True, "source_groups": {k:len(v) for k,v in groups.items()},
              "review_samples": records,
              "scope": "Existing labels cover distance and speed; new field labels require direct pixel review."}
    (args.output / "inventory.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Verified {len(manifest['rows'])} inputs; {len(groups)} real source groups; {len(records)} review samples")


if __name__ == "__main__": main()
