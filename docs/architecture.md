# HESTIA Architecture

## Overview

HESTIA는 ESP32, Raspberry Pi 5, Raspberry Pi 4를 역할별로 분리해 구성합니다.

```text
ESP32
  │
  │ Sensor / Device Event
  ▼
MQTT Broker
  │
  ▼
Raspberry Pi 5
Context Engine
  │
  │ World State / Policy
  │
  ├───────────────┐
  │               │
  ▼               ▼
External API   Raspberry Pi 4
               Learning Engine
                    │
                    │ Personal Baseline
                    │ KDE / HMM / Batch Learning
                    ▼
                  MQTT
```

## ESP32

센서 및 가전 상태를 수집하고 물리 입력을 처리합니다.

주요 역할:

- 센서 값 수집
- 가전 상태 Mock
- 버튼 등 물리 입력
- MQTT Publish

## Raspberry Pi 5

실시간 Context Engine을 담당합니다.

주요 역할:

- MQTT Broker
- 센서 이벤트 수신
- World State 관리
- Clock 주입
- Rule / FSM 기반 상태 판단
- Policy 실행
- 외부 API 연동
- Learning Engine 결과 반영

## Raspberry Pi 4

개인화 학습 계층을 담당합니다.

주요 역할:

- 사용자 History 저장
- 개인 Baseline 계산
- KDE 기반 시간 분포 학습
- 향후 HMM 재추정
- Batch Learning
- 이상 탐지
- 대시보드 / TTS 확장

## MQTT

각 장치와 서비스 간 이벤트를 전달합니다.

예상 흐름:

```text
ESP32
  ↓
sensor/event
  ↓
RPi5 Context Engine
  ↓
World State
  ↓
Policy
```

Learning Engine 결과는 향후 다음과 같이 전달합니다.

```text
RPi4 Learning Engine
        ↓
hestia/model/kde
        ↓
RPi5 Context Engine
        ↓
World State + Policy
        ↓
Final Intervention
```

## Design Principle

확정 가능한 상태는 Rule / FSM으로 판단하고,
모호한 상태는 통계 모델 또는 경량 AI 모델을 사용합니다.

예:

```text
문 열림 / 조명 ON
→ Rule / FSM

식사 / 수면 / 생활 패턴 이상
→ KDE / HMM / 경량 모델
```

이 구조를 통해 모델이 필요한 영역과 규칙으로 충분한 영역을 분리합니다.
