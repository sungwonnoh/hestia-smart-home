"""시각 좌표 변환

epoch 초를 KDE 격자의 좌표로 바꾸는 순수 함수 모음 (상태를 갖지 않으며 Clock 에도 의존하지 않음)

:epoch 초  →  자정 기준 분  →  격자 인덱스  →  density[i]

순환(23:50 과 00:10 이 이웃) 처리는 여기 없음-> KDE 쪽으로 넘김
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo

KST = timezone(timedelta(hours=9))      #한국 표준시를 나타내는 상수 객체 생성
MINUTES_PER_DAY = 1440                  #하루 전체를 분 단위로 환산한 상수 값(60분 x 24시간)-> 하루를 특정 분 간격으로 나눌 때, 전체 칸 수(인덱스 크기)를 계산하는 상수로 활용


def minutes_since_midnight(ts: float, tz: tzinfo = KST) -> int:
    """epoch 초를 → 로컬 자정 기준 경과 분 (0~1439)으로 변환
    초 단위는 버림(격자가 5분 또는 15분 간격)
    """
    dt = datetime.fromtimestamp(ts, tz) #ts 값을 지정된 타임존(tz) 기준의 년-월-일 시:분:초 형태(datetime 객체)로 변환
    return dt.hour * 60 + dt.minute


def grid_index(minutes: float, grid_min: float = 0.0, grid_step: float = 15.0) -> int:
    """자정 기준 분(minutes)을 → density 배열의 '인덱스'로 변환
    경계는 내림(//)으로 처리=> grid_step=15 일 때 585분(09:45)은 38번(09:30~09:45)이 아니라 39번(09:45~10:00)임
    """
    if grid_step <= 0:
        raise ValueError(f"grid_step 은 양수여야 함: {grid_step}")
    return int((minutes - grid_min) // grid_step)       #grid_min: 격자의 시작 기준 시각, 기본적으로는 '자정(00:00 = 0분)'이 시작점


def grid_size(grid_step: float = 15.0) -> int:
    """하루를 grid_step 으로 나눈 칸 수, 15분이면 96."""
    if grid_step <= 0:
        raise ValueError(f"grid_step 은 양수여야 함: {grid_step}")
    return int(MINUTES_PER_DAY // grid_step)


def day_key(ts: float, tz: tzinfo = KST) -> str:
    """epoch 초 → 'YYYY-MM-DD'
    t0 로그의 날짜 키, wake context 의 '오늘' 판정에 쓴다.
    """
    return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d")


def is_same_day(a: float, b: float, tz: tzinfo = KST) -> bool:      #epoch 초 a와 b가 서로 같은 날짜인지
    return day_key(a, tz) == day_key(b, tz)


def clock_str(ts: float, tz: tzinfo = KST) -> str:
    """로그·디버그용 'HH:MM:SS'. 판단에는 쓰지 않음"""
    return datetime.fromtimestamp(ts, tz).strftime("%H:%M:%S")