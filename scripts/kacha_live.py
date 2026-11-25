"""Live check of kacha-bill reading against the real vision API (uses GROQ_API_KEY from .env).

Draws a handwritten-style bill with a deliberate mistake (चीनी 3 kg x 45 written as 145, total
650 instead of 695), sends it to the model, and prints what Khata would tell the shopkeeper.
Run: .venv/bin/python scripts/kacha_live.py
"""
import io
import json
import random
import sys
from pathlib import Path

import dotenv
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from khata.bills import OpenAICompatVision, intro, validate  # noqa: E402

FONTS = "/System/Library/Fonts/Supplemental/"


def font(names, size):
    for n in names:
        try:
            return ImageFont.truetype(FONTS + n, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def draw() -> bytes:
    hand = font(["Noteworthy.ttc", "Chalkboard.ttc", "MarkerFelt.ttc"], 40)
    dev = font(["DevanagariMT.ttc", "Devanagari Sangam MN.ttc", "ITFDevanagari.ttc"], 40)
    img = Image.new("RGB", (900, 1100), (250, 246, 228))
    d = ImageDraw.Draw(img)
    for y in range(140, 1080, 60):
        d.line([(30, y), (870, y)], fill=(170, 190, 220), width=2)
    random.seed(3)
    lines = [(hand, "Gupta Kiryana Store", 60), (hand, "Date: 18-11-2026", 130),
             (dev, "चावल  5 kg x 60 = 300", 260), (hand, "Sarson tel 1 L x 180 = 180", 330),
             (dev, "चीनी 3 kg x 45 = 145", 400), (hand, "Chai patti 250g = 70", 470),
             (hand, "Total = 650/-", 600)]
    for f, t, y in lines:
        d.text((70 + random.randint(-8, 8), y), t, fill=(25, 35, 110), font=f)
    img = img.rotate(1.5, fillcolor=(250, 246, 228)).filter(ImageFilter.GaussianBlur(0.6))
    out = Path("samples/private")
    out.mkdir(parents=True, exist_ok=True)
    img.save(out / "kacha-test.jpg")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return buf.getvalue()


if __name__ == "__main__":
    key = dotenv.dotenv_values(".env").get("GROQ_API_KEY")
    if not key:
        sys.exit("GROQ_API_KEY missing from .env")
    raw = OpenAICompatVision(key).read(draw(), "image/jpeg")
    print(json.dumps(raw, ensure_ascii=False, indent=1))
    print("----")
    print(intro(validate(raw)))
