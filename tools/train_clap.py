# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "scikit-learn"]
# ///
"""Навчити маленьку модель «хлопок / не хлопок» на розмічених кліпах (data/labels.csv).

  uv run tools/train_clap.py

Модель — логістична регресія на ознаках із clap_features.py: на платі це 8 множень і одна експонента.
Перевірка чесна: кожен кліп оцінює модель, яка його не бачила (leave-one-out).
Результат: data/clap_model.json (середні, масштаби, ваги — те, що піде в прошивку)
і data/clap_scores.csv (ймовірність хлопка для кожного кліпу, зокрема нерозмічених).
"""
import csv
import glob
import json
import sys
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import LeaveOneOut, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "tools"))
from clap_features import FEATURES, WHY, features, read_wav  # noqa: E402

CLAP = {"хлопок", "два хлопки"}


def main():
    labels = {}
    with (DATA / "labels.csv").open() as f:
        for row in csv.DictReader(f):
            labels[row["file"]] = row["label"]  # останній рядок перемагає

    files = sorted(str(Path(p).relative_to(DATA)) for p in glob.glob(str(DATA / "clips" / "*" / "*.wav")))
    X_all = np.array([[features(*read_wav(str(DATA / f)))[k] for k in FEATURES] for f in files])
    known = [i for i, f in enumerate(files) if f in labels]
    X = X_all[known]
    y = np.array([labels[files[i]] in CLAP for i in known])
    print(f"розмічено {len(y)}: хлопків {y.sum()}, не хлопків {(~y).sum()}")

    model = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, class_weight="balanced"))
    p = cross_val_predict(model, X, y, cv=LeaveOneOut(), method="predict_proba")[:, 1]
    pred = p >= 0.5
    print(f"leave-one-out: хлопків почула {(pred & y).sum()}/{y.sum()}, хибних {(pred & ~y).sum()}/{(~y).sum()}")
    for i, k in enumerate(known):
        if pred[i] != y[i]:
            print(f"  помилка: {files[k]}  мітка «{labels[files[k]]}»  ймовірність хлопка {p[i]:.2f}")

    model.fit(X, y)
    scaler, lr = model[0], model[1]
    print("\nваги (на стандартизованих ознаках; + за хлопок, − проти):")
    for k, w in sorted(zip(FEATURES, lr.coef_[0]), key=lambda kv: -abs(kv[1])):
        print(f"  {w:+.2f}  {k:10}  {WHY[k]}")

    (DATA / "clap_model.json").write_text(json.dumps({
        "features": FEATURES, "mean": scaler.mean_.tolist(), "scale": scaler.scale_.tolist(),
        "weights": lr.coef_[0].tolist(), "bias": float(lr.intercept_[0]), "threshold": 0.5,
        "trained_on": len(y),
    }, ensure_ascii=False, indent=2))

    scores = model.predict_proba(X_all)[:, 1]
    with (DATA / "clap_scores.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["file", "p_clap", "label"])
        for fname, s in zip(files, scores):
            w.writerow([fname, f"{s:.3f}", labels.get(fname, "")])
    unl = [s for fname, s in zip(files, scores) if fname not in labels]
    print(f"\nнерозмічених {len(unl)}: модель вважає хлопками {sum(s >= 0.5 for s in unl)}, "
          f"не впевнена (0,2–0,8) у {sum(0.2 < s < 0.8 for s in unl)}")


if __name__ == "__main__":
    main()
