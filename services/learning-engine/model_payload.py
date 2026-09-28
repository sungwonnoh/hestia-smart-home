import json
import time

from baseline import build_model


VERSION = 1
SRC_ID = "rpi4"


def build_kde_payload() -> dict:
    """
    baseline.py에서 생성한 KDE 모델을
    hestia/model/kde MQTT 명세 형태로 변환한다.
    """

    model = build_model()

    now = int(time.time())

    payload = {
        "version": VERSION,
        "sent_ts": now,
        "src_id": SRC_ID,
        "trained_at": now,
        "sample_days": model["sample_days"],
        "distributions": model["distributions"],
        "predictability": model["predictability"],
    }

    return payload


if __name__ == "__main__":
    payload = build_kde_payload()

    print(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        )
    )