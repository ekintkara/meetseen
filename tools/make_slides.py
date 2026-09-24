#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test slaytları üretir (PIL) — sentetik toplantı videosu için."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]
FONT = next((f for f in FONT_CANDIDATES if Path(f).exists()), None)

SLIDES = [
    ("Sprint 42 Planlama", ["Backlog: 34 hikaye", "Hedef: API gateway migration",
                            "Risk: regression on auth flow", "Demo: Friday"]),
    ("Mimari Değerlendirme", ["Mikroservis → Modüler monolit?",
                              "Deployment: blue-green strategy",
                              "Database: PostgreSQL 17 upgrade",
                              "Cache invalidation refactor"]),
    ("Zaman Çizelgesi", ["Q3: pilot rollout (TR cluster)", "Q4: full migration",
                         "Eski sistem kapatma: yıl sonu", "Owner: Platform team"]),
    ("Açık Sorular", ["TLS termination nerede?", "Retry policy: exponential?",
                      "Feature flag tooling kararı", "Budget onay bekliyor"]),
]


def render(outdir: Path):
    outdir.mkdir(parents=True, exist_ok=True)
    for i, (title, bullets) in enumerate(SLIDES, 1):
        img = Image.new("RGB", (1280, 720), "white")
        d = ImageDraw.Draw(img)
        big = ImageFont.truetype(FONT, 52) if FONT else ImageFont.load_default()
        small = ImageFont.truetype(FONT, 34) if FONT else ImageFont.load_default()
        tag = ImageFont.truetype(FONT, 22) if FONT else ImageFont.load_default()
        d.rectangle([0, 0, 1280, 110], fill=(28, 62, 140))
        d.text((40, 28), title, font=big, fill="white")
        y = 180
        for b in bullets:
            d.ellipse([40, y + 14, 58, y + 32], fill=(230, 120, 30))
            d.text((80, y), b, font=small, fill=(30, 30, 30))
            y += 90
        d.text((40, 670), f"Sprint 42 / Mimari Kurulu — sayfa {i}/4", font=tag, fill=(120, 120, 120))
        img.save(outdir / f"slide{i}.jpg", quality=92)
        print(f"  {outdir / f'slide{i}.jpg'}")


if __name__ == "__main__":
    render(Path(sys.argv[1] if len(sys.argv) > 1 else "test/slaytlar"))
