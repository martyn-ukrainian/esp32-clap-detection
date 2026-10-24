// Wi-Fi і все, що летить на Pi. Відправка в окремій задачі через чергу: аудіо-цикл мережі не чекає.
// SSID і пароль зашиваються при збірці (WIFI_SSID, WIFI_PASSWORD з оточення).
#pragma once

#include <stdint.h>

// Підключається до Wi-Fi і запускає задачу відправки. Не блокує: поки немає IP, кліпи чекають у черзі.
void net_init();
// Кладе кліп у чергу (копіює семпли). false = черга повна або ліміт за хвилину, кліп пропущено.
bool net_clip(const int16_t *pcm, int n, const char *label, int peak, int rms);
// GET на Pi без тіла, напр. "/toggle". Не блокує. false = черга команд повна.
#define NET_PATH_LEN 64
bool net_command(const char *path);
uint32_t net_clips_dropped();
