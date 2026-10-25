---
license: mit
tags:
  - audio-classification
  - tinyml
  - esp32
  - clap-detection
  - logistic-regression
pipeline_tag: audio-classification
---

# esp32-clap-detection: a 100-byte clap detector for ESP32

> Version 1.4 (24.10.2026), trained on 174 clips from the device. The cross-validation numbers below come from recorded clips; a live on-device test is in progress. Numbers will change as more negatives are recorded.

Two claps toggle the light. An ESP32-S3 with an INMP441 I²S microphone listens on its own: no cloud, no wake-word engine, no neural network. This repo holds the model that decides "was that a clap?".

The model is a logistic regression over 8 hand-made features of the sound around a sharp onset. It is **25 numbers (100 bytes as float32)**. For scale, YAMNet is ~3.7 MB.

## How it works

1. The board keeps the last second of audio (16 kHz, 16-bit) in a ring buffer.
2. When a 10 ms window jumps over a peak threshold, the board waits 150 ms and takes **0.3 s before the onset + 0.15 s after** (0.45 s).
3. It computes a short-time spectrum (256-point FFT, Hann window, 10 ms hop) and 8 features at the strongest onset.
4. Features are standardized (`(x − mean) / scale`) and fed to `sigmoid(w · z + b)`. `p ≥ 0.5` means clap.
5. A separate rule turns claps into intent: **two claps 150–700 ms apart** toggle the light; three or more (applause) do nothing.

| Feature | What it measures | Weight* |
|---|---|---|
| `flux` | suddenness: how much the spectrum grew in 10 ms (log) | +0.40 |
| `band_1_4k` | brightness: share of energy in 1–4 kHz in the 30 ms around the onset | +1.08 |
| `low_300` | dullness: share of energy below 300 Hz in the same 30 ms | −0.84 |
| `width` | share of 500 Hz bands that grew by 6+ dB at the onset | −0.79 |
| `decay` | energy 100–150 ms after the onset relative to the onset (log) | −0.92 |
| `rise` | onset energy relative to 20 ms before (log) | −0.06 |
| `overload` | largest jump between neighbouring samples (mic overload) | +0.72 |
| `level` | onset energy over the background before it (log) | +0.06 |

\* on standardized features; `+` pushes towards "clap". In plain words: a clap is sudden, bright (1–4 kHz), not boomy, and dies out fast. A thump on the bed is boomy; speech is not sudden and does not die out.

## Training data

Only recordings from the target device (INMP441 in a real room), so the licence is clean:

| Label | Clips |
|---|---|
| single clap | 59 |
| two claps | 49 |
| keyboard typing | 25 |
| thump / bed | 8 |
| finger snaps | 4 |
| speech | 2 |
| other negatives | 27 |
| **total** | **174** (108 claps, 66 not) |

Clips are labelled by recording sessions with a countdown page and by listening in a browser. Audio is **not** published: it contains speech from a private home.

Two public datasets were tried as an extra source (ESC-50, FSD50K subset, 4166 clips, augmented to sound like the INMP441: random gain with clipping, room noise, DC high-pass, 16-bit rail). They did **not** help: a model trained only on them catches 92/108 claps but fires on 26/66 negatives. Studio claps and studio keyboards do not sound like a cheap MEMS mic in a real room. They are also CC BY-NC, so they are not part of this model.

## Evaluation

5-fold stratified cross-validation on the 174 device recordings (each clip scored by a model that did not see it):

| | Claps caught | False alarms |
|---|---|---|
| this model | 96 / 108 (89%) | 10 / 66 (15%) |
| old firmware, peak threshold only (first 37 clips, not the same set) | 16 / 17 | 8 / 20 (40%) |

## Limitations

- **Keyboard typing at 30 cm sounds like a clap**, to the model and to a human listening to the clip. Distance helps: from 2–3 m a keystroke is much quieter than a clap. The two-clap rule with silence around the pair removes most of the rest.
- **Finger snaps** are the hardest negative so far (2 of 4 misfire). More snaps are being recorded.
- 66 negatives is small; the false-alarm number will move by a few percent with every new batch.
- Tuned for one microphone (INMP441) and one room.

## Use

```python
import json, numpy as np
from clap_features import features, read_wav   # tools/clap_features.py in the GitHub repo

m = json.load(open("clap_model.json"))
x, rate = read_wav("clip.wav")                 # 16 kHz mono int16, onset at 0.3 s, 0.45 s long
v = features(x, rate)
z = (np.array([v[k] for k in m["features"]]) - m["mean"]) / m["scale"]
p = 1 / (1 + np.exp(-(z @ m["weights"] + m["bias"])))
print("clap" if p >= m["threshold"] else "not a clap", round(float(p), 2))
```

On the ESP32 the same math runs in plain C (firmware in the GitHub repo).

## Links

- Code, firmware and training scripts: [github.com/martyn-ukrainian/esp32-clap-detection](https://github.com/martyn-ukrainian/esp32-clap-detection)
- Hardware: ESP32-S3-DevKitC N16R8, INMP441, Tuya-based Wi-Fi lamps behind a small FastAPI server on a Raspberry Pi.
