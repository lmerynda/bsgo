"""Rebuild the test images from the packed sheets before tests read them.

The repository keeps test images as a few sprite sheets plus index.json.
`ensure()` writes each image back to the path its tests expect (git-ignored)
as a lossless PNG, whatever its original extension. Full game frames come
back as a black canvas holding only the HUD regions the readers look at.
Small non-image files under files/ are copied to their original paths.
"""
import hashlib
import json
from pathlib import Path
import shutil
from threading import Lock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
INDEX = HERE / "index.json"
STAMP = HERE / ".restored"
_lock = Lock()


def _digest():
    data = INDEX.read_bytes()
    for sheet in sorted((HERE / "sheets").glob("*.png")):
        data += sheet.name.encode() + hashlib.sha256(sheet.read_bytes()).digest()
    return hashlib.sha256(data).hexdigest()


def ensure():
    """Idempotent and cheap once restored; safe to call from every test module."""
    with _lock:
        index = json.loads(INDEX.read_text(encoding="utf-8"))
        digest = _digest()
        targets = [ROOT / name for name in index["images"]] + [ROOT / name for name in index["files"]]
        if STAMP.exists() and STAMP.read_text() == digest and all(path.exists() for path in targets):
            return
        from PIL import Image
        sheets = {}
        def sheet(name):
            if name not in sheets:
                with Image.open(HERE / "sheets" / name) as image:
                    sheets[name] = image.convert("RGB")
            return sheets[name]
        for name, row in index["images"].items():
            canvas = Image.new("RGB", tuple(row["size"]))
            for part in row["parts"]:
                x, y, w, h = part["at"]
                piece = sheet(part["sheet"]).crop((x, y, x + w, y + h))
                canvas.paste(piece, tuple(part["box"][:2]))
            path = ROOT / name
            path.parent.mkdir(parents=True, exist_ok=True)
            canvas.save(path, format="PNG", optimize=False)
        for name in index["files"]:
            path = ROOT / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(HERE / "files" / name, path)
        STAMP.write_text(digest)


if __name__ == "__main__":
    ensure()
    print("test fixtures restored")
