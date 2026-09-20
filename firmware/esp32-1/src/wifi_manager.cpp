#include "wifi_manager.h"
#include "logging.h"
#include <WiFi.h>

WifiManager::WifiManager(const char* ssid, const char* password)
    : ssid_(ssid),
      password_(password),
      state_(WifiState::Disconnected),
      lastAttempt_(0),
      backoffMs_(BACKOFF_MIN_MS) {
}

void WifiManager::begin() {
    WiFi.mode(WIFI_STA);                    //ESP32의 Wi-Fi 칩의 두 역할 중 Station=> 공유기에 붙는 쪽
    WiFi.setAutoReconnect(true);            //ESP32의 자체 기능인 재연결(자체 기능은 한 번 성공했던 연결이 끊긴 경우에만 재시도)
    WiFi.persistent(false);                 //Wi-Fi 설정(SSID, 비번)을 플래시 메모리에 저장하지 않음
    logInfo("WIFI", "ssid=\"%s\"", ssid_);
    startAttempt();                         //Wi-Fi 연결 시도, begin()은 시도 후 즉시 반환
}

void WifiManager::startAttempt() {
    logInfo("WIFI", "connecting...");
    WiFi.begin(ssid_, password_);           //ESP32 라이브러리의 begin() 함수=> 실제 접속 시도
    lastAttempt_ = millis();                //시도한 시각 기록
    state_ = WifiState::Connecting;
}

void WifiManager::tick() {                  //loop() 안에서 초당 수십번 호출되며 '지금 무엇을 할 때인가' 판단(대부분의 호출은 아무 일도 없이 끝남)
    uint32_t now = millis();

    switch (state_) {

    case WifiState::Connected:
        if (WiFi.status() != WL_CONNECTED) {    //연결 끊김
            logWarn("WIFI", "disconnected");
            state_      = WifiState::Disconnected;
            backoffMs_  = BACKOFF_MIN_MS;       //재시도 시간 1초로 초기화
            lastAttempt_ = now;
        }
        break;

    case WifiState::Connecting:                 //WiFi.begin()을 부른 뒤 결과를 기다리는 상태
        if (WiFi.status() == WL_CONNECTED) {    //연결 성공
            logInfo("WIFI", "connected ip=%s rssi=%d took=%lums",
                    WiFi.localIP().toString().c_str(),
                    WiFi.RSSI(),                //신호 세기
                    (unsigned long)(now - lastAttempt_));   //연결되는데 걸린 시간
            state_     = WifiState::Connected;
            backoffMs_ = BACKOFF_MIN_MS;
        } else if (now - lastAttempt_ > CONNECT_TIMEOUT_MS) { //타임아웃 초과
            logWarn("WIFI", "attempt timed out, retry in %lums",
                    (unsigned long)backoffMs_);
            state_ = WifiState::Disconnected;
        }
        break;

    case WifiState::Disconnected:           //재시도 시점
        if (now - lastAttempt_ >= backoffMs_) {
            startAttempt();
            backoffMs_ *= 2;
            if (backoffMs_ > BACKOFF_MAX_MS) {
                backoffMs_ = BACKOFF_MAX_MS;
            }
        }
        break;
    }
}

bool WifiManager::isConnected() const {
    return state_ == WifiState::Connected;
}