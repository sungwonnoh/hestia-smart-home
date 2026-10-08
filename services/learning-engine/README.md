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
| synthetic | `synthetic.py` | hydration_lag | Aruba에 수분 섭취 라벨 없음. **검증용 fixture** |
| Context Engine t0 | `baseline.py` | meal / wake / hydration | 현재 t0 명세에 `sleep` type 없음 |

모든 출처는 공통 표본 `KdeSample`(`samples.py`)로 바뀐 뒤 같은 KDE를 탑니다.

```text
aruba.py      ─┐
synthetic.py  ─┼─→ KdeSample ─→ fit_distribution ─→ density + predictability
t0 adapter    ─┘
```

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
