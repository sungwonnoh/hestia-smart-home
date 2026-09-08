"""Clock 주입.

엔진 코드 어디에서도 datetime.now()를 직접 부르지 않는다.
시간은 항상 ctx.clock.now()로만 얻는다.

ReplayClock은 개발용이다. MQTT로 들어오는 센서 시각이 아니라
엔진 내부 시계를 조작해서 쿨다운/타임아웃을 즉시 검증하기 위한 것.
"""

from datetime import datetime, timedelta


class RealClock:
    kind = "real"

    def now(self) -> datetime:
        return datetime.now().astimezone()


class ReplayClock:
    kind = "replay"

    def __init__(self, start: datetime | None = None):
        self._t = start or datetime.now().astimezone()

    def now(self) -> datetime:
        return self._t

    def advance(self, seconds: float) -> datetime:
        self._t += timedelta(seconds=seconds)
        return self._t

    def set(self, iso: str) -> datetime:
        t = datetime.fromisoformat(iso)
        self._t = t if t.tzinfo else t.astimezone()
        return self._t


def parse_duration(text: str) -> float:
    """'+90s', '30m', '2h', '3600' 을 초로."""
    s = text.strip().lstrip("+")
    if not s:
        raise ValueError("빈 값")
    unit = s[-1].lower()
    if unit in "smh":
        n = float(s[:-1])
        return n * {"s": 1, "m": 60, "h": 3600}[unit]
    return float(s)