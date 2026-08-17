#!/usr/bin/env python3
"""Download YouTube captions and convert them to this project's JSON format.

The output for each CSV row is written to:
    Master/<index>/Transcript/transcript-<language>.json

Only subtitle data is downloaded. Audio and Whisper are not used.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable


# Prefer the original-language track. The shorter code is a fallback because
# YouTube does not expose an ``*-orig`` track for every video.
LANGUAGE_TRACKS: dict[str, tuple[str, ...]] = {
    "english": ("en-orig", "en"),
    "en": ("en-orig", "en"),
    "chinese": ("zh-Hans", "zh-Hant", "zh"),
    "zh": ("zh-Hans", "zh-Hant", "zh"),
    "hindi": ("hi",),
    "hi": ("hi",),
    "romanian": ("ro",),
    "ro": ("ro",),
}

LANGUAGE_CODES = {
    "english": "en",
    "en": "en",
    "chinese": "zh",
    "zh": "zh",
    "hindi": "hi",
    "hi": "hi",
    "romanian": "ro",
    "ro": "ro",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download YouTube subtitle tracks from a CSV and convert them to "
            "the transcript JSON format used by this repository."
        )
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path("test_dataset.csv"),
        help="CSV with index, url, and language columns (default: test_dataset.csv).",
    )
    parser.add_argument(
        "--base",
        type=Path,
        default=Path("Master"),
        help="Root directory for transcript output (default: Master).",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        type=int,
        help="Only download these video indices.",
    )
    parser.add_argument(
        "--cookies",
        type=Path,
        help="Optional Netscape-format YouTube cookies file for restricted videos.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing transcript JSON file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the output plan without contacting YouTube.",
    )
    return parser.parse_args()


def read_rows(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    with csv_path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        fields = set(reader.fieldnames or [])
        required = {"index", "url", "language"}
        missing = required - fields
        if missing:
            raise ValueError(f"CSV is missing required columns: {', '.join(sorted(missing))}")

        rows = []
        for line_number, row in enumerate(reader, start=2):
            index = (row.get("index") or "").strip()
            url = (row.get("url") or "").strip()
            language = (row.get("language") or "").strip()
            if not index or not url or not language:
                raise ValueError(
                    f"CSV line {line_number} needs non-empty index, url, and language values."
                )
            try:
                int(index)
            except ValueError as error:
                raise ValueError(f"CSV line {line_number} has a non-integer index: {index}") from error
            rows.append({"index": index, "url": url, "language": language})

    return sorted(rows, key=lambda row: int(row["index"]))


def track_codes(language: str) -> tuple[str, ...]:
    normalized = language.strip().lower()
    return LANGUAGE_TRACKS.get(normalized, (normalized,))


def output_language_code(language: str) -> str:
    normalized = language.strip().lower()
    return LANGUAGE_CODES.get(normalized, normalized.replace(" ", "-"))


def subtitle_file(files: Iterable[Path], preferred_codes: tuple[str, ...]) -> Path | None:
    candidates = list(files)
    for code in preferred_codes:
        matches = sorted(path for path in candidates if f".{code}." in path.name)
        if matches:
            return matches[0]
    return sorted(candidates)[0] if candidates else None


def find_yt_dlp() -> str | None:
    """Locate the yt-dlp executable.

    Prefer the one beside the running interpreter: when this script is invoked as
    ``.venv/bin/python download_transcripts.py`` the venv's bin directory is not
    on PATH, so a plain shutil.which() misses the yt-dlp that was installed with
    the project's requirements.
    """
    candidate = Path(sys.executable).parent / "yt-dlp"
    if candidate.is_file():
        return str(candidate)
    return shutil.which("yt-dlp")


def download_caption_json(url: str, languages: tuple[str, ...], cookies: Path | None) -> dict[str, Any]:
    yt_dlp = find_yt_dlp()
    if not yt_dlp:
        raise RuntimeError("yt-dlp is not installed or is not available on PATH.")

    with tempfile.TemporaryDirectory(prefix="medicalqa-captions-") as temp_dir_string:
        temp_dir = Path(temp_dir_string)
        command = [
            yt_dlp,
            "--no-playlist",
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            ",".join(languages),
            "--sub-format",
            "json3",
            "--no-progress",
            "--output",
            str(temp_dir / "caption.%(ext)s"),
        ]
        if cookies:
            command.extend(["--cookies", str(cookies)])
        command.append(url)

        result = subprocess.run(command, text=True, capture_output=True)
        json3_path = subtitle_file(temp_dir.glob("caption.*.json3"), languages)
        if result.returncode != 0 or json3_path is None:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(detail or "No matching subtitle track was available.")

        with json3_path.open(encoding="utf-8") as file:
            return json.load(file)


def json3_to_segments(payload: dict[str, Any]) -> list[dict[str, float | str]]:
    """Convert YouTube's JSON3 event list to Whisper-compatible segments."""
    events = payload.get("events")
    if not isinstance(events, list):
        raise ValueError("The downloaded subtitle file does not contain a JSON3 events list.")

    raw_segments: list[tuple[float, float | None, str]] = []
    for event in events:
        if not isinstance(event, dict) or "tStartMs" not in event:
            continue
        parts = event.get("segs")
        if not isinstance(parts, list):
            continue
        text = "".join(
            part.get("utf8", "") for part in parts if isinstance(part, dict)
        )
        text = " ".join(text.replace("\n", " ").split())
        if not text:
            continue

        start = float(event["tStartMs"]) / 1000
        duration_ms = event.get("dDurationMs")
        duration = float(duration_ms) / 1000 if duration_ms is not None else None
        raw_segments.append((start, duration, text))

    segments = []
    for position, (start, duration, text) in enumerate(raw_segments):
        if duration is not None:
            end = start + duration
        elif position + 1 < len(raw_segments):
            end = raw_segments[position + 1][0]
        else:
            end = start
        segments.append({"start": start, "end": end, "text": text})

    if not segments:
        raise ValueError("The subtitle track contained no text segments.")
    return segments


def write_transcript(path: Path, segments: list[dict[str, float | str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(segments, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary_path.replace(path)


def main() -> int:
    args = parse_args()
    rows = read_rows(args.csv)
    if args.only:
        requested = set(args.only)
        rows = [row for row in rows if int(row["index"]) in requested]

    if not rows:
        print("No CSV rows matched the requested indices.", file=sys.stderr)
        return 1

    successes = skipped = failures = 0
    for row in rows:
        index = row["index"]
        language = output_language_code(row["language"])
        output_path = args.base / index / "Transcript" / f"transcript-{language}.json"

        if output_path.exists() and not args.overwrite:
            print(f"[{index}] Skipping existing {output_path}")
            skipped += 1
            continue

        print(f"[{index}] {row['url']}")
        print(f"      -> {output_path}")
        if args.dry_run:
            continue

        try:
            caption_json = download_caption_json(
                row["url"], track_codes(row["language"]), args.cookies
            )
            segments = json3_to_segments(caption_json)
            write_transcript(output_path, segments)
            print(f"      Saved {len(segments)} segments.")
            successes += 1
        except Exception as error:
            print(f"      Failed: {error}", file=sys.stderr)
            failures += 1

    print(f"Done: {successes} downloaded, {skipped} skipped, {failures} failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
