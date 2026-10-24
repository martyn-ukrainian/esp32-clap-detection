#include "clap_model.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "clap_model_params.h" // ваги з train/train_ab.py, не правити руками

#define RATE 16000
#define HOP 160   // 10 мс
#define NFFT 256  // кадр 16 мс
#define BINS (NFFT / 2 + 1)
#define VIEW ((CLAP_VIEW_PRE_MS + CLAP_VIEW_POST_MS) * RATE / 1000)
#define FRAMES ((VIEW - NFFT) / HOP + 1)
#define HZ_TO_BIN(hz) ((hz) * NFFT / RATE) // 62,5 Гц на бін

static float S[FRAMES][BINS]; // потужність спектра по кадрах
static float re[NFFT], im[NFFT], hann[NFFT];
static float tw_re[NFFT / 2], tw_im[NFFT / 2]; // e^(-2πik/NFFT): без cos/sin у кожному метелику
static float xs[VIEW];
static int16_t view_buf[VIEW];

static void fft(float *r, float *i)
{
    // Радікс-2 на місці: перестановка бітів, потім метелики
    for (int a = 1, b = 0; a < NFFT; a++) {
        int bit = NFFT >> 1;
        for (; b & bit; bit >>= 1) b ^= bit;
        b ^= bit;
        if (a < b) {
            float t = r[a]; r[a] = r[b]; r[b] = t;
            t = i[a]; i[a] = i[b]; i[b] = t;
        }
    }
    for (int len = 2; len <= NFFT; len <<= 1) {
        int step = NFFT / len;
        for (int s = 0; s < NFFT; s += len) {
            for (int k = 0; k < len / 2; k++) {
                float wr = tw_re[k * step], wi = tw_im[k * step];
                int u = s + k, v = s + k + len / 2;
                float tr = r[v] * wr - i[v] * wi, ti = r[v] * wi + i[v] * wr;
                r[v] = r[u] - tr; i[v] = i[u] - ti;
                r[u] += tr; i[u] += ti;
            }
        }
    }
}

static int cmp_float(const void *a, const void *b)
{
    float d = *(const float *)a - *(const float *)b;
    return (d > 0) - (d < 0);
}

// Ті самі ознаки й у тому ж порядку, що FEATURES у tools/clap_features.py
static void features(const int16_t *x, int n, float out[8])
{
    if (hann[1] == 0) {
        for (int k = 0; k < NFFT; k++) hann[k] = 0.5f - 0.5f * cosf(2 * (float)M_PI * k / (NFFT - 1));
        for (int k = 0; k < NFFT / 2; k++) {
            tw_re[k] = cosf(-2 * (float)M_PI * k / NFFT);
            tw_im[k] = sinf(-2 * (float)M_PI * k / NFFT);
        }
    }

    double mean = 0;
    for (int k = 0; k < n; k++) mean += x[k];
    mean /= n;
    float jump = 0;
    for (int k = 0; k < n; k++) {
        xs[k] = x[k] - mean;
        if (k && fabsf(xs[k] - xs[k - 1]) > jump) jump = fabsf(xs[k] - xs[k - 1]);
    }

    int frames = (n - NFFT) / HOP + 1;
    if (frames > FRAMES) frames = FRAMES;
    float energy[FRAMES], flux[FRAMES];
    for (int t = 0; t < frames; t++) {
        for (int k = 0; k < NFFT; k++) {
            re[k] = xs[t * HOP + k] * hann[k];
            im[k] = 0;
        }
        fft(re, im);
        energy[t] = 1e-9f;
        flux[t] = 0;
        for (int k = 0; k < BINS; k++) {
            S[t][k] = re[k] * re[k] + im[k] * im[k];
            energy[t] += S[t][k];
            if (t >= 3) {
                float d = log1pf(S[t][k]) - log1pf(S[t - 1][k]);
                if (d > 0) flux[t] += d;
            }
        }
    }

    int t = 0; // удар = кадр із найбільшим ростом спектра
    for (int k = 1; k < frames; k++)
        if (flux[k] > flux[t]) t = k;

    float around[BINS] = {}, total = 1e-9f;
    for (int f = (t > 0 ? t - 1 : 0); f < t + 2 && f < frames; f++)
        for (int k = 0; k < BINS; k++) around[k] += S[f][k];
    for (int k = 0; k < BINS; k++) total += around[k];
    float band = 0, low = 0;
    for (int k = HZ_TO_BIN(1000); k <= HZ_TO_BIN(4000); k++) band += around[k];
    for (int k = 0; k * RATE < 300 * NFFT; k++) low += around[k]; // нижче 300 Гц

    int before = t > 0 ? t - 1 : 0, grew = 0;
    for (int b = 0; b < 16; b++) { // смуги по 500 Гц = по 8 бінів
        float now = 1e-9f, was = 1e-9f;
        for (int k = b * 8; k < b * 8 + 8; k++) {
            now += S[t][k];
            was += S[before][k];
        }
        if (10 * log10f(now / was) > 6) grew++;
    }

    float after = 0;
    int na = 0;
    for (int f = t + 10; f < t + 15 && f < frames; f++, na++) after += energy[f];
    after = na ? after / na : energy[frames - 1];

    int nq = t - 3 > 1 ? t - 3 : 1; // фон — медіана енергії кадрів до удару
    float quiet[FRAMES];
    memcpy(quiet, energy, nq * sizeof(float));
    qsort(quiet, nq, sizeof(float), cmp_float);
    float noise = nq % 2 ? quiet[nq / 2] : (quiet[nq / 2 - 1] + quiet[nq / 2]) / 2;

    out[0] = log1pf(flux[t]);
    out[1] = band / total;
    out[2] = low / total;
    out[3] = grew / 16.0f;
    out[4] = log10f(after / energy[t]);
    out[5] = log10f(energy[t] / energy[t >= 2 ? t - 2 : 0]);
    out[6] = jump / 65535;
    out[7] = log10f(energy[t] / noise);
}

float clap_model_p(const int16_t *x, int n)
{
    float v[8];
    features(x, n, v);
    float z = CLAP_MODEL_BIAS;
    for (int k = 0; k < 8; k++) z += CLAP_MODEL_WEIGHTS[k] * (v[k] - CLAP_MODEL_MEAN[k]) / CLAP_MODEL_SCALE[k];
    return 1 / (1 + expf(-z));
}

bool clap_model_window(clap_model_state_t *st, level_t lv, uint32_t now_ms, const ring_t *ring, float *p)
{
    if (lv.peak > CLAP_TRIGGER && now_ms - st->last_trigger_ms >= CLAP_REARM_MS) {
        st->last_trigger_ms = now_ms;
        for (int k = 0; k < CLAP_PENDING; k++)
            if (!st->pending_ms[k]) {
                st->pending_ms[k] = now_ms;
                break;
            }
    }
    // Одне рішення за вікно: решта дозріє в наступних
    for (int k = 0; k < CLAP_PENDING; k++) {
        if (!st->pending_ms[k] || now_ms - st->pending_ms[k] < CLAP_VIEW_POST_MS) continue;
        st->pending_ms[k] = 0;
        int n = ring_last(ring, view_buf, VIEW);
        if (n < VIEW) return false;
        *p = clap_model_p(view_buf, n);
        return true;
    }
    return false;
}
