#include <Arduino.h>
#include "logging.h"
#include "wifi_manager.h"
#include "mqtt_node.h"
#include "time_sync.h"
#include "node_id.h"
#include "payload.h"
#include "secrets.h"

static WifiManager wifi(WIFI_SSID, WIFI_PASSWORD);
static MqttNode    mqtt(wifi, MQTT_HOST, MQTT_PORT);

static bool     wasWifiUp     = false;
static bool     wasMqttUp     = false;
static uint32_t lastHeartbeat = 0;

static void publishStatus() {
    std::string topic   = hestia::nodeStatusTopic(nodeId());
    std::string payload = hestia::buildStatus(nodeId(), nowTs(),
                                              true, isTimeSynced());

    if (mqtt.publish(topic.c_str(), 1, true, payload.c_str())) {
        logInfo("STAT", "%s", payload.c_str());
    } else {
        logWarn("STAT", "publish failed");
    }
    lastHeartbeat = millis();
}

void setup() {
    logInit(115200);
    logInfo("BOOT", "HESTIA node=%s fw=%s schema=%d",
            nodeId(), FW_VERSION, SCHEMA_VERSION);
    wifi.begin();
    mqtt.begin();
}

void loop() {
    wifi.tick();

    bool wifiUp = wifi.isConnected();
    if (wifiUp && !wasWifiUp) {
        timeSyncBegin();
    }
    wasWifiUp = wifiUp;

    mqtt.tick();

    bool mqttUp = mqtt.isConnected();
    if (mqttUp && !wasMqttUp) {
        publishStatus();                    // ① 접속 직후
    }
    wasMqttUp = mqttUp;

    if (timeSyncTick() && mqttUp) {
        publishStatus();                    // ② 동기화 상태 변화
    }

    if (mqttUp && millis() - lastHeartbeat >= HEARTBEAT_MS) {
        publishStatus();                    // ③ 30초마다
    }

    delay(10);
}