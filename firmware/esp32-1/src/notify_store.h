#pragma once

#include <cstdint>
#include "payload.h"

class NotifyStore {
public:
    static const uint8_t MAX_ACTIVE = 4;

    NotifyStore();

    // 새 알림을 보관한다. 같은 notify_id 가 있으면 덮어쓴다.
    // 자리가 없으면 가장 오래된 것을 밀어낸다.
    void add(const hestia::NotifyPush& push, uint32_t now_ms);

    // notify_id 로 제거. 있었으면 true
    bool remove(const char* notify_id);

    void clear();

    uint8_t count() const;

    // 가장 최근에 들어온 것. 없으면 nullptr
    const hestia::NotifyPush* latest() const;

    const hestia::NotifyPush* at(uint8_t i) const;

private:
    struct Slot {
        hestia::NotifyPush push;
        uint32_t received_ms;
        bool     used;
    };

    Slot    slots_[MAX_ACTIVE];
    uint32_t seqCounter_;      // 들어온 순서. latest() 판정용
};