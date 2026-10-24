// Хлопок по одному вікну 10 мс. ТИМЧАСОВО: лише поріг піку і мертвий час.
// Фон і підтвердження спадом (кроки 3–4 README) — сюди ж.
#pragma once

#include <stdint.h>

#include "level.h"

#define CLAP_PEAK 1000000  // тиша 20–27 тис., мова до 320 тис., хлопки 1,3–8,5 млн
#define CLAP_DEAD_MS 120   // після хлопка: відлуння й хвіст того самого хлопка не рахуємо
#define CLAP_IGNORE_MS 1000 // старт мікрофона дає пік ~1,7 млн

typedef struct {
    uint32_t last_ms; // час останнього хлопка; 0 = ще не було
} clap_state_t;

// true, якщо в цьому вікні хлопок. now_ms — час від старту.
bool clap_window(clap_state_t *st, level_t lv, uint32_t now_ms);
