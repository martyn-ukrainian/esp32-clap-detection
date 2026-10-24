# /// script
# requires-python = ">=3.10"
# ///
"""Витягнути з WavCaps (HuggingFace) лише потрібні сегменти AudioSet strong, не качаючи архів на 36 ГБ.

  uv run tools/fetch_wavcaps.py data/datasets/audioset_strong_clapping

Бере segments.txt (id_відео_мс) з теки, кладе raw/<сегмент>.wav (16 кГц моно). Уже наявні пропускає.
YouTube для AudioSet блокує після сотні сегментів, а WavCaps має ті самі 10-с шматки (AudioSet_SL, 108 тис. flac).
Архів розбитий на 7 частин (.z01….zip): читаємо його зміст із кінця .zip і качаємо кожен файл окремим HTTP Range.
"""
import json
import struct
import subprocess
import sys
import time
import urllib.request
import zlib
from pathlib import Path

BASE = "https://huggingface.co/datasets/cvssp/WavCaps/resolve/main/Zip_files/AudioSet_SL/AudioSet_SL."
PARTS = ["z01", "z02", "z03", "z04", "z05", "z06", "zip"]  # номер диска в zip = індекс тут


def get(part, start, length):
    req = urllib.request.Request(BASE + part, headers={"Range": f"bytes={start}-{start + length - 1}"})
    for attempt in range(5):  # CDN HuggingFace інколи не відповідає вчасно
        try:
            return urllib.request.urlopen(req, timeout=120).read()
        except OSError as e:
            print(f"  повтор {attempt + 1}: {e}", flush=True)
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"не скачалось: {part} {start}")


def size(part):
    req = urllib.request.Request(BASE + part, method="HEAD")
    return int(urllib.request.urlopen(req, timeout=60).headers["Content-Length"])


def read_span(sizes, disk, off, n):
    """n байтів від (disk, off); файл може перейти межу частини."""
    out = b""
    while n > 0:
        take = min(n, sizes[disk] - off)
        out += get(PARTS[disk], off, take)
        n, disk, off = n - take, disk + 1, 0
    return out


def central_directory(cache):
    if cache.exists():
        return json.loads(cache.read_text())
    last = size("zip")
    tail = get("zip", last - 65536, 65536)
    k = tail.rfind(b"PK\x06\x06")  # Zip64: записів понад 65535
    _, _, _, _, _, cd_disk, _, n, cd_size, cd_off = struct.unpack("<IQHHIIQQQQ", tail[k:k + 56])
    c = get(PARTS[cd_disk], cd_off, cd_size)
    entries, p = {}, 0
    while c[p:p + 4] == b"PK\x01\x02":
        (_, _, _, _, meth, _, _, _, cs, us, nl, el, kl, dk, _, _, off) = struct.unpack("<IHHHHHHIIIHHHHHII", c[p:p + 46])
        name = c[p + 46:p + 46 + nl].decode()
        ex, q = c[p + 46 + nl:p + 46 + nl + el], 0
        while q < len(ex):  # Zip64 extra: справжні розміри й зсув, якщо в полі 0xFFFFFFFF
            hid, hs = struct.unpack("<HH", ex[q:q + 4])
            d, r = ex[q + 4:q + 4 + hs], 0
            if hid == 1:
                if us == 0xFFFFFFFF:
                    us, r = struct.unpack("<Q", d[r:r + 8])[0], r + 8
                if cs == 0xFFFFFFFF:
                    cs, r = struct.unpack("<Q", d[r:r + 8])[0], r + 8
                if off == 0xFFFFFFFF:
                    off, r = struct.unpack("<Q", d[r:r + 8])[0], r + 8
                if dk == 0xFFFF:
                    dk = struct.unpack("<I", d[r:r + 4])[0]
            q += 4 + hs
        if name.endswith(".flac"):
            entries[name.rsplit("/", 1)[-1][1:-5]] = [meth, cs, dk, off]  # «Y<id>.flac» → id
        p += 46 + nl + el + kl
    cache.write_text(json.dumps(entries))
    return entries


def main():
    root = Path(sys.argv[1])
    raw = root / "raw"
    raw.mkdir(exist_ok=True)
    entries = central_directory(root / "wavcaps_index.json")
    sizes = [size(p) for p in PARTS]
    segs = [s.strip() for s in (root / "segments.txt").read_text().split()]
    got = miss = 0
    for seg in segs:
        if (raw / f"{seg}.wav").exists():
            continue
        e = entries.get(seg.rsplit("_", 1)[0])
        if not e:
            miss += 1
            continue
        meth, cs, disk, off = e
        head = read_span(sizes, disk, off, 30)
        nl, el = struct.unpack("<HH", head[26:30])
        start = off + 30 + nl + el
        if start >= sizes[disk]:
            disk, start = disk + 1, start - sizes[disk]
        data = read_span(sizes, disk, start, cs)
        flac = zlib.decompress(data, -15) if meth == 8 else data
        subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-i", "-", "-ac", "1", "-ar", "16000", str(raw / f"{seg}.wav")],
                       input=flac, check=True)
        got += 1
        print(f"{got:4} {seg}", flush=True)
    print(f"готово: нових {got}, немає у WavCaps {miss}")


if __name__ == "__main__":
    main()
