# This should be the main file to run for generating outputs
# One command to build folders per video and run the pipelines.

import csv, json
import re
from datetime import datetime
import shutil
import subprocess
from pathlib import Path
import tempfile
from typing import Dict, List, Optional
import argparse
import shlex
import os
import sys

# ----------------------- Editable configuration ---------------------------

# Folder names:
APPROACH_FOLDERS = {
    1: "SingleAgent",
    2: "DualAgent",
    3: "MultiAgent-LLMChunking",
    4: "RAG",
}


# Map approaches → Python entrypoints.
# Each script should accept --input and --output.
APPROACH_SCRIPTS = {
    1: "SingleQA/main.py",
    2: "LLMChunking/main.py",
    3: "MultiAgentChunking/main.py",
    4: "RAG/main.py",
}

# Pre-processing script
PREPROC_SCRIPT = "preprocess.py"

# --------------------------------- Helpers ---------------------------------


def safe_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def build_video_root(base: Path, idx: int, lang: Optional[str] = None) -> Path:
    """
    Create the root folder for a single video:
      Master/
        1/
          Audio/
          Transcript/
          Approach 1 (...)/
            QA result file
            intermediate
          ...
    """
    # Keep folder names simple and stable; numbers are enough.
    root = base / f"{idx}"
    safe_mkdir(root)

    # Always-present leaves:
    audio_dir = root / "Audio"
    transcript_dir = root / "Transcript"
    safe_mkdir(audio_dir)
    safe_mkdir(transcript_dir)

    # Approaches + subfolders
    for k, name in APPROACH_FOLDERS.items():
        aroot = root / name
        safe_mkdir(aroot)

    return root


def run_preprocessing(
    url: str, audio_dir: Path, transcript_dir: Path, dry_run: bool, lang: str
) -> None:
    # audio_dir will be the folder containing the audio file. ie. Master/1/Audio
    # preprocess.py will store the audio.wav file to this folder.

    cmd = [
        # sys.executable, not "python": the children must run in the same
        # interpreter (and venv) as run.py, otherwise a bare "python" resolves
        # to whatever is first on PATH and misses every installed dependency.
        sys.executable,
        PREPROC_SCRIPT,
        "--url",
        url,
        "--audio_out",
        str(audio_dir),
        "--transcript_out",
        str(transcript_dir),
        "--lang",
        lang,
    ]
    print("⏳ Pre-processing:", shlex.join(cmd))
    if not dry_run:
        subprocess.run(cmd, check=True)


def find_existing_transcript(transcript_dir: Path) -> Optional[Path]:
    """Return a repository-format transcript JSON file, if one is available."""
    transcripts = sorted(transcript_dir.glob("transcript*.json"))
    return transcripts[0] if transcripts else None


def run_approach(
    idx: int,
    approach_id: int,
    video_root: Path,
    enable: bool,
    dry_run: bool,
    base: Path,
    extra_args: Optional[List[str]] = None,
) -> float:
    """
    Approaches will return how much time does it take to complete its running.
    """
    if not enable:
        return 0.0
    script = APPROACH_SCRIPTS.get(approach_id)
    if not script:
        print(f"Warning: No script configured for approach {approach_id}; skipping.")
        return 0.0

    approach_name = APPROACH_FOLDERS[approach_id]
    approach_root = video_root / approach_name
    qa_dir = approach_root / "QA results"
    inter_dir = approach_root / "intermediate"

    qa_dir.mkdir(parents=True, exist_ok=True)
    inter_dir.mkdir(parents=True, exist_ok=True)

    # Define standardized IO for the scripts:
    qa_out = Path("finalQA.json")
    inter_out = Path("Intermediate.json")
    chunk_out = Path("chunks.json")  # this is the segment chunk
    debug_chunk_out = Path("debug_chunk.json")

    cmd = [
        sys.executable,  # see note in run_preprocessing
        script,
        "--id",
        str(idx),  # this is the index of the video
        # Absolute, so the child looks in the same category folder regardless of
        # the working directory it happens to be launched from.
        "--base",
        str(base.resolve()),
    ]
    if extra_args:
        cmd += extra_args

    print(f"\n▶️  Approach {approach_id}:", shlex.join(cmd))
    if dry_run:
        return 0.0

    proc = subprocess.run(cmd, capture_output=True, text=True)
    # Show the child's log (optional, but helpful)
    if proc.stdout:
        print(proc.stdout.rstrip())

    # Extract the last {"agent_seconds": ...} from the log
    agent_seconds = 0.0
    agent_re = re.compile(r'\{"agent_seconds"\s*:\s*([0-9]+(?:\.[0-9]+)?)\}')
    if proc.stdout:
        matches = agent_re.findall(proc.stdout)
        if matches:
            try:
                agent_seconds = float(matches[-1])  # last occurrence wins
            except ValueError:
                pass
    if agent_seconds == 0.0:
        print("⚠️  Could not extract agent_seconds from approach output.")

    # Move final QA, Intermediate and segment chunks
    move_if_exists(qa_out, qa_dir)
    move_if_exists(inter_out, inter_dir)
    move_if_exists(chunk_out, inter_dir)
    move_if_exists(debug_chunk_out, inter_dir)

    return agent_seconds


def move_if_exists(src: Path, dst_dir: Path):
    """Move all files matching a pattern into dst_dir."""
    if src.exists():
        dst_dir.mkdir(parents=True, exist_ok=True)
        dest = dst_dir / src.name
        shutil.move(str(src), str(dest))
        print(f"✅ Moved {src} → {dest}")
    else:
        print(f"ℹ️ Skipping: {src} not found")


def writeback_times_canonical(csv_path: Path, updates: dict, app_ids: list[int]):
    """
    updates: { idx: {approach_id: seconds_float, ...}, ... }
    app_ids: e.g., [1,2,3,4] to build columns: approach1..approach4
    """
    # 1) Read all existing rows
    with open(csv_path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        rows = list(r)
        fieldnames = list(r.fieldnames or [])
    # 2) Ensure columns exist
    needed = ["index", "url", "language"] + [f"approach{aid}" for aid in app_ids]
    for col in needed:
        if col not in fieldnames:
            fieldnames.append(col)

    # 2.5) Build an index → row map for quick updates
    by_idx = {}
    for row in rows:
        try:
            i = int(str(row.get("index", "")).strip())
            by_idx[i] = row
        except ValueError:
            continue

    # Create missing rows for any idx in updates
    for i in updates.keys():
        if i not in by_idx:
            new_row = {k: "" for k in fieldnames}
            new_row["index"] = i
            by_idx[i] = new_row
            rows.append(new_row)

    # 3) Apply updates
    for i, per_app in updates.items():
        row = by_idx.get(i)
        if not row:
            continue
        for aid, secs in per_app.items():
            row[f"approach{aid}"] = f"{secs:.3f}"

    # 4) Atomic rewrite
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(csv_path.parent), prefix=csv_path.name, suffix=".tmp"
    )
    try:
        with open(tmp_fd, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, csv_path)  # atomic on same filesystem
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def read_videos_csv(csv_path: Path) -> List[Dict[str, str]]:
    """
    Expect columns: index,url,language (title optional).
    If 'index' is missing, auto-number from 1.
    """
    rows = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        autoinc = 1
        for row in reader:
            idx = row.get("index")
            if not idx:
                idx = str(autoinc)
                autoinc += 1
            rows.append(
                {
                    "index": int(idx),
                    "url": row["url"],
                    "language": row.get("language", "").strip(),
                }
            )
    rows.sort(key=lambda r: r["index"])
    return rows


# -------------------------------- CLI -------------------------------------


def main():
    p = argparse.ArgumentParser(
        description="Create per-video folders and run pipelines."
    )
    p.add_argument(
        "--base", default="Master", help="Base output folder (default: Master)"
    )
    p.add_argument("--v", required=True, help="CSV of videos: index,url,title]")
    p.add_argument(
        "--only",
        nargs="*",
        type=int,
        default=None,
        help="Process only these video indices (e.g., --only 1 2 3)",
    )
    p.add_argument(
        "--skip_app",
        nargs="*",
        type=int,
        default=None,
        help="Skip this approach. Default none.",
    )
    p.add_argument(
        "--app",
        nargs="*",
        type=int,
        default=[1, 2, 3, 4],
        help="Run these approachs (default: 1 2 3 4)",
    )
    p.add_argument(
        "--start", type=int, default=None, help="Start from this video index"
    )
    p.add_argument(
        "--preprocess",
        action="store_true",
        help=(
            "Download audio and run Whisper before the QA approaches. By default, "
            "existing files in Master/<index>/Transcript/ are used directly."
        ),
    )
    p.add_argument(
        "--dry-run", action="store_true", help="Print commands, don’t execute"
    )
    args, unknown = p.parse_known_args()

    base = Path(args.base)
    safe_mkdir(base)

    videos = read_videos_csv(Path(args.v))
    # --- Filter: if --only is provided, process only those video indices ---
    if args.only:
        wanted = set(args.only)
        videos = [row for row in videos if row["index"] in wanted]
    # --- Filter: if --start is provided, skip videos before that index ---
    if args.start is not None:
        videos = [row for row in videos if row["index"] >= args.start]
    # --- Filter approaches ---
    skip = set(args.skip_app or [])
    to_run = [aid for aid in args.app if aid not in skip]

    updates_for_idx = {}
    try:
        for v in videos:
            idx, url, lang = v["index"], v["url"], v["language"]
            print(f"\n===== Video {idx} Language: {lang} =====")
            root = build_video_root(base, idx, lang)

            transcript_dir = root / "Transcript"
            if args.preprocess:
                # Explicit opt-in: download audio and create a Whisper transcript.
                run_preprocessing(
                    url=url,
                    audio_dir=root / "Audio",
                    transcript_dir=transcript_dir,
                    lang=lang,
                    dry_run=args.dry_run,
                )
            else:
                transcript_file = find_existing_transcript(transcript_dir)
                if transcript_file is None:
                    print(
                        "⚠️ No transcript found. Run download_transcripts.py first, "
                        "or pass --preprocess to download audio and run Whisper."
                    )
                    continue
                print(f"📄 Using existing transcript: {transcript_file}")

            # Step 2: each approach for this video
            # process the id to each main so that they can know the file
            for aid in to_run:
                try:
                    elapsed = run_approach(
                        idx=idx,
                        approach_id=aid,
                        video_root=root,
                        enable=True,
                        dry_run=args.dry_run,
                        base=base,
                        extra_args=unknown,  # pass through any extra flags to your scripts
                    )
                    # A dry run must not touch the input CSV; otherwise merely
                    # previewing a run stamps approach{n}=0.000 into it.
                    if args.dry_run:
                        continue
                    updates_for_idx.setdefault(idx, {})[aid] = elapsed
                    writeback_times_canonical(Path(args.v), updates_for_idx, to_run)
                    updates_for_idx.clear()  # reset the per-video buffer
                    print(f"💾 wrote approach{aid} time for video {idx}")

                except Exception as e:
                    print(f"Video {idx} with approach {aid} failed! Skipping.")
                    continue
    except Exception as e:
        print("⚠️ Interrupted. Writing any accumulated times to CSV...")
        if updates_for_idx:
            writeback_times_canonical(Path(args.v), updates_for_idx, to_run)
            print("✅ Partial times saved.")
        raise

    print("\n😆 Yes! All videos have been processed.")


if __name__ == "__main__":
    main()
