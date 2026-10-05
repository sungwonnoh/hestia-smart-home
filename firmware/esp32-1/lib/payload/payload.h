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
std::string deviceStateTopic(const char* virtual_id);
std::string notifyAckTopic();

std::string buildPresence(const char* src_id, uint32_t seq, uint32_t sent_ts, bool present, uint8_t energy, uint16_t distance_cm);
std::string buildMotion(const char* src_id, uint32_t seq, uint32_t sent_ts, bool motion);
std::string buildDoor(const char* src_id, uint32_t seq, uint32_t sent_ts, bool open);
std::string buildPower(const char* src_id, uint32_t seq, uint32_t sent_ts, uint16_t watt);
std::string buildBed(const char* src_id, uint32_t seq, uint32_t sent_ts, bool occupied);
std::string buildLight(const char* src_id, uint32_t seq, uint32_t sent_ts, uint32_t illuminance_lux);
std::string buildClimate(const char* src_id, uint32_t seq, uint32_t sent_ts, float temperature_c, uint8_t humidity_pct);
std::string buildDisplayState(const char* src_id, uint32_t seq, uint32_t sent_ts,
                              const char* display,
                              const char* notify_id);       // display: 표시 중인 문구. 없으면 "" / notify_id: 표시 중인 알림 id. nullptr이면 필드를 넣지 않는다.

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
    bool        is_device;      // true면 "device_type" 키, false면 "type" 키
    const char* type_name;      // "motion" 또는 "display_node"
};

// 생성 헬퍼 — 센서는 enum을 거쳐 오타를 막는다
Emulated sensorEmulated(const char* virtual_id, SensorType type);
Emulated deviceEmulated(const char* virtual_id, const char* device_type);

std::string buildAnnounce(const char* node_id, uint32_t seq, uint32_t sent_ts, const char* fw, const Emulated* items, uint8_t count);


// ── 알림 수신·확인 ───────────────────────────
extern const char* const ACK_DELIVERED;   // "DELIVERED"
extern const char* const ACK_SEEN;        // "SEEN"

// notify/push 를 해석한 결과. 문자열은 내용을 복사해 보관한다
// (수신 콜백의 버퍼는 콜백이 끝나면 사라지므로).
struct NotifyPush {
    char notify_id[40];
    char title[64];
    char text[128];
    char priority[8];      // low / normal / high / health
    char scenario[24];
    bool requires_ack;
    bool valid;            // false면 처리하지 않는다
};

// channels 배열에 my_virtual_id 가 들어 있는가
bool isForMe(const JsonDocument& doc, const char* my_virtual_id);

// 파싱 실패나 notify_id 부재 시 valid=false 로 돌려준다
NotifyPush parseNotifyPush(const JsonDocument& doc);

std::string buildNotifyAck(const char* src_id, uint32_t seq, uint32_t sent_ts,
                           const char* notify_id, const char* ack_type);

}  // namespace hestia