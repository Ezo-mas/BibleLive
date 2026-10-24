"""Tests for benchmarks/compare_runs.py and benchmarks/session_replay.py."""

import json

import pytest

from benchmarks.compare_runs import load_run, render, score

LUKE_3_15 = "Luko evangelija, trečias skyrius, penkioliktą eilutę."


def _entry(tp, fn, fps, cached=False, runtime_s=10.0):
    return {
        "tp": tp,
        "fn": fn,
        "fp": len(fps),
        "false_positives": [{"confidence": c} for c in fps],
        "runtime_s": runtime_s,
        "rtf": 0.1,
        "config": {"transcripts_cached": cached},
        "failures": [],
    }


def _write(path, per_recording):
    path.write_text(json.dumps({"aggregate": {"per_recording": per_recording}}), encoding="utf-8")


class TestCompareRuns:
    def test_splits_false_positives_by_confidence(self):
        s = score(_entry(5, 1, ["low", "medium", "high", "low"]))
        assert (s.tp, s.fn, s.fp_low, s.fp_med_high) == (5, 1, 2, 2)

    def test_a_tag_never_picks_up_a_longer_tag_with_the_same_start(self, tmp_path):
        _write(tmp_path / "review5span-k-rec1.json", {"rec1": _entry(3, 0, [])})
        _write(tmp_path / "review5span-kcv-rec1.json", {"rec1": _entry(9, 0, [])})
        _write(tmp_path / "review5span-k-rec2.json", {"rec2": _entry(4, 1, ["high"])})
        run = load_run(tmp_path, "k")
        assert sorted(run) == ["rec1", "rec2"]
        assert run["rec1"].tp == 3

    def test_one_file_holding_several_recordings_counts(self, tmp_path):
        _write(tmp_path / "review5span-all.json", {"a": _entry(1, 0, []), "b": _entry(2, 0, [])})
        assert sorted(load_run(tmp_path, "all")) == ["a", "b"]

    def test_render_flags_missing_recordings_and_cached_runtime(self, tmp_path):
        _write(tmp_path / "review5span-old-a.json", {"a": _entry(1, 0, ["high"])})
        _write(tmp_path / "review5span-old-b.json", {"b": _entry(2, 0, [])})
        _write(tmp_path / "review5span-new-a.json", {"a": _entry(2, 0, [], cached=True)})
        text = render({"old": load_run(tmp_path, "old"), "new": load_run(tmp_path, "new")})
        assert "old: TP 3  FN 0  FP low 0  FP med+high 1  repeats 0  runtime 20s" in text
        assert "MISSING 1 recording(s)" in text
        assert "1/1 replayed from cache, not a speed figure" in text


def _utterance(text, alternate=None, references=(), start_s=1.0):
    transcripts = [{"model": "kmynas", "text": text, "low_confidence_reason": None}]
    if alternate is not None:
        transcripts.append({"model": "canary", "text": alternate, "low_confidence_reason": None})
    return {
        "type": "utterance",
        "time": "10:00:00",
        "start_s": start_s,
        "language": "lt",
        "transcripts": transcripts,
        "references": [{"reference": r, "confidence": c} for r, c in references],
    }


@pytest.mark.audio  # session_replay imports the audio pipeline
class TestSessionReplay:
    def test_a_single_model_utterance_parses_like_live(self):
        from benchmarks.session_replay import Replayer, label

        _, refs = Replayer().feed(_utterance(LUKE_3_15))
        assert [label(r) for r in refs] == ["Luke 3:15 [high]"]

    def test_two_models_agreeing_keep_the_confidence_and_one_alone_is_low(self):
        from benchmarks.session_replay import Replayer, label

        replayer = Replayer()
        _, agreed = replayer.feed(_utterance(LUKE_3_15, alternate=LUKE_3_15))
        _, alone = replayer.feed(
            _utterance(
                "Romiečiams aštuntas skyrius, dvidešimt aštunta eilutė.",
                alternate="Labas rytas.",
                start_s=20.0,
            )
        )
        assert [label(r) for r in agreed] == ["Luke 3:15 [high]"]
        assert [label(r) for r in alone] == ["Romans 8:28 [low]"]

    def test_replay_counts_utterances_that_differ_from_what_was_sent_live(self):
        from benchmarks.session_replay import Replayer, replay

        lines = [
            json.dumps({"type": "session", "stt_engine": "kmynas"}),
            json.dumps(_utterance(LUKE_3_15, references=[("Luke 3:15", "high")])),
            json.dumps(_utterance(LUKE_3_15, references=[], start_s=900.0)),
            json.dumps(
                {"type": "utterance", "language": "lt", "transcripts": [], "references": []}
            ),
        ]
        out, compared, differ = replay(lines, Replayer(), diff_only=True)
        assert (compared, differ) == (2, 1)
        assert len(out) == 1 and "DIFF" in out[0] and "live: -" in out[0]
