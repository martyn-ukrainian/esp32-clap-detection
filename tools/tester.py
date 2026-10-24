# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "pyserial"]
# ///
"""Тестер моделі наживо: модель рахує сама плата й шле рішення на Pi (/event) → тут видно рішення і затримку. У браузері.
Кліпи йдуть окремо й пізніше: їх можна послухати й позначити помилку.

  uv run train/train_ab.py         спершу: зберігає модель B у data/clap_model.json
  uv run tools/tester.py           → http://127.0.0.1:8766
  SERIAL=/dev/cu.usbmodem… uv run tools/tester.py    рішення плати по USB-кабелю, без Wi-Fi

Кнопки «помилка?» пишуть мітку в data/labels.csv (як tools/label.py): потім rsync кліпів і перетренувати.
Плату не чіпає й світло не перемикає: лише бере нові кліпи з Pi (/session/clips, /clips/...) і рахує модель тут.
Кліп є лише тоді, коли плата щось почула (пік над порогом прошивки). Тиша = кліпів немає.
"""
import csv
import io
import json
import os
import sys
import threading
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from clap_features import features, read_wav  # noqa: E402

PI = os.environ.get("PI_URL", "http://home-pi.local:7076")
PORT = 8766
LABELS = ROOT / "data" / "labels.csv"
MODEL = json.loads((ROOT / "data" / "clap_model.json").read_text())
PAIR_MS = (150, 700)  # як intent.h у прошивці: пауза між хлопками пари

clips = []  # нові кліпи з моменту запуску, найсвіжіші в кінці
wavs = {}   # id → байти WAV, щоб програти в браузері
events = []  # рішення моделі на платі (/session/events): p, пара, затримки


def p_clap(x, rate) -> float:
    v = features(x, rate)
    z = (np.array([v[k] for k in MODEL["features"]]) - MODEL["mean"]) / MODEL["scale"]
    return float(1 / (1 + np.exp(-(z @ MODEL["weights"] + MODEL["bias"]))))


def onsets(x) -> list:
    # Удари: вікно 10 мс вище 15% піку після щонайменше 80 мс тиші. Мс від початку кліпу
    env = np.array([np.abs(x[i:i + 160]).max() for i in range(0, len(x), 160)])
    thr, hits, last = max(0.15 * env.max(), 1500), [], -99
    for k, e in enumerate(env):
        if e > thr and k - last > 8:
            hits.append(k * 10)
        if e > thr:
            last = k
    return hits


def judge(x, rate) -> dict:
    # Кожен удар окремо: вирізати 0,3 с до й 0,5 с після, як кліп звичайної прошивки
    hits = []
    for ms in onsets(x):
        a = max(0, int((ms - 300) * rate / 1000))
        part = x[a:a + int(0.8 * rate)]
        if len(part) > rate // 4:
            hits.append({"ms": ms, "p": round(p_clap(part, rate), 2)})
    claps = [h for h in hits if h["p"] >= MODEL["threshold"]]
    pair = any(PAIR_MS[0] <= b["ms"] - a["ms"] <= PAIR_MS[1] for a, b in zip(claps, claps[1:]))
    return {"hits": hits, "claps": len(claps), "pair": pair, "p": round(p_clap(x, rate), 2)}


def poll():
    since = time.time()
    while True:
        try:
            with urllib.request.urlopen(f"{PI}/session/clips?since={since}", timeout=5) as r:
                new = json.load(r)
            for c in new:
                since = max(since, c["at"])
                with urllib.request.urlopen(f"{PI}/{quote(c['file'])}", timeout=10) as r:
                    data = r.read()
                x, rate = read_wav(io.BytesIO(data))
                cid = str(len(clips))
                wavs[cid] = data
                env = [int(np.abs(x[i:i + 160]).max()) for i in range(0, len(x), 160)]
                clips.append({"id": cid, "at": c["at"], "file": Path(c["file"]).name, "rel": c["file"], "session": c.get("session", ""),
                              "peak": int(np.abs(x).max()), "sec": round(len(x) / rate, 1), "env": env,
                              **judge(x, rate)})
        except Exception as e:  # Pi недоступний — пробуємо далі
            print("Pi:", e)
            time.sleep(2)
        time.sleep(0.2)


def poll_events():
    # Рішення моделі на самій платі: окремо від кліпів, щоб їх скачування не гальмувало показ
    since = time.time()
    while True:
        try:
            with urllib.request.urlopen(f"{PI}/session/events?since={since}", timeout=3) as r:
                for e in json.load(r):
                    since = max(since, e["at"])
                    events.append({**e, "id": len(events)})
        except Exception as e:
            print("Pi (події):", e)
            time.sleep(1)
        time.sleep(0.1)


def read_serial(port):
    # Рішення плати просто з USB-логу: без Wi-Fi, затримка = лише час, що модель чекає після удару.
    # Відкриття порту перезапускає плату (так влаштований USB на цій платі). Плата перезапустилась сама —
    # порт на мить зникає: тоді чекаємо й відкриваємо знову, а перезапуск позначаємо в таблиці.
    import re
    import serial
    while True:
        try:
            s = serial.Serial()
            s.port, s.baudrate, s.timeout, s.dtr, s.rts = port, 115200, 0.2, False, False
            s.open()
            buf = b""
            while True:
                buf += s.read(4096)
                *lines, buf = buf.split(b"\n")
                for raw in lines:
                    line = raw.decode("utf-8", "replace")
                    m = re.search(r"удар: модель ([0-9.]+)", line)
                    if m or "ДВА ХЛОПКИ" in line:
                        events.append({"id": len(events), "at": time.time(), "p": float(m.group(1)) if m else 1.0,
                                       "pair": not m, "board_ms": 150 if m else 0, "net_ms": 0, "via": "USB"})
                    elif "rst:" in line:
                        print("плата перезапустилась:", line.strip())
                        events.append({"id": len(events), "at": time.time(), "p": 0, "pair": False, "board_ms": 0,
                                       "net_ms": 0, "via": "USB", "reboot": line.strip()[:60]})
        except Exception as e:
            print("USB:", e)
            time.sleep(1)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        # Позначка «модель помилилась» → data/labels.csv, як на сторінці tools/label.py (для перетренування)
        if self.path != "/label":
            self.send(404, b"", "text/plain")
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        clip = next(c for c in clips if c["id"] == body["id"])
        new = not LABELS.exists()
        with LABELS.open("a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["file", "label", "guess", "pector", "labelled_at"])
            w.writerow([clip["rel"], body["label"], "tester", "", datetime.now().isoformat(timespec="seconds")])
        clip["label"] = body["label"]
        self.send(200, b'{"ok":true}', "application/json")

    def do_GET(self):
        if self.path == "/":
            self.send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif self.path.startswith("/clips.json"):
            info = {"model": MODEL.get("model", "?"), "trained_on": MODEL.get("trained_on"), "clips": clips[-100:],
                    "events": events[-50:]}
            self.send(200, json.dumps(info, ensure_ascii=False).encode(), "application/json")
        elif self.path.startswith("/wav/") and self.path[5:] in wavs:
            self.send(200, wavs[self.path[5:]], "audio/wav")
        else:
            self.send(404, b"", "text/plain")


PAGE = r"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Тестер хлопків</title>
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1b;--mute:#76736c;--line:#e3e0d9;--yes:#2f7d4f;--no:#8a8780;--pair:#b8860b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.4 -apple-system,system-ui,sans-serif;padding:16px}
h1{font-size:20px;margin:0 0 4px}.mute{color:var(--mute);font-size:13px}
#big{margin-top:12px;border-radius:16px;padding:22px 16px;text-align:center;background:var(--card);border:1px solid var(--line)}
#verdict{font-size:min(12vw,64px);font-weight:800;line-height:1.05}#sub{margin-top:6px;color:var(--mute)}
#big.yes{background:var(--yes);color:#fff;border-color:var(--yes)}#big.pair{background:var(--pair);color:#fff;border-color:var(--pair)}
#big.yes #sub,#big.pair #sub{color:#eef}
.tally{margin-top:10px}.tally span{margin-right:14px}
table{width:100%;border-collapse:collapse;margin-top:12px;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
td,th{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle}th{color:var(--mute);font-size:12px;font-weight:600}
canvas{width:220px;height:36px;display:block}tr{cursor:pointer}tr:hover{background:#faf9f6}
.tag{padding:2px 8px;border-radius:10px;color:#fff;font-size:12px;white-space:nowrap}.y{background:var(--yes)}.n{background:var(--no)}.p{background:var(--pair)}
.fix button{font:inherit;font-size:12px;border:1px solid var(--line);background:var(--card);border-radius:8px;padding:3px 7px;margin-right:4px;cursor:pointer}
.miss{color:#a3542b;font-weight:600}tr.neg{display:none}body.all tr.neg{display:table-row}tr.neg td{color:var(--mute)}
@media (max-width:560px){canvas{width:120px}.hide{display:none}}
</style>
<h1>Тестер хлопків</h1>
<div class="mute" id="model"></div>
<div id="big"><div id="verdict">Слухаю…</div><div id="sub">мовчу, поки плата не почує хлопок</div></div>
<h2 class="mute" style="font-size:13px;margin:14px 0 0">Рішення плати (модель на ESP32)</h2>
<table><thead><tr><th>час</th><th>плата</th><th>ймовірність</th><th>затримка</th></tr></thead><tbody id="ev"></tbody></table>
<h2 class="mute" style="font-size:13px;margin:14px 0 0">Кліпи (приходять пізніше; модель на ноуті, тут можна послухати й позначити помилку)</h2>
<div class="tally mute"><span id="tally"></span>
 <label><input type="checkbox" id="all"> показувати й «не хлопок»</label></div>
<table><thead><tr><th>час</th><th>звук</th><th>модель</th><th class="hide">удари, мс (ймовірність)</th><th class="hide">пік</th><th>помилка?</th></tr></thead><tbody id="log"></tbody></table>
<script>
const $=s=>document.querySelector(s);let shown=0,shownEv=0,idle=0;const audio=new Audio();
function tag(c){return c.pair?'<span class="tag p">два хлопки</span>':c.claps?'<span class="tag y">хлопок</span>':'<span class="tag n">не хлопок</span>'}
function draw(cv,c){const g=cv.getContext("2d"),w=cv.width=cv.clientWidth*2,h=cv.height=cv.clientHeight*2,m=32768;
 g.fillStyle=getComputedStyle(document.body).getPropertyValue("--mute");const bw=w/c.env.length;
 c.env.forEach((e,i)=>{const y=Math.max(1,e/m*h);g.fillRect(i*bw,(h-y)/2,Math.max(1,bw-0.5),y)});
 c.hits.forEach(t=>{g.fillStyle=t.p>=.5?"#2f7d4f":"#c0392b";g.fillRect(t.ms/10*bw,0,2,h)})}
async function tick(){const d=await (await fetch("/clips.json")).json();
 $("#model").textContent=`модель ${d.model}, навчена на ${d.trained_on} кліпах з плати · рахує сама плата · світло не перемикає`;
 const cs=d.clips;const fresh=cs.filter(c=>+c.id>=shown);
 for(const c of fresh){const tr=document.createElement("tr");const t=new Date(c.at*1000).toLocaleTimeString();
  tr.innerHTML=`<td>${t}</td><td><canvas></canvas></td><td>${tag(c)}</td><td class="hide">${c.hits.map(h=>`${h.ms} (${h.p.toFixed(2)})`).join(", ")}</td><td class="hide">${c.peak}</td><td class="fix"></td>`;
  const fix=tr.querySelector(".fix");
  for(const [lab,txt] of (c.claps?[["клавіатура","клавіатура"],["інше","не хлопок"]]:[["хлопок","це хлопок"]])){
   const b=document.createElement("button");b.textContent=txt;
   b.onclick=async e=>{e.stopPropagation();await fetch("/label",{method:"POST",body:JSON.stringify({id:c.id,label:lab})});fix.textContent="✓ "+txt};fix.appendChild(b)}
  if(!c.claps)tr.className="neg";
  tr.onclick=()=>{audio.src="/wav/"+c.id;audio.play()};$("#log").prepend(tr);draw(tr.querySelector("canvas"),c);shown=+c.id+1}
 for(const e of d.events.filter(e=>e.id>=shownEv)){ // мовчить, доки плата не вирішить «хлопок»; тоді показує і знову слухає
  shownEv=e.id+1;const pi=Math.max(0,Math.round(Date.now()-e.at*1000)),total=e.board_ms+e.net_ms+pi;
  const lat=e.via?`затримка ~${total} мс: модель чекає 150 після удару · по USB`:`затримка ~${total} мс: плата ${e.board_ms} (модель чекає 150) · Wi-Fi +${e.net_ms} · Pi→екран ${pi}`;
  const tr=document.createElement("tr");tr.innerHTML=`<td>${new Date(e.at*1000).toLocaleTimeString()}</td><td>${e.pair?'<span class="tag p">два хлопки</span>':e.p>=.5?'<span class="tag y">хлопок</span>':'<span class="tag n">ні</span>'}</td><td>${e.pair?"":e.p.toFixed(2)}</td><td>${total} мс</td>`;
  if(e.reboot)tr.innerHTML=`<td>${new Date(e.at*1000).toLocaleTimeString()}</td><td colspan="3" class="miss">плата перезапустилась: ${e.reboot}</td>`;
  else if(!e.pair&&e.p<.5)tr.className="neg";$("#ev").prepend(tr);if(e.reboot)continue;
  if(!e.pair&&e.p<.5)continue;
  $("#big").className=e.pair?"pair":"yes";$("#verdict").textContent=e.pair?"ДВА ХЛОПКИ":"ХЛОПОК";$("#sub").textContent=lat+(e.pair?" · світло перемкнулось би":"");
  clearTimeout(idle);idle=setTimeout(()=>{$("#big").className="";$("#verdict").textContent="Слухаю…";$("#sub").textContent="мовчу, поки плата не почує хлопок"},2500)}
 const n=cs.length,y=cs.filter(c=>c.claps).length,p=cs.filter(c=>c.pair).length;
 $("#tally").innerHTML=`<span>кліпів <b>${n}</b></span><span>з хлопком <b>${y}</b></span><span>пар <b>${p}</b></span><span>не хлопок <b>${n-y}</b></span>`}
$("#all").onchange=e=>document.body.classList.toggle("all",e.target.checked);
setInterval(()=>tick().catch(()=>{}),200);tick();
</script>
"""

if __name__ == "__main__":
    threading.Thread(target=poll, daemon=True).start()
    port = os.environ.get("SERIAL")  # SERIAL=/dev/cu.usbmodem… — рішення плати по USB, а не через Wi-Fi і Pi
    if port:
        threading.Thread(target=read_serial, args=(port,), daemon=True).start()
    else:
        threading.Thread(target=poll_events, daemon=True).start()
    print(f"тестер: http://127.0.0.1:{PORT}  (Pi: {PI}, модель {MODEL.get('model', '?')})")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
