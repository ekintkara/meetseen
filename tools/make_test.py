#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sentetik iki dilli toplantı videosu üretir (macOS `say` + slaytlar).

Kullanım: .venv/bin/python tools/make_test.py [çıktı_dizini]
Üretir:   <çıktı>/test.mp4  — TR (Yelda) + EN (Samantha) karışık,
          başta sohbet, sonra iş; arkada 4 slayt.
"""
import shutil
import subprocess
import sys
from pathlib import Path

SEGMENTS = [
    ("tr", "Merhaba Ahmet, nasılsın? Eee, hafta sonu nasıl geçti? Biz eee şey yaptık, "
           "akşama yemek yedik, çok iyiydi ya. Hava da güzel değil mi, dışarı çıkmalı bir ara."),
    ("en", "Good morning everyone, can you hear me okay? Great, let's get started with the sprint review."),
    ("tr", "Tamam o zaman. Bu sprintde asıl konu API gateway migration. Eee, backlogda "
           "otuz dört hikaye var, dördü blocker. Şey, authentication akışında regression "
           "riski var, onun için test kapsamını artırmamız lazım."),
    ("en", "Right. From the architecture side, I recommend a blue-green deployment strategy "
           "for zero downtime during the rollout. We should also decide on the feature flag "
           "tooling before the pilot starts."),
    ("tr", "Ok, blue-green ile gidelim, bu bir karar olsun lütfen. Pilotu üçüncü çeyrekte "
           "Türkiye clusterında başlatıyoruz. Eee bir de, Ahmet credentialları hazırlayacak, "
           "cuma gününe kadar tamamlar mısın?"),
    ("en", "Sure. One open question from my side: where should TLS termination live, "
           "at the gateway or at the service mesh? We need to resolve this before the pilot."),
    ("tr", "Bunu konuşmamız lazım, açık soru olarak kalsın o zaman. Son olarak eski sistemin "
           "kapanması yıl sonunda olacak. Risk olarak, budget onayı hâlâ bekliyor, onu da not "
           "etsek iyi olur. Peki, kapatıyoruz, iyi çalışmalar!"),
]
VOICES = {"tr": "Yelda", "en": "Samantha"}


def sh(*cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"HATA ({cmd[0]}): {r.stderr[:400]}")


def main(outdir: Path):
    outdir.mkdir(parents=True, exist_ok=True)
    tmp = outdir / "_tmp"
    tmp.mkdir(exist_ok=True)

    print("1/4 ses sentezi (say)...")
    parts = []
    for i, (lang, text) in enumerate(SEGMENTS):
        aiff = tmp / f"seg{i}.aiff"
        voice = VOICES[lang]
        try:
            sh("say", "-v", voice, "-o", aiff, text)
        except SystemExit:
            if lang == "en":  # Samantha yoksa herhangi bir İngilizce ses
                sh("say", "-v", "en_US", "-o", aiff, text)
            else:
                raise
        parts.append(aiff)
        print(f"   seg{i} [{lang}] {aiff.stat().st_size} bayt")

    print("2/4 ses birleştirme (0.6 sn aralıkla)...")
    gap = tmp / "gap.wav"
    sh("ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
       "-t", "0.6", gap)
    concat = tmp / "list.txt"
    concat.write_text("".join(
        f"file '{p.name}'\nfile 'gap.wav'\n" for p in parts), encoding="utf-8")
    wav = outdir / "toplanti.wav"
    sh("ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
       "-i", concat, "-ar", "16000", "-ac", "1", wav)
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(wav)],
        capture_output=True, text=True).stdout.strip())

    print("3/4 slaytlar...")
    sh(sys.executable, Path(__file__).parent / "make_slides.py", tmp / "slaytlar")

    print("4/4 video derleme...")
    per = dur / 4
    slides = tmp / "slides.txt"
    slides.write_text("".join(
        f"file 'slaytlar/slide{i}.jpg'\nduration {per:.2f}\n" for i in range(1, 5))
        + "file 'slaytlar/slide4.jpg'\n", encoding="utf-8")
    mp4 = outdir / "test.mp4"
    sh("ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", slides,
       "-i", wav, "-fps_mode", "vfr", "-pix_fmt", "yuv420p", "-c:v", "libx264",
       "-preset", "fast", "-c:a", "aac", "-shortest", "-t", f"{dur:.2f}", mp4)
    shutil.rmtree(tmp)
    print(f"OK: {mp4} ({dur:.0f} sn, {mp4.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "test"))
