from pathlib import Path
import csv
from datetime import datetime

INPUT_PATH = Path("data/processed/aruba/meal_preparation.csv")
OUTPUT_PATH = Path("data/processed/aruba/breakfast_preparation.csv")

BREAKFAST_START = 5   # 05:00
BREAKFAST_END = 11    # 11:00


def parse_time(time_str):
    return datetime.strptime(time_str, "%H:%M:%S.%f").time()


def filter_breakfast():
    # 날짜별 첫 번째 아침 Meal_Preparation 저장
    first_event_by_date = {}

    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            date = row["date"]
            time_str = row["time"]

            t = parse_time(time_str)

            # 05:00 <= time < 11:00
            if not (BREAKFAST_START <= t.hour < BREAKFAST_END):
                continue

            # 같은 날짜에서는 가장 첫 번째 이벤트만 사용
            if date not in first_event_by_date:
                first_event_by_date[date] = {
                    "date": date,
                    "time": time_str,
                    "activity": "Breakfast_Preparation",
                }

    rows = list(first_event_by_date.values())

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["date", "time", "activity"],
        )

        writer.writeheader()
        writer.writerows(rows)

    print(f"[DONE] extracted {len(rows)} breakfast events")
    print(f"[OUTPUT] {OUTPUT_PATH}")


if __name__ == "__main__":
    filter_breakfast()