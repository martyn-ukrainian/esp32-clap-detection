// Перевірка на Mac: ймовірність хлопка з C-коду прошивки для WAV-кліпів (те саме бачення, що board_view у train_ab.py).
//   c++ -O2 -I firmware/src/detect firmware/test/check_clap_model.cpp firmware/src/detect/clap_model.cpp firmware/src/detect/ring.cpp -o /tmp/check
//   /tmp/check data/clips/2026-10-24/*.wav
#include <stdio.h>
#include <stdlib.h>

#include "clap_model.h"

int main(int argc, char **argv)
{
    static int16_t x[16000 * 10];
    for (int a = 1; a < argc; a++) {
        FILE *f = fopen(argv[a], "rb");
        if (!f) continue;
        fseek(f, 44, SEEK_SET); // WAV від сервера: заголовок 44 байти, далі int16
        int n = fread(x, 2, sizeof(x) / 2, f);
        fclose(f);
        int pre = n >= 24000 ? 8000 : 4800; // удар на 0,5 с у 2-секундних кліпах, інакше на 0,3 с
        int a0 = pre - CLAP_VIEW_PRE_MS * 16;
        int len = (CLAP_VIEW_PRE_MS + CLAP_VIEW_POST_MS) * 16;
        if (a0 + len > n) continue;
        printf("%s\t%.3f\n", argv[a], clap_model_p(x + a0, len));
    }
}
