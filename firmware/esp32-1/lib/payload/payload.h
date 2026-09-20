#pragma once

#include <ArduinoJson.h>
#include <cstdint>
#include <string>

namespace hestia {

enum class SensorType : uint8_t { Presence, Motion, Door, Power, Bed, Light, Climate };

void putEnvelope(JsonDocument& doc,
                 const char* src_id,
                 uint32_t seq,
                 uint32_t sent_ts);

const char*  typeName(SensorType type);
uint8_t      qosFor(SensorType type);
bool         isRetained(SensorType type);
std::string   sensorStateTopic(const char* virtual_id);

std::string buildPresence(const char* src_id, uint32_t seq, uint32_t sent_ts, bool present, uint8_t energy, uint16_t distance_cm);
std::string buildMotion(const char* src_id, uint32_t seq, uint32_t sent_ts, bool motion);
std::string buildDoor(const char* src_id, uint32_t seq, uint32_t sent_ts, bool open);
std::string buildPower(const char* src_id, uint32_t seq, uint32_t sent_ts, uint16_t watt);
std::string buildBed(const char* src_id, uint32_t seq, uint32_t sent_ts, bool occupied);
std::string buildLight(const char* src_id, uint32_t seq, uint32_t sent_ts, uint32_t illuminance_lux);
std::string buildClimate(const char* src_id, uint32_t seq, uint32_t sent_ts, float temperature_c, uint8_t humidity_pct);

class SeqCounter {
public:
    static const uint8_t MAX_IDS = 8;

    SeqCounter();
    
    // virtual_id의 다음 seq를 out_seq에 담음
    // 처음 보는 id면 0부터 시작
    // 슬롯이 가득 차면 false를 반환하고 out_seq는 건드리지 않음
    bool next(const char* virtual_id, uint32_t& out_seq);

private:
    struct Slot {
        const char* id;
        uint32_t    seq;
    };

    Slot slots_[MAX_IDS];
    uint8_t count_;
};

//노드(esp32보드) status
std::string buildStatus(const char* node_id, uint32_t sent_ts, bool online, bool ts_synced);
std::string nodeStatusTopic(const char* node_id);
std::string nodeAnnounceTopic(const char* node_id);

//노드(esp32보드) announce
struct Emulated {
    const char* virtual_id;
    SensorType  type;
};

std::string buildAnnounce(const char* node_id, uint32_t seq, uint32_t sent_ts, const char* fw, const Emulated* items, uint8_t count);

}  // namespace hestia