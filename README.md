# HESTIA Smart Home

> 제24회 임베디드 소프트웨어 경진대회 출품 프로젝트

HESTIA는 센서와 생활 로그를 바탕으로 사용자의 현재 상태를 추론하고, 개인별 생활 패턴과 상황을 함께 고려해 필요한 시점에만 개입하는 초개인화 스마트홈 시스템 입니다.


---

## Architecture

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

- **ESP32**: 센서/가전 Mock 및 물리 입력
- **Raspberry Pi 5**: MQTT Broker, Context Engine, 실시간 상태 추론 및 Policy 판단
- **Raspberry Pi 4**: 사용자 History 저장, 개인별 Baseline 학습, 배치 처리, 향후 대시보드/TTS
- **MQTT**: 장치와 서비스 간 이벤트 및 모델 결과 전달

---

# Project Structure

```text
hestia-smart-home/
│
├── apps/                           # 애플리케이션 계층
│   └── display/                    # 현재 `display/`를 중심으로 화면 출력 및 사용자 인터페이스
├── config/                         # 서비스 실행 시 필요한 공통 설정 파일
├── data/
│   ├── raw/                        # 다운로드한 원본 데이터
│   └── processed/                  # 전처리한 데이터
├── docs/                           # 시스템 설계, 개발 문서, 실험 기록
├── firmware/                       # ESP32 등 임베디드 장치에서 동작하는 펌웨어를 관리
│   └── esp32-1/
├── prototype/                      # 센서 및 실제 하드웨어 연결 전 기능 검증을 위한 프로토타입 코드
├── schemas/
│   └── mqtt/                       # MQTT Topic별 Payload Schema를 정의
├── scripts/
│   └── datasets/                   # 공개 데이터셋을 HESTIA에서 사용할 수 있는 형태로 변환하는 전처리 스크립트
├── services/
│   ├── context-engine/             # Raspberry Pi 5에서 동작하는 실시간 Context Engine
│   └── learning-engine/            # Raspberry Pi 4에서 동작할 개인화 학습 계층
├── tests/                          # 테스트 코드
├── .gitignore
└── README.md
```

---

## Dataset

개인 생활 패턴 Baseline 검증에는 **CASAS Aruba Smart Home Dataset**을 사용합니다.

- Dataset: https://zenodo.org/records/17180309
- 원본 및 전처리 데이터는 GitHub에 포함하지 않습니다.
- 다운로드 및 전처리 방법은 [`docs/dataset-setup.md`](docs/dataset-setup.md)를 참고하세요.

## Learning Engine

현재 Learning Engine에서는 아침 준비 시각을 이용해 Gaussian KDE 기반 개인 Baseline을 생성합니다.

예:

```bash
python3 services/learning-engine/baseline.py --time 09:40
```

상세 설명은 [`docs/learning-engine.md`](docs/learning-engine.md)를 참고하세요.

## Context Engine

Context Engine은 MQTT 이벤트를 받아 현재 World State를 관리하고,
Rule / FSM / 개인 Baseline을 결합해 최종 개입 여부를 판단합니다.

전체 구조는 [`docs/architecture.md`](docs/architecture.md)를 참고하세요.

## Development Status

현재 주요 개발 방향:

- Context Engine 고도화
- KDE Baseline MQTT 연동
- 사용자별 Baseline 저장
- Batch Learning
- HMM 기반 모호한 Activity 추론

세부 진행 상황은 [`docs/development-status.md`](docs/development-status.md)를 참고하세요.
