#include "payload.h"
#include <cstring>

namespace hestia {

void putEnvelope(JsonDocument& doc,
                 const char* src_id,
                 uint32_t seq,
                 uint32_t sent_ts) {
    doc["version"] = SCHEMA_VERSION;
    doc["sent_ts"] = sent_ts;
    doc["src_id"]  = src_id;
    doc["seq"]     = seq;
}

const char* typeName(SensorType type){
    switch (type) {
        case SensorType::Presence: return "presence";
        case SensorType::Motion: return "motion";
        case SensorType::Door: return "door";
        case SensorType::Power: return "power";
        case SensorType::Bed: return "bed";
        case SensorType::Light: return "light";
        case SensorType::Climate: return "climate";
    }
    return "unknown";
}

uint8_t qosFor(SensorType type) {
    switch (type) {
        case SensorType::Motion:
            return 0;
        default:
            return 1;
    }
}

bool isRetained(SensorType type) {
    switch (type) {
        case SensorType::Presence: return true;
        case SensorType::Motion: return false;
        case SensorType::Door: return true;
        case SensorType::Power: return true;
        case SensorType::Bed: return true;
        case SensorType::Light: return true;
        case SensorType::Climate: return true;
    }
    return true;
}

std::string sensorStateTopic(const char* virtual_id){
    std::string t = "hestia/sensor/";
    t += virtual_id;
    t += "/state";
    return t;
}

std::string deviceStateTopic(const char* virtual_id){
    std::string t = "hestia/device/";
    t += virtual_id;
    t += "/state";
    return t;
}

std::string notifyAckTopic(){
    return "hestia/notify/ack";
}

std::string buildPresence(const char* src_id, uint32_t seq, uint32_t sent_ts, 
    bool present, uint8_t energy, uint16_t distance_cm){
        JsonDocument doc;
        putEnvelope(doc, src_id, seq, sent_ts);

        doc["type"] = typeName(SensorType::Presence);
        doc["present"] = present;
        doc["confidence"] = "high";
        doc["energy"] = energy;
        doc["distance_cm"] = distance_cm;

        std::string out;
        serializeJson(doc, out);
        return out;    
    }

std::string buildMotion(const char* src_id, uint32_t seq, uint32_t sent_ts,
                        bool motion) {
    JsonDocument doc;
    putEnvelope(doc, src_id, seq, sent_ts);

    doc["type"] = typeName(SensorType::Motion);
    doc["motion"] = motion;
    doc["confidence"] = "low";

    std::string out;
    serializeJson(doc, out);
    return out;
}

std::string buildDoor(const char* src_id, uint32_t seq, uint32_t sent_ts,
                      bool open) {
    JsonDocument doc;
    putEnvelope(doc, src_id, seq, sent_ts);

    doc["type"] = typeName(SensorType::Door);
    doc["open"] = open;

    std::string out;
    serializeJson(doc, out);
    return out;
}

std::string buildPower(const char* src_id, uint32_t seq, uint32_t sent_ts,
                       uint16_t watt) {
    JsonDocument doc;
    putEnvelope(doc, src_id, seq, sent_ts);

    doc["type"] = typeName(SensorType::Power);
    doc["watt"] = watt;

    std::string out;
    serializeJson(doc, out);
    return out;
}

std::string buildBed(const char* src_id, uint32_t seq, uint32_t sent_ts,
                     bool occupied) {
    JsonDocument doc;
    putEnvelope(doc, src_id, seq, sent_ts);

    doc["type"] = typeName(SensorType::Bed);
    doc["occupied"] = occupied;

    std::string out;
    serializeJson(doc, out);
    return out;
}

std::string buildLight(const char* src_id, uint32_t seq, uint32_t sent_ts,
                       uint32_t illuminance_lux) {
    JsonDocument doc;
    putEnvelope(doc, src_id, seq, sent_ts);

    doc["type"] = typeName(SensorType::Light);
    doc["illuminance_lux"] = illuminance_lux;

    std::string out;
    serializeJson(doc, out);
    return out;
}

std::string buildClimate(const char* src_id, uint32_t seq, uint32_t sent_ts,
                         float temperature_c, uint8_t humidity_pct) {
    JsonDocument doc;
    putEnvelope(doc, src_id, seq, sent_ts);

    doc["type"] = typeName(SensorType::Climate);
    doc["temperature_c"] = temperature_c;
    doc["humidity_pct"] = humidity_pct;

    std::string out;
    serializeJson(doc, out);
    return out;
}

std::string buildDisplayState(const char* src_id, uint32_t seq, uint32_t sent_ts,
                              const char* display,
                              const char* notify_id) {
    JsonDocument doc;
    putEnvelope(doc, src_id, seq, sent_ts);

    doc["device_type"] = "display_node";
    doc["source"]      = "esp32";
    doc["power"]       = "ON";
    doc["display"]     = display;

    if (notify_id != nullptr && notify_id[0] != '\0') {
        doc["notify_id"] = notify_id;
    }

    std::string out;
    serializeJson(doc, out);
    return out;
}

//SeqCounter 클래스 함수
SeqCounter::SeqCounter() : count_(0) { }

bool SeqCounter::next(const char* virtual_id, uint32_t& out_seq) {
    // 이미 등록된 id인지
    for (uint8_t i = 0; i < count_; i++) {
        if (strcmp(slots_[i].id, virtual_id) == 0) { //strcmp는 두 문자열이 같으면 0 반환
            slots_[i].seq++;
            out_seq = slots_[i].seq;
            return true;
        }
    }
    // 처음 보는 id — 빈 슬롯에 등록
    if (count_ < MAX_IDS) {
        slots_[count_].id  = virtual_id; // count_가 곧 다음 빈 칸
        slots_[count_].seq = 0;
        out_seq = 0;
        count_++;
        return true;
    }
    // 슬롯 부족
    return false;
}

//노드(esp32보드) status 함수
std::string buildStatus(const char* node_id, uint32_t sent_ts,
                        bool online, bool ts_synced){
    JsonDocument doc;
    doc["version"] = SCHEMA_VERSION;
    doc["sent_ts"] = sent_ts;
    doc["src_id"]  = node_id;
    doc["online"]  = online;
    doc["ts_synced"] = ts_synced;

    std::string out;
    serializeJson(doc, out);
    return out;
}

std::string nodeStatusTopic(const char* node_id){
    std::string t = "hestia/node/";
    t += node_id;
    t += "/status";
    return t;
}

std::string nodeAnnounceTopic(const char* node_id){
    std::string t = "hestia/node/";
    t += node_id;
    t += "/announce";
    return t;
}


//노드(esp32) announce
Emulated sensorEmulated(const char* virtual_id, SensorType type) {
    return Emulated{ virtual_id, false, typeName(type) };
}

Emulated deviceEmulated(const char* virtual_id, const char* device_type) {
    return Emulated{ virtual_id, true, device_type };
}


//노드(esp32) announce
std::string buildAnnounce(const char* node_id, uint32_t seq, uint32_t sent_ts,
                          const char* fw,
                          const Emulated* items, uint8_t count){
    JsonDocument doc;
    putEnvelope(doc, node_id, seq, sent_ts);
    doc["fw"] = fw;
    JsonArray arr = doc["emulates"].to<JsonArray>();

    for (uint8_t i = 0; i < count; i++){
        JsonObject item = arr.add<JsonObject>();
        item["virtual_id"] = items[i].virtual_id;
        if (items[i].is_device) {
            item["device_type"] = items[i].type_name;
        } else {
            item["type"] = items[i].type_name;
        }
        item["source"] = "esp32";
    }

    std::string out;
    serializeJson(doc, out);
    return out;
}


// ── 알림 수신·확인 ───────────────────────────
const char* const ACK_DELIVERED = "DELIVERED";
const char* const ACK_SEEN      = "SEEN";

static void copyField(char* dst, size_t cap, const char* src) {
    if (src == nullptr) { dst[0] = '\0'; return; }
    strncpy(dst, src, cap - 1);
    dst[cap - 1] = '\0';
}

bool isForMe(const JsonDocument& doc, const char* my_virtual_id) {
    if (my_virtual_id == nullptr) return false;

    JsonArrayConst arr = doc["channels"].as<JsonArrayConst>();
    if (arr.isNull()) return false;

    for (JsonVariantConst v : arr) {
        const char* s = v.as<const char*>();
        if (s != nullptr && strcmp(s, my_virtual_id) == 0) {
            return true;
        }
    }
    return false;
}

NotifyPush parseNotifyPush(const JsonDocument& doc) {
    NotifyPush p{};              // 전 필드를 0으로
    p.valid = false;

    copyField(p.notify_id, sizeof(p.notify_id), doc["notify_id"] | "");
    if (p.notify_id[0] == '\0') {
        return p;                // ack·cancel 을 짝지을 수 없다
    }

    copyField(p.title,    sizeof(p.title),    doc["payload"]["title"] | "");
    copyField(p.text,     sizeof(p.text),     doc["payload"]["text"]  | "");
    copyField(p.priority, sizeof(p.priority), doc["priority"]         | "normal");
    copyField(p.scenario, sizeof(p.scenario), doc["scenario"]         | "");

    p.requires_ack = doc["requires_ack"] | false;
    p.valid = true;
    return p;
}

std::string buildNotifyAck(const char* src_id, uint32_t seq, uint32_t sent_ts,
                           const char* notify_id, const char* ack_type) {
    JsonDocument doc;
    putEnvelope(doc, src_id, seq, sent_ts);

    doc["notify_id"] = notify_id;
    doc["ack_type"]  = ack_type;

    std::string out;
    serializeJson(doc, out);
    return out;
}

}  // namespace hestia