#pragma once

#include <cstdint>
#include <string>
#include "wifi_manager.h"

enum class MqttState : uint8_t { Disconnected, Connecting, Connected };                 //MQTT 상태 세 가지

class MqttNode {
public:
    MqttNode(WifiManager& wifi, const char* host, uint16_t port);

    void begin();                  // setup()에서 1회
    void tick();                   // loop()에서 매번
    bool isConnected() const;

    // 성공하면 true. 미연결이거나 큐가 가득 차면 false
    bool publish(const char* topic, uint8_t qos, bool retain, const char* payload);


    // 수신 콜백. (topic, payload, len)
    typedef void (*MessageHandler)(const char* topic, const char* payload, size_t len);
    void setMessageHandler(MessageHandler handler);
    void subscribe(const char* topic, uint8_t qos);

private:
    static const uint32_t BACKOFF_MIN_MS = 1000;
    static const uint32_t BACKOFF_MAX_MS = 30000;

    void startAttempt();
    void handleConnect();
    void handleDisconnect(int reason);
    void resubscribe();

    WifiManager& wifi_;
    const char*  host_;
    uint16_t     port_;

    std::string  willTopic_;       // 수명 때문에 멤버로 보관
    std::string  willPayload_;

    MqttState    state_;
    uint32_t     lastAttempt_;
    uint32_t     backoffMs_;

    MessageHandler msgHandler_;

    static const uint8_t MAX_SUBS = 6;
    struct Sub { const char* topic; uint8_t qos; };
    Sub     subs_[MAX_SUBS];
    uint8_t subCount_;
};