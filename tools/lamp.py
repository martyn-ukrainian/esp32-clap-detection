# /// script
# requires-python = ">=3.10"
# dependencies = ["cryptography"]
# ///
"""Лампи Ledvance SMART+ WiFi напряму по LAN (Tuya 3.3), без хмари і без tinytuya.

Еталон для прошивки ESP32: ті самі кадри, AES і CRC. `-v` друкує байти.

  uv run tools/lamp.py scan                 знайти лампи в мережі
  uv run tools/lamp.py status [lamp1]       стан
  uv run tools/lamp.py on|off|toggle [all]  керувати (за замовчуванням all)
"""
import json
import socket
import struct
import sys
import time
import zlib
from hashlib import md5
from pathlib import Path

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PORT = 6668
PREFIX, SUFFIX = 0x000055AA, 0x0000AA55
CMD_CONTROL, CMD_STATUS, CMD_DP_QUERY = 7, 8, 10
DP_POWER = "20"
DP_MODE, DP_BRIGHT, DP_COLOUR = "21", "22", "24"  # white/colour; яскравість білого 10–1000; колір HSV hex
VERSION_HEADER = b"3.3" + b"\0" * 12
# Ключ, яким усі Tuya 3.3 шифрують UDP-оголошення (публічно відомий)
UDP_KEY = md5(b"yGAdlopoPVldABfn").digest()

verbose = "-v" in sys.argv


def load_env() -> dict:
    env = {}
    for line in (Path(__file__).resolve().parent.parent / ".env").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        env[name.strip()] = value.strip().strip("'\"")
    return env


def lamps_from_env() -> dict:
    env = load_env()
    lamps = {}
    for name, value in env.items():
        if name.endswith("_ID"):
            prefix = name[: -len("_ID")]
            lamps[prefix.lower()] = {"id": value, "key": env[f"{prefix}_KEY"], "ip": env.get(f"{prefix}_IP")}
    return lamps


def aes(key: bytes, data: bytes, decrypt=False) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    if decrypt:
        d = cipher.decryptor()
        raw = d.update(data) + d.finalize()
        u = padding.PKCS7(128).unpadder()
        return u.update(raw) + u.finalize()
    p = padding.PKCS7(128).padder()
    e = cipher.encryptor()
    return e.update(p.update(data) + p.finalize()) + e.finalize()


def pack(seq: int, cmd: int, payload: bytes) -> bytes:
    # 55AA | seq | cmd | довжина (payload + crc + suffix) | payload | crc32 | AA55
    head = struct.pack(">4I", PREFIX, seq, cmd, len(payload) + 8) + payload
    return head + struct.pack(">2I", zlib.crc32(head), SUFFIX)


def unpack_payload(frame: bytes) -> bytes:
    # Відповідь пристрою: заголовок 16 байт, код повернення 4 байти, дані, crc, suffix
    data = frame[16:-8]
    if len(data) % 16 == 4 or data[4:7] == b"3.3":
        data = data[4:]
    if data.startswith(b"3.3"):
        data = data[15:]
    return data


def scan(seconds=7) -> dict:
    """Слухає UDP 6667: кожна лампа раз на ~5 с оголошує id, ip і версію."""
    found = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind(("", 6667))
    sock.settimeout(0.5)
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            frame, _ = sock.recvfrom(4096)
        except socket.timeout:
            continue
        try:
            info = json.loads(aes(UDP_KEY, unpack_payload(frame), decrypt=True))
        except Exception:
            continue
        found[info["gwId"]] = info
    sock.close()
    return found


class Lamp:
    def __init__(self, name, id, key, ip):
        self.name, self.id, self.key, self.ip = name, id, key.encode(), ip
        self.seq = 0

    def _call(self, cmd: int, body: dict) -> dict | None:
        body = {"devId": self.id, "uid": self.id, "t": str(int(time.time())), **body}
        plain = json.dumps(body, separators=(",", ":")).encode()
        payload = aes(self.key, plain)
        if cmd == CMD_CONTROL:
            payload = VERSION_HEADER + payload
        self.seq += 1
        frame = pack(self.seq, cmd, payload)
        if verbose:
            print(f"  → {plain.decode()}\n  → {frame.hex()}")
        with socket.create_connection((self.ip, PORT), timeout=3) as s:
            s.sendall(frame)
            # Буває порожнє підтвердження, а стан приходить окремим кадром
            for _ in range(3):
                head = s.recv(16)
                if len(head) < 16:
                    return None
                _, _, rcmd, length = struct.unpack(">4I", head)
                rest = b""
                while len(rest) < length:
                    rest += s.recv(length - len(rest))
                data = unpack_payload(head + rest)
                if data:
                    reply = json.loads(aes(self.key, data, decrypt=True))
                    if verbose:
                        print(f"  ← cmd {rcmd}: {reply}")
                    return reply
        return None

    def status(self) -> dict:
        return self._call(CMD_DP_QUERY, {"gwId": self.id}).get("dps", {})

    def set_power(self, on: bool):
        self._call(CMD_CONTROL, {"dps": {DP_POWER: on}})

    def set_max(self):
        """Увімкнути на повну, не міняючи режиму: білий → яскравість 1000, колір → V=1000 (H і S ті самі)."""
        dps = self.status()
        if dps.get(DP_MODE) == "colour" and len(dps.get(DP_COLOUR, "")) == 12:
            update = {DP_COLOUR: dps[DP_COLOUR][:8] + "03e8"}  # HHHH SSSS VVVV
        else:
            update = {DP_BRIGHT: 1000}
        self._call(CMD_CONTROL, {"dps": {DP_POWER: True, **update}})

    def toggle(self) -> bool:
        on = not self.status().get(DP_POWER, False)
        self.set_power(on)
        return on


def main():
    args = [a for a in sys.argv[1:] if a != "-v"]
    cmd = args[0] if args else "status"
    target = args[1] if len(args) > 1 else "all"
    lamps = lamps_from_env()

    if cmd == "scan" or any(not l["ip"] for l in lamps.values()):
        found = scan()
        names = {l["id"]: n for n, l in lamps.items()}
        for gw, info in found.items():
            if gw in names:
                lamps[names[gw]]["ip"] = lamps[names[gw]]["ip"] or info["ip"]
        if cmd == "scan":
            for gw, info in found.items():
                print(f"{names.get(gw, '?'):6} {info['ip']:15} {gw}  v{info.get('version')}  {info.get('productKey')}")
            return

    selected = lamps.items() if target == "all" else [(target, lamps[target])]
    for name, l in selected:
        if not l["ip"]:
            print(f"{name}: не знайдена в мережі")
            continue
        lamp = Lamp(name, l["id"], l["key"], l["ip"])
        if cmd == "status":
            dps = lamp.status()
            print(f"{name} {l['ip']}: {'увімк' if dps.get(DP_POWER) else 'вимк'}  {dps}")
        elif cmd in ("on", "off"):
            lamp.set_power(cmd == "on")
            print(f"{name}: {cmd}")
        elif cmd == "toggle":
            print(f"{name}: {'увімк' if lamp.toggle() else 'вимк'}")
        else:
            print(__doc__)
            return


if __name__ == "__main__":
    main()
