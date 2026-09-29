import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

sys.path.append(
    str(ROOT / "services" / "learning-engine")
)

sys.path.append(
    str(ROOT / "services" / "context-engine")
)


from baseline import build_model
from meal_policy import evaluate_meal_policy


def main():
    model = build_model()

    meal = model["distributions"]["meal_time"]
    density = meal["density"]
    grid_min = meal["grid_min"]
    grid_step = meal["grid_step"]

    predictability = model["predictability"]["meal_time"]
    sample_days = model["sample_days"]

    def tail_probability(query_time: str) -> float:
        hour, minute = map(
            int,
            query_time.split(":"),
        )

        query_minute = hour * 60 + minute

        index = (
            query_minute - grid_min
        ) // grid_step

        index = max(
            0,
            min(
                index,
                len(density) - 1,
            ),
        )

        return sum(
            density[index:]
        )

    tail_0730 = tail_probability("07:30")
    tail_0940 = tail_probability("09:40")

    result_0730 = evaluate_meal_policy(
        tail_probability=tail_0730,
        predictability=predictability,
    )

    result_0940 = evaluate_meal_policy(
        tail_probability=tail_0940,
        predictability=predictability,
    )

    print("=== Aruba KDE Policy Validation ===")
    print()
    print(f"Observed days       : {sample_days}")
    print(f"Predictability      : {predictability:.3f}")
    print()

    print("07:30")
    print(
        f"Tail probability    : "
        f"{tail_0730 * 100:.2f}%"
    )
    print(
        f"Candidate           : "
        f"{result_0730.candidate}"
    )
    print(
        f"Reason              : "
        f"{result_0730.reason}"
    )

    print()

    print("09:40")
    print(
        f"Tail probability    : "
        f"{tail_0940 * 100:.2f}%"
    )
    print(
        f"Candidate           : "
        f"{result_0940.candidate}"
    )
    print(
        f"Reason              : "
        f"{result_0940.reason}"
    )


if __name__ == "__main__":
    main()