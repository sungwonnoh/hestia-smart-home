# HESTIA Learning Engine

사용자의 과거 행동 시각으로 개인 시간 패턴(KDE density)과 predictability를 학습해
`hestia/model/kde`로 RPi5 Context Engine에 전달합니다.

KDE는 `EATING`, `SLEEPING` 같은 상태를 직접 판정하지 않습니다.
Context Engine이 판단할 때 쓰는 "이 사람의 평소 시간 패턴"을 제공합니다.

## 분포

| distribution | 의미 | 값 | 격자 | KDE |
|---|---|---|---|---|
| `wake_time` | 기상 시각 | 자정 기준 분 | 15분 × 96칸 | circular |
| `sleep_time` | 취침 시각 | 자정 기준 분 | 15분 × 96칸 | circular |
| `meal_time` | 식사 활동 시작 시각 | 자정 기준 분 | 15분 × 96칸 | circular |
| `hydration_lag` | 기상 후 첫 수분 섭취까지 | 경과 분 | 5분 × 24칸 (0~120분) | 직선 |

- circular: 1440분 주기. 23:50과 00:10은 20분 차이로 계산합니다.
- `hydration_lag` 끝값 120분은 Context Engine `hydration_window_sec`(7200초) 기준입니다.
- 표본이 없거나 KDE를 계산할 수 없는 분포는 모델에서 빠집니다 (빈 배열을 보내지 않음).

## 데이터 출처

실제 Context Engine t0 로그가 쌓이기 전까지 공개 데이터와 synthetic으로 개발·검증합니다.

| 출처 | 모듈 | 분포 | 비고 |
|---|---|---|---|
| CASAS Aruba | `aruba.py` | meal / sleep / wake | sleep·wake는 `Sleeping` 라벨 **proxy** |
| CASAS Milan / Tulum2 / Cairo | `casas.py` | sleep / wake | 거주자별 **proxy**. predictability 비교·검증용 |
| synthetic | `synthetic.py` | hydration_lag | CASAS에 수분 섭취 라벨 없음. **검증용 fixture** |
| Context Engine t0 | `baseline.py` | meal / wake / hydration | |
| Context Engine t0 `sleep_start` / `sleep_end` | `sleep_sessions.py` | sleep / wake | 같은 t0 로그. 짝지어 수면으로 만든 뒤 밤잠만 학습 |

모든 출처는 공통 표본 `KdeSample`(`samples.py`)로 바뀐 뒤 같은 KDE를 탑니다.

```text
aruba.py      ─┐
synthetic.py  ─┼─→ KdeSample ─→ fit_distribution ─→ density + predictability
t0 adapter    ─┘
```

## 식사: 끼니 구간 (meal_time peaks)

`meal_time`은 하루 전체 식사(아침·점심·저녁) 분포라 봉우리가 여러 개입니다.
전체 predictability는 규칙적인 사람도 낮게(0.07~0.11) 나와 판단 게이트로 쓸 수 없어서,
끼니가 2개 이상이면 **끼니 구간과 끼니별 predictability**를 함께 보냅니다 (Context Engine 합의).

```json
"meal_time": {
  "grid_min": 0, "grid_step": 15, "density": [...],
  "peaks": [
    {"center": 465,  "from": 45,  "to": 615, "predictability": 0.41},
    {"center": 675,  "from": 615, "to": 900, "predictability": 0.54},
    {"center": 1065, "from": 900, "to": 45,  "predictability": 0.65}
  ]
}
```

| 필드 | 의미 |
|---|---|
| `center` | 그 끼니에 식사가 가장 몰린 칸의 시작 분 (구간 가운데가 아님) |
| `from` / `to` | 끼니 구간, 자정 기준 분. `from` 포함 · `to` 미포함, `from > to` 면 자정을 넘음 |
| `predictability` | 그 구간 식사만으로 다시 그린 KDE(96칸)의 predictability. 식사 2개 미만이면 `null` |

- 끼니 = 발행하는 `meal_time` density 에서 균등분포(1/96)보다 높은 봉우리, 경계 = 봉우리 사이 최저점 (고정 식사 시각 없음)
- 구간은 하루를 빈틈없이 나눕니다 (앞 끼니 `to` == 다음 끼니 `from`)
- 봉우리가 1개 이하면 `peaks` 를 넣지 않습니다 → Context Engine 은 전체 predictability 사용
- 끼니 수는 사람마다 다릅니다 (CASAS: Cairo 3, Aruba 2, Tulum2 2)
- 임계값(`[thresholds.meal] predictability_min`)은 Context Engine 정책입니다

## 수면: 밤잠 / 낮잠 구분 (학습 데이터 선별)

`sleep_time` / `wake_time`은 **밤잠만** 학습합니다. 구분은 `sleep_sessions.py`가 RPi4 배치에서 합니다.
지금 이 수면이 밤잠인지 실시간으로 판정하는 일은 RPi5 Context Engine 담당입니다.

```text
수면 기록 (CASAS 라벨 / Context Engine sleep_start · sleep_end)
  ↓ merge_sessions      화장실 등으로 끊긴 구간 병합 (간격 ≤ 15분 또는 Bed_to_Toilet)
  ↓ drop_implausible    24시간 이상은 기록 오류로 제외
  ↓ classify_sessions   정오~다음 날 정오마다 가장 긴 수면 = 밤잠, 나머지 = 낮잠
  ↓ night_samples       밤잠의 시작 → sleep_time, 끝 → wake_time
```

- 고정 취침·기상 시각을 쓰지 않습니다.
- 기상이 관측되지 않은 수면(기록 종료 등)은 취침 시각만 학습합니다.
- 알려진 한계: 그 밤의 수면 기록이 없으면 오후 낮잠이 밤잠으로 분류됩니다 (CASAS 6명 631밤 중 1건).

Context Engine 은 수면 시작·끝을 t0 로그에 따로 남깁니다 (meal / wake / hydration 과 같은 형식, `t0` 는 epoch):

```json
{"date": "2026-09-01", "type": "sleep_start", "t0": 1788271200, "area": "bedroom", "source": "sensor", "prompted": false, "duration_sec": 0}
{"date": "2026-09-02", "type": "sleep_end",   "t0": 1788300000, "source": "sensor", "prompted": false, "duration_sec": 0}
```

- 시각 순으로 `sleep_start` 와 다음 `sleep_end` 를 짝짓습니다. 끝이 없는 시작은 기상 미관측(취침만 학습), 시작이 없는 끝은 버립니다.
- `area` 는 저장만 하고 밤잠 / 낮잠 판단에는 쓰지 않습니다.
- **wake_time 출처**: 기상이 관측된 밤잠이 있으면 그 `sleep_end` 를 쓰고 `wake` 레코드는 쓰지 않습니다 (같은 기상을 두 번 학습하지 않도록). 없으면 `wake` 레코드를 씁니다.

## 실행

저장소 루트에서 실행합니다.

```bash
# 기본: Aruba breakfast CSV (meal_time만)
python3 services/learning-engine/baseline.py

# 개발용 4개 분포: Aruba 원본 + synthetic hydration
python3 services/learning-engine/baseline.py \
    --aruba-raw data/raw/casas/aruba/aruba.txt --synthetic-hydration

# Context Engine t0 로그
python3 services/learning-engine/baseline.py --t0-jsonl /data/hestia/t0_log.jsonl

# Aruba 원본 라벨·표본 수 확인
python3 services/learning-engine/aruba.py

# CASAS 거주자별 sleep / wake 요약, Context Engine 형식(sleep_start / sleep_end) JSONL 내보내기
python3 services/learning-engine/casas.py --export-jsonl /tmp/t0_export
python3 services/learning-engine/baseline.py --t0-jsonl /tmp/t0_export/aruba_R1_t0.jsonl

# 특정 시각의 tail probability 조회 (breakfast meal_time)
python3 services/learning-engine/debug_kde_query.py --time 09:40

# hestia/model/kde payload 확인 / 발행 (QoS 1, retained)
# 입력 옵션(--t0-jsonl / --aruba-raw / --synthetic-hydration)은 baseline.py와 같다
python3 services/learning-engine/model_payload.py
python3 services/learning-engine/mqtt_publisher.py --host <broker> --port 1883
```

payload는 발행 전에 명세(필수 필드, 격자, density 합 1, predictability 0~1, JSON 직렬화)를 검증합니다.
학습할 수 없는 distribution은 빈 배열 대신 키를 빼고 보냅니다. Context Engine은 일부만 온 payload도 받습니다.

Aruba 원본(`data/raw/`)은 gitignore 대상입니다. 받는 방법은 [`docs/dataset-setup.md`](../../docs/dataset-setup.md)를 참고하세요.

## 설치

```bash
pip install -r services/learning-engine/requirements.txt        # 실행
pip install -r services/learning-engine/requirements-dev.txt    # + pytest
cd services/learning-engine && python3 -m pytest tests -q
```

Python 3.9 이상. Context Engine 계약 테스트는 Python 3.11 이상에서만 돌고 그 외에는 건너뜁니다.

## RPi E2E 확인 (SLEEP.md §10)

```bash
# RPi4: 학습 → 발행
python3 services/learning-engine/mqtt_publisher.py --host <broker> \
    --aruba-raw data/raw/casas/aruba/aruba.txt --synthetic-hydration

# 어디서든: retained 모델 수신·검증 (sleep_time / wake_time 필수)
python3 services/learning-engine/verify_model.py --host <broker>
#   종료 코드 0 통과 / 1 검증 실패 / 2 수신 없음

# RPi5: 보관 확인 → 재시작 → retained 복구 확인
journalctl -u hestia-engine | grep "모델 갱신: kde"
sudo systemctl restart hestia-engine
journalctl -u hestia-engine -n 50 | grep "모델 갱신: kde"

# 테스트 후 개발용 retained 모델 삭제 (RPi5 는 재시작해야 메모리에서도 비워짐)
mosquitto_pub -h <broker> -t hestia/model/kde -r -n
```

## Predictability

```text
predictability = 1 - H / H_max        (0 ~ 1, 높을수록 규칙적)
```

`H_max`는 격자 칸 수 기준입니다 (시각 분포 ln 96, hydration ln 24).
칸 수가 다르므로 `hydration_lag`와 시각 분포의 값을 직접 비교하지 않습니다.

검증 결과는 [`results/predictability.md`](results/predictability.md)에 있습니다.

```bash
python3 services/learning-engine/validate_predictability.py \
    --output services/learning-engine/results/predictability.md
```

요약 (시각 분포, n = 212, 20회 평균):

| std (분) | 10 | 30 | 45 | 60 | 75 | 90 | 120 |
|---|---|---|---|---|---|---|---|
| predictability | 0.769 | 0.529 | 0.440 | 0.377 | 0.328 | 0.288 | 0.225 |

| Aruba | predictability |
|---|---|
| breakfast (212일) | 0.330 |
| meal 전체 (1606건, 아침·점심·저녁) | 0.070 |
| wake_time (proxy) | 0.319 |
| sleep_time (proxy) | 0.347 |

CASAS 6명 (proxy): sleep_time 0.342 ~ 0.524, wake_time 0.317 ~ 0.386 (표 5절).

- std가 커질수록 predictability가 일관되게 감소합니다.
- 자정 중심 분포도 낮 중심과 같은 값이 나옵니다 (circular).
- 표본 수 영향은 작습니다 (std 30분: n 14 → 0.515, n 212 → 0.529).
- 하루 여러 번인 `meal_time`은 규칙적이어도 entropy가 커서 값이 낮습니다.

`config/policy.toml`의 `predictability_min = 0.05`는 이전(정규화 전) 계산 기준입니다.
현재 스케일에서는 std 120분도 0.225라 사실상 모두 통과합니다.
새 값은 위 결과를 근거로 팀에서 정합니다 (이 모듈에서 수정하지 않음).

## 고도화 옵션 (기본 꺼짐)

`weighting.py`. 값은 실험으로 정하며 모듈에 기본값을 두지 않습니다.

| 옵션 | 식 | 설정 |
|---|---|---|
| Recent weighting | `weight = exp(-λ · age_days)` | `SampleWeighting(recent_lambda=λ)` |
| Prompted attenuation | prompted 표본만 `weight × w` (0 < w ≤ 1, 완전 제외 안 함) | `SampleWeighting(prompted_weight=w)` |
| Cold start blending | `α · personal + (1 − α) · prior`, `α = days / (days + half_days)` | `ColdStart(half_days, priors={name: density})` |

```python
from baseline import build_model
from weighting import ColdStart, SampleWeighting

build_model(
    samples,
    sample_weighting=SampleWeighting(recent_lambda=0.05, prompted_weight=0.3),
    cold_start=ColdStart(half_days=14, priors={"meal_time": prior_96}),
)
```

- 가중치를 쓰면 bandwidth도 유효 표본 수(Kish) 기준으로 계산됩니다 (`meta[...]["weighting"]["effective_samples"]`).
- prior는 외부에서 주입합니다. prior가 없는 distribution은 개인 분포를 그대로 씁니다.
- predictability는 섞은 뒤의 최종 density로 계산합니다.

## 역할 분담

```text
RPi4  Learning Engine
      - t0 로그 축적
      - KDE density / predictability 학습
      - hestia/model/kde 발행 (QoS 1, retained)

        ↓ MQTT

RPi5  Context Engine
      - ModelStore에 최신 모델 보관
      - tail probability / percentile 조회
      - Rule / Policy와 결합해 개입 여부 결정
```
