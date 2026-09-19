# Learning Engine

## Overview

Learning Engine은 사용자의 과거 생활 데이터를 기반으로
개인별 생활 패턴 Baseline을 생성합니다.

현재 MVP에서는 CASAS Aruba의 아침 준비 시각을 이용해
Gaussian KDE 기반 Baseline을 구현합니다.

목표 질문:

> "이 사용자를 기준으로 09:40에 아침 준비를 시작하는 것이 평소보다 이상하게 늦은가?"

---

## Pipeline

```text
breakfast_preparation.csv
        ↓
시간을 실수값으로 변환
        ↓
Gaussian KDE
        ↓
개인별 시간 분포
        ↓
Tail Probability
+
Predictability
        ↓
Time Anomaly
+
Pattern Reliability
        ↓
NORMAL / INTERVENE
```

---

## KDE Baseline

실행:

```bash
python3 services/learning-engine/baseline.py --time 07:30
```

또는:

```bash
python3 services/learning-engine/baseline.py --time 09:40
```

현재 Aruba 데이터 기준 예시:

```text
Observed days       : 212
Mean                : 07:18
Std                 : 75.2 min

Query               : 09:40
Tail probability    : 2.82%
Predictability      : 0.036

Time anomaly        : YES
Pattern reliable    : NO

Decision            : NORMAL
```

---

## Tail Probability

특정 시각 이후에 활동이 발생할 KDE 확률입니다.

현재 기준:

```text
TAIL_THRESHOLD = 0.05
```

따라서:

```text
tail_probability < 0.05
→ Time anomaly = YES
```

---

## Predictability

KDE 분포의 Entropy를 이용해 사용자의 생활 패턴 규칙성을 계산합니다.

```text
높은 값 → 규칙적인 패턴
낮은 값 → 불규칙한 패턴
```

현재 기준:

```text
PREDICTABILITY_THRESHOLD = 0.05
```

따라서:

```text
predictability >= 0.05
→ Pattern reliable = YES
```

---

## Final Decision

현재 MVP 정책:

```python
if tail_probability < 0.05 and predictability >= 0.05:
    decision = "INTERVENE"
else:
    decision = "NORMAL"
```

단순히 시간이 늦다는 이유만으로 개입하지 않고,
사용자의 패턴 자체가 충분히 규칙적인 경우에만 개입 후보로 판단합니다.

---

## Predictability Validation

Synthetic Data를 이용해 표준편차와 Predictability의 관계를 검증했습니다.

실행:

```bash
python3 services/learning-engine/validate_predictability.py
```

검증 결과:

```text
std 10 min  → predictability 0.380
std 20 min  → predictability 0.257
std 30 min  → predictability 0.176
std 45 min  → predictability 0.111
std 60 min  → predictability 0.069
std 75 min  → predictability 0.029
std 90 min  → predictability 0.022
std 120 min → predictability 0.004
```

표준편차가 증가할수록 Predictability가 일관되게 감소하는 것을 확인했습니다.

---

## Next Step

Learning Engine의 다음 목표는 KDE 결과를 MQTT Payload로 변환해
Raspberry Pi 5의 Context Engine에서 사용할 수 있도록 연결하는 것입니다.

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
