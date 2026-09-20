#include "time_sync.h"
#include "logging.h"
#include <ctime>
#include <Arduino.h> 

static const uint32_t SYNC_THRESHOLD = 1700000000;   // 2023-11. 명세서 기준

static bool lastSynced = false;                      //이전에 동기화 되었는가를 나타내는 플래그  

void timeSyncBegin() {
    configTime(0, 0, "pool.ntp.org", "time.google.com");
    logInfo("NTP", "requested (utc), waiting...");
}

bool isTimeSynced() {
    return (uint32_t)time(nullptr) > SYNC_THRESHOLD;
}

uint32_t nowTs() {
    return (uint32_t)time(nullptr);
}

bool timeSyncTick() {                                   //NTP 동기화 상태가 바뀐 순간에만 true (미동기 → 동기, 또는 그 반대)
    bool now = isTimeSynced();

    if (now == lastSynced) {
        return false;
    }

    lastSynced = now;

    if (now) {
        time_t t = time(nullptr);
        char iso[32];
        strftime(iso, sizeof(iso), "%Y-%m-%d %H:%M:%S", gmtime(&t));
        logInfo("NTP", "synced ts=%lu utc=%s", (unsigned long)t, iso);
    } else {
        logWarn("NTP", "lost sync");
    }

    return true;
}