import numpy as np
from scipy.stats import gaussian_kde


BREAKFAST_START = 5.0
BREAKFAST_END = 11.0


def calculate_predictability(kde):
    grid = np.linspace(
        BREAKFAST_START,
        BREAKFAST_END,
        300,
    )

    density = kde(grid)

    probability = density / density.sum()

    probability = probability[
        probability > 0
    ]

    entropy = -np.sum(
        probability * np.log(probability)
    )

    max_entropy = np.log(len(grid))

    normalized_entropy = entropy / max_entropy

    predictability = 1 - normalized_entropy

    return predictability


def evaluate(std_minutes, sample_count=212):
    std_hours = std_minutes / 60

    times = np.random.normal(
        loc=7.5,
        scale=std_hours,
        size=sample_count,
    )

    # 아침 시간대 밖으로 나간 값은 잘라냄
    times = np.clip(
        times,
        BREAKFAST_START,
        BREAKFAST_END,
    )

    kde = gaussian_kde(times)

    predictability = calculate_predictability(kde)

    actual_std_minutes = np.std(times) * 60

    print(
        f"target std={std_minutes:3d} min "
        f"| actual std={actual_std_minutes:6.1f} min "
        f"| predictability={predictability:.3f}"
    )


if __name__ == "__main__":
    np.random.seed(42)

    std_values = [
        10,
        20,
        30,
        45,
        60,
        75,
        90,
        120,
    ]

    print()
    print("===== Predictability Validation =====")
    print()

    for std_minutes in std_values:
        evaluate(std_minutes)

    print()