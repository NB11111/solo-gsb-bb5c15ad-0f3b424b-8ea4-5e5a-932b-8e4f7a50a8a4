"""Engine + end-to-end API tests for the speaking-time panel (发言时长).

Run with: python3 test_speaking.py  (or via pytest).
"""
import os
import shutil
import tempfile

os.environ.setdefault("STATIC_DIR", "/nonexistent")
_TMP = tempfile.mkdtemp(prefix="speaking-api-")
os.environ["DATA_DIR"] = _TMP

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.speaking import speaking_stats  # noqa: E402


def _doc(segments):
    return {"segments": segments, "annotations": []}


def _by_speaker(result):
    return {s["speaker"]: s for s in result["speakers"]}


def test_basic_solo_and_overlap():
    # A: [0,10), B: [5,15)  ->  A solo 0-5, overlap 5-10, B solo 10-15
    doc = _doc([
        {"id": "s1", "start": 0, "end": 10, "speaker": "A", "text": ""},
        {"id": "s2", "start": 5, "end": 15, "speaker": "B", "text": ""},
    ])
    res = speaking_stats(doc)
    a, b = _by_speaker(res)["A"], _by_speaker(res)["B"]
    assert (a["soloMs"], a["overlapMs"], a["spokenMs"]) == (5000, 5000, 10000)
    assert (b["soloMs"], b["overlapMs"], b["spokenMs"]) == (5000, 5000, 10000)
    assert a["solo"] == 5.0 and a["overlap"] == 5.0 and a["spoken"] == 10.0
    assert len(res["overlapIntervals"]) == 1
    ov = res["overlapIntervals"][0]
    assert (ov["start"], ov["end"], ov["duration"]) == (5.0, 10.0, 5.0)
    assert ov["speakers"] == ["A", "B"] and ov["speakerCount"] == 2
    assert ov["all"] is True
    assert res["allIntervals"] == [{
        "start": 5.0, "end": 10.0, "duration": 5.0,
        "durationMs": 5000, "speakers": ["A", "B"]}]
    # wall-clock overlap is 5s, charged twice it is 10s
    assert res["summary"]["overlapMs"] == 5000
    assert res["summary"]["overlapChargedMs"] == 10000
    assert res["summary"]["soloMs"] == 10000
    assert res["summary"]["speakers"] == 2


def test_merge_same_speaker_overlaps_first():
    # A speaks [0,10) twice (overlapping segments) and B [5,15):
    # without merging per speaker, A would be double-charged on 5-10.
    doc = _doc([
        {"id": "s1", "start": 0, "end": 8, "speaker": "A"},
        {"id": "s2", "start": 2, "end": 10, "speaker": "A"},
        {"id": "s3", "start": 5, "end": 15, "speaker": "B"},
    ])
    res = speaking_stats(doc)
    a = _by_speaker(res)["A"]
    # merged A = [0,10): solo 0-5, overlap 5-10 -> spoken 10s, not 16s
    assert (a["soloMs"], a["overlapMs"], a["spokenMs"]) == (5000, 5000, 10000)
    assert len(res["overlapIntervals"]) == 1
    assert (res["overlapIntervals"][0]["startMs"],
            res["overlapIntervals"][0]["endMs"]) == (5000, 10000)


def test_three_speakers_all_and_subset():
    # A [0,20), B [5,20), C [10,15):
    #   0-5 A solo; 5-10 A+B overlap; 10-15 all three; 15-20 A+B
    doc = _doc([
        {"id": "a", "start": 0, "end": 20, "speaker": "A"},
        {"id": "b", "start": 5, "end": 20, "speaker": "B"},
        {"id": "c", "start": 10, "end": 15, "speaker": "C"},
    ])
    res = speaking_stats(doc)
    sp = _by_speaker(res)
    assert (sp["A"]["soloMs"], sp["A"]["overlapMs"]) == (5000, 15000)
    assert (sp["B"]["soloMs"], sp["B"]["overlapMs"]) == (0, 15000)
    assert (sp["C"]["soloMs"], sp["C"]["overlapMs"]) == (0, 5000)
    # A+B slices (5-10 and 15-20) are separated by the all-three slice,
    # so they stay as two distinct intervals; all-three is one interval.
    intervals = [(o["start"], o["end"], o["speakerCount"])
                 for o in res["overlapIntervals"]]
    assert intervals == [(5.0, 10.0, 2), (10.0, 15.0, 3), (15.0, 20.0, 2)]
    assert [o["all"] for o in res["overlapIntervals"]] == [False, True, False]
    assert len(res["allIntervals"]) == 1
    assert (res["allIntervals"][0]["start"],
            res["allIntervals"][0]["end"]) == (10.0, 15.0)
    # overlap wall time = 15s; per-speaker charge = 5+5 + 3*5 + 5+5 = 35s
    assert res["summary"]["overlapMs"] == 15000
    assert res["summary"]["overlapChargedMs"] == 35000


def test_coalesce_contiguous_same_speaker_set():
    # A long segment [0,20); B = [5,8) and [8,12) touching -> one B region;
    # after per-speaker merge B is [5,12), so overlap is one interval.
    doc = _doc([
        {"id": "a", "start": 0, "end": 20, "speaker": "A"},
        {"id": "b1", "start": 5, "end": 8, "speaker": "B"},
        {"id": "b2", "start": 8, "end": 12, "speaker": "B"},
    ])
    res = speaking_stats(doc)
    assert len(res["overlapIntervals"]) == 1
    assert (res["overlapIntervals"][0]["start"],
            res["overlapIntervals"][0]["end"]) == (5.0, 12.0)


def test_touching_not_overlap():
    # A [0,5), B [5,10): half-open intervals only touch -> both solo.
    doc = _doc([
        {"id": "a", "start": 0, "end": 5, "speaker": "A"},
        {"id": "b", "start": 5, "end": 10, "speaker": "B"},
    ])
    res = speaking_stats(doc)
    sp = _by_speaker(res)
    assert sp["A"]["soloMs"] == 5000 and sp["A"]["overlapMs"] == 0
    assert sp["B"]["soloMs"] == 5000 and sp["B"]["overlapMs"] == 0
    assert res["overlapIntervals"] == []
    assert res["allIntervals"] == []


def test_ignore_empty_speaker_and_zero_length():
    doc = _doc([
        {"id": "s0", "start": 0, "end": 10, "speaker": ""},
        {"id": "s0b", "start": 0, "end": 10, "speaker": "   "},
        {"id": "s1", "start": 0, "end": 0, "speaker": "A"},
        {"id": "s2", "start": 5, "end": 5, "speaker": "A"},
        {"id": "s3", "start": 8, "end": 4, "speaker": "A"},
        {"id": "s4", "start": 1, "end": 4, "speaker": "A"},
        {"id": "s5", "start": None, "end": 4, "speaker": "A"},
    ])
    res = speaking_stats(doc)
    assert [s["speaker"] for s in res["speakers"]] == ["A"]
    a = _by_speaker(res)["A"]
    assert (a["soloMs"], a["overlapMs"], a["spokenMs"]) == (3000, 0, 3000)
    assert res["summary"]["solo"] == 3.0
    assert res["summary"]["speakers"] == 1


def test_empty_document():
    res = speaking_stats(_doc([]))
    assert res["speakers"] == []
    assert res["overlapIntervals"] == []
    assert res["allIntervals"] == []
    assert res["summary"]["speakers"] == 0
    assert res["summary"]["soloMs"] == 0


def test_millisecond_rounding():
    # 0.00049s segments round to zero milliseconds and are ignored;
    # fractional results are returned as seconds with 3 decimals.
    doc = _doc([
        {"id": "a", "start": 0.0, "end": 1.2345, "speaker": "A"},
        {"id": "b", "start": 1.0, "end": 2.0, "speaker": "B"},
    ])
    res = speaking_stats(doc)
    sp = _by_speaker(res)
    # A rounds to [0,1235)ms, B [1000,2000)ms
    assert sp["A"]["soloMs"] == 1000
    assert sp["A"]["overlapMs"] == 235
    assert sp["A"]["spoken"] == 1.235
    ov = res["overlapIntervals"][0]
    assert (ov["startMs"], ov["endMs"], ov["durationMs"]) == (1000, 1235, 235)
    assert (ov["start"], ov["end"], ov["duration"]) == (1.0, 1.235, 0.235)


def test_speaker_order_is_first_appearance():
    doc = _doc([
        {"id": "1", "start": 0, "end": 1, "speaker": "张"},
        {"id": "2", "start": 1, "end": 2, "speaker": "李"},
        {"id": "3", "start": 2, "end": 3, "speaker": "王"},
    ])
    assert [s["speaker"] for s in speaking_stats(doc)["speakers"]] == \
        ["张", "李", "王"]


def test_single_speaker_is_never_all_overlap():
    doc = _doc([
        {"id": "a1", "start": 0, "end": 5, "speaker": "A"},
        {"id": "a2", "start": 4, "end": 9, "speaker": "A"},
    ])
    res = speaking_stats(doc)
    # merged into [0,9): solo only
    assert _by_speaker(res)["A"]["spokenMs"] == 9000
    assert res["overlapIntervals"] == []
    assert res["allIntervals"] == []


def test_api():
    with TestClient(app) as c:
        assert c.get("/api/health").json()["status"] == "ok"
        p = c.post("/api/projects", json={"name": "发言时长访谈"}).json()
        pid = p["id"]
        saved = _doc([
            {"id": "s1", "start": 0, "end": 10, "speaker": "A"},
            {"id": "s2", "start": 5, "end": 15, "speaker": "B"},
        ])
        c.put(f"/api/projects/{pid}",
              json={"name": "发言时长访谈", "document": saved})

        # analyze the saved document
        r = c.post(f"/api/projects/{pid}/speaking-time", json={})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["projectId"] == pid
        assert data["projectName"] == "发言时长访谈"
        assert data["summary"]["soloMs"] == 10000
        assert len(data["overlapIntervals"]) == 1
        assert data["overlapIntervals"][0]["start"] == 5.0

        # body may be omitted entirely
        assert c.post(f"/api/projects/{pid}/speaking-time").status_code == 200

        # request document overrides the saved one (unsaved editor state)
        dirty = _doc([
            {"id": "n1", "start": 0, "end": 2, "speaker": "受访者"},
            {"id": "n2", "start": 1, "end": 3, "speaker": "访谈者"},
        ])
        r = c.post(f"/api/projects/{pid}/speaking-time",
                   json={"document": dirty})
        assert r.status_code == 200, r.text
        data = r.json()
        assert {s["speaker"] for s in data["speakers"]} == {"受访者", "访谈者"}
        assert data["overlapIntervals"][0]["start"] == 1.0
        assert data["overlapIntervals"][0]["end"] == 2.0

        # unknown project -> 404
        assert c.post("/api/projects/nope/speaking-time",
                      json={}).status_code == 404

        print("ALL SPEAKING TIME TESTS PASSED")


def run():
    test_basic_solo_and_overlap()
    test_merge_same_speaker_overlaps_first()
    test_three_speakers_all_and_subset()
    test_coalesce_contiguous_same_speaker_set()
    test_touching_not_overlap()
    test_ignore_empty_speaker_and_zero_length()
    test_empty_document()
    test_millisecond_rounding()
    test_speaker_order_is_first_appearance()
    test_single_speaker_is_never_all_overlap()
    test_api()


if __name__ == "__main__":
    run()
    shutil.rmtree(_TMP, ignore_errors=True)
