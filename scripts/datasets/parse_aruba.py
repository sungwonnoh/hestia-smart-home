from pathlib import Path
import csv

INPUT_PATH = Path("data/raw/casas/aruba/aruba.txt")
OUTPUT_PATH = Path("data/processed/aruba/meal_preparation.csv")

TARGET_ACTIVITY = "Meal_Preparation"


def parse_aruba():
    rows = []

    with open(INPUT_PATH, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()

            if not line:
                continue

            # Meal_Preparation 시작 이벤트만 추출
            if TARGET_ACTIVITY not in line:
                continue

            if "begin" not in line.lower():
                continue

            parts = line.split()

            # 예:
            # 2010-11-04 07:32:15.123456 M003 ON Meal_Preparation begin
            if len(parts) < 6:
                continue

            date = parts[0]
            time = parts[1]

            rows.append({
                "date": date,
                "time": time,
                "activity": TARGET_ACTIVITY,
            })

    rows.sort(key=lambda x: (x["date"], x["time"]))

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["date", "time", "activity"]
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"[DONE] extracted {len(rows)} Meal_Preparation begin events")
    print(f"[OUTPUT] {OUTPUT_PATH}")


if __name__ == "__main__":
    parse_aruba()