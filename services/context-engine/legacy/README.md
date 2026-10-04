# legacy

Context Engine 재구성(PR #13) 이전 코드. `hestia_engine/` 패키지가 대체한다.

**새 코드는 전부 `hestia_engine/` 에 있다.** 이 폴더는 KDE 연동 작업의
참조용으로만 남겨두며, 이식이 끝나면 삭제한다.

## 파일별 처분

| 파일              | 줄  | 대체                                     | 살릴 로직                                       |
| ----------------- | --- | ---------------------------------------- | ----------------------------------------------- |
| `engine.py`       | 264 | `hestia_engine/engine.py` + `context.py` | MQTT 배선 패턴 (runner.py 작성 시), 게이팅 순서 |
| `replay.py`       | 120 | `hestia_engine/replay.py`                | 없음 — 타이머가 없어 시간 기반 전이가 묻힌다    |
| `kde_model.py`    | 180 | `hestia_engine/model.py`                 | 격자 검증, 꼬리확률 누적합                      |
| `meal_policy.py`  | 66  | `hestia_engine/model.py`                 | reason 코드 넷, 임계값(policy.toml 로 이동)     |
| `kde_context.py`  | 48  | `hestia_engine/model.py`                 | 조회 흐름                                       |
| `model_store.py`  | 38  | `hestia_engine/model.py`                 | 보관 구조                                       |
| `model_ingest.py` | 30  | `messages.py` 의 `_dispatch`             | 없음                                            |

746줄 중 실제로 옮길 로직은 80줄 안쪽이다. 이식이 아니라 재작성이다.

## 왜 전부 새로 쓰는가

이 코드들은 Context Engine 이 준비되기 전에 KDE 파이프라인을 검증하려고
따로 만든 것이다. 그래서 제 나름의 World State, Clock, Replay 를 갖고 있고,
현재 구조와 전제가 다르다.

- `payload` 를 dict 로 받는다 (현재는 bytes/str — 실제 MQTT 와 같은 조건)
- 시각을 `"09:40"` 문자열로 다룬다 (현재는 epoch, `recv_ts` 기준)
- Replay 에 타이머가 없다 (메시지가 안 오는 구간의 판단이 전부 묻힘)
- `version`/`src_id` 검증이 중복이다 (`check_envelope` 가 이미 수행)

검증된 것은 이 코드가 아니라 `services/learning-engine/baseline.py` 쪽이다.
여기 있는 것들은 그것을 읽는 얇은 층이다.

## tests/

`tests/context-engine/` 에 있던 테스트 다섯. 이 코드들을 대상으로 한다.
`pyproject.toml` 의 `testpaths` 밖이라 실행되지 않는다.
