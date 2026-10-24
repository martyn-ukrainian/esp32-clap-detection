// Кільцевий буфер звуку: тримає останні size семплів, нове пише поверх найстарішого.
// Чиста логіка, пам'ять дає той, хто створює.
#pragma once

#include <stdint.h>

typedef struct {
    int16_t *buf;
    int size;
    int head;       // куди піде наступний семпл
    uint32_t total; // скільки записано за весь час (щоб знати, чи буфер уже повний)
} ring_t;

void ring_init(ring_t *r, int16_t *buf, int size);
void ring_write(ring_t *r, const int16_t *src, int n);
// Копіює останні n семплів у dst, від старшого до новішого. Повертає скільки скопійовано (менше n, якщо ще не набралось).
int ring_last(const ring_t *r, int16_t *dst, int n);
