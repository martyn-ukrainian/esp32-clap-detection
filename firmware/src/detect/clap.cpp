#include "clap.h"

bool clap_window(clap_state_t *st, level_t lv, uint32_t now_ms)
{
    if (now_ms < CLAP_IGNORE_MS) return false;
    if (st->last_ms && now_ms - st->last_ms < CLAP_DEAD_MS) return false;
    if (lv.peak <= CLAP_PEAK) return false;
    st->last_ms = now_ms;
    return true;
}
