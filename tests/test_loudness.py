"""Loudness normalization: measurement parsing and the download-time hook."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bilibili_podcast.bilibili import loudness
from bilibili_podcast.bilibili.loudness import (
    LoudnessMeasurement,
    TP_HEADROOM_DB,
    TARGET_LUFS,
    _parse_loudnorm_json,
    measure_loudness,
    normalize_loudness,
)

_ANALYSIS_JSON = """
[Parsed_loudnorm_0 @ 0x55] 
{
	"input_i" : "-34.67",
	"input_tp" : "-10.46",
	"input_lra" : "12.70",
	"input_thresh" : "-46.15",
	"output_i" : "-16.29",
	"output_tp" : "-4.00",
	"output_lra" : "3.90",
	"output_thresh" : "-27.25",
	"normalization_type" : "dynamic",
	"target_offset" : "1.81"
}
"""


def test_parse_loudnorm_json_reads_last_object():
    data = _parse_loudnorm_json(_ANALYSIS_JSON)
    assert data is not None
    assert data["input_i"] == "-34.67"
    assert data["target_offset"] == "1.81"


def test_parse_loudnorm_json_returns_none_without_object():
    assert _parse_loudnorm_json("no json here") is None
    assert _parse_loudnorm_json("") is None


def test_parse_loudnorm_json_returns_none_on_broken_json():
    assert _parse_loudnorm_json("{not json}") is None


def test_measurement_filter_args_contain_all_measured_values():
    measurement = LoudnessMeasurement(
        integrated=-34.67, true_peak=-10.46, lra=12.70, threshold=-46.15
    )
    args = measurement.to_filter_args()
    assert f"I={TARGET_LUFS}" in args
    assert f"TP={TP_HEADROOM_DB}" in args
    assert "measured_I=-34.67" in args
    assert "measured_TP=-10.46" in args
    assert "measured_LRA=12.7" in args
    assert "measured_thresh=-46.15" in args
    assert "linear=true" in args
    assert "offset=" not in args


def test_measurement_filter_args_include_offset_when_given():
    measurement = LoudnessMeasurement(
        integrated=-16.0, true_peak=-1.0, lra=5.0, threshold=-30.0
    )
    assert "offset=1.5" in measurement.to_filter_args(1.5)


def test_measure_loudness_returns_none_when_file_missing(monkeypatch):
    monkeypatch.setattr(loudness, "_ffprobe_duration", lambda path: 0.0)
    assert measure_loudness(Path("/nonexistent/none.m4a")) is None


def test_measure_loudness_skips_short_files(monkeypatch):
    monkeypatch.setattr(loudness, "_ffprobe_duration", lambda path: 3.0)
    assert measure_loudness(Path("short.m4a")) is None


def test_measure_loudness_parses_analysis_output(monkeypatch):
    monkeypatch.setattr(loudness, "_ffprobe_duration", lambda path: 600.0)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr=_ANALYSIS_JSON)

    monkeypatch.setattr(loudness.subprocess, "run", fake_run)
    measurement = measure_loudness(Path("episode.m4a"))
    assert measurement is not None
    assert measurement.integrated == pytest.approx(-34.67)
    assert measurement.true_peak == pytest.approx(-10.46)
    assert measurement.lra == pytest.approx(12.70)
    assert measurement.threshold == pytest.approx(-46.15)


def test_measure_loudness_returns_none_on_unparsable_output(monkeypatch):
    monkeypatch.setattr(loudness, "_ffprobe_duration", lambda path: 600.0)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="boom")

    monkeypatch.setattr(loudness.subprocess, "run", fake_run)
    assert measure_loudness(Path("episode.m4a")) is None


def test_normalize_loudness_returns_false_for_missing_file():
    assert normalize_loudness(Path("/nonexistent/none.m4a")) is False


def test_normalize_loudness_keeps_original_when_encode_fails(tmp_path, monkeypatch):
    source = tmp_path / "episode.m4a"
    source.write_bytes(b"original")
    monkeypatch.setattr(
        loudness,
        "measure_loudness",
        lambda path: LoudnessMeasurement(
            integrated=-30.0, true_peak=-10.0, lra=8.0, threshold=-40.0
        ),
    )

    def boom(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(loudness.subprocess, "run", boom)
    assert normalize_loudness(source) is False
    assert source.read_bytes() == b"original"
    # No half-written file may survive a failed encode.
    assert list(tmp_path.glob("*.loudnorm.*")) == []


def test_normalize_loudness_replaces_file_on_success(tmp_path, monkeypatch):
    source = tmp_path / "episode.m4a"
    source.write_bytes(b"original")
    monkeypatch.setattr(
        loudness,
        "measure_loudness",
        lambda path: LoudnessMeasurement(
            integrated=-30.0, true_peak=-10.0, lra=8.0, threshold=-40.0
        ),
    )

    def fake_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"normalized")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(loudness.subprocess, "run", fake_run)
    assert normalize_loudness(source) is True
    assert source.read_bytes() == b"normalized"
    assert list(tmp_path.glob("*.loudnorm.*")) == []


def test_normalize_loudness_rejects_empty_output(tmp_path, monkeypatch):
    source = tmp_path / "episode.m4a"
    source.write_bytes(b"original")
    monkeypatch.setattr(
        loudness,
        "measure_loudness",
        lambda path: LoudnessMeasurement(
            integrated=-30.0, true_peak=-10.0, lra=8.0, threshold=-40.0
        ),
    )

    def fake_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(loudness.subprocess, "run", fake_run)
    assert normalize_loudness(source) is False
    assert source.read_bytes() == b"original"
    assert list(tmp_path.glob("*.loudnorm.*")) == []


def test_normalize_hook_runs_when_enabled(tmp_path, monkeypatch):
    """The download hook calls the normalizer for a freshly fetched file."""
    from bilibili_podcast.bilibili import audio as audio_mod

    calls: list[Path] = []
    target = tmp_path / "BV1xx.m4a"
    target.write_bytes(b"audio")

    monkeypatch.setattr(
        audio_mod, "normalize_loudness", lambda path: calls.append(path) or True
    )
    monkeypatch.setattr(audio_mod._config, "NORMALIZE_LOUDNESS", True)

    assert audio_mod._normalize_if_enabled(target) == target
    assert calls == [target]


def test_normalize_hook_skips_when_disabled(tmp_path, monkeypatch):
    from bilibili_podcast.bilibili import audio as audio_mod

    calls: list[Path] = []
    target = tmp_path / "BV1xx.m4a"
    target.write_bytes(b"audio")

    monkeypatch.setattr(
        audio_mod, "normalize_loudness", lambda path: calls.append(path) or True
    )
    monkeypatch.setattr(audio_mod._config, "NORMALIZE_LOUDNESS", False)

    assert audio_mod._normalize_if_enabled(target) == target
    assert calls == []


def test_normalize_hook_survives_normalizer_failure(tmp_path, monkeypatch):
    """A failing normalizer must not break the download."""
    from bilibili_podcast.bilibili import audio as audio_mod

    target = tmp_path / "BV1xx.m4a"
    target.write_bytes(b"audio")

    def boom(path):
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr(audio_mod, "normalize_loudness", boom)
    monkeypatch.setattr(audio_mod._config, "NORMALIZE_LOUDNESS", True)

    assert audio_mod._normalize_if_enabled(target) == target


def test_download_audio_local_normalizes_before_returning(tmp_path, monkeypatch):
    """A file downloaded through yt-dlp comes back loudness-normalized."""
    from bilibili_podcast.bilibili import audio as audio_mod

    seen: list[Path] = []
    monkeypatch.setattr(
        audio_mod,
        "normalize_loudness",
        lambda path: seen.append(path) or True,
    )
    monkeypatch.setattr(audio_mod._config, "NORMALIZE_LOUDNESS", True)

    class FakeYdl:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            # yt-dlp writes the extracted audio next to the output template.
            outtmpl = Path(self.opts["outtmpl"])
            outtmpl.with_suffix(".m4a").write_bytes(b"audio")

    monkeypatch.setattr(audio_mod.yt_dlp, "YoutubeDL", FakeYdl)
    monkeypatch.setattr(audio_mod, "materialize_cookie_file", lambda: (None, False))

    result = audio_mod._download_audio_local(object(), "BV1xx", tmp_path)
    assert result == tmp_path / "BV1xx.m4a"
    assert seen == [result]

