# HESTIA Context Engine 프로토타입

MQTT 수신 → World State 갱신 → Context 판단 → Policy → MQTT 발행까지
end-to-end로 도는지만 확인하는 최소 구성.

## 토픽

| 토픽                     | 방향 | 페이로드                          | 의미             |
| ------------------------ | ---- | --------------------------------- | ---------------- |
| `home/sensor/{id}/state` | 입력 | `{"value": true}`                 | 지속 상태        |
| `home/device/{id}/state` | 입력 | `{"state": "ON"}`                 | 지속 상태        |
| `home/device/{id}/event` | 입력 | `{"event": "COMPLETE"}`           | 순간 이벤트      |
| `home/context/user`      | 출력 | `{"context": "WATCHING_TV"}`      | 엔진의 판단 결과 |
| `home/notify/tv`         | 출력 | `{"type": ..., "message": ...}`   | TV 배너          |
| `home/notify/push`       | 출력 | `{"type": ..., "message": ...}`   | 푸시             |
| `home/dev/clock`         | 입력 | `{"op":"advance","value":"+30m"}` | 개발용 시계 조작 |
| `home/dev/dump`          | 입력 | `{}`                              | World State 출력 |

접두어는 `config.py`의 `PREFIX` 한 곳에서 바꾼다.

`home/dev/*` 는 개발 전용이다. 실기 배포 시 구독 목록에서 뺀다.

## 준비

```bash
sudo apt install -y mosquitto mosquitto-clients   # RPi5
pip install paho-mqtt
```

## 실행

터미널 1 — 엔진:

```bash
python3 engine.py --replay      # ReplayClock (시간 이동 가능)
python3 engine.py               # RealClock
```

터미널 2 — 목 발행기:

```bash
python3 mock.py
> case1
```

브로커 없이 로직만 보려면:

```bash
python3 selftest.py
```

## CASE 1 기대 출력

```
[CONTEXT] WATCHING_TV  (변경됨)
[CONTEXT] WATCHING_TV [EVENT] WASHER_COMPLETE [POLICY] TV_NOTIFICATION [ACTION] TV Banner: 세탁이 완료되었습니다.
```

`mosquitto_sub -t 'home/#' -v` 로 발행까지 같이 본다.

## 파일

- `config.py` — 토픽 접두어, sensor_id/device_id → area/role 매핑
- `clock.py` — RealClock / ReplayClock
- `world.py` — World State, 상태 전이 → 파생 이벤트
- `context.py` — World State → 컨텍스트 라벨
- `policy.py` — 컨텍스트 + 이벤트 → 알림 채널, 쿨다운
- `engine.py` — `ingest()` 파이프라인 + MQTT 배선
- `mock.py` — 터미널 목 발행기
- `selftest.py` — 브로커 없이 CASE 1 재생
