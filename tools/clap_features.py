# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""Ознаки хлопка з кліпу: те, що видно на docs/sound-pictures.png, у числах. Власний код, не pector.

  uv run tools/clap_features.py data/clips      таблиця ознак для кожного кліпу

Кліп 16 кГц int16. Звук ріжеться на кадри по 10 мс (як вікно детектора на платі), кожен кадр → спектр.
Удар = кадр, де спектр найбільше виріс порівняно з попереднім (spectral flux). Далі все міряємо навколо нього.
"""
import glob
import sys
import wave

import numpy as np

HOP = 160    # 10 мс при 16 кГц
NFFT = 256   # кадр 16 мс з перекриттям: досить, щоб бачити 1–4 кГц окремо від низьких
BANDS = 16   # смуги по 500 Гц для «ширини»

FEATURES = ["flux", "band_1_4k", "low_300", "width", "decay", "rise", "overload", "level"]
WHY = {
    "flux": "раптовість: наскільки спектр виріс за 10 мс (лог)",
    "band_1_4k": "дзвінкість: частка 1–4 кГц у 30 мс навколо удару",
    "low_300": "глухість: частка нижче 300 Гц у тих самих 30 мс",
    "width": "ширина: частка смуг по 500 Гц, що виросли на 6+ дБ в момент удару",
    "decay": "спад: енергія через 100–150 мс відносно удару (лог)",
    "rise": "фронт: енергія удару відносно кадру за 20 мс до нього (лог)",
    "overload": "перевантаження: найбільший стрибок між сусідніми семплами, частка діапазону",
    "level": "гучність удару над фоном до нього (лог)",
}


def read_wav(path):
    with wave.open(path) as w:
        rate = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(float)
    return x - x.mean(), rate


def spectra(x):
    # Кадр t починається з t*HOP; Hann, щоб краї кадру не давали фальшивих частот
    win = np.hanning(NFFT)
    n = max(0, (len(x) - NFFT) // HOP + 1)
    frames = np.stack([x[t * HOP:t * HOP + NFFT] * win for t in range(n)])
    return np.abs(np.fft.rfft(frames, axis=1)) ** 2


def features(x, rate) -> dict:
    S = spectra(x)
    f = np.fft.rfftfreq(NFFT, 1 / rate)
    logS = np.log1p(S)
    flux = np.r_[0, np.maximum(0, np.diff(logS, axis=0)).sum(1)]
    flux[:3] = 0  # перші кадри без історії
    t = int(flux.argmax())

    around = S[max(0, t - 1):t + 2].sum(0)  # ~30 мс навколо удару
    total = around.sum() + 1e-9
    energy = S.sum(1) + 1e-9

    edges = np.linspace(0, rate / 2, BANDS + 1)
    band_now = np.array([S[t, (f >= a) & (f < b)].sum() for a, b in zip(edges, edges[1:])]) + 1e-9
    band_before = np.array([S[max(0, t - 1), (f >= a) & (f < b)].sum() for a, b in zip(edges, edges[1:])]) + 1e-9
    grew = 10 * np.log10(band_now / band_before) > 6

    after = energy[t + 10:t + 15]
    noise = np.median(energy[:max(1, t - 3)])
    jumps = np.abs(np.diff(x)).max() / 65535

    return {
        "flux": float(np.log1p(flux[t])),
        "band_1_4k": float(around[(f >= 1000) & (f <= 4000)].sum() / total),
        "low_300": float(around[f < 300].sum() / total),
        "width": float(grew.mean()),
        "decay": float(np.log10((after.mean() if len(after) else energy[-1]) / energy[t])),
        "rise": float(np.log10(energy[t] / energy[max(0, t - 2)])),
        "overload": float(jumps),
        "level": float(np.log10(energy[t] / noise)),
    }


if __name__ == "__main__":
    print("файл\t" + "\t".join(FEATURES))
    for p in sorted(glob.glob(sys.argv[1] + "/*/*.wav")):
        x, r = read_wav(p)
        v = features(x, r)
        print(p.split("/")[-1][:10] + "\t" + "\t".join(f"{v[k]:.2f}" for k in FEATURES))
