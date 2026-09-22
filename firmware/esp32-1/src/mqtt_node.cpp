#include "mqtt_node.h"
#include "logging.h"
#include "node_id.h"
#include "payload.h"
#include <espMqttClient.h>

// 내부 태스크를 끄고, loop()는 MqttNode::tick()에서만 돌림
// 인자 없는 생성자는 priority/core를 받는 쪽이 잡혀 내부 태스크가 켜지고,
// 라이브러리 태스크와 우리 loop이 같은 상태 기계를 동시에 돌려
// TCP 소켓이 두 개 열리고 성공한 연결을 스스로 닫음
static espMqttClient s_mqtt(espMqttClientTypes::UseInternalTask::NO);

MqttNode::MqttNode(WifiManager& wifi, const char* host, uint16_t port)
    : wifi_(wifi),
      host_(host),
      port_(port),
      state_(MqttState::Disconnected),
      lastAttempt_(0),
      backoffMs_(BACKOFF_MIN_MS) {
}

void MqttNode::begin() {                                                        //접속 전 설정, setup()에서 한번 호출
    willTopic_   = hestia::nodeStatusTopic(nodeId());
    willPayload_ = hestia::buildStatus(nodeId(), 0, false, false);              //willTopic, willPayload-> LWT 메시지(보드가 죽은 뒤 발행될 메시지) 미리 생성

    //클라이언트 설정 다섯가지
    s_mqtt.setServer(host_, port_);                                             //어디로 접속할지
    s_mqtt.setClientId(nodeId());                                               //브로커가 해당 연결을 식별하는 이름
    s_mqtt.setKeepAlive(60);                                                    //60초마다 브로커와 생존 신호(빈 패킷)를 주고받음=> LWT 발동 기준
    s_mqtt.setCleanSession(true);                                               //접속 해제시, 브로커가 해당 연결 상태를 기억하지 않게 함
    s_mqtt.setWill(willTopic_.c_str(), 1, true, willPayload_.c_str());          //node status (LWT)가 QoS=1, retained=true

    //콜백 등록
    s_mqtt.onConnect([this](bool sessionPresent) {                              //접속에 성공할 시, 실행할 코드 지정
        (void)sessionPresent;
        this->handleConnect();
    });
    s_mqtt.onDisconnect([this](espMqttClientTypes::DisconnectReason reason) {
        this->handleDisconnect(static_cast<int>(reason));
    });

    //설정 확인 로그
    logInfo("MQTT", "broker=%s:%u client=%s", host_, port_, nodeId());
    logInfo("MQTT", "will=%s", willTopic_.c_str());
}

void MqttNode::startAttempt() {
    logInfo("MQTT", "connecting...");
    s_mqtt.connect();
    lastAttempt_ = millis();
    state_ = MqttState::Connecting;
}

void MqttNode::handleConnect() {
    logInfo("MQTT", "connected, took=%lums",
            (unsigned long)(millis() - lastAttempt_));
    state_     = MqttState::Connected;
    backoffMs_ = BACKOFF_MIN_MS;
}

void MqttNode::handleDisconnect(int reason) {
    uint32_t now = millis();

    if (state_ == MqttState::Connected) {
        logWarn("MQTT", "disconnected, reason=%d", reason);
        backoffMs_ = BACKOFF_MIN_MS;
    } else if (state_ == MqttState::Connecting) {
        logWarn("MQTT", "connect failed, reason=%d, retry in %lums",
                reason, (unsigned long)backoffMs_);
    } else {
        return;          // 이미 Disconnected — 중복 콜백 무시
    }

    lastAttempt_ = now;
    state_       = MqttState::Disconnected;
}

void MqttNode::tick() {
    s_mqtt.loop();

    if (!wifi_.isConnected()) {
        return;
    }

    if (state_ == MqttState::Disconnected) {
        uint32_t now = millis();
        if (now - lastAttempt_ >= backoffMs_) {
            startAttempt();
            backoffMs_ *= 2;
            if (backoffMs_ > BACKOFF_MAX_MS) {
                backoffMs_ = BACKOFF_MAX_MS;
            }
        }
    }
}

bool MqttNode::isConnected() const {
    return state_ == MqttState::Connected;
}

bool MqttNode::publish(const char* topic, uint8_t qos, bool retain,
                       const char* payload) {
    if (state_ != MqttState::Connected) {
        return false;
    }
    uint16_t packetId = s_mqtt.publish(topic, qos, retain, payload);
    return (qos == 0) || (packetId != 0);
}