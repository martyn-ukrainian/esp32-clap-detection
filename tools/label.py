# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""Прослухати кліпи з Pi в браузері й розмітити: що це насправді. Лише локально, кліпи нікуди не йдуть.

  rsync -a pi:clap-light/data/clips/ data/clips/      спершу стягнути свіжі кліпи
  uv run tools/label.py                               → http://127.0.0.1:8765
  uv run tools/label.py __хлопок                      лише кліпи, в імені яких є цей шматок (напр. із запису /record)
  uv run tools/label.py @data/review.txt              лише кліпи зі списку, в його порядку (рядок: файл<TAB>модель<TAB>мітка)

Кліпи грають по черзі. Біля кожного — моя здогадка (за частотами) і що каже pector з фільтром 1–4 кГц.
Мітки пишуться в data/labels.csv (останній рядок для файлу перемагає).
Клавіші: пробіл — пауза, 1 хлопок, 2 два хлопки, 3 гупання/ліжко, 4 мова, 5 інше, ← → — попередній / наступний.
"""
import csv
import glob
import json
import os
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LABELS = DATA / "labels.csv"
sys.path.insert(0, str(ROOT / "tools"))
from pector_check import bandpass, read_wav, run  # noqa: E402

PORT = int(os.environ.get("PORT", 8765))  # друга сторінка поруч: PORT=8767 uv run tools/label.py …
ONLY = sys.argv[1] if len(sys.argv) > 1 else ""  # показувати лише кліпи з цим шматком в імені


def guess(x, r) -> dict:
    # Та сама евристика, що в README «Виміри»: частка енергії нижче 300 Гц і в 1–4 кГц
    S = np.abs(np.fft.rfft(x)) ** 2
    f = np.fft.rfftfreq(len(x), 1 / r)
    total = S.sum() + 1e-9
    low = S[f < 300].sum() / total
    band = S[(f >= 1000) & (f <= 4000)].sum() / total
    kind = "гул" if low > 0.8 else "хлопок" if band > 0.3 else "інше"
    onsets, double, _ = run(bandpass(x, r), r)
    return {
        "peak": int(np.abs(x).max()),
        "low": round(float(low), 2),
        "band": round(float(band), 2),
        "rail": int((np.abs(x) >= 32000).sum()),
        "guess": kind,
        "pector": "два хлопки" if double else "удар" if onsets else "—",
    }


def load_labels() -> dict:
    if not LABELS.exists():
        return {}
    with LABELS.open() as f:
        return {row["file"]: row["label"] for row in csv.DictReader(f)}


def build_index() -> list:
    labels = load_labels()
    clips = []
    if ONLY.startswith("@"):
        # Список на перевірку: замість моєї здогадки — що каже модель і яка мітка стоїть зараз
        for line in Path(ONLY[1:]).read_text().splitlines():
            rel, p, was = line.split("\t")
            x, r = read_wav(str(DATA / rel))
            c = guess(x, r)
            c.update(guess=f"модель {p}", pector=f"мітка: {was}")
            clips.append({"file": rel, "time": Path(rel).stem[:6], "day": Path(rel).parent.name,
                          "label": labels.get(rel, ""), **c})
        return clips
    for p in sorted(glob.glob(str(DATA / "clips" / "*" / "*.wav"))):
        rel = str(Path(p).relative_to(DATA))
        if ONLY and ONLY not in Path(p).name:
            continue
        x, r = read_wav(p)
        clips.append({"file": rel, "time": Path(p).stem[:6], "day": Path(p).parent.name,
                      "label": labels.get(rel, ""), **guess(x, r)})
    return clips


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self.send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif self.path == "/clips.json":
            self.send(200, json.dumps(INDEX, ensure_ascii=False).encode(), "application/json")
        elif self.path.startswith(("/clips/", "/datasets/")):
            # Лише файли з індексу: жодних довільних шляхів
            rel = unquote(self.path[1:])
            if rel not in {c["file"] for c in INDEX}:
                self.send(404, b"", "text/plain")
                return
            self.send(200, (DATA / rel).read_bytes(), "audio/wav")
        else:
            self.send(404, b"", "text/plain")

    def do_POST(self):
        if self.path != "/label":
            self.send(404, b"", "text/plain")
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        new = not LABELS.exists()
        with LABELS.open("a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["file", "label", "guess", "pector", "labelled_at"])
            clip = next(c for c in INDEX if c["file"] == body["file"])
            clip["label"] = body["label"]
            w.writerow([clip["file"], body["label"], clip["guess"], clip["pector"],
                        datetime.now().isoformat(timespec="seconds")])
        self.send(200, b'{"ok":true}', "application/json")


PAGE = r"""<!doctype html><meta charset="utf-8"><title>Кліпи clap-light</title>
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1b;--mute:#76736c;--line:#e3e0d9;--clap:#2f7d4f;--hum:#a3542b;--other:#5a5f8a;--now:#fff7d6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.4 -apple-system,system-ui,sans-serif;padding:16px}
header{position:sticky;top:0;background:var(--bg);padding:8px 0 12px;z-index:2;border-bottom:1px solid var(--line)}
h1{font-size:18px;margin:0 0 8px}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
button{font:inherit;border:1px solid var(--line);background:var(--card);border-radius:8px;padding:6px 10px;cursor:pointer}
button:hover{border-color:var(--ink)}button.main{background:var(--ink);color:#fff;border-color:var(--ink);min-width:110px}
.stats{color:var(--mute);font-size:13px}canvas{width:100%;height:70px;display:block;background:var(--card);border:1px solid var(--line);border-radius:8px;margin-top:10px}
table{width:100%;border-collapse:collapse;margin-top:12px;background:var(--card);border-radius:8px;overflow:hidden}
td,th{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}th{font-weight:600;color:var(--mute);font-size:12px}
tr.now{background:var(--now)}tr{cursor:pointer}.tag{padding:1px 7px;border-radius:10px;font-size:12px;color:#fff}
.модель{color:var(--ink)!important}.хлопок{background:var(--clap)}.гул{background:var(--hum)}.інше{background:var(--other)}
.lab{font-weight:600}.wrap{overflow-x:auto}.labels button{padding:6px 9px}.labels kbd{color:var(--mute);font-size:11px;margin-right:3px}
label{color:var(--mute);font-size:13px}
</style>
<header>
 <h1>Кліпи clap-light</h1>
 <div class="row">
  <button class="main" id="play">▶ Почати</button>
  <button id="prev">←</button><button id="next">→</button>
  <label><input type="checkbox" id="loud" checked> лише гучні (пік ≥ 2000)</label>
  <label><input type="checkbox" id="unl"> лише без мітки</label>
  <label>пауза між кліпами <select id="gap"><option value="300">0,3 с</option><option value="800" selected>0,8 с</option><option value="1500">1,5 с</option></select></label>
 </div>
 <div class="row labels" style="margin-top:8px">
  <span class="stats">Твоя мітка:</span>
  <button data-l="хлопок"><kbd>1</kbd>хлопок</button><button data-l="два хлопки"><kbd>2</kbd>два хлопки</button>
  <button data-l="гупання/ліжко"><kbd>3</kbd>гупання/ліжко</button><button data-l="мова"><kbd>4</kbd>мова</button><button data-l="інше"><kbd>5</kbd>інше</button>
  <span class="stats" id="stats"></span>
 </div>
 <div class="stats" id="cur" style="margin-top:8px"></div>
 <canvas id="wave" width="1200" height="140"></canvas>
</header>
<div class="wrap"><table><thead><tr><th>#</th><th>день</th><th>час</th><th>моя здогадка</th><th>pector + 1–4 кГц</th><th>пік</th><th>&lt;300 Гц</th><th>1–4 кГц</th><th>у стелі</th><th>твоя мітка</th></tr></thead><tbody id="tb"></tbody></table></div>
<script>
let all=[],list=[],i=0,playing=false,timer=null;const audio=new Audio();
const $=s=>document.querySelector(s);
const fmt=t=>t.slice(0,2)+":"+t.slice(2,4)+":"+t.slice(4,6);
async function load(){all=await (await fetch("/clips.json")).json();filter();}
function filter(){const cur=list[i]&&list[i].file;list=all.filter(c=>(!$("#loud").checked||c.peak>=2000)&&(!$("#unl").checked||!c.label));
 i=Math.max(0,list.findIndex(c=>c.file===cur));render();}
function render(){const tb=$("#tb");tb.innerHTML="";list.forEach((c,k)=>{const tr=document.createElement("tr");if(k===i)tr.className="now";
 tr.innerHTML=`<td>${k+1}</td><td>${c.day.slice(5)}</td><td>${fmt(c.time)}</td><td><span class="tag ${c.guess}">${c.guess}</span></td><td>${c.pector}</td><td>${c.peak}</td><td>${Math.round(c.low*100)}%</td><td>${Math.round(c.band*100)}%</td><td>${c.rail}</td><td class="lab">${c.label||""}</td>`;
 tr.onclick=()=>{i=k;playCur(true)};tb.appendChild(tr);});
 const done=all.filter(c=>c.label).length;$("#stats").textContent=`розмічено ${done} з ${all.length}`;showCur();}
function showCur(){const c=list[i];if(!c){$("#cur").textContent="нічого не лишилось";return}
 $("#cur").innerHTML=`<b>${k1(i)}</b> · ${c.day} ${fmt(c.time)} · моя здогадка: <b>${c.guess}</b> · pector: <b>${c.pector}</b> · твоя: <b>${c.label||"—"}</b>`;
 const row=$("#tb").children[i];if(row)row.scrollIntoView({block:"nearest"});}
const k1=k=>`${k+1} / ${list.length}`;
async function drawWave(url){const ctx=$("#wave").getContext("2d"),W=1200,H=140;ctx.clearRect(0,0,W,H);
 try{const ac=new (window.AudioContext||window.webkitAudioContext)();const buf=await ac.decodeAudioData(await (await fetch(url)).arrayBuffer());ac.close();
 const d=buf.getChannelData(0),step=Math.ceil(d.length/W);ctx.fillStyle="#1d1d1b";
 for(let x=0;x<W;x++){let mn=1,mx=-1;for(let j=x*step;j<(x+1)*step&&j<d.length;j++){mn=Math.min(mn,d[j]);mx=Math.max(mx,d[j])}
  ctx.fillRect(x,(1-mx)*H/2,1,Math.max(1,(mx-mn)*H/2))}
 ctx.fillStyle="#a3542b";ctx.fillRect(W*0.3/0.8,0,1,H);}catch(e){}}
function playCur(force){clearTimeout(timer);const c=list[i];if(!c)return;render();const url="/"+encodeURI(c.file);
 audio.src=url;drawWave(url);if(playing||force){playing=true;$("#play").textContent="⏸ Пауза";audio.play().catch(()=>{})}}
audio.onended=()=>{if(!playing)return;timer=setTimeout(()=>{if(i<list.length-1){i++;playCur()}else{playing=false;$("#play").textContent="▶ Почати"}},+$("#gap").value)};
$("#play").onclick=()=>{if(playing){playing=false;audio.pause();clearTimeout(timer);$("#play").textContent="▶ Далі"}else{playing=true;playCur(true)}};
$("#prev").onclick=()=>{if(i>0){i--;playCur(true)}};$("#next").onclick=()=>{if(i<list.length-1){i++;playCur(true)}};
async function setLabel(l){const c=list[i];if(!c)return;await fetch("/label",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({file:c.file,label:l})});
 c.label=l;if($("#unl").checked){filter()}else render();}
document.querySelectorAll("[data-l]").forEach(b=>b.onclick=()=>setLabel(b.dataset.l));
const keys={"1":"хлопок","2":"два хлопки","3":"гупання/ліжко","4":"мова","5":"інше"};
document.addEventListener("keydown",e=>{if(e.target.tagName==="SELECT")return;if(e.key===" "){e.preventDefault();$("#play").click()}
 else if(keys[e.key])setLabel(keys[e.key]);else if(e.key==="ArrowRight")$("#next").click();else if(e.key==="ArrowLeft")$("#prev").click();});
$("#loud").onchange=filter;$("#unl").onchange=filter;load();
</script>"""

if __name__ == "__main__":
    print("рахую ознаки кліпів…")
    INDEX = build_index()
    print(f"{len(INDEX)} кліпів → http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
