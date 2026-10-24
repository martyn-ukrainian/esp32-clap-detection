#include "intent.h"

void intent_on_clap(intent_state_t *st, uint32_t now_ms)
{
    if (st->toggled_ms && now_ms - st->toggled_ms < INTENT_LOCK_MS) return; // щойно перемкнули
    if (st->count > 0 && now_ms - st->last_ms < INTENT_GAP_MIN_MS) return; // зарано, не другий хлопок
    if (st->count > 0 && now_ms - st->last_ms > INTENT_GAP_MAX_MS) st->count = 0; // задовго: нова серія
    st->count++;
    st->last_ms = now_ms;
}

intent_t intent_tick(intent_state_t *st, uint32_t now_ms)
{
    // Другий хлопок у паузі 150–700 мс — перемикаємо одразу, третього не чекаємо (так швидше на ~0,5 с)
    if (st->count == 2) {
        st->count = 0;
        st->toggled_ms = now_ms;
        return INTENT_TOGGLE;
    }
    if (st->count == 1 && now_ms - st->last_ms > INTENT_GAP_MAX_MS) st->count = 0;
    return INTENT_NONE;
}
