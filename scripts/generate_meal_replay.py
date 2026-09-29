import json
from pathlib import Path


OUTPUT_DIR = Path("logs/replay")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def make_base_density() -> list[float]:
    """24시간 / 15분 간격 = 96 bins."""
    return [0.0] * 96


def build_user_a() -> dict:
    """
    User A:
    평소 아침 식사가 07:30~09:15 부근에 몰려 있는 사용자.

    09:40에는 남아 있는 tail probability가 매우 작도록 구성한다.
    """

    density = make_base_density()

    density[30] = 0.25   # 07:30
    density[31] = 0.35   # 07:45
    density[32] = 0.25   # 08:00
    density[33] = 0.10   # 08:15
    density[34] = 0.03   # 08:30
    density[35] = 0.01   # 08:45
    density[36] = 0.005  # 09:00
    density[37] = 0.005  # 09:15

    return {
        "version": 1,
        "sent_ts": 1790340000,
        "src_id": "rpi4",
        "trained_at": 1790340000,
        "sample_days": 21,
        "distributions": {
            "meal_time": {
                "grid_min": 0,
                "grid_step": 15,
                "density": density,
            }
        },
        "predictability": {
            "meal_time": 0.35,
        },
    }


def build_user_b() -> dict:
    """
    User B:
    아침 식사 시간이 07:30~11:30 정도까지 넓게 퍼져 있는 사용자.

    09:40 이후에도 probability mass가 충분히 남도록 구성한다.
    """

    density = make_base_density()

    # 07:30 ~ 09:30
    # index 30 ~ 38
    for i in range(30, 39):
        density[i] = 0.05

    # 09:45 ~ 11:00
    # index 39 ~ 44
    for i in range(39, 45):
        density[i] = 0.075

    # 남은 probability mass
    density[45] = 0.05  # 11:15
    density[46] = 0.05  # 11:30

    return {
        "version": 1,
        "sent_ts": 1790340000,
        "src_id": "rpi4",
        "trained_at": 1790340000,
        "sample_days": 21,
        "distributions": {
            "meal_time": {
                "grid_min": 0,
                "grid_step": 15,
                "density": density,
            }
        },
        "predictability": {
            "meal_time": 0.25,
        },
    }


def validate_model_payload(
    model_payload: dict,
) -> None:
    """Replay 파일 생성 전에 KDE payload를 간단히 검증한다."""

    density = (
        model_payload[
            "distributions"
        ][
            "meal_time"
        ][
            "density"
        ]
    )

    if len(density) != 96:
        raise ValueError(
            f"density must have 96 bins, "
            f"got {len(density)}"
        )

    density_sum = sum(density)

    if abs(density_sum - 1.0) > 1e-6:
        raise ValueError(
            f"density must sum to 1.0, "
            f"got {density_sum}"
        )


def write_replay(
    filename: str,
    model_payload: dict,
) -> None:
    validate_model_payload(
        model_payload
    )

    events = [
        {
            "ts": 1790340000,
            "topic": "hestia/model/kde",
            "payload": model_payload,
        },
        {
            "ts": 1790349600,
            "query_time": "09:40",
            "topic": "hestia/sensor/test/state",
            "payload": {
                "type": "tick",
            },
        },
    ]

    path = (
        OUTPUT_DIR
        / filename
    )

    with path.open(
        "w",
        encoding="utf-8",
    ) as f:
        for event in events:
            f.write(
                json.dumps(
                    event,
                    ensure_ascii=False,
                )
                + "\n"
            )

    density = (
        model_payload[
            "distributions"
        ][
            "meal_time"
        ][
            "density"
        ]
    )

    print(
        f"{path}: "
        f"{len(density)} bins, "
        f"sum={sum(density)}"
    )


def main() -> None:
    user_a = build_user_a()
    user_b = build_user_b()

    # tuple 실수 같은 타입 오류를 미리 잡는다.
    assert isinstance(
        user_a,
        dict,
    )

    assert isinstance(
        user_b,
        dict,
    )

    write_replay(
        "meal_user_a.jsonl",
        user_a,
    )

    write_replay(
        "meal_user_b.jsonl",
        user_b,
    )


if __name__ == "__main__":
    main()