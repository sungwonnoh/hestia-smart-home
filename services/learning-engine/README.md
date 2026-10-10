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

- circular: 1440분 주기. 23:50과 00:10은 20분 차이로 계산합니다.
- `hydration_lag` 는 팀 결정으로 KDE 에서 뺐습니다. Context Engine HYDRATION_PROMPT 는 분포 대신 간격 규칙을 쓰고,
  t0 로그의 `hydration` 레코드는 KDE 학습에서 제외됩니다.
- 표본이 없거나 KDE를 계산할 수 없는 분포는 모델에서 빠집니다 (빈 배열을 보내지 않음).

## 데이터 출처

실제 Context Engine t0 로그가 쌓이기 전까지 공개 데이터(CASAS)로 개발·검증합니다.

| 출처 | 모듈 | 분포 | 비고 |
|---|---|---|---|
| CASAS Aruba | `aruba.py` | meal / sleep / wake | sleep·wake는 `Sleeping` 라벨 **proxy** |
| CASAS Milan / Tulum2 / Cairo | `casas.py` | sleep / wake | 거주자별 **proxy**. predictability 비교·검증용 |
| Context Engine t0 | `baseline.py` | meal (+ 이전 로그의 wake) | `hydration` 은 제외 |
| Context Engine t0 `sleep_start` / `sleep_end` | `sleep_sessions.py` | sleep / wake | 같은 t0 로그. 짝지어 수면으로 만든 뒤 밤잠만 학습 |

모든 출처는 공통 표본 `KdeSample`(`samples.py`)로 바뀐 뒤 같은 KDE를 탑니다.

```text
aruba.py / casas.py ─┐
t0 adapter          ─┴─→ KdeSample ─→ fit_distribution ─→ density + predictability
```

## 식사: 학습 시각 (t0 / eat_t0)

Context Engine meal 레코드는 식사 묶음 하나에 시각을 둘 남깁니다.

| 끼니 | `t0` (묶음 시작) | `eat_t0` (EATING 판정) |
|---|---|---|
| 조리 후 먹음 | 인덕션 ON 09:20 | 09:45 |
| 조리 없이 먹음 | 주방 진입 12:00 | 12:06 |
| 조리만 하고 안 먹음 | 09:20 | `null` |

- **`eat_t0 = null` 인 meal 은 학습에서 뺍니다** (식사가 아님)
- `eat_t0` 필드가 아예 없는 레코드(이전 로그)는 판단할 수 없어 `t0` 로 학습합니다
- `--meal-source t0|eat_t0` (기본 `t0`): `eat_t0` 로 학습하면 `eat_t0` 가 있는 레코드만 쓰고 날짜도 `eat_t0` 기준입니다.
  어느 쪽으로 학습할지는 실데이터로 비교해 정합니다

CASAS 개발 데이터도 같은 단위로 만듭니다 (`aruba.meal_sessions`).
조리(`Meal_Preparation`)·먹기(`Eating`) 라벨을 Context Engine `fsm.meal` 규칙
(10분 유예, 2분 미만 제외, 2시간 상한)으로 묶어 `t0` = 묶음 시작, `eat_t0` = 묶음 안 첫 먹기 시작.
CASAS 는 먹기 라벨을 다 붙이지 않아 (Aruba `Eating` 은 219일 중 137일) 먹기 라벨이 없는 묶음은
`null` 이 아니라 **`eat_t0` 필드 없음(모름)** 으로 둡니다. Aruba: 라벨 1606 → 식사 묶음 1020, 먹기 시각 있음 210.

## 식사: 끼니 구간 (meal_time peaks)

`meal_time`은 하루 전체 식사(아침·점심·저녁) 분포라 봉우리가 여러 개입니다.
전체 predictability는 규칙적인 사람도 낮게(0.07~0.11) 나와 판단 게이트로 쓸 수 없어서,
끼니가 2개 이상이면 **끼니 구간과 끼니별 predictability**를 함께 보냅니다 (Context Engine 합의).

아래는 CASAS Cairo 값입니다 (세 끼 모두 기본 기준을 통과).

```json
"meal_time": {
  "grid_min": 0, "grid_step": 15, "density": [...],
  "peaks": [
    {"center": 465,  "from": 45,  "to": 615, "predictability": 0.41, "days_ratio": 0.87, "meals_per_day": 0.87},
    {"center": 675,  "from": 615, "to": 900, "predictability": 0.54, "days_ratio": 0.67, "meals_per_day": 0.67},
    {"center": 1065, "from": 900, "to": 45,  "predictability": 0.65, "days_ratio": 0.76, "meals_per_day": 0.76}
  ]
}
```

| 필드 | 의미 |
|---|---|
| `center` | 그 끼니에 식사가 가장 몰린 칸의 시작 분 (구간 가운데가 아님) |
| `from` / `to` | 끼니 구간, 자정 기준 분. `from` 포함 · `to` 미포함, `from > to` 면 자정을 넘음 |
| `predictability` | 그 구간 식사만으로 다시 그린 KDE(96칸)의 predictability. 식사 2개 미만이면 `null` |
| `days_ratio` | 그 구간에 식사가 있었던 날 / `meal_time` sample_days. 끼니를 먹는 날의 비율 |
| `meals_per_day` | 그 구간 식사 수 / sample_days. 1보다 크면 구간에 끼니가 여럿이거나 한 끼가 쪼개져 기록됨 |

- 끼니 = 발행하는 `meal_time` density 에서 균등분포(1/96)보다 높은 봉우리, 경계 = 봉우리 사이 최저점 (고정 식사 시각 없음)
- **다음 끼니는 보내지 않습니다** (Context Engine MEAL 설계 합의값, 임시 — 실데이터로 재검토)
  - `days_ratio` < 0.5 (`MEAL_MIN_DAYS_RATIO`): 과반의 날에 먹어야 식사 습관으로 본다.
    구간 tail 은 구간 안에서 다시 정규화돼 빈도를 지운다. `days_ratio` p 인 끼니에 거름 알림을 하면
    정상인데도 1−p 의 날에 울린다 (0.67 → 3일에 한 번, 0.86 → 일주일에 한 번)
  - `meals_per_day` > 1.5 (`MEAL_MAX_MEALS_PER_DAY`): 두 끼가 골짜기 없이 이어져 한 봉우리가 된 경우.
    보내면 앞 끼니를 먹은 것이 뒤 끼니의 "이미 먹음"이 되어 뒤 끼니 거름을 못 잡는다
  - 그 시간대는 이웃 끼니에 합치지 않고 비워 둡니다 → Context Engine 은 그 시간대에 식사 판단을 하지 않음.
    거른 끼니와 이유는 `meta.meal_time.meal_peaks_dropped` 에 남습니다
- `days_ratio` / `meals_per_day` 는 가중치 없이 실제 횟수로 셉니다. sample_days 는 식사 기록이 있는 날만 셉니다
- 봉우리가 1개 이하면 `peaks` 를 넣지 않습니다 → Context Engine 은 전체 predictability 사용.
  봉우리가 2개 이상이었으면 거른 뒤 1개면 1개, 0개면 `[]` 를 보냅니다
- 끼니 수는 사람마다 다릅니다 (CASAS: Cairo 3, Aruba 2, Tulum2 2)
- 임계값(`[thresholds.meal] predictability_min`)은 Context Engine 정책입니다

## 수면: 밤잠 / 낮잠 구분 (학습 데이터 선별)

`sleep_time` / `wake_time`은 **밤잠만** 학습합니다. 구분은 `sleep_sessions.py`가 RPi4 배치에서 합니다.
지금 이 수면이 밤잠인지 실시간으로 판정하는 일은 RPi5 Context Engine 담당입니다.

```text
수면 기록 (CASAS 라벨 / Context Engine sleep_start · sleep_end)
  ↓ merge_sessions      끊긴 구간 병합: 간격 ≤ 15분, CASAS Bed_to_Toilet, 또는
                        Context Engine awake_areas 가 화장실뿐이거나 비어 있음 (간격 무관)
  ↓ drop_implausible    24시간 이상은 기록 오류로 제외
  ↓ classify_sessions   정오~다음 날 정오마다 가장 긴 수면 = 밤잠, 나머지 = 낮잠
  ↓ night_samples       밤잠의 시작 → sleep_time, 끝 → wake_time
```

- 고정 취침·기상 시각을 쓰지 않습니다.
- 기상이 관측되지 않은 수면(기록 종료 등)은 취침 시각만 학습합니다.
- 알려진 한계: 그 밤의 수면 기록이 없으면 오후 낮잠이 밤잠으로 분류됩니다 (CASAS 6명 631밤 중 1건).

Context Engine 은 수면 시작·끝을 t0 로그에 따로 남깁니다 (meal 과 같은 형식, `t0` 는 epoch):

```json
{"date": "2026-09-01", "type": "sleep_start", "t0": 1788271200, "area": "bedroom", "awake_areas": ["bathroom"], "source": "sensor", "prompted": false, "duration_sec": 0}
{"date": "2026-09-02", "type": "sleep_end",   "t0": 1788300000, "source": "sensor", "prompted": false, "duration_sec": 0}
```

- 시각 순으로 `sleep_start` 와 다음 `sleep_end` 를 짝짓습니다. 끝이 없는 시작은 기상 미관측(취침만 학습), 시작이 없는 끝은 버립니다.
- `area` 는 저장만 하고 밤잠 / 낮잠 판단에는 쓰지 않습니다.
- **수면 잇기 (`awake_areas`)**: `sleep_start.awake_areas` 는 직전 `sleep_end` 뒤 깨어 있는 동안 들른 구역입니다
  (다시 잠든 구역 제외, `null` = 모름). 화장실만 다녀왔거나(`["bathroom"]`) 잠든 구역을 떠나지 않았으면(`[]`)
  **간격과 관계없이 같은 수면**으로 잇습니다 (`RESUME_AREAS`). 새벽에 1~2시간 깨어 있다 다시 잔 것도 하룻밤이 됩니다.
  주방·거실 등에 다녀왔거나 모르면 간격 규칙(15분)을 따릅니다. 잇는 방향으로만 쓰므로 15분 이내 간격은 늘 같은 수면입니다.
- **wake_time 출처**: 기상이 관측된 밤잠이 있으면 그 `sleep_end` 를 쓰고 `wake` 레코드는 쓰지 않습니다 (같은 기상을 두 번 학습하지 않도록). 없으면 `wake` 레코드를 씁니다.

## 실행

저장소 루트에서 실행합니다.

```bash
# 기본: Aruba breakfast CSV (meal_time만)
python3 services/learning-engine/baseline.py

# 개발용 3개 분포: Aruba 원본 (meal / sleep·wake proxy)
python3 services/learning-engine/baseline.py --aruba-raw data/raw/casas/aruba/aruba.txt

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
# 입력 옵션(--t0-jsonl / --aruba-raw / --meal-source)은 baseline.py와 같다
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
    --aruba-raw data/raw/casas/aruba/aruba.txt

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

`H_max`는 격자 칸 수 기준입니다 (15분 × 96칸 → ln 96).

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
| meal 전체 (식사 묶음 1020건, 아침·점심·저녁) | 0.064 |
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
