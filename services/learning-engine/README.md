# HESTIA Learning Engine

사용자의 과거 생활 데이터를 기반으로 개인별 생활 패턴 Baseline을 생성하고, 현재 행동 시각이 평소 패턴에서 얼마나 벗어났는지 판단합니다.

현재 MVP에서는 CASAS Aruba의 아침 준비 시각을 이용해 KDE 기반 Baseline을 구현합니다.

## 목적

다음 질문에 답하는 것이 현재 Learning Engine의 역할입니다.

> "이 사람 기준으로 현재 시각의 아침 준비가 평소보다 이상하게 늦은가?"

## 전체 흐름

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

## baseline.py

파일:

```text
services/learning-engine/baseline.py
```

실행 예시:

```bash
python3 services/learning-engine/baseline.py --time 07:30
```

```bash
python3 services/learning-engine/baseline.py --time 09:40
```

현재 Aruba 데이터 기준:

```text
Observed days       : 212
Mean                : 07:18
Std                 : 75.2 min
```

### Tail Probability

특정 시각 이후에 활동이 발생할 KDE 확률입니다.

예:

```text
09:40
→ Tail Probability 2.82%
```

이는 KDE Baseline에서 09:40보다 늦게 아침 준비가 시작될 확률이 약 2.82%라는 의미입니다.

현재 threshold:

```text
TAIL_THRESHOLD = 0.05
```

따라서:

```text
tail_probability < 0.05
→ Time anomaly = YES
```

## Predictability

KDE 분포의 entropy를 이용해 사용자의 생활 패턴이 얼마나 규칙적인지 계산합니다.

```text
높은 값 → 규칙적인 패턴
낮은 값 → 불규칙한 패턴
```

현재 threshold:

```text
PREDICTABILITY_THRESHOLD = 0.05
```

따라서:

```text
predictability >= 0.05
→ Pattern reliable = YES
```

## 최종 판단

현재 MVP 정책:

```python
if tail_probability < 0.05 and predictability >= 0.05:
    decision = "INTERVENE"
else:
    decision = "NORMAL"
```

즉 단순히 시간이 늦다는 이유만으로 개입하지 않습니다.

```text
현재 시각이 평소보다 이례적임
AND
사용자의 생활 패턴 자체가 충분히 규칙적임
```

두 조건을 모두 만족할 때만 개입 후보로 판단합니다.

## Aruba 예시

09:40 입력:

```text
Tail probability    : 2.82%
Predictability      : 0.036

Time anomaly        : YES
Pattern reliable    : NO

Decision            : NORMAL
```

해석:

```text
09:40은 평소보다 상당히 늦지만,
이 사용자의 아침 활동 시간이 비교적 불규칙하기 때문에
시간 정보만으로는 개입하지 않는다.
```

## Predictability Validation

파일:

```text
services/learning-engine/validate_predictability.py
```

Synthetic Data의 표준편차를 변경하며 predictability가 규칙성을 올바르게 표현하는지 검증합니다.

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

표준편차가 증가할수록 predictability가 일관되게 감소하는 것을 확인했습니다.

## 현재 역할 분담

```text
RPi4
Learning Engine
- 사용자 History 저장
- KDE Baseline 생성
- Predictability 계산
- 향후 Batch Learning 수행

        ↓ MQTT

RPi5
Context Engine
- 현재 World State 관리
- Learning Engine의 Baseline 활용
- Rule / Policy와 결합
- 최종 개입 여부 결정
```

향후에는 Learning Engine 결과를 `hestia/model/kde` MQTT Topic으로 전달하도록 확장합니다.
