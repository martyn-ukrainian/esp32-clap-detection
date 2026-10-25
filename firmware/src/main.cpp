// Цикл: мікрофон → гучність вікна 10 мс → удар → модель «хлопок?» → два хлопки → GET /toggle на Pi. Плюс кліпи кандидатів (CLIP_UPLOAD).
#include "clap.h"
#include "clap_model.h"
#include "clap_model_params.h"
#include "config.h"
#include "intent.h"
#include "level.h"
#include "mic.h"
#include "net.h"
#include "ring.h"

#include "esp_log.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"

#include <stdio.h>

static const char *TAG = "clap";

#define MS_TO_WINDOWS(ms) ((ms) * MIC_SAMPLE_RATE / 1000 / WINDOW_SAMPLES)
#define CLIP_SAMPLES ((CLIP_PRE_MS + CLIP_POST_MS) * MIC_SAMPLE_RATE / 1000)

// Подія = від першого вікна з піком над порогом до CLIP_QUIET_MS тиші. Одна подія — один кліп.
typedef struct {
    bool active;
    int quiet;     // вікон тиші поспіль
    int post_left; // вікон до вирізання кліпу, 0 = кліп уже вирізано
    int peak;
    int rms;
} event_t;

static int16_t ring_buf[RING_SAMPLES];
static int16_t clip[CLIP_SAMPLES];

static void track_event(event_t *ev, level_t lv, const ring_t *ring, uint32_t now_ms)
{
    if (now_ms < CLAP_IGNORE_MS) return; // старт мікрофона
    if (lv.peak > CLIP_PEAK_THRESHOLD) {
        ev->quiet = 0;
        if (!ev->active) {
            *ev = {};
            ev->active = true;
            ev->post_left = MS_TO_WINDOWS(CLIP_POST_MS);
        }
        if (lv.peak > ev->peak) ev->peak = lv.peak;
        if (lv.rms > ev->rms) ev->rms = lv.rms;
    } else if (ev->active && ++ev->quiet >= MS_TO_WINDOWS(CLIP_QUIET_MS)) {
        ev->active = false;
    }

    if (ev->post_left > 0 && --ev->post_left == 0) {
        int n = ring_last(ring, clip, CLIP_SAMPLES);
        // Детектора ще немає, тож усе — «кандидат». Мітки відкинуто/хлопок/два_хлопки дасть clap і intent.
        if (net_clip(clip, n, CLIP_LABEL, ev->peak, ev->rms)) {
            ESP_LOGI(TAG, "кандидат: пік %d, кліп %d мс у черзі", ev->peak, n * 1000 / MIC_SAMPLE_RATE);
        } else {
            ESP_LOGW(TAG, "кандидат: пік %d, кліп пропущено (усього %lu)", ev->peak, (unsigned long)net_clips_dropped());
        }
    }
}

extern "C" void app_main()
{
    mic_init();
    ESP_LOGI(TAG, "мікрофон: BCLK %d, WS %d, SD %d", MIC_BCLK_GPIO, MIC_WS_GPIO, MIC_DIN_GPIO);
#if DATASET
    ESP_LOGW(TAG, "прошивка для датасету: світло не перемикаю, кліпи %d+%d мс", CLIP_PRE_MS, CLIP_POST_MS);
#endif
    ESP_LOGI(TAG, "модель хлопка %s", CLAP_MODEL_VERSION);
    net_init();

    // Watchdog на цей цикл: не кликнув esp_task_wdt_reset за LOOP_WDT_MS — паніка й перезапуск
    esp_task_wdt_config_t wdt = {};
    wdt.timeout_ms = LOOP_WDT_MS;
    wdt.trigger_panic = true;
    if (esp_task_wdt_reconfigure(&wdt) == ESP_ERR_INVALID_STATE) esp_task_wdt_init(&wdt);
    esp_task_wdt_add(NULL);

    static int32_t raw[WINDOW_SAMPLES];
    static int16_t pcm[WINDOW_SAMPLES];
    level_state_t st = {};
    ring_t ring;
    ring_init(&ring, ring_buf, RING_SAMPLES);
    event_t ev = {};
    clap_model_state_t model = {};
    intent_state_t intent = {};
    uint32_t now_ms = 0; // час за кількістю семплів, без таймера
    level_t loudest = {};
    int windows = 0;
    while (true) {
        esp_task_wdt_reset();
        int n = mic_read(raw, WINDOW_SAMPLES);
        level_t lv = level_window(raw, n, &st, pcm);
        ring_write(&ring, pcm, n);
        now_ms += n * 1000 / MIC_SAMPLE_RATE;

        float p;
        if (now_ms >= CLAP_IGNORE_MS && clap_model_window(&model, lv, now_ms, &ring, &p)) {
            bool is_clap = p >= 0.5f;
            ESP_LOGI(TAG, "удар: модель %.2f → %s", p, is_clap ? "ХЛОПОК" : "ні");
            // at — коли був звук, sent — коли пішло (uptime, мс): Pi рахує, скільки чекало на платі й у Wi-Fi
            unsigned long t = esp_timer_get_time() / 1000;
            char params[64];
            snprintf(params, sizeof(params), "p=%.2f at=%lu sent=%lu", p, t - CLAP_VIEW_POST_MS, t);
            net_udp("event", params);
            if (is_clap) intent_on_clap(&intent, now_ms - CLAP_VIEW_POST_MS);
        }
        if (intent_tick(&intent, now_ms) == INTENT_TOGGLE) {
#if DATASET
            ESP_LOGI(TAG, "ДВА ХЛОПКИ (датасет: світло не чіпаю)");
#else
            ESP_LOGI(TAG, "ДВА ХЛОПКИ → перемкнути");
            net_udp("toggle", ""); // першим: світло важливіше за звіт
#endif
            unsigned long t = esp_timer_get_time() / 1000;
            char params[64];
            snprintf(params, sizeof(params), "pair=1 at=%lu sent=%lu", t, t);
            net_udp("event", params);
        }
#if CLIP_UPLOAD
        track_event(&ev, lv, &ring, now_ms);
#endif

        if (lv.peak > loudest.peak) loudest = lv;
        if (++windows < LOG_EVERY_WINDOWS) continue;
#if LOG_RAW
        // Нулі весь час = мікрофон не відповідає (дроти, пайка, L/R).
        // Сирий 00000000 = SD притиснутий до землі або мікрофон без живлення, ffffffff = SD висить угорі.
        ESP_LOGI(TAG, "rms %7d  пік %8d  сирі %08lx %08lx %08lx %08lx", loudest.rms, loudest.peak,
                 (unsigned long)raw[0], (unsigned long)raw[1], (unsigned long)raw[2], (unsigned long)raw[3]);
#else
        ESP_LOGI(TAG, "rms %7d  пік %8d", loudest.rms, loudest.peak);
#endif
        loudest = {};
        windows = 0;
    }
}
