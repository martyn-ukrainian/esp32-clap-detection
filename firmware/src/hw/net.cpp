#include "net.h"
#include "config.h"

#include <stdio.h>
#include <string.h>

#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_random.h"
#include "esp_wifi.h"
#include "lwip/sockets.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "nvs_flash.h"

static const char *TAG = "net";

#define CONNECTED_BIT BIT0

typedef struct {
    int16_t *pcm; // у PSRAM, звільняє задача відправки
    int n;
    char label[32];
    int peak;
    int rms;
    int64_t queued_us; // коли кліп вирізано: звук був CLIP_POST_MS тому
} clip_job_t;

static EventGroupHandle_t events;
static QueueHandle_t queue;
static QueueHandle_t commands; // шляхи GET, напр. "/toggle"; окремо від кліпів, щоб не чекали за ними
static uint32_t dropped = 0;
static int udp_sock = -1;
static struct sockaddr_in udp_to;
static uint32_t udp_boot; // випадкове на кожен старт: Pi відрізняє повтор від нового пакета після перезапуску
static uint32_t udp_seq = 0;
static int64_t sent_at[CLIP_MAX_PER_MIN]; // часи останніх кліпів, для ліміту за хвилину
static int sent_idx = 0;

static void on_wifi(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(events, CONNECTED_BIT);
        wifi_event_sta_disconnected_t *ev = (wifi_event_sta_disconnected_t *)data;
        ESP_LOGW(TAG, "Wi-Fi відпав (причина %d), підключаюсь знову", ev->reason);
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *ev = (ip_event_got_ip_t *)data;
        ESP_LOGI(TAG, "IP " IPSTR, IP2STR(&ev->ip_info.ip));
        xEventGroupSetBits(events, CONNECTED_BIT);
    }
}

// Кирилиця в мітці → %XX, інакше URL зламається
static void url_encode(const char *src, char *dst, int cap)
{
    static const char *hex = "0123456789ABCDEF";
    int j = 0;
    for (const unsigned char *p = (const unsigned char *)src; *p && j < cap - 4; p++) {
        if ((*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z') || (*p >= '0' && *p <= '9') || *p == '_' || *p == '-') {
            dst[j++] = *p;
        } else {
            dst[j++] = '%';
            dst[j++] = hex[*p >> 4];
            dst[j++] = hex[*p & 15];
        }
    }
    dst[j] = 0;
}

static void send_clip(const clip_job_t *job)
{
    char label[100];
    url_encode(job->label, label, sizeof(label));
    char url[256];
    // age — скільки мс минуло від звуку до відправки (Pi рахує з нього затримку)
    int age = CLIP_POST_MS + (int)((esp_timer_get_time() - job->queued_us) / 1000);
    snprintf(url, sizeof(url), "%s/clip?label=%s&peak=%d&rms=%d&age=%d", PI_URL, label, job->peak, job->rms, age);

    esp_http_client_config_t cfg = {};
    cfg.url = url;
    cfg.method = HTTP_METHOD_POST;
    cfg.timeout_ms = 5000;
    esp_http_client_handle_t client = esp_http_client_init(&cfg);
    esp_http_client_set_header(client, "Content-Type", "application/octet-stream");
    esp_http_client_set_post_field(client, (const char *)job->pcm, job->n * sizeof(int16_t));
    esp_err_t err = esp_http_client_perform(client);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "кліп %s, %d мс → HTTP %d", job->label, job->n * 1000 / MIC_SAMPLE_RATE,
                 esp_http_client_get_status_code(client));
    } else {
        ESP_LOGW(TAG, "кліп %s не пішов: %s", job->label, esp_err_to_name(err));
    }
    esp_http_client_cleanup(client);
}

static void send_get(const char *path)
{
    char url[160];
    if (strstr(path, "at="))  // подія з часом звуку: дописати час відправки, Pi порахує, скільки вона чекала на платі
        snprintf(url, sizeof(url), "%s%s&sent=%lu", PI_URL, path, (unsigned long)(esp_timer_get_time() / 1000));
    else
        snprintf(url, sizeof(url), "%s%s", PI_URL, path);
    esp_http_client_config_t cfg = {};
    cfg.url = url;
    cfg.timeout_ms = 5000;
    esp_http_client_handle_t client = esp_http_client_init(&cfg);
    esp_err_t err = esp_http_client_perform(client);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "%s → HTTP %d", path, esp_http_client_get_status_code(client));
    } else {
        ESP_LOGW(TAG, "%s: Pi не відповідає (%s): %s", path, PI_URL, esp_err_to_name(err));
    }
    esp_http_client_cleanup(client);
}

static void sender_task(void *arg)
{
    xEventGroupWaitBits(events, CONNECTED_BIT, pdFALSE, pdTRUE, portMAX_DELAY);
    send_get("/event?boot=1"); // перевірка зв'язку з Pi, лампи не чіпає
    clip_job_t job;
    while (true) {
        xQueueReceive(queue, &job, portMAX_DELAY);
        xEventGroupWaitBits(events, CONNECTED_BIT, pdFALSE, pdTRUE, portMAX_DELAY);
        send_clip(&job);
        heap_caps_free(job.pcm);
    }
}

static void command_task(void *arg)
{
    char path[NET_PATH_LEN];
    while (true) {
        xQueueReceive(commands, path, portMAX_DELAY);
        xEventGroupWaitBits(events, CONNECTED_BIT, pdFALSE, pdTRUE, portMAX_DELAY);
        send_get(path);
    }
}

void net_init()
{
    esp_err_t err = nvs_flash_init(); // Wi-Fi тримає калібрування в NVS
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    events = xEventGroupCreate();
    queue = xQueueCreate(CLIP_QUEUE_LEN, sizeof(clip_job_t));
    commands = xQueueCreate(4, NET_PATH_LEN);

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t init_cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init_cfg));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &on_wifi, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &on_wifi, NULL));

    wifi_config_t wifi_cfg = {};
    strncpy((char *)wifi_cfg.sta.ssid, WIFI_SSID, sizeof(wifi_cfg.sta.ssid));
    strncpy((char *)wifi_cfg.sta.password, WIFI_PASSWORD, sizeof(wifi_cfg.sta.password));
    // Дві точки з однією назвою (роутер і підсилювач), беремо найсильнішу
    wifi_cfg.sta.scan_method = WIFI_ALL_CHANNEL_SCAN;
    wifi_cfg.sta.sort_method = WIFI_CONNECT_AP_BY_SIGNAL;
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg));
    ESP_ERROR_CHECK(esp_wifi_start());
    // Без енергозбереження: інакше плата прокидається раз на ~300 мс, пінг 2–3 с і кліпи по 64 КБ застрягають
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    ESP_LOGI(TAG, "Wi-Fi: %s, Pi: %s, UDP %d", WIFI_SSID, PI_URL, PI_UDP_PORT);

    udp_boot = esp_random();
    udp_sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    udp_to.sin_family = AF_INET;
    udp_to.sin_port = htons(PI_UDP_PORT);
    inet_pton(AF_INET, PI_HOST, &udp_to.sin_addr);

    // Той самий пріоритет, що в app_main (1): мережа не відбиратиме час у мікрофона
    xTaskCreate(sender_task, "net_send", 6144, NULL, 1, NULL);
    xTaskCreate(command_task, "net_cmd", 6144, NULL, 1, NULL);
}

bool net_clip(const int16_t *pcm, int n, const char *label, int peak, int rms)
{
    int64_t now = esp_timer_get_time();
    // Найстаріший із останніх CLIP_MAX_PER_MIN кліпів молодший за хвилину = ліміт вичерпано
    if (sent_at[sent_idx] && now - sent_at[sent_idx] < 60 * 1000000LL) {
        dropped++;
        return false;
    }
    clip_job_t job = {};
    job.pcm = (int16_t *)heap_caps_malloc(n * sizeof(int16_t), MALLOC_CAP_SPIRAM);
    if (!job.pcm) {
        dropped++;
        return false;
    }
    memcpy(job.pcm, pcm, n * sizeof(int16_t));
    job.n = n;
    strncpy(job.label, label, sizeof(job.label) - 1);
    job.peak = peak;
    job.rms = rms;
    job.queued_us = now;
    if (xQueueSend(queue, &job, 0) != pdTRUE) {
        heap_caps_free(job.pcm);
        dropped++;
        return false;
    }
    sent_at[sent_idx] = now;
    sent_idx = (sent_idx + 1) % CLIP_MAX_PER_MIN;
    return true;
}

void net_udp(const char *kind, const char *params)
{
    if (udp_sock < 0 || !(xEventGroupGetBits(events) & CONNECTED_BIT)) return;
    char msg[128];
    int n = snprintf(msg, sizeof(msg), "%s %08lx %lu %s", kind, (unsigned long)udp_boot, (unsigned long)++udp_seq, params);
    for (int k = 0; k < 2; k++) // Wi-Fi і так повторює кадр без ACK; другий пакет — від втрати в черзі
        sendto(udp_sock, msg, n, 0, (struct sockaddr *)&udp_to, sizeof(udp_to));
}

bool net_command(const char *path)
{
    char buf[NET_PATH_LEN] = {};
    strncpy(buf, path, sizeof(buf) - 1);
    return xQueueSend(commands, buf, 0) == pdTRUE;
}

uint32_t net_clips_dropped()
{
    return dropped;
}
