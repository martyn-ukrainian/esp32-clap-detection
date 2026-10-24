"""Сервер світла: ESP32 шле HTTP-запит, сервер перемикає лампи по LAN (Tuya 3.3).

Чіпаємо лише живлення (DP 20): яскравість і колір лампи пам'ятають самі.
Кожен запит пишеться в data/AAAA-MM-DD.csv. Усі роути з кнопками: /docs.

  uv run fastapi dev server/main.py --port 7076     локально
  docker compose up -d --build                      на Pi
"""
import asyncio
import re
import sys
import threading
import wave
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from lamp import DP_POWER, Lamp, lamps_from_env  # noqa: E402

DATA = ROOT / "data"
CLIP_MAX_BYTES = 16000 * 2 * 10  # 10 с при 16 кГц, 16 біт; більше плата не має слати

app = FastAPI(title="clap-light")
lock = threading.Lock()  # два хлопки підряд не мають перемикати лампи паралельно
lamps = [Lamp(name, l["id"], l["key"], l["ip"]) for name, l in lamps_from_env().items() if l["ip"]]
session = {"label": "", "trial": 0}  # запис датасету: що зараз робить людина і який це повтор
lights = {"enabled": True}  # False — /toggle від плати лише пишеться в лог, лампи не чіпає
clips_log = []  # останні кліпи для сторінки /record: чи почула плата повтор
UDP_PORT = 7077  # = PI_UDP_PORT у прошивці
udp_seen = []  # останні (boot, seq) з UDP, щоб повтор не перемкнув удруге
events_log = []  # рішення моделі на платі: /event?p=…&at=…&sent=…
net_best = [float("inf")]  # найменше (прийшла − sent) за весь час: «мережа без черги»
session_log = []  # (час, label, trial) кожної зміни сесії: кліп приходить ~2 с після звуку, повтор шукаємо за часом звуку


def log_event(request: Request, kind: str, extra: dict | None = None):
    # Один рядок на запит: час, звідки, що, параметри як key=value
    log_line(request.client.host, kind, {**request.query_params, **(extra or {})})


def log_line(source: str, kind: str, params: dict):
    now = datetime.now()
    DATA.mkdir(exist_ok=True)
    path = DATA / f"{now:%Y-%m-%d}.csv"
    new = not path.exists()
    with path.open("a") as f:
        if new:
            f.write("time,source,kind,params\n")
        details = " ".join(f"{k}={v}" for k, v in params.items())
        f.write(f"{now:%H:%M:%S.%f}"[:-3] + f",{source},{kind},{details}\n")


def power_states() -> dict:
    states = {}
    for lamp in lamps:
        try:
            states[lamp.name] = lamp.status().get(DP_POWER)
        except OSError as e:
            states[lamp.name] = f"помилка: {e}"
    return states


def set_all(on: bool):
    # Тимчасово: «увімкнути» = на максимальну яскравість
    for lamp in lamps:
        try:
            lamp.set_max() if on else lamp.set_power(False)
        except OSError as e:
            print(f"{lamp.name}: {e}")


@app.get("/status")
def status(request: Request) -> dict:
    """Стан ламп, нічого не міняє."""
    log_event(request, "status")
    with lock:
        return power_states()


@app.get("/toggle")
def toggle(request: Request) -> dict:
    """Хоч одна світить → вимкнути всі, інакше ввімкнути всі на максимум. Це шле плата на подвійний хлопок."""
    log_event(request, "toggle")
    return do_toggle()


def do_toggle() -> dict:
    if session["label"]:
        return {"skipped": f"іде запис «{session['label']}», світло не чіпаю"}
    if not lights["enabled"]:
        return {"skipped": "хлопки вимкнено (/lights?enabled=1 — увімкнути)"}
    with lock:
        set_all(not any(v is True for v in power_states().values()))
        return power_states()


@app.get("/lights")
def set_lights(request: Request, enabled: bool | None = None) -> dict:
    """Чи перемикає /toggle (від плати) лампи. /lights?enabled=0 — ні, лише лог; =1 — так. Без параметра — стан.

    /on, /off і /status працюють завжди.
    """
    if enabled is not None:
        lights["enabled"] = enabled
        log_event(request, "lights")
    return {"toggle_enabled": lights["enabled"]}


@app.get("/on")
def on(request: Request) -> dict:
    """Увімкнути всі на максимальну яскравість (режим і колір лишаються)."""
    log_event(request, "on")
    with lock:
        set_all(True)
        return power_states()


@app.get("/off")
def off(request: Request) -> dict:
    log_event(request, "off")
    with lock:
        set_all(False)
        return power_states()


@app.get("/event")
def event(request: Request) -> dict:
    """Подія від плати, напр. /event?peak=4200000&clap=1. Лише лог, лампи не чіпає."""
    if not request.query_params:
        raise HTTPException(400, "порожня подія")
    log_event(request, "event")
    record_event(request.query_params)
    return {"ok": True}


def record_event(q) -> None:
    if q.get("boot") == "1":
        net_best[0] = float("inf")  # плата перезапустилась: її годинник знову з нуля
    if "at" in q and "sent" in q:
        # Рішення моделі на платі (p) або пара хлопків (pair). Годинники плати й Pi різні, тому мережу міряємо
        # відносно найшвидшої події: (прийшла − sent) − мінімум цього ж = скільки ця подія затрималась у Wi-Fi
        now = datetime.now().timestamp()
        offset = now * 1000 - int(q["sent"])
        net_best[0] = min(net_best[0], offset)
        events_log.append({"at": now, "p": float(q.get("p", 1)), "pair": q.get("pair") == "1",
                           "board_ms": int(q["sent"]) - int(q["at"]), "net_ms": round(offset - net_best[0]),
                           "via": q.get("via", "http")})
        del events_log[:-200]


class BoardUdp(asyncio.DatagramProtocol):
    """UDP від плати (порт UDP_PORT): без з'єднання й черги, тож команда не чекає за кліпами й повільним Wi-Fi.

    Датаграма — один рядок: «toggle <boot> <seq>» або «event <boot> <seq> p=0.93 at=… sent=…» («pair=1» для пари).
    Плата шле кожну двічі; повтор з тим самим (boot, seq) відкидаємо.
    """

    def datagram_received(self, data, addr):
        parts = data.decode(errors="replace").split()
        if len(parts) < 3 or (parts[1], parts[2]) in udp_seen:
            return
        udp_seen.append((parts[1], parts[2]))
        del udp_seen[:-50]
        kind, params = parts[0], dict(p.split("=", 1) for p in parts[3:] if "=" in p)
        log_line(addr[0], f"udp_{kind}", {"seq": parts[2], **params})
        if kind == "toggle":
            # Лампи — мережеві виклики: не в циклі подій, щоб не гальмувати решту
            asyncio.get_running_loop().run_in_executor(None, do_toggle)
        elif kind == "event":
            record_event({**params, "via": "udp"})


@app.on_event("startup")
async def start_udp():
    await asyncio.get_running_loop().create_datagram_endpoint(BoardUdp, local_addr=("0.0.0.0", UDP_PORT))


@app.get("/session/events")
def session_events(since: float = 0) -> list:
    """Рішення моделі на платі після since (unix-час): для tools/tester.py."""
    return [e for e in events_log if e["at"] > since]


@app.get("/record", response_class=HTMLResponse)
def record_page():
    """Сторінка запису датасету: вибрати звук, відлік 1-2-зараз, по кожному повтору видно, чи прийшов кліп."""
    return (Path(__file__).parent / "record.html").read_text()


@app.get("/clips/{day}/{name}")
def clip_file(day: str, name: str):
    """Сам кліп (WAV) для tools/tester.py. Лише з data/clips/, без довільних шляхів."""
    path = DATA / "clips" / day / name
    if not re.fullmatch(r"\d{4}-\d\d-\d\d", day) or "/" in name or not name.endswith(".wav") or not path.is_file():
        raise HTTPException(404, "немає такого кліпу")
    return FileResponse(path, media_type="audio/wav")


@app.get("/session/clips")
def session_clips(since: float = 0) -> list:
    """Кліпи, що прийшли після since (unix-час): для сторінки /record."""
    return [c for c in clips_log if c["at"] > since]


@app.get("/session")
def set_session(request: Request, label: str = "", trial: int = 0) -> dict:
    """Запис датасету: /session?label=хлопок — усі наступні кліпи отримають цю мітку, а /toggle від плати не перемикає світло.

    /session без label — кінець запису, світло знову працює.
    """
    session["label"] = re.sub(r"[^\w-]", "_", label)[:32]
    session["trial"] = trial
    at = datetime.now().timestamp()
    session_log.append((at, session["label"], trial))
    del session_log[:-50]
    log_event(request, "session")
    return {"session": session["label"] or "вимкнено", "trial": trial, "at": at}


def session_at(t: float) -> tuple[str, int]:
    """Яка сесія й повтор були в момент t: остання зміна не пізніше t."""
    for at, label, trial in reversed(session_log):
        if at <= t:
            return label, trial
    return session["label"], session["trial"]


@app.post("/clip")
async def clip(request: Request, label: str, rate: int = 16000) -> dict:
    """Звук навколо кандидата: тіло — сирі семпли int16 LE моно, напр. POST /clip?label=хлопок&peak=4200000.

    label — що вирішив детектор (відкинуто / хлопок / два_хлопки). Інші параметри просто йдуть у лог.
    Зберігає data/clips/AAAA-MM-DD/ГГХХСС_мс_label.wav.
    """
    if not 8000 <= rate <= 48000:
        raise HTTPException(400, "rate поза 8000–48000")
    body = await request.body()
    if not body or len(body) % 2:
        raise HTTPException(400, "тіло має бути семплами int16")
    if len(body) > CLIP_MAX_BYTES:
        raise HTTPException(413, "кліп довший за 10 с")
    safe_label = re.sub(r"[^\w-]", "_", label)[:32]  # лише в назву файлу: без / і пробілів
    now = datetime.now()
    # Плата шле кліп, коли вже минув «хвіст» після звуку: 0,5 с у звичайній прошивці, 1,5 с у датасетній
    seconds = len(body) / 2 / rate
    tail = seconds - (0.5 if seconds >= 1.5 else 0.3)
    s_label, s_trial = session_at(now.timestamp() - tail - 0.3)
    if s_label:
        safe_label += f"__{s_label}"
        if s_trial:
            safe_label += f"_{s_trial:03d}"

    folder = DATA / "clips" / f"{now:%Y-%m-%d}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (f"{now:%H%M%S_%f}"[:-3] + f"_{safe_label}.wav")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(body)

    name = str(path.relative_to(DATA))
    seconds = round(seconds, 2)
    log_event(request, "clip", {"file": name, "sec": seconds, "session": s_label, "trial": s_trial})
    clips_log.append({"at": now.timestamp(), "file": name, "peak": request.query_params.get("peak", ""),
                      "session": s_label, "trial": s_trial})
    del clips_log[:-200]
    return {"ok": True, "file": name, "sec": seconds}
