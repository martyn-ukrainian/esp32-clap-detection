# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""Нарізати окремі хлопки з чужих наборів у кліпи як із Pi: 0,8 с (300 мс до удару, 500 після), 16 кГц, int16, моно.

  uv run tools/cut_claps.py            усі джерела з data/datasets/ → data/datasets/cut/<джерело>/*.wav + manifest.csv

Джерела (звідки і як качали — docs/datasets.md):
  audioset_strong_clapping   10-с сегменти YouTube + events.tsv з часом кожного хлопка (розмітка Google)
  fsd50k_clapping            записи Freesound, перевірені людьми як «Clapping» без оплесків і музики
  esc50_clapping             40 кліпів ESC-50, клас clapping
  freesound_search           решта Freesound з пошуку «hand clap» (не перевірені, тому окремо)

Удар шукаємо так само, як clap_features.py: кадр 10 мс, де спектр найбільше виріс (spectral flux).
Для AudioSet — найбільший flux біля мітки часу, для решти — усі різкі піки у файлі.
У manifest.csv — ознаки з clap_features.py, щоб відсіяти нехлопкове порогами, а не на слух.
"""
import csv
import subprocess
import sys
import wave
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DS = ROOT / "data" / "datasets"
OUT = DS / "cut"
sys.path.insert(0, str(ROOT / "tools"))
from clap_features import FEATURES, HOP, NFFT, features, spectra  # noqa: E402

RATE = 16000
PRE, POST = int(0.3 * RATE), int(0.5 * RATE)
MAX_PER_FILE = 10  # щоб один довгий запис не став половиною набору
MIN_GAP = 12       # кадрів (120 мс) між ударами в одному файлі

# «Чистий» хлопок — у межах справжніх хлопків з INMP441 (data/labels.csv, 17 шт., ~10-й перцентиль):
# дзвінкий, не глухий, різкий фронт і спад за 100–150 мс. Швидкі серії й хлопки у фоні сюди не проходять.
def is_clean(v):
    return v["band_1_4k"] >= 0.3 and v["low_300"] <= 0.3 and v["rise"] >= 1.0 and v["decay"] <= -0.5 and v["level"] >= 0.5


def load(path):
    # ffmpeg читає mp3/wav/m4a однаково і одразу дає 16 кГц моно
    raw = subprocess.run(["ffmpeg", "-v", "quiet", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", str(RATE), "-"],
                         capture_output=True).stdout
    x = np.frombuffer(raw, "<i2").astype(float)
    return x - x.mean() if len(x) else x


def flux_curve(x):
    S = spectra(x)
    logS = np.log1p(S)
    flux = np.r_[0, np.maximum(0, np.diff(logS, axis=0)).sum(1)]
    return flux, S.sum(1)


def peaks(x):
    """Усі різкі удари у файлі: локальний максимум flux, помітно вище типового, і гучний відносно файлу."""
    flux, energy = flux_curve(x)
    if len(flux) < 10:
        return []
    thr = np.median(flux) + 4 * (np.median(np.abs(flux - np.median(flux))) + 1e-9)
    loud = energy.max() / 1000  # не тихіше за −30 дБ від найгучнішого
    found = []
    for t in np.argsort(-flux):
        if flux[t] < thr or len(found) >= MAX_PER_FILE * 3:
            break
        if energy[min(len(energy) - 1, t + 1)] < loud or any(abs(t - u) < MIN_GAP for u in found):
            continue
        found.append(int(t))
    return sorted(found)


def onset_near(x, sec):
    """AudioSet дає час із точністю ~0,1 с: беремо найрізкіший кадр у ±100 мс від мітки."""
    flux, _ = flux_curve(x)
    t0 = int(sec * RATE / HOP)
    a, b = max(1, t0 - 10), min(len(flux), t0 + 11)
    return a + int(flux[a:b].argmax()) if b > a else None


def window(x, frame):
    # Удар у кадрі frame; серединка кадру NFFT/2 — це і є момент удару
    c = frame * HOP + NFFT // 2
    seg = x[max(0, c - PRE):c + POST]
    if c - PRE < 0:
        seg = np.r_[np.zeros(PRE - c), seg]
    if len(seg) < PRE + POST:
        seg = np.r_[seg, np.zeros(PRE + POST - len(seg))]
    return seg


def save(path, seg):
    peak = np.abs(seg).max()
    if peak > 32767:  # після декодування mp3 буває трохи за межею
        seg = seg * (32767 / peak)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(RATE)
        w.writeframes(np.round(seg).astype("<i2").tobytes())


def jobs():
    """(джерело, файл, список кадрів-ударів або часів у с)."""
    ev = defaultdict(list)
    tsv = DS / "audioset_strong_clapping" / "events.tsv"
    if tsv.exists():
        for seg, a, _b, _split in csv.reader(tsv.open(), delimiter="\t"):
            ev[seg].append(float(a))
    for p in sorted((DS / "audioset_strong_clapping" / "raw").glob("*.wav")):
        yield "audioset", p, sorted(set(ev.get(p.stem, [])))[:MAX_PER_FILE]
    for src, d in [("fsd50k", "fsd50k_clapping/raw"), ("esc50", "esc50_clapping"), ("freesound", "freesound_search/raw")]:
        for p in sorted((DS / d).glob("*")):
            if p.suffix in (".mp3", ".wav"):
                yield src, p, None


def main():
    rows = []
    for src, path, times in jobs():
        x = load(path)
        if len(x) < RATE // 50:
            continue
        if times is None and len(x) < RATE // 2:
            # обрізаний one-shot (пак SMG, 0,05–0,2 с): тиша навколо, щоб удар знайшовся; фон додасть аугментація
            x = np.r_[np.zeros(PRE), x, np.zeros(POST)]
        frames = [onset_near(x, s) for s in times] if times is not None else peaks(x)
        frames = [f for f in dict.fromkeys(frames) if f is not None][:MAX_PER_FILE]
        for i, f in enumerate(frames):
            seg = window(x, f)
            name = f"{path.stem}_{i:02d}.wav"
            save(OUT / src / name, seg)
            v = features(seg, RATE)
            rows.append({"file": f"{src}/{name}", "source": src, "from": path.name,
                         "at_s": round(f * HOP / RATE, 3), "clean": int(is_clean(v)), **{k: round(v[k], 3) for k in FEATURES}})
        print(f"{src:9} {path.name:28} ударів {len(frames)}", flush=True)
    with (OUT / "manifest.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    by = defaultdict(lambda: [0, 0])
    for r in rows:
        by[r["source"]][0] += 1
        by[r["source"]][1] += r["clean"]
    print("\nкліпів (усього, чистих):", {k: tuple(v) for k, v in by.items()},
          "усього", len(rows), "чистих", sum(r["clean"] for r in rows))


if __name__ == "__main__":
    main()
