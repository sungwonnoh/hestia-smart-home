#pragma once

// 이 노드(esp32 보드)의 ID MAC 주소 뒤 3바이트로 생성 — "esp32-ab34cd"
// 반환 주소는 프로그램 내내 유효=> SeqCounter나 setWill()에 넘겨도 됨
const char* nodeId();