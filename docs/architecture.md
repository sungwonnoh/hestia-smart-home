# HESTIA Architecture

## Overview

HESTIA는 ESP32, Raspberry Pi 5, Raspberry Pi 4를 역할별로 분리해 구성합니다.

```text
ESP32
  │
  │ Sensor / Device Event
  ▼
MQTT Broker (RPi5)
  │
  ▼
Raspberry Pi 5
Context Engine
  │
  ├─ World State
  ├─ Rule / FSM
  ├─ KDE Context
  └─ Policy
       │
       ▼
Intervention Candidate

Raspberry Pi 4
Learning Engine
  │
  ├─ History
  ├─ KDE Baseline
  ├─ Predictability
  └─ Batch Learning
       │
       ▼
hestia/model/kde
       │
       └──────────────→ RPi5 Context Engine
```

## ESP32

센서 및 가전 상태를 수집하고 물리 입력을 처리합니다.

주요 역할:

- 센서 값 수집
- 가전 상태 Mock
- 버튼 등 물리 입력
- MQTT Publish

## Raspberry Pi 5

실시간 Context Engine과 MQTT Broker를 담당합니다.

주요 역할:

- MQTT Broker
- 센서 이벤트 수신
- Learning Engine 모델 수신
- World State 관리
- Clock 주입
- Rule / FSM 기반 상태 판단
- KDE 기반 개인화 Context 조회
- Policy 실행
- 외부 API 연동
- Intervention Candidate 생성

현재 Context Engine은 MQTT와 Replay 입력을 모두 단일 진입점으로 처리합니다.

```text
MQTT / Replay
      ↓
ContextEngine.ingest()
      ↓
 ┌───────────────┐
 │               │
model          sensor
 │               │
 ▼               ▼
handle_model   handle_event
```

## Raspberry Pi 4

개인화 학습 계층을 담당합니다.

주요 역할:

- 사용자 History 저장
- 개인 Baseline 계산
- KDE 기반 시간 분포 학습
- Predictability 계산
- MQTT Model Payload 생성
- 주기적 Batch Learning
- 향후 HMM 재추정
- 대시보드 / TTS 확장

현재 KDE 모델은 다음 topic으로 RPi5에 전달합니다.

```text
hestia/model/kde
```

## KDE Model Flow

현재 구현된 개인화 Meal KDE 흐름은 다음과 같습니다.

```text
CASAS Aruba
  ↓
Meal_Preparation begin
  ↓
Breakfast Proxy
  ↓
Gaussian KDE
  ↓
24h / 15min / 96-bin density
  ↓
Predictability
  ↓
MQTT Model Payload
  ↓
hestia/model/kde
  ↓
RPi5 ModelStore
  ↓
Tail Probability + Predictability
  ↓
Meal Policy
  ↓
Intervention Candidate
```

동일한 시각이라도 사용자별 KDE 분포가 다르면 서로 다른 판단을 내릴 수 있습니다.

```text
09:40

User A
→ tail probability 낮음
→ candidate = True

User B
→ tail probability 높음
→ candidate = False
```

## MQTT

각 장치와 서비스 간 이벤트 및 모델을 전달합니다.

센서 이벤트 흐름:

```text
ESP32
  ↓
hestia/sensor/#
  ↓
RPi5 Context Engine
  ↓
World State / Policy
```

Learning Engine 흐름:

```text
RPi4 Learning Engine
  ↓
hestia/model/kde
  ↓
RPi5 Context Engine
  ↓
ModelStore
  ↓
KDE Context
  ↓
Policy
```

KDE 모델은 QoS 1, retained 메시지로 전달하도록 구성합니다.

## Design Principle

확정 가능한 상태는 Rule / FSM으로 판단하고,
모호하거나 개인차가 큰 영역은 통계 모델 또는 경량 AI 모델을 사용합니다.

예:

```text
문 열림 / 조명 ON
→ Rule / FSM

식사 / 수면 / 생활 패턴 이상
→ KDE / HMM / 경량 모델
```

현재 KDE는 실시간 최종 결정을 직접 내리는 모델이 아니라,
RPi5 Policy가 사용할 개인화 Context를 제공하는 역할을 담당합니다.
