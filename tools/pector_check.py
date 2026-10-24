# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""Детектор pector (порт на Python) на кліпах із Pi: чи бачить він удар і подвійний хлопок.

Алгоритм і параметри — github.com/JorenSix/pector, pector_config_ESP32: блок 512 @16 кГц, поріг 900, тиша -10 дБ,
подвійний = два удари ближче 0,45 с. Семпли як у його ESP32-прикладі: int16 * 40 / 32768.

  uv run tools/pector_check.py data/clips [--bandpass]     --bandpass: спершу фільтр 1–4 кГц
"""
import sys, wave, glob, numpy as np
BANDPASS="--bandpass" in sys.argv
N=512; THR=900.0; SIL=-10.0; MIN_IOI=0.1; DBL=0.45; GAIN=40
win=0.54-0.46*np.cos(2*np.pi*np.arange(N)/(N-1))
def run(x, r):
    x=np.clip(x*GAIN/32768.0,-1,1)  # у їхньому коді int16 переповнюється; тут обрізаємо
    m1=np.zeros(N//2); p1=np.zeros(N//2); p2=np.zeros(N//2); onsets=[]; last=-9; best=0
    for b in range(len(x)//N):
        blk=x[b*N:(b+1)*N]; X=np.fft.rfft(blk*win)[:N//2]
        m0=np.abs(X); p0=np.angle(X)
        dev=2*p1-p2
        ov=np.sqrt(np.abs(m1**2+m0**2-2*m1*m0*np.cos(dev-p0))).sum()
        t=b*N/r; db=20*np.log10(np.sqrt((blk**2).mean())+1e-12)
        if b>=3: best=max(best,ov)   # перші блоки — розгін історії фаз, не рахуємо
        if b>=3 and ov>THR and t-last>MIN_IOI and db>SIL:
            onsets.append(t); last=t
        m1=m0; p2=p1; p1=p0
    dbl=any(b-a<DBL for a,b in zip(onsets,onsets[1:]))
    return onsets,dbl,best
def bandpass(x, r):
    X=np.fft.rfft(x); f=np.fft.rfftfreq(len(x),1/r); X[(f<1000)|(f>4000)]=0
    return np.fft.irfft(X,len(x))


def read_wav(p):
    w=wave.open(p); r=w.getframerate(); x=np.frombuffer(w.readframes(w.getnframes()),"<i2").astype(float); w.close()
    return x-x.mean(), r


if __name__ == "__main__":
    print("файл\tударів\tподвійний\tmax_onset")
    for p in sorted(glob.glob(sys.argv[1]+"/*/*.wav")):
        x,r=read_wav(p)
        on,dbl,best=run(bandpass(x,r) if BANDPASS else x, r)
        print(p.split("/")[-1][:10], len(on), int(dbl), round(best), sep="\t")
