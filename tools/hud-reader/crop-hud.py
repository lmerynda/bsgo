"""Crop visible HUD pixels only; calibrated for the observed 2560x1440 layout."""
import argparse
from pathlib import Path
from PIL import Image, ImageOps
from hud_reader import DISTANCE, SPEED

parser = argparse.ArgumentParser()
parser.add_argument('image', type=Path)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
image = Image.open(args.image).convert('RGB')
if image.size != (2560, 1440):
    raise SystemExit(f'Uncalibrated screenshot size: {image.size}')
args.output.mkdir(parents=True, exist_ok=True)
regions = {'distance': DISTANCE, 'speed': SPEED}
for name, box in regions.items():
    crop = image.crop(box)
    gray = ImageOps.grayscale(crop)
    binary = gray.point(lambda value: 0 if value >= 160 else 255)
    enlarged = binary.resize((binary.width*4, binary.height*4), Image.Resampling.NEAREST)
    ImageOps.expand(enlarged, border=16, fill=255).save(args.output / f'{name}.png')
