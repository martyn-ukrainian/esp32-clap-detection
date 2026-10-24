// Хлопок за моделлю: ті самі 8 ознак, що tools/clap_features.py, і логістична регресія (train/train_ab.py, модель B).
// Чиста математика, без ESP-заголовків (перевірка на Mac: tools/check_clap_model.sh).
#pragma once

#include <stdint.h>

#include "level.h"
#include "ring.h"

#define CLAP_VIEW_PRE_MS 300   // модель бачить 0,3 с до удару
#define CLAP_VIEW_POST_MS 150  // і 0,15 с після: стільки чекаємо, перш ніж вирішити (250 — на 0,6% влучніше, але довше)
#define CLAP_TRIGGER 330000    // пік вікна (24 біти), з якого звук розглядаємо; тихіше модель не бачила
#define CLAP_REARM_MS 150      // новий удар не раніше: хвіст того самого звуку не рахуємо
#define CLAP_PENDING 4         // скільки ударів можуть чекати рішення одночасно

typedef struct {
    uint32_t pending_ms[CLAP_PENDING]; // коли почули удар; 0 = місце вільне
    uint32_t last_trigger_ms;
} clap_model_state_t;

// Ймовірність хлопка для звуку: n семплів 16 біт, удар на CLAP_VIEW_PRE_MS від початку.
float clap_model_p(const int16_t *x, int n);

// Кликати на кожне вікно 10 мс, після ring_write. true — дозріло рішення про удар, у *p його ймовірність.
// Удар був CLAP_VIEW_POST_MS тому.
bool clap_model_window(clap_model_state_t *st, level_t lv, uint32_t now_ms, const ring_t *ring, float *p);
