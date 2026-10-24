// Хлопки в часі → що людина хоче. Два хлопки з паузою 150–700 мс = перемкнути, одразу на другому.
// Третього не чекаємо (оплески теж перемкнуть), зате швидко; після перемикання INTENT_LOCK_MS ігнор.
#pragma once

#include <stdint.h>

#define INTENT_GAP_MIN_MS 150
#define INTENT_GAP_MAX_MS 700
#define INTENT_LOCK_MS 1500 // після перемикання хлопки ігноруємо, щоб не перемкнуло вдруге

typedef enum {
    INTENT_NONE,
    INTENT_TOGGLE,
} intent_t;

typedef struct {
    int count;        // хлопків у поточній серії
    uint32_t last_ms; // час останнього з них
    uint32_t toggled_ms; // час останнього перемикання; 0 = ще не було
} intent_state_t;

void intent_on_clap(intent_state_t *st, uint32_t now_ms);
// Кликати на кожне вікно. Повертає рішення, коли серія закінчилась, інакше INTENT_NONE.
intent_t intent_tick(intent_state_t *st, uint32_t now_ms);
