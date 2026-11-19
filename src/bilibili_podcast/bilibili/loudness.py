"""Loudness normalization for downloaded audio.

Podcast clients play a stream at a fixed digital level, so an episode whose
integrated loudness sits below the reference (−16 LUFS for Apple Podcasts)
sounds quiet and forces the listener to raise the volume on every device.
This module normalizes a freshly downloaded file to that reference with
ffmpeg's ``loudnorm`` filter.

Two measured facts drove the design (see ``docs/FUNCTIONAL.md``):

* **Two-pass is required.** A single dynamic ``loudnorm`` pass cannot hit a
  target for quiet material — it falls back to dynamic normalization and
  lands several LU short. Pass one measures the file, pass two applies the
  measured values so the gain is constant.
* **AAC re-encoding overshoots the requested true peak.** loudnorm limits its
  PCM output to the requested ``TP``, but the AAC encoder pushes the peak up
  again — measured +2.6 dB on a −34.7 LUFS source. A trailing ``alimiter``
  does *not* help, because the overshoot happens after it. The only reliable
  fix is to ask loudnorm for a true-peak target low enough that the encoded
  file still lands under the ceiling. ``TP_HEADROOM_DB`` reserves that margin.

Normalization is applied at download time only: existing episodes keep the
loudness they were published with.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from bilibili_podcast.logger import get_logger

logger = get_logger()

#: Integrated loudness target in LUFS. Apple Podcasts recommends −16 LUFS for
#: stereo spoken-word content.
TARGET_LUFS = -16.0

#: Loudness range target in LU. ``loudnorm`` refuses to exceed 50 and uses
#: 11 for speech, which keeps the limiter from pumping on dialogue.
TARGET_LRA = 11.0

#: True-peak target handed to loudnorm. The AAC encoder adds roughly 2.6 dB
#: on top of this, so the value is deliberately below the −1.5 dBTP ceiling we
#: actually want in the delivered file. Measured on a −34.7 LUFS source:
#: TP=−1.5 → +1.1 dBFS delivered, TP=−3.0 → −0.4 dBFS, TP=−4.5 → −1.7 dBFS.
#: −3.0 is the setting that actually lands on the loudness target: −4.0 was
#: measured to deliver only −18.2 LUFS because loudnorm stops short of the
#: requested gain when the peak headroom gets tight.
TP_HEADROOM_DB = -3.0

#: Audio bitrate for the re-encoded file. The source streams are AAC-LC at
#: 64 kbps; re-encoding at the same rate keeps the size roughly unchanged
#: (measured +1.1%) while still sounding clean at the target loudness.
TARGET_BITRATE = "64k"

#: Sample rate and channel layout kept from the source.
TARGET_SAMPLE_RATE = 48000
TARGET_CHANNELS = 2

#: Skip files shorter than this: loudness measurement on a few seconds of
#: audio is noise, and the overhead is not worth it.
MIN_DURATION_SECONDS = 10.0


@dataclass(frozen=True)
class LoudnessMeasurement:
    """Measured loudness of a file, as reported by ``loudnorm``."""

    integrated: float
    true_peak: float
    lra: float
    threshold: float

    def to_filter_args(self, target_offset: float = 0.0) -> str:
        """Render the second-pass ``loudnorm`` filter string."""
        parts = [
            f"I={TARGET_LUFS}",
            f"TP={TP_HEADROOM_DB}",
            f"LRA={TARGET_LRA}",
            f"measured_I={self.integrated}",
            f"measured_TP={self.true_peak}",
            f"measured_LRA={self.lra}",
            f"measured_thresh={self.threshold}",
        ]
        if target_offset:
            parts.append(f"offset={target_offset}")
        # ``linear=true`` applies a single constant gain when the measured
        # range allows it; loudnorm falls back to dynamic mode otherwise.
        parts.append("linear=true")
        return ":".join(parts)


def _ffprobe_duration(path: Path) -> float:
    """Return the media duration in seconds; 0.0 when unknown."""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "csv=p=0", str(path),
            ],
            capture_output=True, text=True, timeout=60, check=True,
        )
        return float(out.stdout.strip() or 0)
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0.0


def measure_loudness(path: Path) -> LoudnessMeasurement | None:
    """Measure ``path`` with loudnorm's analysis pass.

    Returns ``None`` when ffmpeg is unavailable, the file is too short, or the
    analysis output cannot be parsed — the caller then leaves the file
    untouched rather than risking a broken encode.
    """
    duration = _ffprobe_duration(path)
    if 0 < duration < MIN_DURATION_SECONDS:
        logger.info(f"===> {path.name}: too short for loudness normalization")
        return None

    cmd = [
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", str(path),
        "-af", f"loudnorm=I={TARGET_LUFS}:TP={TP_HEADROOM_DB}:LRA={TARGET_LRA}:print_format=json",
        "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"===> {path.name}: loudness analysis failed: {exc}")
        return None

    payload = _parse_loudnorm_json(proc.stderr)
    if payload is None:
        logger.warning(f"===> {path.name}: cannot parse loudnorm analysis output")
        return None

    try:
        measurement = LoudnessMeasurement(
            integrated=float(payload["input_i"]),
            true_peak=float(payload["input_tp"]),
            lra=float(payload["input_lra"]),
            threshold=float(payload["input_thresh"]),
        )
    except (KeyError, TypeError, ValueError):
        logger.warning(f"===> {path.name}: incomplete loudnorm analysis output")
        return None

    offset = float(payload.get("target_offset") or 0.0)
    logger.info(
        f"===> {path.name}: measured {measurement.integrated:.1f} LUFS "
        f"(peak {measurement.true_peak:.1f} dBTP, offset {offset:.2f})"
    )
    return measurement


def _parse_loudnorm_json(text: str) -> dict | None:
    """Pull the last JSON object out of loudnorm's analysis output."""
    start = text.rfind("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def normalize_loudness(path: Path) -> bool:
    """Normalize ``path`` in place to the target loudness.

    Returns ``True`` when the file was rewritten, ``False`` when it was left
    untouched (analysis failed, ffmpeg missing, or the encode failed). The
    original file is only replaced after the new one is fully written, so a
    failure never leaves a truncated audio file behind.
    """
    if not path.is_file():
        return False

    measurement = measure_loudness(path)
    if measurement is None:
        return False

    tmp = path.with_suffix(".loudnorm" + path.suffix)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(path),
        "-af", f"loudnorm={measurement.to_filter_args()}",
        "-c:a", "aac", "-b:a", TARGET_BITRATE,
        "-ar", str(TARGET_SAMPLE_RATE), "-ac", str(TARGET_CHANNELS),
        str(tmp),
    ]
    try:
        subprocess.run(cmd, check=True, timeout=3600)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"===> {path.name}: loudness normalization failed: {exc}")
        tmp.unlink(missing_ok=True)
        return False

    if not tmp.is_file() or tmp.stat().st_size == 0:
        logger.warning(f"===> {path.name}: loudness normalization produced no output")
        tmp.unlink(missing_ok=True)
        return False

    tmp.replace(path)
    logger.info(f"===> {path.name}: normalized to {TARGET_LUFS} LUFS")
    return True
