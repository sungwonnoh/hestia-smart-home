#include <Arduino.h>
#include "logging.h"
#include "wifi_manager.h"
#include "mqtt_node.h"
#include "time_sync.h"
#include "node_id.h"
#include "payload.h"
#include "display.h"
#include "notify_store.h"
#include "console.h"
#include "secrets.h"

static const char* MY_VIRTUAL_ID = "vd-10";

static WifiManager wifi(WIFI_SSID, WIFI_PASSWORD);
static MqttNode    mqtt(wifi, MQTT_HOST, MQTT_PORT);
static NotifyStore notifies;
static hestia::SeqCounter seqs;

static bool     wasWifiUp     = false;
static bool     wasMqttUp     = false;
static uint32_t lastHeartbeat = 0;
static uint32_t lastDeviceState = 0;

static const uint32_t DEVICE_STATE_MS = 300000;   // 5분

// ── 발행 ─────────────────────────────────────
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

static void publishAnnounce() {
    static const hestia::Emulated kEmulates[] = {
        hestia::deviceEmulated(MY_VIRTUAL_ID, "display_node"),
    };

    uint32_t seq = 0;
    if (!seqs.next(nodeId(), seq)) {
        logError("ANN", "seq slots full");
        return;
    }

    std::string topic   = hestia::nodeAnnounceTopic(nodeId());
    std::string payload = hestia::buildAnnounce(nodeId(), seq, nowTs(),
                                                FW_VERSION, kEmulates, 1);
    if (mqtt.publish(topic.c_str(), 1, false, payload.c_str())) {
        logInfo("ANN", "%s", payload.c_str());
    }
}

static void publishDeviceState() {
    const hestia::NotifyPush* top = notifies.latest();
    const char* nid = (top != nullptr) ? top->notify_id : nullptr;

    uint32_t seq = 0;
    if (!seqs.next(MY_VIRTUAL_ID, seq)) {
        logError("DEV", "seq slots full");
        return;
    }

    std::string topic = hestia::deviceStateTopic(MY_VIRTUAL_ID);
    std::string payload = hestia::buildDisplayState(
        MY_VIRTUAL_ID, seq, nowTs(), displayCurrent(), nid);

    if (mqtt.publish(topic.c_str(), 1, true, payload.c_str())) {
        logInfo("DEV", "%s", payload.c_str());
    } else {
        logWarn("DEV", "publish failed");
    }
    lastDeviceState = millis();
}

static void publishAck(const char* notify_id, const char* ack_type) {
    uint32_t seq = 0;
    if (!seqs.next(MY_VIRTUAL_ID, seq)) {
        logError("NOTI", "seq slots full");
        return;
    }

    std::string topic   = hestia::notifyAckTopic();
    std::string payload = hestia::buildNotifyAck(MY_VIRTUAL_ID, seq, nowTs(),
                                                 notify_id, ack_type);
    if (mqtt.publish(topic.c_str(), 1, false, payload.c_str())) {
        logInfo("NOTI", "ack %s %s", ack_type, notify_id);
    } else {
        logWarn("NOTI", "ack publish failed");
    }
}

// ── 수신 ─────────────────────────────────────
static void onNotifyPush(const char* payload, size_t len) {
    JsonDocument doc;
    if (deserializeJson(doc, payload, len) != DeserializationError::Ok) {
        logWarn("NOTI", "push parse failed");
        return;
    }
    if (!hestia::isForMe(doc, MY_VIRTUAL_ID)) {
        return;                              // 남의 알림
    }

    hestia::NotifyPush p = hestia::parseNotifyPush(doc);
    if (!p.valid) {
        logWarn("NOTI", "push missing notify_id");
        return;
    }

    notifies.add(p, millis());
    displayShow(p.title, p.text, p.priority);
    publishAck(p.notify_id, hestia::ACK_DELIVERED);
    publishDeviceState();
}

static void onNotifyCancel(const char* payload, size_t len) {
    JsonDocument doc;
    if (deserializeJson(doc, payload, len) != DeserializationError::Ok) {
        return;
    }
    const char* nid = doc["notify_id"] | "";
    if (nid[0] == '\0') return;

    if (!notifies.remove(nid)) {
        return;                              // 내가 안 들고 있던 알림
    }
    logInfo("NOTI", "cancel %s", nid);

    const hestia::NotifyPush* top = notifies.latest();
    if (top != nullptr) {
        displayShow(top->title, top->text, top->priority);
    } else {
        displayClear();
    }
    publishDeviceState();
}

static void onMessage(const char* topic, const char* payload, size_t len) {
    if (strcmp(topic, "hestia/notify/push") == 0) {
        onNotifyPush(payload, len);
    } else if (strcmp(topic, "hestia/notify/cancel") == 0) {
        onNotifyCancel(payload, len);
    } else {
        logInfo("MQTT", "rx %s (%u bytes)", topic, (unsigned)len);
    }
}

// ── 콘솔 ─────────────────────────────────────
static void onConsoleLine(const char* line) {
    if (strcmp(line, "list") == 0) {
        logInfo("CON", "active=%u display=\"%s\"",
                notifies.count(), displayCurrent());
        for (uint8_t i = 0; i < NotifyStore::MAX_ACTIVE; i++) {
            const hestia::NotifyPush* p = notifies.at(i);
            if (p != nullptr) {
                logInfo("CON", "  [%u] %s %s", i, p->notify_id, p->text);
            }
        }
        return;
    }

    if (strncmp(line, "seen", 4) == 0) {
        const char* nid = nullptr;
        if (line[4] == ' ') {
            nid = line + 5;                  // "seen n-001"
        } else {
            const hestia::NotifyPush* top = notifies.latest();
            if (top == nullptr) {
                logWarn("CON", "no active notify");
                return;
            }
            nid = top->notify_id;
        }
        publishAck(nid, hestia::ACK_SEEN);
        return;
    }

    logWarn("CON", "unknown: %s  (try: seen, list)", line);
}

// ── 생명주기 ─────────────────────────────────
void setup() {
    logInit(115200);
    logInfo("BOOT", "HESTIA node=%s vid=%s fw=%s schema=%d",
            nodeId(), MY_VIRTUAL_ID, FW_VERSION, SCHEMA_VERSION);

    consoleBegin(onConsoleLine);

    wifi.begin();
    mqtt.begin();
    mqtt.setMessageHandler(onMessage);
    mqtt.subscribe("hestia/notify/push", 1);
    mqtt.subscribe("hestia/notify/cancel", 1);
    mqtt.subscribe("hestia/system/profile", 1);
}

void loop() {
    wifi.tick();

    bool wifiUp = wifi.isConnected();
    if (wifiUp && !wasWifiUp) {
        timeSyncBegin();
    }
    wasWifiUp = wifiUp;

    mqtt.tick();
    consoleTick();

    bool mqttUp = mqtt.isConnected();
    bool tsChanged = timeSyncTick();

    if (mqttUp && !wasMqttUp) {
        publishStatus();
        publishAnnounce();
        publishDeviceState();        // 부팅 직후 display:"" — retained 청소
    } else if (tsChanged && mqttUp) {
        publishStatus();
    }
    wasMqttUp = mqttUp;

    if (mqttUp && millis() - lastHeartbeat >= HEARTBEAT_MS) {
        publishStatus();
    }
    if (mqttUp && millis() - lastDeviceState >= DEVICE_STATE_MS) {
        publishDeviceState();
    }

    delay(10);
}