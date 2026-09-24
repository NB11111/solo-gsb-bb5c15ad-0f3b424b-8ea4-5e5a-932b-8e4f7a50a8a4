"""发言时长统计引擎（speaking time）。

依据当前转写片段的**半开时间区间** ``[start, end)``，统计每位说话人的：

1. **独讲时长**——该区间内只有该说话人一人的片段覆盖；
2. **同讲时长**——该区间内有两位及以上说话人同时发言；有几位说话人
   重叠，就给**每人各计一次**该区间的时长；
3. **全体同时发言区间**——某一时刻全部（已识别）说话人都在发言的区间。

算法分两步，保证片段重叠不会被重复累加：

1. 先按说话人把各自的片段区间做**并集合并**（重叠或端点相接都合并），
   于是同一位说话人的任意时刻至多被覆盖一次；
2. 取全部合并后区间的起止时刻作为切分点，逐段统计该半开小区间内活动的
   说话人集合：集合大小为 1 时计入该说话人的独讲时长，大于 1 时给集合中的
   每位说话人各计一次同讲时长。

忽略**空说话人**（未填写或仅空白）与**零长度片段**（``end <= start``，
含非法时间），时间一律按**毫秒整数**计算，输出时换算为秒（保留三位小数）。
"""
from __future__ import annotations

from typing import Any

EPS = 1e-6


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:  # NaN
        return None
    return out


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Union of half-open intervals (overlapping or touching merge into one)."""
    if not intervals:
        return []
    intervals = sorted(intervals)
    merged: list[list[int]] = [list(intervals[0])]
    for start, end in intervals[1:]:
        if start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1][1] = end
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def speaking_stats(document: dict) -> dict:
    """Compute per-speaker solo / overlap speaking time.

    Returns ``{"speakers", "overlapIntervals", "allIntervals", "summary"}``.
    Times in the response are seconds (millisecond integer counts are kept
    under the ``*Ms`` keys). ``overlapIntervals`` only lists slices with two
    or more active speakers; ``all`` (and ``allIntervals``) marks those where
    every known speaker is active.
    """
    # speaker name -> merged millisecond intervals; insertion order keeps the
    # speakers in the order of their first segment appearance.
    per_speaker: dict[str, list[tuple[int, int]]] = {}
    for raw in (document or {}).get("segments", []):
        if not isinstance(raw, dict):
            continue
        speaker = str(raw.get("speaker") or "").strip()
        if not speaker:
            continue
        start, end = _num(raw.get("start")), _num(raw.get("end"))
        if start is None or end is None or end < start:
            continue
        start_ms, end_ms = round(start * 1000 + EPS), round(end * 1000 + EPS)
        if end_ms <= start_ms:
            # zero-length (also after millisecond rounding) is ignored
            continue
        per_speaker.setdefault(speaker, []).append((start_ms, end_ms))

    intervals_by_speaker = {
        speaker: _merge_intervals(intervals)
        for speaker, intervals in per_speaker.items()
    }

    # Sweep events: +1 speaker at start, -1 at end (half-open intervals, so
    # end events take effect before the following slice).
    starts: dict[int, list[str]] = {}
    ends: dict[int, list[str]] = {}
    boundaries: set[int] = set()
    for speaker, intervals in intervals_by_speaker.items():
        for s, e in intervals:
            starts.setdefault(s, []).append(speaker)
            ends.setdefault(e, []).append(speaker)
            boundaries.add(s)
            boundaries.add(e)

    active: set[str] = set()
    solo_ms: dict[str, int] = {sp: 0 for sp in intervals_by_speaker}
    overlap_ms: dict[str, int] = {sp: 0 for sp in intervals_by_speaker}
    raw_overlaps: list[dict] = []
    prev_t: int | None = None

    for t in sorted(boundaries):
        if prev_t is not None and t > prev_t and active:
            length = t - prev_t
            if len(active) == 1:
                solo_ms[next(iter(active))] += length
            else:
                for sp in active:
                    overlap_ms[sp] += length
                raw_overlaps.append({
                    "startMs": prev_t, "endMs": t,
                    "speakers": sorted(active),
                })
        for sp in ends.get(t, ()):
            active.discard(sp)
        for sp in starts.get(t, ()):
            active.add(sp)
        prev_t = t

    total_speakers = len(intervals_by_speaker)

    def _sec(ms: int) -> float:
        return round(ms / 1000, 3)

    speakers_out = []
    for speaker, intervals in intervals_by_speaker.items():
        spoken = sum(e - s for s, e in intervals)
        speakers_out.append({
            "speaker": speaker,
            "soloMs": solo_ms[speaker],
            "overlapMs": overlap_ms[speaker],
            "spokenMs": spoken,
            "solo": _sec(solo_ms[speaker]),
            "overlap": _sec(overlap_ms[speaker]),
            "spoken": _sec(spoken),
        })

    # Coalesce adjacent slices that share the exact same active-speaker set so
    # one contiguous overlap region surfaces as a single interval.
    overlap_intervals = []
    for ov in raw_overlaps:
        if overlap_intervals and overlap_intervals[-1]["speakers"] == ov["speakers"] \
                and overlap_intervals[-1]["endMs"] == ov["startMs"]:
            overlap_intervals[-1]["endMs"] = ov["endMs"]
        else:
            overlap_intervals.append(dict(ov))
    for ov in overlap_intervals:
        ov["start"] = _sec(ov["startMs"])
        ov["end"] = _sec(ov["endMs"])
        ov["durationMs"] = ov["endMs"] - ov["startMs"]
        ov["duration"] = _sec(ov["durationMs"])
        ov["speakerCount"] = len(ov["speakers"])
        ov["all"] = ov["speakerCount"] == total_speakers and total_speakers > 0

    all_intervals = [
        {"start": ov["start"], "end": ov["end"],
         "duration": ov["duration"], "durationMs": ov["durationMs"],
         "speakers": ov["speakers"]}
        for ov in overlap_intervals if ov["all"]
    ]

    total_solo = sum(solo_ms.values())
    total_overlap = sum(
        ov["durationMs"] for ov in overlap_intervals)  # wall-clock overlap
    total_overlap_charged = sum(overlap_ms.values())  # charged per speaker
    return {
        "speakers": speakers_out,
        "overlapIntervals": overlap_intervals,
        "allIntervals": all_intervals,
        "summary": {
            "speakers": total_speakers,
            "overlapIntervals": len(overlap_intervals),
            "allIntervals": len(all_intervals),
            "soloMs": total_solo,
            "overlapMs": total_overlap,
            "overlapChargedMs": total_overlap_charged,
            "solo": _sec(total_solo),
            "overlap": _sec(total_overlap),
            "overlapCharged": _sec(total_overlap_charged),
        },
    }
