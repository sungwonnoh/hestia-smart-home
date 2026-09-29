from kde_model import parse_kde_payload
from model_store import ModelStore


KDE_TOPIC = "hestia/model/kde"


def ingest_model_message(
    topic: str,
    payload: dict,
    store: ModelStore,
) -> bool:
    """
    Learning Engine에서 전달된 model MQTT 메시지를 처리한다.

    처리한 메시지면 True,
    대상이 아니면 False 반환.
    """

    if topic == KDE_TOPIC:
        model = parse_kde_payload(
            payload
        )

        store.set_kde(
            model
        )

        return True

    return False