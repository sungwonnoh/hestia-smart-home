"""HESTIA Context Engine.

MQTT로 센서 이벤트와 학습 모델을 받아 판단 로직에 넘긴다.
시각은 항상 self.clock을 통해서만 얻는다. time.time() 직접 호출 금지.
"""

import json
import sys

import paho.mqtt.client as mqtt

from clock import Clock, RealClock
from kde_context import get_meal_time_context
from meal_policy import evaluate_meal_policy
from model_ingest import ingest_model_message
from model_store import ModelStore


BROKER = "127.0.0.1"
PORT = 1883

SENSOR_TOPIC = "hestia/sensor/#"
MODEL_TOPIC = "hestia/model/+"


class ContextEngine:
    def __init__(self, clock: Clock):
        self.clock = clock

        # RPi4에서 전달받은 최신 학습 모델 저장
        self.model_store = ModelStore()

    def ingest(
        self,
        topic: str,
        payload: dict,
    ) -> None:
        """모든 입력의 단일 진입점.

        실제 MQTT와 Replay가 동일한 처리 경로를 사용한다.
        """

        if topic.startswith("hestia/model/"):
            handled = self.handle_model(
                topic,
                payload,
            )

            if not handled:
                print(
                    f"[MODEL] unsupported topic: {topic}",
                    flush=True,
                )

            return

        self.handle_event(
            topic,
            payload,
        )

    def handle_event(
        self,
        topic: str,
        payload: dict,
    ) -> None:
        """센서 이벤트 하나를 처리한다."""

        now = self.clock.now()

        # 현재는 기존 구조 유지.
        # 최종 명세에서는 recv_ts 기준으로 판단하도록 확장 예정.
        event_ts = payload.get(
            "ts",
            now,
        )

        print(
            f"[{now:.3f}] "
            f"{topic} "
            f"ts={event_ts} "
            f"{payload}",
            flush=True,
        )

        # TODO: FSM 상태 전이
        # TODO: World State 갱신
        # TODO: Policy 연결

    def handle_model(
        self,
        topic: str,
        payload: dict,
    ) -> bool:
        """Learning Engine에서 전달된 모델 메시지를 처리한다."""

        handled = ingest_model_message(
            topic,
            payload,
            self.model_store,
        )

        if handled:
            kde = self.model_store.get_kde()

            if kde is not None:
                print(
                    "[MODEL] KDE updated "
                    f"trained_at={kde.trained_at} "
                    f"sample_days={kde.sample_days}",
                    flush=True,
                )

        return handled

    def get_meal_kde_context(
        self,
        query_time: str,
    ) -> dict:
        """현재 meal_time KDE 정보를 조회한다."""

        return get_meal_time_context(
            self.model_store,
            query_time,
        )

    def evaluate_meal_intervention(
        self,
        query_time: str,
    ) -> dict:
        """현재 시각 기준 KDE 기반 meal 개입 후보를 계산한다."""

        context = self.get_meal_kde_context(
            query_time
        )

        if not context["available"]:
            return {
                "available": False,
                "candidate": False,
                "tail_probability": None,
                "predictability": None,
                "reason": "KDE_MODEL_UNAVAILABLE",
            }

        result = evaluate_meal_policy(
            tail_probability=context[
                "tail_probability"
            ],
            predictability=context[
                "predictability"
            ],
        )

        return {
            "available": True,
            "candidate": result.candidate,
            "tail_probability": result.tail_probability,
            "predictability": result.predictability,
            "reason": result.reason,
        }


def make_mqtt_client(
    engine: ContextEngine,
) -> mqtt.Client:

    def on_connect(
        client,
        userdata,
        flags,
        reason_code,
        properties,
    ):
        print(
            f"connected rc={reason_code}",
            flush=True,
        )

        client.subscribe(
            SENSOR_TOPIC,
            qos=1,
        )

        client.subscribe(
            MODEL_TOPIC,
            qos=1,
        )

        print(
            f"subscribed: {SENSOR_TOPIC}",
            flush=True,
        )

        print(
            f"subscribed: {MODEL_TOPIC}",
            flush=True,
        )

    def on_message(
        client,
        userdata,
        msg,
    ):
        try:
            payload = json.loads(
                msg.payload.decode()
            )

        except (
            json.JSONDecodeError,
            UnicodeDecodeError,
        ) as e:
            print(
                f"payload 파싱 실패 "
                f"{msg.topic}: {e}",
                flush=True,
            )
            return

        try:
            engine.ingest(
                msg.topic,
                payload,
            )

        except ValueError as e:
            print(
                f"[INGEST] invalid payload "
                f"{msg.topic}: {e}",
                flush=True,
            )

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2
    )

    client.on_connect = on_connect
    client.on_message = on_message

    return client


def main() -> int:
    engine = ContextEngine(
        clock=RealClock()
    )

    client = make_mqtt_client(
        engine
    )

    client.connect(
        BROKER,
        PORT,
        keepalive=60,
    )

    client.loop_forever()

    return 0


if __name__ == "__main__":
    sys.exit(main())