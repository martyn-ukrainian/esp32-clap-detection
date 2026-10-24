# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "scipy"]
# ///
"""FSD50K (дзеркало Fhrozen/FSD50k на Hugging Face) → кліпи у форматі плати, як prepare_esc50.py.

  cd train/data/raw/fsd50k && ./download.sh     кліпи з selection.csv (вибір класів і ліцензії — там же)
  uv run train/prepare_fsd50k.py

Результат: train/data/fsd50k/<клас>/<id>.wav і train/data/fsd50k/manifest.csv (ліцензія й автор для кожного кліпу).
"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "train" / "data" / "raw" / "fsd50k"
OUT = ROOT / "train" / "data" / "fsd50k"
sys.path.insert(0, str(ROOT / "train"))
from prepare_esc50 import cut_and_save, load  # noqa: E402


def main():
    rows, missing = [], 0
    with (SRC / "selection.csv").open() as f:
        for row in csv.DictReader(f):
            src = SRC / "audio" / f"{row['fname']}.wav"
            if not src.exists():
                missing += 1
                continue
            dst = OUT / row["category"] / f"{row['fname']}.wav"
            if not cut_and_save(load(src), dst):
                continue
            rows.append({"file": str(dst.relative_to(OUT)), "category": row["category"], "role": row["role"],
                         "fold": row["split"], "source": "FSD50K", "license": row["license"],
                         "uploader": row["uploader"]})
    with (OUT / "manifest.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    pos = sum(r["role"] == "positive" for r in rows)
    print(f"{len(rows)} кліпів: позитивів {pos}, негативів {len(rows) - pos}; не скачано {missing} → {OUT}")


if __name__ == "__main__":
    main()
