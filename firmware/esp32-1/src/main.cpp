#include <Arduino.h>
#include "logging.h"
#include "wifi_manager.h"
#include "mqtt_node.h"
#include "node_id.h"
#include "payload.h"
#include "secrets.h"

static WifiManager wifi(WIFI_SSID, WIFI_PASSWORD);
static MqttNode    mqtt(wifi, MQTT_HOST, MQTT_PORT);

void setup() {
    logInit(115200);
    logInfo("BOOT", "HESTIA node=%s fw=%s schema=%d",
            nodeId(), FW_VERSION, SCHEMA_VERSION);
    wifi.begin();
    mqtt.begin();
}

void loop() {
    wifi.tick();
    mqtt.tick();
    delay(10);
}