#include "ring.h"

void ring_init(ring_t *r, int16_t *buf, int size)
{
    r->buf = buf;
    r->size = size;
    r->head = 0;
    r->total = 0;
}

void ring_write(ring_t *r, const int16_t *src, int n)
{
    for (int i = 0; i < n; i++) {
        r->buf[r->head] = src[i];
        r->head = (r->head + 1) % r->size;
    }
    r->total += n;
}

int ring_last(const ring_t *r, int16_t *dst, int n)
{
    if (n > r->size) n = r->size;
    if ((uint32_t)n > r->total) n = r->total;
    int start = (r->head - n + r->size) % r->size;
    for (int i = 0; i < n; i++) dst[i] = r->buf[(start + i) % r->size];
    return n;
}
