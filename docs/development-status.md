# Development Status

## Context Engine

- [x] Clock 주입 구조
- [x] MQTT / Replay ingest 단일 진입점
- [x] World State 기본 구조
- [x] Policy 계층 기본 구조
- [x] KDE Model Parser
- [x] KDE ModelStore
- [x] `hestia/model/kde` ingest
- [x] Meal KDE Context 조회
- [x] Tail Probability 기반 Meal Policy
- [x] Predictability 기반 Pattern Reliability
- [x] Replay A/B 개인화 검증
- [x] Context Engine 자동 테스트
- [ ] 실제 RPi MQTT E2E 테스트
- [ ] retained model 재수신 검증
- [ ] HOME / AWAY 상태 검출
- [ ] meal_done 상태 연결
- [ ] FSM / World State와 Meal Policy 통합
- [ ] Node Health Monitoring

## Learning Engine

- [x] CASAS Aruba 데이터 확보
- [x] `Meal_Preparation begin` 파싱
- [x] Breakfast Proxy 생성
- [x] Gaussian KDE Baseline
- [x] 24시간 / 15분 / 96-bin density 생성
- [x] Tail Probability
- [x] Entropy Predictability
- [x] KDE MQTT Model Payload 생성
- [x] MQTT Model Publisher 구현
- [x] Replay용 A/B KDE 모델 생성
- [ ] 실제 RPi4 → MQTT → RPi5 Publish 검증
- [ ] Predictability Threshold 재검증
- [ ] 전체 `meal_time` 데이터로 확장
- [ ] 사용자별 Baseline 영속 저장
- [ ] 주기적 Batch Learning
- [ ] HMM 기반 모호한 Activity 추론

## Current Meal KDE Flow

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
ContextEngine.ingest()
  ↓
ModelStore
  ↓
Tail Probability
  ↓
Meal Policy
  ↓
Intervention Candidate
```

## Replay Validation

동일한 시각 `09:40`에서 사용자별 KDE 분포만 다르게 설정해
개인화 판단이 달라지는 것을 검증했습니다.

```text
User A
tail_probability = 0.0
predictability = 0.35
candidate = True

User B
tail_probability = 0.60
predictability = 0.25
candidate = False
```

즉:

```text
same time
same policy
different KDE
→ different intervention candidate
```

를 확인했습니다.

## Test Status

실행:

```bash
python3 -m pytest tests/context-engine
```

현재 결과:

```text
20 passed
```

## Next Priorities

1. 실제 RPi4 → MQTT → RPi5 KDE E2E 테스트
2. retained KDE 모델 재수신 검증
3. Predictability Threshold 재검증
4. breakfast proxy를 전체 `meal_time` 데이터로 확장
5. HOME / AWAY, meal_done 등 World State와 Meal Policy 연결
6. HMM 기반 모호한 Activity 추론 확장
