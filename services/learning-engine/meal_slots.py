"""
meal_time 분포에서 끼니 시간대를 찾는다.

고정 식사 시각을 쓰지 않는다. 그 사람의 하루 전체 식사 분포에서

    봉우리   균등분포(1 / 칸 수)보다 높은 국소 최댓값 = 끼니
    경계     이웃한 두 봉우리 사이에서 가장 낮은 칸

을 찾는다. 원(circular) 위에서 계산하므로 자정을 넘는 끼니도 이어진다.
사람마다 끼니 수가 다르다 (CASAS: Cairo 3, Aruba 2, Tulum2 2).
"""

from __future__ import annotations

from typing import Sequence

import numpy as np


def find_peaks(density: Sequence[float]) -> list[int]:
    """원 위의 국소 최댓값 중 균등분포보다 높은 칸 (시간 순)."""

    d = np.asarray(density, dtype=float)
    n = len(d)
    uniform = 1 / n

    return [
        i for i in range(n)
        if d[i] > d[i - 1] and d[i] >= d[(i + 1) % n] and d[i] > uniform
    ]


def segment_bins(density: Sequence[float], peaks: Sequence[int]) -> np.ndarray:
    """
    칸마다 속한 끼니 번호 (peaks 의 순서). 이웃한 두 봉우리 사이 가장 낮은 칸이 경계이고,
    경계 칸은 앞 끼니에 넣는다. 봉우리가 하나 이하면 모두 0.
    """

    d = np.asarray(density, dtype=float)
    n = len(d)
    seg = np.zeros(n, dtype=int)

    if len(peaks) <= 1:
        return seg

    valleys = []
    for k, p in enumerate(peaks):
        q = peaks[(k + 1) % len(peaks)]
        span = [(p + j) % n for j in range(1, (q - p) % n)]
        valleys.append(min(span, key=lambda i: d[i]) if span else p)

    for k, p in enumerate(peaks):
        end = valleys[k]
        start = valleys[k - 1]

        j = p
        while True:
            seg[j] = k
            if j == end:
                break
            j = (j + 1) % n

        j = (p - 1) % n
        while j != start:
            seg[j] = k
            j = (j - 1) % n

    return seg


def assign_slots(
    values: Sequence[float],
    density: Sequence[float],
    peaks: Sequence[int],
    grid_step: float,
) -> np.ndarray:
    """식사 시각(자정 기준 분)마다 끼니 번호."""

    seg = segment_bins(density, peaks)
    bins = (np.asarray(values, dtype=float) // grid_step).astype(int) % len(seg)

    return seg[bins]


def slot_ranges(density: Sequence[float], peaks: Sequence[int]) -> list[tuple[int, int]]:
    """
    끼니마다 (시작 칸, 끝 칸 + 1). 끝은 포함하지 않는다.
    원 위에서 이어진 한 구간이라 시작 > 끝 이면 자정을 넘는다.
    끼니 구간은 하루 전체를 빈틈없이 나누므로 앞 끼니의 끝 == 다음 끼니의 시작.
    """

    seg = segment_bins(density, peaks)
    n = len(seg)
    ranges = []

    for k in range(len(peaks)):
        # 앞 칸이 다른 끼니인 칸이 이 끼니의 시작
        start = next(i for i in range(n) if seg[i] == k and seg[i - 1] != k)
        end = start
        while seg[end % n] == k:
            end += 1
        ranges.append((start, end % n))

    return ranges
