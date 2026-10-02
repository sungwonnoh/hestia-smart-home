# HESTIA Backend (FastAPI)

Flutter 앱과 Context Engine/MQTT 사이의 Backend Adapter. RPi5 에서 실행한다.

```text
ESP32 ─MQTT─▶ Mosquitto (RPi5) ─▶ Context Engine
                   │
                   ▼
             FastAPI (이 서비스)
               ├── REST /api/v1/*  → Flutter
               └── WS   /ws/monitor → HESTIA Monitor
```

- 판단(상황 추론)은 하지 않는다. Context Engine 결과를 변환·저장·전달만 한다.
- MQTT 원본 payload 는 내부 cache 에 보존하고, REST 응답은 Flutter Domain Model 형태로 바꾼다.
- 인증은 없다 (명세상 미정).

## 실행

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# Mosquitto 가 있을 때
MQTT_HOST=localhost uvicorn app.main:app --host 0.0.0.0 --port 8000

# 브로커 없이 API 만 확인
HESTIA_MQTT_ENABLED=0 uvicorn app.main:app --port 8000
```

API 문서: http://localhost:8000/docs

## 환경변수

| 이름 | 기본값 | 설명 |
|---|---|---|
| `MQTT_HOST` / `MQTT_PORT` | `localhost` / `1883` | Mosquitto 주소 |
| `HESTIA_MQTT_ENABLED` | `1` | `0` 이면 MQTT 없이 실행 |
| `HESTIA_MQTT_CLIENT_ID` | `hestia-api` | MQTT client id |
| `HESTIA_SRC_ID` | `rpi5-api` | API 가 발행하는 메시지의 `src_id` |
| `HESTIA_DB_PATH` | `hestia.db` | SQLite 파일 (`*.db` 는 git 제외) |
| `HESTIA_DEVICE_STALE_SEC` | `900` | 이 시간 동안 보고 없으면 가전 offline |

## API

| Method | Path | 설명 |
|---|---|---|
| GET | `/api/v1/health` | 상태, MQTT 연결, 수신 통계 |
| GET/POST | `/api/v1/rooms` | 공간 조회/추가 (중복 409) |
| GET | `/api/v1/devices` | 가전 + MQTT 최신 상태 (Flutter Device 형태) |
| PUT | `/api/v1/devices/{id}` | 이름·종류·공간·`virtualId` 변경 |
| GET | `/api/v1/context/current` | context 6종 최신값 (받은 것만) |
| GET | `/api/v1/notifications` | 알림 목록 (취소 제외, 최신순) |
| POST | `/api/v1/notifications/{id}/ack` | `{"ackType": "SEEN"\|"DELIVERED"}` → DB 기록 + `hestia/notify/ack` 발행 |
| GET | `/api/v1/explanations/latest` | 최신 판단 근거 (없으면 404) |
| GET | `/api/v1/explanations` | 판단 근거 목록 |
| GET | `/api/v1/explanations/{id}` | 알림의 `explanationId` 로 조회 |
| GET/PUT | `/api/v1/preferences` | 알림/개인화 설정 |
| GET/PUT | `/api/v1/setup` | 최초 설정 (설정 전 GET 은 404) |
| WS | `/ws/monitor` | 접속 시 `snapshot`, 이후 MQTT 이벤트 실시간 전달 |

### 가전 ↔ MQTT 장치 연결

Flutter 가전(`tv-01`)과 MQTT 장치(`vd-01`)는 `virtualId` 로 연결한다.
비어 있으면 `hestia/registry/devices` 나 실제 상태 보고에서 **같은 종류의 장치**를 찾아
자동으로 연결하고, 같은 공간(area)의 장치를 우선한다. 필요하면 `PUT /devices/{id}` 로 직접 지정한다.

### 알림 ↔ 판단 근거 연결

`hestia/notify/push` payload 에 `context` (예: `"activity"`) 가 있으면 그 context 의
현재 판단과 알림을 연결해 `explanationId` 를 채운다.

## MQTT 구독 토픽

`hestia/sensor/+/state`, `hestia/device/+/state`, `hestia/device/+/event`, `hestia/context/+`,
`hestia/model/+`, `hestia/notify/push`, `hestia/notify/cancel`, `hestia/intervention/outcome`,
`hestia/registry/devices`, `hestia/system/profile`

발행: `hestia/notify/ack` (retain 하지 않음)

잘못된 JSON, schema 위반, 토픽과 `src_id` 불일치는 버리고 `/health` 의 `ingest` 통계에 센다.

## 테스트 / 로컬 E2E

```bash
pytest                                  # 브로커 없이 Mock MQTT 메시지로 검증

# Mosquitto 가 있을 때: API 실행 후 데모 시나리오 발행
python scripts/mock_mqtt.py --interval 1
curl localhost:8000/api/v1/context/current
```
