// Гучність вікна: DC-фільтр, RMS і пік. Чиста математика, без ESP-заголовків (щоб ганяти на Mac).
#pragma once

#include <stdint.h>

// Стан DC-фільтра між вікнами. На старті нулі.
typedef struct {
    float dc; // повільне середнє = постійний зсув мікрофона
} level_state_t;

typedef struct {
    int rms;
    int peak;
} level_t;

// Сирі слоти INMP441 → RMS і пік після зсуву до 24 біт і прибирання DC.
// pcm (можна NULL): той самий звук у 16 бітах, для кліпів.
level_t level_window(const int32_t *raw, int n, level_state_t *st, int16_t *pcm);
