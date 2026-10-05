#include <unity.h>
#include "payload.h"

using namespace hestia;

void setUp(void) {}
void tearDown(void) {}

// ── 환경 확인 ────────────────────────────────
void test_sanity(void) {
    TEST_ASSERT_EQUAL(2, 1 + 1);
}

void test_schema_version_defined(void) {
    TEST_ASSERT_EQUAL(1, SCHEMA_VERSION);
}

void test_type_name(void) {
    TEST_ASSERT_EQUAL_STRING("presence", typeName(SensorType::Presence));
    TEST_ASSERT_EQUAL_STRING("motion",   typeName(SensorType::Motion));
    TEST_ASSERT_EQUAL_STRING("door",     typeName(SensorType::Door));
    TEST_ASSERT_EQUAL_STRING("power",    typeName(SensorType::Power));
    TEST_ASSERT_EQUAL_STRING("bed",      typeName(SensorType::Bed));
    TEST_ASSERT_EQUAL_STRING("light",    typeName(SensorType::Light));
    TEST_ASSERT_EQUAL_STRING("climate",  typeName(SensorType::Climate));
}

// ── Envelope ─────────────────────────────────
void test_envelope_four_fields(void) {
    std::string json = buildDoor("vs-06", 88, 1755500000, true);

    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_EQUAL(1, doc["version"].as<int>());
    TEST_ASSERT_EQUAL(1755500000, doc["sent_ts"].as<uint32_t>());
    TEST_ASSERT_EQUAL_STRING("vs-06", doc["src_id"].as<const char*>());
    TEST_ASSERT_EQUAL(88, doc["seq"].as<uint32_t>());
}

// ── QoS / retained 정책 ──────────────────────
void test_motion_policy(void) {
    TEST_ASSERT_EQUAL(0, qosFor(SensorType::Motion));
    TEST_ASSERT_FALSE(isRetained(SensorType::Motion));
}

void test_other_types_policy(void) {
    SensorType others[] = {
        SensorType::Presence, SensorType::Door, SensorType::Power,
        SensorType::Bed, SensorType::Light, SensorType::Climate
    };
    for (int i = 0; i < 6; i++) {
        TEST_ASSERT_EQUAL(1, qosFor(others[i]));
        TEST_ASSERT_TRUE(isRetained(others[i]));
    }
}

// ── 토픽 ─────────────────────────────────────
void test_topics(void) {
    TEST_ASSERT_EQUAL_STRING("hestia/sensor/vs-01/state",
                             sensorStateTopic("vs-01").c_str());
    TEST_ASSERT_EQUAL_STRING("hestia/node/esp32-ab34cd/status",
                             nodeStatusTopic("esp32-ab34cd").c_str());
    TEST_ASSERT_EQUAL_STRING("hestia/node/esp32-ab34cd/announce",
                             nodeAnnounceTopic("esp32-ab34cd").c_str());
    TEST_ASSERT_EQUAL_STRING("hestia/device/vd-10/state",
                             deviceStateTopic("vd-10").c_str());
    TEST_ASSERT_EQUAL_STRING("hestia/notify/ack",
                             notifyAckTopic().c_str());
}

// ── confidence 규칙 ──────────────────────────
void test_presence_confidence_high(void) {
    std::string json = buildPresence("vs-01", 4821, 1755500000, true, 42, 180);
    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_EQUAL_STRING("presence", doc["type"].as<const char*>());
    TEST_ASSERT_TRUE(doc["present"].as<bool>());
    TEST_ASSERT_EQUAL_STRING("high", doc["confidence"].as<const char*>());
    TEST_ASSERT_EQUAL(42, doc["energy"].as<int>());
    TEST_ASSERT_EQUAL(180, doc["distance_cm"].as<int>());
}

void test_motion_confidence_low(void) {
    std::string json = buildMotion("vs-03", 112, 1755500000, true);
    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_EQUAL_STRING("low", doc["confidence"].as<const char*>());
}

void test_no_confidence_on_deterministic_types(void) {
    JsonDocument doc;

    deserializeJson(doc, buildDoor("vs-06", 1, 100, true));
    TEST_ASSERT_TRUE(doc["confidence"].isNull());

    deserializeJson(doc, buildPower("vs-08", 1, 100, 1180));
    TEST_ASSERT_TRUE(doc["confidence"].isNull());

    deserializeJson(doc, buildBed("vs-07", 1, 100, true));
    TEST_ASSERT_TRUE(doc["confidence"].isNull());

    deserializeJson(doc, buildLight("vs-05l", 1, 100, 12));
    TEST_ASSERT_TRUE(doc["confidence"].isNull());

    deserializeJson(doc, buildClimate("vs-05c", 1, 100, 24.3f, 48));
    TEST_ASSERT_TRUE(doc["confidence"].isNull());
}

// ── 나머지 센서 필드 ─────────────────────────
void test_climate_fields(void) {
    std::string json = buildClimate("vs-05c", 56, 1755500000, 24.3f, 48);
    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_EQUAL_FLOAT(24.3f, doc["temperature_c"].as<float>());
    TEST_ASSERT_EQUAL(48, doc["humidity_pct"].as<int>());
}

void test_power_has_no_state(void) {
    std::string json = buildPower("vs-08", 204, 1755500000, 1180);
    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_EQUAL(1180, doc["watt"].as<int>());
    TEST_ASSERT_TRUE(doc["state"].isNull());
}

// ── seq 카운터 ───────────────────────────────
void test_seq_starts_at_zero_and_increments(void) {
    SeqCounter c;
    uint32_t s;

    TEST_ASSERT_TRUE(c.next("vs-01", s));
    TEST_ASSERT_EQUAL(0, s);
    TEST_ASSERT_TRUE(c.next("vs-01", s));
    TEST_ASSERT_EQUAL(1, s);
    TEST_ASSERT_TRUE(c.next("vs-01", s));
    TEST_ASSERT_EQUAL(2, s);
}

void test_seq_independent_per_virtual_id(void) {
    SeqCounter c;
    uint32_t s;

    c.next("vs-01", s);              // 0
    c.next("vs-01", s);              // 1
    c.next("vs-03", s);
    TEST_ASSERT_EQUAL(0, s);         // vs-03은 독립적으로 0부터

    c.next("vs-01", s);
    TEST_ASSERT_EQUAL(2, s);         // vs-01은 이어서 2
}

void test_seq_slots_full(void) {
    SeqCounter c;
    uint32_t s;
    const char* ids[] = {"a","b","c","d","e","f","g","h"};

    for (int i = 0; i < 8; i++) {
        TEST_ASSERT_TRUE(c.next(ids[i], s));
    }
    TEST_ASSERT_FALSE(c.next("overflow", s));   // 9번째는 실패
}

// ── status ───────────────────────────────────
void test_status_has_no_seq(void) {
    std::string json = buildStatus("esp32-ab34cd", 1755500000, true, true);
    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_EQUAL_STRING("esp32-ab34cd", doc["src_id"].as<const char*>());
    TEST_ASSERT_TRUE(doc["online"].as<bool>());
    TEST_ASSERT_TRUE(doc["ts_synced"].as<bool>());
    TEST_ASSERT_TRUE(doc["seq"].isNull());
}

void test_status_lwt_form(void) {
    std::string json = buildStatus("esp32-ab34cd", 0, false, false);
    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_FALSE(doc["online"].as<bool>());
    TEST_ASSERT_FALSE(doc["ts_synced"].as<bool>());
}

// ── announce ─────────────────────────────────
void test_announce_emulates(void) {
    Emulated items[] = {
        sensorEmulated("vs-01", SensorType::Presence),
        deviceEmulated("vd-10", "display_node")
    };
    std::string json = buildAnnounce("esp32-ab34cd", 1, 1755500000, "0.1.0", items, 2);

    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_EQUAL(1, doc["seq"].as<uint32_t>());
    TEST_ASSERT_EQUAL_STRING("0.1.0", doc["fw"].as<const char*>());

    JsonArray arr = doc["emulates"].as<JsonArray>();
    TEST_ASSERT_EQUAL(2, arr.size());

    TEST_ASSERT_EQUAL_STRING("vs-01", arr[0]["virtual_id"].as<const char*>());
    TEST_ASSERT_EQUAL_STRING("presence", arr[0]["type"].as<const char*>());
    TEST_ASSERT_EQUAL_STRING("esp32", arr[0]["source"].as<const char*>());
    TEST_ASSERT_TRUE(arr[0]["device_type"].isNull());

    TEST_ASSERT_EQUAL_STRING("vd-10", arr[1]["virtual_id"].as<const char*>());
    TEST_ASSERT_EQUAL_STRING("display_node", arr[1]["device_type"].as<const char*>());
    TEST_ASSERT_EQUAL_STRING("esp32", arr[1]["source"].as<const char*>());
    TEST_ASSERT_TRUE(arr[1]["type"].isNull());
}

void test_announce_empty_list(void) {
    std::string json = buildAnnounce("esp32-ab34cd", 1, 1755500000, "0.1.0", nullptr, 0);
    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_EQUAL(0, doc["emulates"].as<JsonArray>().size());
}


// ── display_node state ───────────────────────
void test_display_state_empty(void) {
    std::string json = buildDisplayState("vd-10", 7, 1791209970, "", nullptr);
    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_EQUAL_STRING("vd-10", doc["src_id"].as<const char*>());
    TEST_ASSERT_EQUAL(7, doc["seq"].as<uint32_t>());
    TEST_ASSERT_EQUAL_STRING("display_node", doc["device_type"].as<const char*>());
    TEST_ASSERT_EQUAL_STRING("esp32", doc["source"].as<const char*>());
    TEST_ASSERT_EQUAL_STRING("ON", doc["power"].as<const char*>());

    TEST_ASSERT_EQUAL_STRING("", doc["display"].as<const char*>());
    TEST_ASSERT_FALSE(doc["display"].isNull());    // 키는 있어야 한다
    TEST_ASSERT_TRUE(doc["notify_id"].isNull());   // 키가 없어야 한다
}

void test_display_state_showing(void) {
    std::string json = buildDisplayState("vd-10", 8, 1791209970,
                                         "물 한 잔 드세요", "n-20261005-001");
    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_EQUAL_STRING("물 한 잔 드세요", doc["display"].as<const char*>());
    TEST_ASSERT_EQUAL_STRING("n-20261005-001", doc["notify_id"].as<const char*>());
}

void test_display_state_blank_notify_id_omitted(void) {
    std::string json = buildDisplayState("vd-10", 9, 1791209970, "", "");
    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_TRUE(doc["notify_id"].isNull());   // "" 도 걸러낸다
}


// ── 알림 수신 ────────────────────────────────
static void loadPush(JsonDocument& doc, const char* json) {
    deserializeJson(doc, json);
}

void test_is_for_me(void) {
    JsonDocument doc;

    loadPush(doc, R"({"channels":["vd-01","vd-10"]})");
    TEST_ASSERT_TRUE(isForMe(doc, "vd-10"));

    loadPush(doc, R"({"channels":["vd-01"]})");
    TEST_ASSERT_FALSE(isForMe(doc, "vd-10"));

    loadPush(doc, R"({"channels":["voice"]})");
    TEST_ASSERT_FALSE(isForMe(doc, "vd-10"));

    loadPush(doc, R"({"channels":[]})");
    TEST_ASSERT_FALSE(isForMe(doc, "vd-10"));

    loadPush(doc, R"({})");                       // channels 자체가 없음
    TEST_ASSERT_FALSE(isForMe(doc, "vd-10"));
}

void test_parse_push_full(void) {
    JsonDocument doc;
    loadPush(doc, R"({"version":1,"sent_ts":1791197088,"src_id":"rpi5",
        "notify_id":"n-20261005-001","scenario":"WAKE_ROUTINE",
        "priority":"normal","channels":["vd-10"],
        "requires_ack":true,"ack_deadline":1791197688,"escalation_level":1,
        "payload":{"title":"수분 섭취","text":"물 한 잔 드세요"}})");

    NotifyPush p = parseNotifyPush(doc);

    TEST_ASSERT_TRUE(p.valid);
    TEST_ASSERT_EQUAL_STRING("n-20261005-001", p.notify_id);
    TEST_ASSERT_EQUAL_STRING("수분 섭취", p.title);
    TEST_ASSERT_EQUAL_STRING("물 한 잔 드세요", p.text);
    TEST_ASSERT_EQUAL_STRING("normal", p.priority);
    TEST_ASSERT_EQUAL_STRING("WAKE_ROUTINE", p.scenario);
    TEST_ASSERT_TRUE(p.requires_ack);
}

void test_parse_push_missing_fields(void) {
    JsonDocument doc;

    // notify_id 만 있는 최소 페이로드
    loadPush(doc, R"({"notify_id":"n-001"})");
    NotifyPush p = parseNotifyPush(doc);
    TEST_ASSERT_TRUE(p.valid);
    TEST_ASSERT_EQUAL_STRING("", p.title);
    TEST_ASSERT_EQUAL_STRING("", p.text);
    TEST_ASSERT_EQUAL_STRING("normal", p.priority);   // 기본값
    TEST_ASSERT_FALSE(p.requires_ack);

    // notify_id 없음 → 처리 불가
    loadPush(doc, R"({"priority":"high"})");
    TEST_ASSERT_FALSE(parseNotifyPush(doc).valid);

    // 타입이 다름
    loadPush(doc, R"({"notify_id":12345,"requires_ack":"yes"})");
    TEST_ASSERT_FALSE(parseNotifyPush(doc).valid);    // 숫자는 문자열로 못 읽음
}

void test_notify_ack(void) {
    std::string json = buildNotifyAck("vd-10", 42, 1791197100,
                                      "n-20261005-001", ACK_DELIVERED);
    JsonDocument doc;
    deserializeJson(doc, json);

    TEST_ASSERT_EQUAL_STRING("vd-10", doc["src_id"].as<const char*>());
    TEST_ASSERT_EQUAL(42, doc["seq"].as<uint32_t>());
    TEST_ASSERT_EQUAL_STRING("n-20261005-001", doc["notify_id"].as<const char*>());
    TEST_ASSERT_EQUAL_STRING("DELIVERED", doc["ack_type"].as<const char*>());

    json = buildNotifyAck("vd-10", 43, 1791197200, "n-20261005-001", ACK_SEEN);
    deserializeJson(doc, json);
    TEST_ASSERT_EQUAL_STRING("SEEN", doc["ack_type"].as<const char*>());
}


int main(int argc, char** argv) {
    UNITY_BEGIN();

    RUN_TEST(test_sanity);
    RUN_TEST(test_schema_version_defined);
    RUN_TEST(test_type_name);

    RUN_TEST(test_envelope_four_fields);
    RUN_TEST(test_motion_policy);
    RUN_TEST(test_other_types_policy);
    RUN_TEST(test_topics);

    RUN_TEST(test_presence_confidence_high);
    RUN_TEST(test_motion_confidence_low);
    RUN_TEST(test_no_confidence_on_deterministic_types);
    RUN_TEST(test_climate_fields);
    RUN_TEST(test_power_has_no_state);

    RUN_TEST(test_seq_starts_at_zero_and_increments);
    RUN_TEST(test_seq_independent_per_virtual_id);
    RUN_TEST(test_seq_slots_full);

    RUN_TEST(test_status_has_no_seq);
    RUN_TEST(test_status_lwt_form);

    RUN_TEST(test_announce_emulates);
    RUN_TEST(test_announce_empty_list);

    RUN_TEST(test_display_state_empty);
    RUN_TEST(test_display_state_showing);
    RUN_TEST(test_display_state_blank_notify_id_omitted);

    RUN_TEST(test_is_for_me);
    RUN_TEST(test_parse_push_full);
    RUN_TEST(test_parse_push_missing_fields);
    RUN_TEST(test_notify_ack);

    UNITY_END();
    return 0;
}