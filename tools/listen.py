# /// script
# requires-python = ">=3.10"
# dependencies = ["pyserial"]
# ///
"""Записує гучні звуки з ESP32 у data/AAAA-MM-DD.csv, щоб підбирати поріг на справжніх звуках.

  uv run tools/listen.py [поріг піку]     за замовчуванням 1000000

Тиша не пишеться. Вікно з піком понад третину порогу → рядок CSV: час Mac, rms, пік, хлопок (так/ні).
«Майже хлопки» теж потрібні: на них видно, чи не зависокий поріг.
Поки працює, порт зайнятий: перед `pio run -t upload` зупинити (Ctrl+C).
"""
import glob
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import serial

LINE = re.compile(r"rms\s+(\d+)\s+пік\s+(\d+)")
DATA = Path(__file__).resolve().parent.parent / "data"


def open_port() -> serial.Serial:
    while True:
        ports = glob.glob("/dev/cu.usbmodem*")
        if ports:
            try:
                port = serial.Serial(ports[0], 115200, timeout=1)
                port.dtr = False  # RTS/DTR тримають плату в reset
                port.rts = False
                print(f"слухаю {ports[0]}")
                return port
            except serial.SerialException:
                pass
        time.sleep(2)


def main():
    threshold = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
    sys.stdout.reconfigure(line_buffering=True)
    DATA.mkdir(exist_ok=True)
    port = open_port()
    while True:
        try:
            raw = port.readline()
        except serial.SerialException:
            print("плата відключилась, чекаю")
            port = open_port()
            continue
        match = LINE.search(raw.decode(errors="replace"))
        if not match or int(match[2]) < threshold // 3:
            continue
        now = datetime.now()
        rms, peak = int(match[1]), int(match[2])
        path = DATA / f"{now:%Y-%m-%d}.csv"
        new = not path.exists()
        with path.open("a") as f:
            if new:
                f.write("time,rms,peak,clap\n")
            f.write(f"{now:%H:%M:%S.%f}"[:-3] + f",{rms},{peak},{'так' if peak > threshold else 'ні'}\n")
        if peak > threshold:
            print(f"{now:%H:%M:%S.%f}"[:-3] + f"  хлопок?  пік {peak:8d}  rms {rms:8d}")


if __name__ == "__main__":
    main()
