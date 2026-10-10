# HESTIA Weather (RPi4)

기상청 **초단기실황**과 **기상특보**를 조회해 `hestia/external/weather`로 발행합니다.
Context Engine(RPi5)이 실내 센서와 함께 보조 신호로 씁니다.

## 토픽

| 토픽 | 방향 | QoS | retained |
|---|---|---|---|
| `hestia/external/weather` | RPi4 → CE | 1 | 아님 |
| `hestia/external/weather/get` | CE → RPi4 | 1 | 아님 (retained 요청은 무시) |

- **update**: 새 정시 실황을 받거나 집 구역 특보가 바뀌면 바로 발행합니다. 같은 값은 다시 보내지 않습니다. 브로커에 다시 연결되면 마지막 값을 한 번 더 보냅니다.
- **reply**: `get`을 받으면 캐시한 값으로 바로 응답합니다 (API를 다시 부르지 않음). 캐시가 없거나 90분보다 오래됐으면 한 번 조회한 뒤 응답합니다. 날씨를 한 번도 받지 못했으면 응답하지 않습니다.
- `get` payload는 비어 있어도 됩니다 (`{}`).

retained를 쓰지 않으므로 CE는 **재시작 직후 `get`을 한 번 보내야** 바로 날씨를 받습니다.

## payload

```json
{
  "version": 1, "sent_ts": 1791612600, "src_id": "rpi4",
  "reason": "update",
  "source": "kma",
  "location": {"name": "서울", "nx": 60, "ny": 127},
  "observed_at": 1791612000,
  "temperature_c": 31.2,
  "humidity_pct": 58.0,
  "precip_1h_mm": 0.0,
  "precip_type": "NONE",
  "wind_speed_ms": 1.8,
  "warnings": [{"name": "폭염경보", "type": "HEAT", "level": "WARNING"}],
  "warnings_issued_at": 1791610800
}
```

| 필드 | 의미 |
|---|---|
| `reason` | `update`(새 실황) / `reply`(get 응답) |
| `observed_at` | 관측 정시 (epoch 초). CE 는 이 값으로 오래된 날씨를 거른다 |
| `temperature_c` / `humidity_pct` | 기온(T1H) / 습도(REH) |
| `precip_1h_mm` | 1시간 강수량(RN1) |
| `precip_type` | 강수형태(PTY): `NONE` `RAIN` `RAIN_SNOW` `SNOW` `DRIZZLE` `DRIZZLE_SNOW` `SNOW_FLURRY` |
| `wind_speed_ms` | 풍속(WSD) |
| `warnings` | 집 구역에 지금 발효 중인 특보. `[]` = 없음, `null` = 모름 (조회 전·실패·`warning_areas` 비어 있음) |
| `warnings[].type` | `HEAT` `COLD_WAVE` `HEAVY_RAIN` `HEAVY_SNOW` `WIND` `DRY` `TYPHOON` `HIGH_SEAS` `STORM_SURGE` `TSUNAMI` `FOG` `YELLOW_DUST` `OTHER` |
| `warnings[].level` | `ADVISORY`(주의보) `WARNING`(경보) `SEVERE_WARNING`(중대경보) `OTHER` |
| `warnings[].name` | 통보문 원문 이름 (예: `폭염경보`) |
| `warnings_issued_at` | 근거가 된 통보문 발표 시각 (epoch 초). 최근 5일 통보문이 없으면 `null` |

실황 결측값은 `null`입니다.

### 특보를 어떻게 읽나

"기상청_기상특보 조회서비스"의 통보문(`getWthrWrnMsg`, 전국 `stnId=108`)에서 **가장 최근 통보문의 `t6`(현재 발효 중인 특보 전체)**을 읽습니다.

```text
t6 = "o 폭염경보 : 서울, 대구 o 호우주의보 : 강원도(태백)"   → 서울이 들어간 폭염경보만 집 특보
t6 = "o 없 음"                                             → 특보 없음
```

- 집 구역은 `[location] warning_areas`의 이름이 구역 문자열에 들어 있는지로 판단합니다 (기본값은 `name`).
- 조회 기간은 오늘 기준 6일 전까지만 허용돼서(넘으면 resultCode 99) 최근 5일을 봅니다. 5일 동안 전국에 통보문이 하나도 없으면 특보가 없는 것으로 봅니다.
- 10분마다 조회합니다.

## 얼마나 실시간인가

관측은 **매시 정시에 한 번** 만들어지고 몇 분 뒤부터 조회됩니다.
그래서 정시 + 12분에 조회하고, 이번 정시 자료가 아직 없으면 2분마다 다시 봅니다.
더 자주 불러도 값은 같습니다. 특보는 발표 시각이 정해져 있지 않아 10분마다 봅니다.
하루 호출은 실황 약 50~100회 + 특보 약 150회로, 서비스별 개발 계정 한도(10,000회)보다 훨씬 적습니다.

## 설정

- **위치**: `config/homes/<home>.toml`의 `[location]` (`lat` / `lon` → 기상청 5km 격자, 또는 `nx` / `ny` 직접 지정). 지금은 서울시청 → (60, 127)입니다.
- **환경변수**: RPi4의 `/etc/hestia/weather.env`에 둡니다 (repo 밖). 예시는 `deploy/weather.env.example`입니다.

| 변수 | 기본값 | |
|---|---|---|
| `KMA_SERVICE_KEY` | — | 필수. 공공데이터포털 인증키 (Decoding 권장, Encoding 도 됨). "기상청_단기예보 조회서비스"와 "기상청_기상특보 조회서비스" 둘 다 활용 신청 |
| `MQTT_HOST` / `MQTT_PORT` | `localhost` / `1883` | 브로커는 RPi5 |
| `HESTIA_CONFIG` | `/data/hestia/config` | `homes/` 가 있는 디렉터리 |
| `HESTIA_HOME` | `demo` | |

## RPi4 설치

```bash
pip install ./services/weather            # Python 3.9 / 3.10 이면 tomli 도 같이 설치됨

sudo mkdir -p /etc/hestia
sudo cp deploy/weather.env.example /etc/hestia/weather.env
sudo nano /etc/hestia/weather.env         # KMA_SERVICE_KEY, MQTT_HOST 채우기
sudo chmod 600 /etc/hestia/weather.env

# 키·위치 확인: 한 번 조회해서 출력만 한다 (MQTT 없음)
set -a; source /etc/hestia/weather.env; set +a
python3 -m hestia_weather.runner --once

sudo cp deploy/hestia-weather.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hestia-weather
journalctl -u hestia-weather -f
```

RPi5에서 확인:

```bash
mosquitto_sub -v -t 'hestia/external/weather'
mosquitto_pub -t 'hestia/external/weather/get' -m '{}' -q 1     # reply 가 오는지
```

## 테스트

```bash
cd services/weather && python3 -m pytest -q
```

기상청 응답과 MQTT client 는 가짜로 바꿔 끼웁니다. 실제 API 호출은 `--once`로 확인합니다.

## 남은 일

- [ ] 실제 폭염 특보가 있을 때의 `t6`으로 서울 매칭 확인 (지금 테스트는 2026-10 실제 응답 + 폭염 가정 문자열)
- [ ] Context Engine: `hestia/external/weather` 구독, 재시작 직후 `get` 발행, `observed_at` 기준 오래된 값 거르기, 폭염 판단에 OR 결합 (`scenarios.py` `_track_air`)
- [ ] payload 형식 CE 담당자와 확정
