import argparse

from baseline import (
    build_model,
    GRID_STEP,
)


def time_to_minutes(time_str: str) -> int:
    hour, minute = map(
        int,
        time_str.split(":"),
    )

    return hour * 60 + minute


def calculate_tail_probability(
    density,
    query_time,
):
    """
    query_time 이후 density 합을 계산한다.
    """

    query_minutes = time_to_minutes(
        query_time
    )

    start_index = (
        query_minutes // GRID_STEP
    )

    tail_probability = sum(
        density[start_index:]
    )

    return tail_probability


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--time",
        required=True,
        help="HH:MM",
    )

    args = parser.parse_args()

    model = build_model()

    density = (
        model["distributions"]
        ["meal_time"]
        ["density"]
    )

    predictability = (
        model["predictability"]
        ["meal_time"]
    )

    tail_probability = (
        calculate_tail_probability(
            density,
            args.time,
        )
    )

    print()
    print("===== KDE DEBUG =====")
    print()

    print(
        f"Query           : "
        f"{args.time}"
    )

    print(
        f"Tail probability: "
        f"{tail_probability * 100:.2f}%"
    )

    print(
        f"Predictability  : "
        f"{predictability:.3f}"
    )

    print()


if __name__ == "__main__":
    main()