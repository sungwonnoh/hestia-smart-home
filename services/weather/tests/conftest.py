from __future__ import annotations

import json
import urllib.parse
from datetime import datetime

import pytest

from hestia_weather.kma import KST


def kst(*args) -> float:
    return datetime(*args, tzinfo=KST).timestamp()


def ncst_response(base_date: str, base_time: str, **values) -> bytes:
    """초단기실황 정상 응답 (JSON)."""
    values = {"T1H": "24.1", "REH": "61", "RN1": "0", "PTY": "0", "WSD": "1.8", **values}
    items = [
        {"baseDate": base_date, "baseTime": base_time, "category": k,
         "nx": 60, "ny": 127, "obsrValue": v}
        for k, v in values.items()
    ]
    return json.dumps({"response": {
        "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
        "body": {"dataType": "JSON", "items": {"item": items},
                 "pageNo": 1, "numOfRows": 100, "totalCount": len(items)},
    }}).encode()


NO_DATA = json.dumps({"response": {
    "header": {"resultCode": "03", "resultMsg": "NO_DATA"},
}}).encode()

KEY_ERROR_XML = (
    b"<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg>"
    b"<returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>"
    b"<returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"
)


def warning_response(*items: dict) -> bytes:
    """기상특보 통보문(getWthrWrnMsg) 응답. item 은 tmFc / tmSeq / t6 만 있으면 된다."""
    full = [{"stnId": "108", "other": "o 없음 ", "t1": "", "t2": "", "t3": "", "t4": "",
             "t5": "", "t7": "o 없음", "warFc": "1 ", **it} for it in items]
    return json.dumps({"response": {
        "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
        "body": {"dataType": "JSON", "items": {"item": full},
                 "pageNo": 1, "numOfRows": 100, "totalCount": len(full)},
    }}).encode()


class FakeKma:
    """초단기실황: base_time(HH00) → 응답, 없는 시각은 NO_DATA.
    특보: self.warning (기본 NO_DATA). 호출한 URL 을 남긴다."""

    def __init__(self, responses: dict[str, bytes] | None = None, warning: bytes = NO_DATA) -> None:
        self.responses = responses or {}
        self.warning = warning
        self.urls: list[str] = []
        self.fail: Exception | None = None
        self.warning_fail: Exception | None = None

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        if "getWthrWrnMsg" in url:
            if self.warning_fail is not None:
                raise self.warning_fail
            return self.warning
        if self.fail is not None:
            raise self.fail
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        return self.responses.get(q["base_time"][0], NO_DATA)

    def ncst_urls(self) -> list[str]:
        return [u for u in self.urls if "getUltraSrtNcst" in u]

    def params(self, i: int = -1) -> dict[str, str]:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.urls[i]).query)
        return {k: v[0] for k, v in q.items()}


@pytest.fixture
def fake_kma() -> FakeKma:
    return FakeKma()
