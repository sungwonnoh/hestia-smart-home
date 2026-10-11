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
| GET/POST | `/api/v1/medications` | 복약 일정 조회/추가 (추가는 201) |
| PUT/DELETE | `/api/v1/medications/{id}` | 복약 일정 수정/삭제 (없으면 404, 삭제는 204) |
| GET | `/api/v1/weather` | 외부 날씨 최신값 (아직 못 받았으면 404) |
| WS | `/ws/monitor` | 접속 시 `snapshot`, 이후 MQTT 이벤트 실시간 전달 |

### 가전 ↔ MQTT 장치 연결

Flutter 가전(`tv-01`)과 MQTT 장치(`vd-01`)는 `virtualId` 로 연결한다.
비어 있으면 `hestia/registry/devices` 나 실제 상태 보고에서 **같은 종류의 장치**를 찾아
자동으로 연결하고, 같은 공간(area)의 장치를 우선한다. 필요하면 `PUT /devices/{id}` 로 직접 지정한다.

### 복약 일정

홈에서 원하는 사용자만 등록한다 (최초 설정에는 없음). Backend 는 저장만 하고, 알림 시점은 Context Engine 이 판단한다 (#40).

```json
{"name": "감기약", "schedule": {"type": "AFTER_MEAL", "delayMin": 30},
 "days": 5, "startDate": "2026-10-09", "refillRequired": false}
{"name": "혈압약", "schedule": {"type": "FIXED", "times": ["08:00", "20:00"]},
 "days": 30, "startDate": "2026-10-09", "refillRequired": true}
```

| 필드 | 값 |
|---|---|
| `schedule.type` | `AFTER_MEAL` 식후 / `FIXED` 정해진 시각 |
| `schedule.delayMin` | `AFTER_MEAL` 만. 식사가 끝나고 몇 분 뒤 (0~180, 비우면 30). 앱은 0 = 식사 직후, 30 = 식후 30분 |
| `schedule.times` | `FIXED` 만. `HH:MM` 1~6개. 하루 순서로 정렬하고 중복은 뺀다 |
| `days` | 며칠분 (1~365) |
| `startDate` | 복용 시작일. 비우면 등록한 날, 수정 때 비우면 기존 값 유지 |
| `refillRequired` | 주기적으로 처방받는 약 (앱이 남은 3일부터 "처방 확인" 표시, 엔진이 재처방 알림) |
| `endDate` | 응답만. 마지막 복용일 = `startDate + days - 1` |

- **식후 약은 끼니를 고르지 않는다.** 엔진이 그 사람의 끼니 구간(`meal_time.peaks`)에 하루 최대 세 번 건다.
  끼니 구간에는 아침·저녁 같은 이름이 없기 때문이다. 감기약처럼 "하루 세 번 식후" 처방이 대상이다.
- **'아침·저녁만', 식전, 자기 전 약은 `FIXED` 로 등록한다.**
- 응답에서 쓰지 않는 값은 `null` / `[]` 다 (`FIXED` 의 `delayMin`, `AFTER_MEAL` 의 `times`).
- 이전 형식(`slots` + `mealTiming`)으로 저장된 일정은 Backend 가 켜질 때 한 번 바꾼다.
  아침·점심·저녁 모두 식사 직후/식후 30분이면 `AFTER_MEAL` 0/30분, 나머지는 끼니별 기본 시각의 `FIXED`
  (아침 08:00, 점심 12:00, 저녁 18:00, 자기 전 22:00).

일정이 바뀔 때마다(추가·수정·삭제) **전체 목록**을 `hestia/registry/medications` 에 retained 로 발행한다.
MQTT 에 (재)연결될 때도 다시 발행하므로 브로커가 꺼져 있던 동안의 변경도 반영된다.
일정이 없으면 빈 목록을 발행해 엔진이 이전 일정을 지우게 한다. 날짜는 엔진이 `end_date` 로 스스로 판단한다 (매일 보내지 않는다).

```json
{"version": 1, "sent_ts": 1791500000, "src_id": "rpi5-api",
 "medications": [
   {"id": "med-08a0b745", "name": "감기약", "schedule": {"type": "after_meal", "delay_min": 30},
    "start_date": "2026-10-09", "days": 5, "end_date": "2026-10-13", "refill_notice": false},
   {"id": "med-3f91c2d0", "name": "혈압약", "schedule": {"type": "fixed", "times": ["08:00", "20:00"]},
    "start_date": "2026-10-09", "days": 30, "end_date": "2026-11-07", "refill_notice": true}]}
```

MQTT 는 Context Engine `parse_medications` 형식이다: snake_case, schedule `type` 은 소문자 `after_meal` / `fixed`.
**엔진은 모르는 `type` 이 하나라도 있으면 목록 전체를 버리므로** 이 두 가지만 보낸다.
REST 의 `refillRequired` 는 `refill_notice` 로 보낸다. 형식을 바꾸면 `version` 을 올린다.

### 외부 날씨 (`/weather`)

RPi4 weather 서비스(`services/weather`)가 기상청 초단기실황·기상특보를 `hestia/external/weather` 로 보낸다.
retained 가 아니므로 Backend 는 MQTT 에 (재)연결될 때마다 `hestia/external/weather/get` 을 보내 최신 값을 받는다.
더 오래된 관측이 늦게 와도 덮어쓰지 않는다.

```json
{"locationName": "서울", "observedAt": "2026-10-10T12:00:00+09:00",
 "temperatureC": 31.2, "humidityPct": 58.0, "precipitationMm": 0.0, "precipType": "NONE",
 "windSpeedMs": 1.8, "warnings": [{"name": "폭염경보", "type": "HEAT", "level": "WARNING"}],
 "stale": false}
```

| 필드 | 의미 |
|---|---|
| `observedAt` | 기상청 관측 정시. 관측은 1시간에 한 번이다 |
| `precipitationMm` | 1시간 강수량 (MQTT `precip_1h_mm`) |
| `warnings` | 집 구역에 발효 중인 특보. `[]` = 없음, `null` = 아직 모름 |
| `stale` | 관측이 90분보다 오래됨 — RPi4 갱신이 끊겼을 수 있다 |

### 알림 변환 (`hestia/notify/push` → Notification)

MQTT 원본 스키마는 바꾸지 않고 Backend 에서 Flutter DTO 로 바꾼다.

| MQTT | DTO | 규칙 |
|---|---|---|
| `notify_id` | `id` | 그대로 |
| `scenario` | `scenario` | 원본 보존 |
| `scenario` | `type` | 아래 표. MQTT 에 `type` 이 와도 쓰지 않는다 |
| `priority` | `priority` | 모르는 값은 `normal` |
| `payload.title` | `title` | 없으면 `payload.text` → `scenario` → `"HESTIA 알림"` |
| `payload.text` | `message` | 없으면 `""` |
| `channels` | `roomId` | virtual_id → registry `area`. 아래 참고 |
| `sent_ts` | `createdAt` | epoch → ISO 8601 (로컬 타임존 offset 포함) |

| scenario | type |
|---|---|
| `SAFETY` | `SAFETY` |
| `SENSOR_FAULT` | `WARNING` |
| `MEDICATION_PROMPT` | `REMINDER` |
| `WAKE_ROUTINE` | `REMINDER` |
| `SLEEP_ROUTINE` | `INFO` |
| 그 외 | `priority` 가 `safety` 면 `SAFETY`, 아니면 `INFO` |

`channels` 는 공간이 아니라 발송 대상 virtual_id 목록이다. 앞에서부터 `hestia/registry/devices` 의
`area` 를 찾은 첫 채널을 `roomId` 로 쓴다. `voice` (RPi4 TTS) 는 건너뛴다.
찾지 못하면 (voice 만 있음, registry 수신 전, 미등록 장치) 오류 없이 `roomId = null` 이다.
registry 가 정본이므로 앱 설정의 가전 공간으로 대신 채우지 않는다. 장치 채널이 있는데 공간을 못 찾으면 warning 로그를 남긴다.
area 와 앱 room id 는 같은 내부 ID 체계라 별도 변환 표 없이 그대로 쓴다.

`requires_ack`, `ack_deadline`, `escalation_level` 은 검증 후 원본 payload(`notifications.payload_json`)에만 보존하고 DTO 에는 넣지 않는다.
최상위 `title`/`message`/`area`/`type` 같은 옛 형식은 지원하지 않는다.

```json
// MQTT
{"version": 1, "sent_ts": 1755500000, "src_id": "rpi5", "notify_id": "n-20260818-03",
 "scenario": "WAKE_ROUTINE", "priority": "normal", "channels": ["vd-05", "voice"],
 "requires_ack": true, "ack_deadline": 1755500600, "escalation_level": 1,
 "payload": {"title": "수분 섭취", "text": "물 한 잔 드세요"}}

// GET /api/v1/notifications (registry: vd-05 → kitchen)
{"id": "n-20260818-03", "scenario": "WAKE_ROUTINE", "type": "REMINDER", "priority": "normal",
 "title": "수분 섭취", "message": "물 한 잔 드세요", "createdAt": "2025-08-18T15:53:20+09:00",
 "roomId": "kitchen", "explanationId": null, "delivered": false, "seen": false}
```

### 알림 ACK

| 경로 | 처리 |
|---|---|
| `hestia/notify/ack` 수신 (채널 노드) | `notify_id` 로 찾아 `DELIVERED` → `delivered`, `SEEN` → `seen` + `delivered`. 모르는 알림은 무시, 다른 `ack_type` 은 schema 위반으로 버림 |
| `POST /notifications/{id}/ack` (Flutter) | DB 기록 후 `hestia/notify/ack` 발행. 다시 수신돼도 처음 기록 시각을 유지한다 |

DELIVERED 는 채널에 표시됨, SEEN 은 사용자가 실제로 확인함이다. 화면에 띄웠다고 SEEN 으로 처리하지 않는다.

### 알림 ↔ 판단 근거 연결

`explanationId` 는 MQTT 필드가 아니라 Backend 개념이다. 판단 식별자는 Context Engine 의
`decision_id` 를 쓰는 방향이지만, `decision_id` ↔ 후속 `notify_id` 를 엔진이 어떻게 전달·보존하는지
확인되지 않아 **지금은 연결하지 않는다** (`explanationId = null`, 판단 근거의 `action = null`).
scenario 나 명세 밖 필드로 추정하지 않는다. Flutter 는 `explanationId` 가 null 이면 최신 판단 근거를 연다.

### 책임 범위

Backend 는 Context Engine 이 발행한 context/notify 를 변환·저장·전달만 한다.
`SENSOR_FAULT` 같은 판단으로 알림을 새로 만들지 않는다 (알림 생성은 엔진 책임).

### 확인 필요

- `decision_id` ↔ `notify_id` 연결 방식 (위 참고)
- Context Engine 이 `SENSOR_FAULT` 를 `notify/push` 로 발행하는지
- 앱 ↔ `hestia/registry/devices` 동기화의 발행 주체: registry 는 RPi4 가 retained 로 발행하고
  엔진은 수신 시 매핑 전체를 교체한다. FastAPI 출력 토픽(명세 17-A.4)에는 없으므로 정해질 때까지 발행하지 않는다.

## MQTT 구독 토픽

`hestia/sensor/+/state`, `hestia/device/+/state`, `hestia/device/+/event`, `hestia/context/+`,
`hestia/model/+`, `hestia/notify/push`, `hestia/notify/ack`, `hestia/notify/cancel`, `hestia/intervention/outcome`,
`hestia/registry/devices`, `hestia/system/profile`, `hestia/external/weather`

발행: `hestia/notify/ack` (retain 하지 않음), `hestia/registry/medications` (retained),
`hestia/external/weather/get` ((재)연결 시, retain 하지 않음)

잘못된 JSON, schema 위반, 토픽과 `src_id` 불일치는 버리고 `/health` 의 `ingest` 통계에 센다.

## 테스트 / 로컬 E2E

```bash
pytest                                  # 브로커 없이 Mock MQTT 메시지로 검증

# Mosquitto 가 있을 때: API 실행 후 데모 시나리오 발행
python scripts/mock_mqtt.py                  # 가전 상태만 (기본, retain 안 함)
python scripts/mock_mqtt.py --mode full      # + context·복약 알림 (Context Engine 흉내)
curl localhost:8000/api/v1/context/current
```

`mock_mqtt.py` 는 실제 Context Engine 과 같은 브로커에서 써도 안전한 쪽이 기본이다.

- `hestia/registry/devices` 는 엔진의 역할 매핑을 덮어쓰므로 발행하지 않는다.
- `--mode full` 은 엔진 출력과 섞이므로 `hestia-engine` 을 멈추고 쓴다. src_id 는 `mock-engine`.
- 기본은 retain 하지 않는다. `--retain` 으로 남긴 메시지는 같은 `--mode` 에 `--clear` 를 붙여 지운다.

RPi5 (Docker, `--network host`):

```bash
docker exec hestia-api python scripts/mock_mqtt.py
docker stop hestia-engine && docker exec hestia-api python scripts/mock_mqtt.py --mode full
docker start hestia-engine
```

## Flutter 계약 fixture

`apps/test/fixtures/api/` 를 Backend 와 Flutter 테스트가 같이 쓴다.

| 경로 | 만드는 쪽 | 검증하는 쪽 |
|---|---|---|
| `responses/*.json` | `scripts/export_api_fixtures.py` (registry + mock_mqtt full 시나리오를 넣은 실제 응답) | `apps/test/api_contract_test.dart` 가 파싱 |
| `requests/*.json` | Flutter 모델 `toJson` 결과 (`api_contract_test.dart` 가 일치 확인) | `tests/test_contract.py` 가 서버에 보내 저장 확인 |

응답 구조를 바꾸면 `tests/test_contract.py` 가 실패한다. fixture 를 다시 만들고 Flutter 테스트를 돌린다.

```bash
python scripts/export_api_fixtures.py
cd ../apps && flutter test test/api_contract_test.dart
```
