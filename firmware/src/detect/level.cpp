#include "level.h"

#include <cmath>

level_t level_window(const int32_t *raw, int n, level_state_t *st, int16_t *pcm)
{
    double sum = 0;
    int peak = 0;
    for (int i = 0; i < n; i++) {
        float s = raw[i] >> 8; // 24 біти
        st->dc += (s - st->dc) * 0.001f;
        float x = s - st->dc;
        sum += x * x;
        if (fabsf(x) > peak) peak = fabsf(x);
        if (pcm) pcm[i] = fmaxf(-32768, fminf(32767, x / 256)); // 24 → 16 біт
    }
    level_t out;
    out.rms = n ? (int)sqrt(sum / n) : 0;
    out.peak = peak;
    return out;
}
