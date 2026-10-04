from dataclasses import dataclass
from typing import Dict, List


@dataclass
class KDEDistribution:
    grid_min: int
    grid_step: int
    density: List[float]


@dataclass
class KDEModel:
    version: int
    sent_ts: int
    src_id: str
    trained_at: int
    sample_days: int
    distributions: Dict[str, KDEDistribution]
    predictability: Dict[str, float]


def parse_kde_payload(payload: dict) -> KDEModel:
    """
    hestia/model/kde payload 검증 및 파싱
    """

    required_fields = [
        "version",
        "sent_ts",
        "src_id",
        "trained_at",
        "sample_days",
        "distributions",
        "predictability",
    ]

    for field in required_fields:
        if field not in payload:
            raise ValueError(
                f"missing KDE payload field: {field}"
            )

    distributions = {}

    for name, dist in payload["distributions"].items():

        for field in [
            "grid_min",
            "grid_step",
            "density",
        ]:
            if field not in dist:
                raise ValueError(
                    f"missing distribution field: "
                    f"{name}.{field}"
                )

        density = dist["density"]

        if not isinstance(density, list):
            raise ValueError(
                f"{name}.density must be list"
            )

        if len(density) == 0:
            raise ValueError(
                f"{name}.density is empty"
            )

        # 현재 meal_time 명세:
        # 하루 24시간 / 15분 = 96칸
        if (
            name == "meal_time"
            and dist["grid_step"] == 15
            and len(density) != 96
        ):
            raise ValueError(
                "meal_time density must have 96 bins"
            )

        distributions[name] = KDEDistribution(
            grid_min=dist["grid_min"],
            grid_step=dist["grid_step"],
            density=density,
        )

    return KDEModel(
        version=payload["version"],
        sent_ts=payload["sent_ts"],
        src_id=payload["src_id"],
        trained_at=payload["trained_at"],
        sample_days=payload["sample_days"],
        distributions=distributions,
        predictability=payload["predictability"],
    )


def time_to_minutes(time_str: str) -> int:
    """
    HH:MM -> 자정 기준 분
    """

    hour, minute = map(
        int,
        time_str.split(":"),
    )

    return hour * 60 + minute


def calculate_tail_probability(
    model: KDEModel,
    distribution_name: str,
    query_time: str,
) -> float:
    """
    주어진 시각 이후 density 합을 계산한다.
    """

    if distribution_name not in model.distributions:
        raise ValueError(
            f"distribution not found: "
            f"{distribution_name}"
        )

    dist = model.distributions[
        distribution_name
    ]

    query_minutes = time_to_minutes(
        query_time
    )

    start_index = int(
        (
            query_minutes
            - dist.grid_min
        )
        // dist.grid_step
    )

    start_index = max(
        0,
        min(
            start_index,
            len(dist.density),
        ),
    )

    return float(
        sum(
            dist.density[
                start_index:
            ]
        )
    )


def get_predictability(
    model: KDEModel,
    distribution_name: str,
) -> float:
    """
    distribution별 predictability 반환
    """

    if (
        distribution_name
        not in model.predictability
    ):
        raise ValueError(
            f"predictability not found: "
            f"{distribution_name}"
        )

    return float(
        model.predictability[
            distribution_name
        ]
    )