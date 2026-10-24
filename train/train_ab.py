# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "scipy", "scikit-learn"]
# ///
"""Дві моделі хлопка й чесне порівняння на справжніх кліпах з плати.

  uv run train/train_ab.py

A — лише набори (ESC-50, FSD50K), «перекладені» на наш мікрофон програмно (аугментація нижче).
B — лише кліпи з INMP441: розмічені на сторінці tools/label.py (data/labels.csv)
    і записані через /record (мітка в імені файлу: ..._датасет__хлопок_007.wav).
A+B — разом.
Перевірка завжди на справжніх кліпах з плати: B і A+B — 5 частин по черзі (кліп не бачила під час навчання), A їх не бачила взагалі.
"""
import csv
import glob
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from scipy.signal import lfilter
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SETS = [ROOT / "train" / "data" / "esc50", ROOT / "train" / "data" / "fsd50k"]
sys.path.insert(0, str(ROOT / "tools"))
from clap_features import FEATURES, features, read_wav  # noqa: E402

CLAP = {"хлопок", "два хлопки", "два_хлопки"}
SKIP = "виключити"  # мітка «сам не знаю, що там»: кліп у навчання не йде
QUIET = 1500  # кліп сесії /record тихіший за це — плата зловила фон між повторами, не сам звук
AUG_PER_CLIP = 3
BOARD_PRE, BOARD_POST = 0.3, 0.15  # плата вирішує через 150 мс після удару, бачить 0,3 с до нього (= CLAP_VIEW_POST_MS)
rng = np.random.default_rng(7)


def board_view(x, rate):
    # Шматок кліпу, який бачить плата: удар стоїть на 0,3 с (звичайні кліпи, набори) або на 0,5 с (датасетні, 2 с)
    pre = 0.5 if len(x) >= 1.5 * rate else 0.3
    a = int((pre - BOARD_PRE) * rate)
    v = x[a:a + int((BOARD_PRE + BOARD_POST) * rate)]
    return v - v.mean()  # як у прошивці: середнє лише по цьому шматку


def feats(x, rate) -> list:
    v = features(board_view(x, rate), rate)
    return [v[k] for k in FEATURES]


def room_noise() -> list:
    # Фон кімнати: тихі кліпи з плати, де нічого не сталось
    pool = []
    for p in glob.glob(str(DATA / "clips" / "*" / "*.wav")):
        x, _ = read_wav(p)
        if np.abs(x).max() < 1500:
            pool.append(x)
    return pool


def as_our_mic(x, noise_pool):
    # Студійний кліп → як його почув би INMP441 у кімнаті
    y = x / (np.abs(x).max() + 1e-9)
    y *= 32767 * 10 ** (rng.uniform(-30, 6) / 20)          # від тихого до перевантаження (+6 дБ над стелею)
    if noise_pool:
        n = noise_pool[rng.integers(len(noise_pool))]
        y = y + np.resize(n, len(y)) * rng.uniform(0.5, 2)
    y = lfilter([1, -1], [1, -0.995], y)                     # INMP441 і DC-фільтр прошивки: нижче ~15 Гц немає
    return np.clip(np.round(y), -32768, 32767)              # 16 біт і стеля, як у level.cpp


def datasets(noise_pool):
    X, y = [], []
    rows = []
    for root in SETS:
        if (root / "manifest.csv").exists():
            with (root / "manifest.csv").open() as f:
                rows += [(root / row["file"], row["role"] == "positive") for row in csv.DictReader(f)]
    for path, positive in rows:
        x, r = read_wav(str(path))
        for _ in range(AUG_PER_CLIP):
            X.append(feats(as_our_mic(x, noise_pool), r))
            y.append(positive)
    return np.array(X), np.array(y)


def real_set():
    labels = {}
    with (DATA / "labels.csv").open() as f:
        for row in csv.DictReader(f):
            labels[row["file"]] = row["label"]
    # Записи /record: мітка сесії в імені файлу
    for p in glob.glob(str(DATA / "clips" / "*" / "*__*_[0-9][0-9][0-9].wav")):
        rel = str(Path(p).relative_to(DATA))
        if rel in labels:
            continue
        x, _ = read_wav(p)
        if np.abs(x).max() < QUIET:
            continue
        labels[rel] = Path(p).stem.split("__")[1].rsplit("_", 1)[0]
    files = sorted(f for f in labels if labels[f] != SKIP)
    X = np.array([feats(*read_wav(str(DATA / f))) for f in files])
    print("справжні з плати за мітками:", dict(Counter(labels[f] for f in files)))
    return X, np.array([labels[f] in CLAP for f in files]), files


def new_model():
    return make_pipeline(StandardScaler(), LogisticRegression(C=1.0, class_weight="balanced", max_iter=1000))


MODEL_VERSION = "1.4"  # модель B на баченні плати (0,3 + 0,15 с); 1.2 — те саме з 0,25 с; зовнішні хлопки (1.3) гірші, див. README


def save_b(X, y):
    # B на всіх справжніх кліпах → data/clap_model.json (tools/tester.py) і заголовок для прошивки
    m = new_model().fit(X, y)
    scaler, lr = m[0], m[1]
    arr = lambda v: "{" + ", ".join(f"{a:.7g}f" for a in v) + "}"
    (ROOT / "firmware" / "src" / "detect" / "clap_model_params.h").write_text(
        f"// Згенеровано train/train_ab.py — не правити руками. Модель B {MODEL_VERSION}, {len(y)} кліпів з плати.\n"
        "// Порядок ознак: " + ", ".join(FEATURES) + "\n#pragma once\n\n"
        f'#define CLAP_MODEL_VERSION "{MODEL_VERSION}"\n'
        f"static const float CLAP_MODEL_MEAN[8] = {arr(scaler.mean_)};\n"
        f"static const float CLAP_MODEL_SCALE[8] = {arr(scaler.scale_)};\n"
        f"static const float CLAP_MODEL_WEIGHTS[8] = {arr(lr.coef_[0])};\n"
        f"static const float CLAP_MODEL_BIAS = {lr.intercept_[0]:.7g}f;\n")
    (DATA / "clap_model.json").write_text(json.dumps({
        "model": "B", "version": MODEL_VERSION, "features": FEATURES, "mean": scaler.mean_.tolist(), "scale": scaler.scale_.tolist(),
        "weights": lr.coef_[0].tolist(), "bias": float(lr.intercept_[0]), "threshold": 0.5,
        "trained_on": len(y),
    }, ensure_ascii=False, indent=2))


def report(name, p, y):
    pred = p >= 0.5
    print(f"{name:28} хлопків почула {(pred & y).sum():2}/{y.sum()}   хибних {(pred & ~y).sum():2}/{(~y).sum()}")


def main():
    noise = room_noise()
    Xe, ye = datasets(noise)
    Xr, yr, files = real_set()
    print(f"набори: {len(ye)} прикладів (хлопків {ye.sum()}); фон кімнати: {len(noise)} тихих кліпів")
    print(f"справжні з плати: {len(yr)} (хлопків {yr.sum()})\n")

    a = new_model().fit(Xe, ye)
    report("A: лише набори", a.predict_proba(Xr)[:, 1], yr)

    pb, pab = np.zeros(len(yr)), np.zeros(len(yr))
    for keep, test in StratifiedKFold(5, shuffle=True, random_state=7).split(Xr, yr):
        pb[test] = new_model().fit(Xr[keep], yr[keep]).predict_proba(Xr[test])[:, 1]
        Xab = np.vstack([Xe, Xr[keep]])
        yab = np.r_[ye, yr[keep]]
        w = np.r_[np.full(len(ye), 0.2), np.full(len(keep), 1.0)]  # свої кліпи важать більше
        m = new_model()
        m.fit(Xab, yab, logisticregression__sample_weight=w)
        pab[test] = m.predict_proba(Xr[test])[:, 1]
    report("B: лише наш мікрофон", pb, yr)
    save_b(Xr, yr)
    report("A+B", pab, yr)

    print("\nде помиляються (ймовірність хлопка A / B / A+B):")
    pa = a.predict_proba(Xr)[:, 1]
    for i, f in enumerate(files):
        wrong = [(p >= 0.5) != yr[i] for p in (pa[i], pb[i], pab[i])]
        if any(wrong):
            mark = " ".join(("✗" if w else "·") for w in wrong)
            print(f"  {mark}  {Path(f).name[:10]}  {'хлопок' if yr[i] else 'не хлопок':10} {pa[i]:.2f} / {pb[i]:.2f} / {pab[i]:.2f}")


if __name__ == "__main__":
    main()
