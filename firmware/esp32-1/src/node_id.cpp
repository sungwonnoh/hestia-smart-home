#include "node_id.h"
#include <cstdio>
#include <esp_mac.h>

const char* nodeId() {
    static char buf[16] = {0};                          //static 지역변수=> 수명: 프로그램 내내

    if (buf[0] == '\0') {                               //buf가 비어있으면, 0으로만 채워져 있으면
        uint8_t mac[6];
        esp_read_mac(mac, ESP_MAC_WIFI_STA);            //ESP_MAC_WIFI_STA-> esp32의 Wi-Fi 클라이언트 MAC 주소
        snprintf(buf, sizeof(buf), "esp32-%02x%02x%02x",
                 mac[3], mac[4], mac[5]);
    }

    return buf;
}