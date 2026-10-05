#include "notify_store.h"
#include <cstring>

NotifyStore::NotifyStore() : seqCounter_(0) {
    for (uint8_t i = 0; i < MAX_ACTIVE; i++) {
        slots_[i].used = false;
        slots_[i].received_ms = 0;
    }
}

void NotifyStore::add(const hestia::NotifyPush& push, uint32_t now_ms) {
    // 같은 id 가 이미 있으면 갱신
    for (uint8_t i = 0; i < MAX_ACTIVE; i++) {
        if (slots_[i].used &&
            strcmp(slots_[i].push.notify_id, push.notify_id) == 0) {
            slots_[i].push = push;
            slots_[i].received_ms = ++seqCounter_;
            return;
        }
    }

    // 빈 자리
    for (uint8_t i = 0; i < MAX_ACTIVE; i++) {
        if (!slots_[i].used) {
            slots_[i].push = push;
            slots_[i].received_ms = ++seqCounter_;
            slots_[i].used = true;
            return;
        }
    }

    // 자리 없음 — 가장 오래된 것을 밀어낸다
    uint8_t oldest = 0;
    for (uint8_t i = 1; i < MAX_ACTIVE; i++) {
        if (slots_[i].received_ms < slots_[oldest].received_ms) {
            oldest = i;
        }
    }
    slots_[oldest].push = push;
    slots_[oldest].received_ms = ++seqCounter_;
}

bool NotifyStore::remove(const char* notify_id) {
    if (notify_id == nullptr) return false;

    for (uint8_t i = 0; i < MAX_ACTIVE; i++) {
        if (slots_[i].used &&
            strcmp(slots_[i].push.notify_id, notify_id) == 0) {
            slots_[i].used = false;
            return true;
        }
    }
    return false;
}

void NotifyStore::clear() {
    for (uint8_t i = 0; i < MAX_ACTIVE; i++) {
        slots_[i].used = false;
    }
}

uint8_t NotifyStore::count() const {
    uint8_t n = 0;
    for (uint8_t i = 0; i < MAX_ACTIVE; i++) {
        if (slots_[i].used) n++;
    }
    return n;
}

const hestia::NotifyPush* NotifyStore::latest() const {
    const hestia::NotifyPush* best = nullptr;
    uint32_t bestSeq = 0;

    for (uint8_t i = 0; i < MAX_ACTIVE; i++) {
        if (slots_[i].used && slots_[i].received_ms >= bestSeq) {
            bestSeq = slots_[i].received_ms;
            best = &slots_[i].push;
        }
    }
    return best;
}

const hestia::NotifyPush* NotifyStore::at(uint8_t i) const {
    if (i >= MAX_ACTIVE || !slots_[i].used) return nullptr;
    return &slots_[i].push;
}