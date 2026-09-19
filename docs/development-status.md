# Development Status

## Context Engine

- [x] Clock 주입 구조
- [x] MQTT ingest 단일 진입점
- [x] World State
- [x] Policy 계층 기본 구조
- [ ] Replay 검증
- [ ] 중복 프로세스 정리
- [ ] 신규 MQTT Schema Parser
- [ ] Node Health Monitoring
- [ ] AWAY 상태 검출

## Learning Engine

- [x] CASAS Aruba 데이터 확보
- [x] `Meal_Preparation begin` 파싱
- [x] Breakfast Proxy 생성
- [x] Gaussian KDE Baseline
- [x] Tail Probability
- [x] Entropy Predictability
- [x] Synthetic Predictability Validation
- [ ] MQTT Model Result Publish
- [ ] 사용자별 Baseline 저장
- [ ] 주기적 Batch Learning
- [ ] HMM 기반 모호한 Activity 추론

## Recent Work

최근 구현한 개인 Baseline 흐름:

```text
CASAS Aruba
  ↓
Meal_Preparation begin 추출
  ↓
아침 시간대 필터링
  ↓
하루 첫 이벤트 선택
  ↓
breakfast_preparation.csv
  ↓
Gaussian KDE
  ↓
Tail Probability
  ↓
Entropy Predictability
  ↓
Time Anomaly + Pattern Reliability
  ↓
NORMAL / INTERVENE
```

## Next Priorities

1. KDE 결과 MQTT Publish
2. RPi5 Context Engine에서 `hestia/model/kde` 수신
3. 사용자별 Baseline 저장 구조 정의
4. Batch Learning 스케줄링
5. HMM 기반 모호한 Activity 추론 확장
