import json
import os

import paho.mqtt.client as mqtt

from model_payload import build_kde_payload


MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))

TOPIC = "hestia/model/kde"


def publish_kde_model():
    payload = build_kde_payload()

    client = mqtt.Client()

    client.connect(
        MQTT_HOST,
        MQTT_PORT,
        60,
    )

    result = client.publish(
        TOPIC,
        json.dumps(payload),
        qos=1,
        retain=True,
    )

    result.wait_for_publish()

    print(
        f"[PUBLISHED] topic={TOPIC}"
    )

    print(
        f"[MODEL] sample_days="
        f"{payload['sample_days']}"
    )

    print(
        f"[MODEL] predictability="
        f"{payload['predictability']['meal_time']:.3f}"
    )

    client.disconnect()


if __name__ == "__main__":
    publish_kde_model()