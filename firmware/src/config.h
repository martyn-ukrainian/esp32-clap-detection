// Піни й налаштування clap-light. Усе, що підключено до ESP32-S3, — тут.
#pragma once

#include "driver/gpio.h"

// INMP441 (I²S). VDD на 3V3, GND на GND, L/R на GND (лівий канал). Не 38: RGB-світлодіод плати v1.1.
#define MIC_BCLK_GPIO GPIO_NUM_12 // SCK
#define MIC_WS_GPIO GPIO_NUM_11   // WS
#define MIC_DIN_GPIO GPIO_NUM_10  // SD

#define MIC_SAMPLE_RATE 16000
#define WINDOW_SAMPLES (MIC_SAMPLE_RATE / 100) // 10 мс, вікно детектора
#define LOG_EVERY_WINDOWS 10                  // лог rms/пік раз на 100 мс (найгучніше вікно з десяти)

// 1 = у лозі ще й сирі семпли (діагностика проводів). listen.py читає лише rms і пік.
#define LOG_RAW 0

// Pi дім-хабу. IP, а не home-pi.local: ESP-IDF без компонента mDNS імен .local не знає. IP закріпити в роутері.
#define PI_HOST "192.168.0.105"
#define PI_URL "http://" PI_HOST ":7076"
#define PI_UDP_PORT 7077 // toggle і рішення моделі: UDP, без з'єднання й черги (server/main.py, BoardUdp)

// Щоб плата не мовчала до ранку (завмерла 25.10 о 01:01 і стояла 12 год)
#define ALIVE_EVERY_MS 30000          // пульс на Pi: час роботи, пам'ять, Wi-Fi, причина останнього старту
#define WIFI_LOST_RESTART_MS 300000   // 5 хв без Wi-Fi — перезапуск
#define LOOP_WDT_MS 10000             // цикл мікрофона застряг на 10 с — перезапуск (watchdog)

// 1 = прошивка для збору датасету (pio run -e dataset): /toggle не шле, кліпи довші й частіші. Див. нижче.
#ifndef DATASET
#define DATASET 0
#endif

// Кліпи кандидатів на Pi (POST /clip) на час підбору порогів. Потім 0: мікрофон чує мову в кімнаті.
#define CLIP_UPLOAD 1
#define CLIP_PEAK_THRESHOLD 150000            // пік кандидата: низько, щоб у датасет ішли й тихі звуки (мова поруч — до 320 тис.)
#define CLIP_PRE_MS 300                       // скільки звуку до початку події
#define CLIP_POST_MS 500                      // і після (влазить і другий хлопок пари)
#define CLIP_QUIET_MS 500                     // тиша, після якої подія закінчилась; ≥ CLIP_POST_MS, інакше другий хлопок затирає кліп першого
#define CLIP_MAX_PER_MIN 30
#define CLIP_QUEUE_LEN 4
#define RING_SAMPLES MIC_SAMPLE_RATE          // 1 с

#if DATASET
// Пара хлопків з будь-якою паузою до ~1,2 с влазить в один кліп.
#undef CLIP_PRE_MS
#undef CLIP_POST_MS
#undef CLIP_QUIET_MS
#undef RING_SAMPLES
#define CLIP_PRE_MS 500
#define CLIP_POST_MS 1500
#define CLIP_QUIET_MS 1500
#define RING_SAMPLES (2 * MIC_SAMPLE_RATE) // 2 с: увесь кліп
#define CLIP_LABEL "датасет"
#else
#define CLIP_LABEL "кандидат"
#endif
