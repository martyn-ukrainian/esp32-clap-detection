#include "mic.h"
#include "config.h"

#include "driver/i2s_std.h"
#include "freertos/FreeRTOS.h"

static i2s_chan_handle_t mic;

void mic_init()
{
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    ESP_ERROR_CHECK(i2s_new_channel(&chan_cfg, NULL, &mic));

    // INMP441 віддає 24 біти в 32-бітному слоті, вирівняні вліво
    i2s_std_config_t std_cfg = {};
    std_cfg.clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(MIC_SAMPLE_RATE);
    std_cfg.slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO);
    std_cfg.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT; // L/R на GND
    std_cfg.gpio_cfg.mclk = GPIO_NUM_NC;
    std_cfg.gpio_cfg.bclk = MIC_BCLK_GPIO;
    std_cfg.gpio_cfg.ws = MIC_WS_GPIO;
    std_cfg.gpio_cfg.dout = GPIO_NUM_NC;
    std_cfg.gpio_cfg.din = MIC_DIN_GPIO;
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(mic, &std_cfg));
    ESP_ERROR_CHECK(i2s_channel_enable(mic));
}

int mic_read(int32_t *buf, int max_samples)
{
    size_t got = 0;
    i2s_channel_read(mic, buf, max_samples * sizeof(int32_t), &got, portMAX_DELAY);
    return got / sizeof(int32_t);
}
