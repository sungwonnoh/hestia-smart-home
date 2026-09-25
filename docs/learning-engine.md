# Learning Engine

## Overview

Learning Engine은 사용자의 과거 생활 데이터를 기반으로
개인별 생활 패턴 Baseline을 생성합니다.

현재 MVP에서는 CASAS Aruba의 아침 준비 시각을 이용해
Gaussian KDE 기반 Meal Baseline을 구현합니다.

현재 목표 질문:

> "이 사용자에게 09:40이라는 시각이 평소 Meal 패턴 대비 얼마나 늦은 시각인가?"

Learning Engine은 최종 개입 여부를 직접 결정하지 않습니다.

RPi4는 KDE 모델과 Predictability를 생성하고,
최종 판단은 RPi5 Context Engine의 Policy가 담당합니다.

## Pipeline

```text
breakfast_preparation.csv
        ↓
시간을 분 단위로 변환
        ↓
Gaussian KDE
        ↓
24시간 / 15분 Grid
        ↓
96-bin density
        ↓
Predictability
        ↓
MQTT Model Payload
        ↓
hestia/model/kde
        ↓
RPi5 Context Engine
```

## KDE Model

현재 KDE는 하루 전체를 다음 Grid로 표현합니다.

```text
grid_min  = 0
grid_step = 15
grid_size = 96
```

즉:

```text
24시간 × 60분 / 15분 = 96 bins
```

각 bin의 density는 전체 합이 약 1.0이 되도록 정규화합니다.

현재 Aruba Breakfast Proxy 기준:

```text
Observed days : 212
```

예시 Query:

```text
07:30
→ tail probability ≈ 45.10%

09:40
→ tail probability ≈ 4.01%

predictability ≈ 0.330
```

## Tail Probability

특정 시각 이후에 활동이 발생할 probability mass를 계산합니다.

```text
낮은 tail probability
→ 평소보다 늦은 시각일 가능성이 높음

높은 tail probability
→ 아직 평소 활동 범위 안에 있음
```

현재 Policy의 임시 기준:

```text
TAIL_THRESHOLD = 0.05
```

따라서:

```text
tail_probability < 0.05
→ Meal Time Anomaly
```

## Predictability

KDE density의 Entropy를 이용해 사용자의 생활 패턴 규칙성을 계산합니다.

```text
높은 값
→ 특정 시간대에 활동이 집중됨
→ 규칙적인 패턴

낮은 값
→ 여러 시간대에 활동이 분산됨
→ 불규칙한 패턴
```

현재 Aruba Breakfast Proxy 기준:

```text
predictability ≈ 0.330
```

현재 Policy에는 임시로 다음 기준을 사용합니다.

```text
PREDICTABILITY_THRESHOLD = 0.05
```

단, 기존 KDE 정의에서 사용하던 threshold이므로
24시간 / 96-bin 구조에 맞춰 재검증이 필요합니다.

## MQTT Model Payload

Learning Engine은 KDE 결과를 다음 topic으로 전달합니다.

```text
hestia/model/kde
```

Payload 예시:

```json
{
  "version": 1,
  "sent_ts": 1790341434,
  "src_id": "rpi4",
  "trained_at": 1790341434,
  "sample_days": 212,
  "distributions": {
    "meal_time": {
      "grid_min": 0,
      "grid_step": 15,
      "density": []
    }
  },
  "predictability": {
    "meal_time": 0.330
  }
}
```

현재 MQTT 설정:

```text
QoS = 1
retain = true
```

## Context Engine Integration

RPi5는 KDE payload를 수신해 최신 모델을 메모리에 저장합니다.

```text
hestia/model/kde
        ↓
ContextEngine.ingest()
        ↓
model_ingest.py
        ↓
kde_model.py
        ↓
ModelStore
        ↓
kde_context.py
```

Policy는 다음 두 값을 사용합니다.

```text
tail_probability
predictability
```

현재 최소 Meal Policy:

```python
if (
    tail_probability < TAIL_THRESHOLD
    and predictability >= PREDICTABILITY_THRESHOLD
):
    candidate = True
else:
    candidate = False
```

이 결과는 최종 Intervention이 아니라
World State / FSM과 결합되기 전의 `intervention candidate`입니다.

## Replay Validation

실제 Raspberry Pi 연결 전에도 개인화 동작을 검증할 수 있도록
Replay 기반 A/B 시나리오를 구성했습니다.

동일한 시각:

```text
09:40
```

User A:

```text
tail_probability = 0.0
predictability = 0.35
candidate = True
reason = MEAL_TIME_ANOMALY
```

User B:

```text
tail_probability = 0.60
predictability = 0.25
candidate = False
reason = MEAL_TIME_NORMAL
```

즉 동일한 시각과 동일한 Policy에서도
개인별 KDE 분포에 따라 판단 결과가 달라지는 것을 확인했습니다.

## Test

Context Engine 관련 테스트:

```bash
python3 -m pytest tests/context-engine
```

현재 결과:

```text
20 passed
```

검증 항목:

- KDE payload parsing
- 96-bin validation
- ModelStore
- Model ingest
- KDE Context 조회
- Tail Probability
- Predictability
- Meal Policy
- MQTT / Replay ingest routing
- A/B Personalized Meal Scenario

## Current Limitation

현재 학습 데이터는 전체 Meal 패턴이 아니라
다음 규칙으로 생성한 Breakfast Proxy입니다.

```text
05:00 ~ 11:00
+
하루 첫 Meal_Preparation begin
```

따라서 현재 구현은 KDE 구조와 개인화 판단 로직 검증이 목적입니다.

향후에는 breakfast / lunch / dinner를 포함한
전체 `meal_time` multi-modal distribution으로 확장합니다.

## Next Step

다음 단계:

1. 실제 RPi4에서 KDE 모델 생성
2. `hestia/model/kde` Publish
3. RPi5 Context Engine 수신
4. retained KDE 모델 재수신 검증
5. Predictability Threshold 재검증
6. World State / FSM과 Meal Policy 연결
