# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "scipy"]
# ///
"""ESC-50 → кліпи у форматі плати: 16 кГц моно int16, 0,3 с до удару і 0,5 с після.

  curl -L -o train/data/raw/esc50.zip https://codeload.github.com/karolpiczak/ESC-50/zip/refs/heads/master
  unzip -q train/data/raw/esc50.zip -d train/data/raw
  uv run train/prepare_esc50.py

Удар шукаємо так само, як clap_features.py: кадр із найбільшим ростом спектра.
Беремо лише класи, корисні для хлопка: позитиви (clapping) і важкі негативи (різкі, короткі).
Результат: train/data/esc50/<клас>/<файл>.wav і train/data/esc50/manifest.csv. Ліцензія ESC-50: CC BY-NC.
"""
import csv
import sys
import wave
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "train" / "data" / "raw" / "ESC-50-master"
OUT = ROOT / "train" / "data" / "esc50"
sys.path.insert(0, str(ROOT / "tools"))
from clap_features import HOP, spectra  # noqa: E402

RATE = 16000
PRE, POST = int(0.3 * RATE), int(0.5 * RATE)

POSITIVE = {"clapping"}
NEGATIVE = {
    # різкі удари, найближчі до хлопка
    "door_wood_knock", "mouse_click", "keyboard_typing", "footsteps", "can_opening",
    "glass_breaking", "clock_tick", "fireworks", "crackling_fire", "water_drops",
    # спальня
    "coughing", "sneezing", "laughing", "snoring", "breathing",
}


def load(path):
    with wave.open(str(path)) as w:
        rate, width = w.getframerate(), w.getsampwidth()
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2" if width == 2 else "<i4").astype(float)
    if width == 4:
        x /= 65536
    x = resample_poly(x, RATE, rate) if rate != RATE else x
    return x - x.mean()


def strongest_onset(x) -> int:
    logS = np.log1p(spectra(x))
    flux = np.r_[0, np.maximum(0, np.diff(logS, axis=0)).sum(1)]
    flux[:3] = 0
    return int(flux.argmax()) * HOP


def cut_and_save(x, dst: Path) -> bool:
    # Вирізає найрізкіший удар як кліп плати. False — у файлі тиша
    if np.abs(x).max() < 50:
        return False
    at = strongest_onset(x)
    clip = np.pad(x, (PRE, POST))[at:at + PRE + POST]  # зсув на PRE через pad
    clip *= 0.5 * 32767 / (np.abs(clip).max() + 1e-9)  # пік −6 дБ; рівень міняє вже аугментація
    dst.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dst), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(clip.astype("<i2").tobytes())
    return True


def main():
    rows = []
    with (SRC / "meta" / "esc50.csv").open() as f:
        for row in csv.DictReader(f):
            cat = row["category"]
            if cat not in POSITIVE | NEGATIVE:
                continue
            dst = OUT / cat / row["filename"]
            if not cut_and_save(load(SRC / "audio" / row["filename"]), dst):
                continue
            rows.append({"file": str(dst.relative_to(OUT)), "category": cat,
                         "role": "positive" if cat in POSITIVE else "negative",
                         "fold": row["fold"], "source": "ESC-50", "license": "CC BY-NC 3.0"})
    with (OUT / "manifest.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    pos = sum(r["role"] == "positive" for r in rows)
    print(f"{len(rows)} кліпів: позитивів {pos}, негативів {len(rows) - pos} → {OUT}")


if __name__ == "__main__":
    main()
