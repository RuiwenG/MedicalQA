"""Remove Whisper's repetition loops from the transcripts before QA generation.

Whisper sometimes loops over music, silence or crosstalk, writing the same short
phrase over and over ("Right." 45 times in 4 seconds). One fixed rule, applied to
every video: a run of MIN_RUN or more consecutive segments whose text is identical
and at most MAX_CHARS long is collapsed to its first segment, timestamps unchanged.
Nothing else is edited.

The raw transcript is kept in <base>/<n>/Transcript-openai-whisper/ (copied there
first for videos that don't have it yet) and the cleaned one is written to
<base>/<n>/Transcript/, which generation reads. Re-running is safe: cleaning always
starts from the raw copy.

    python clean_transcripts.py [--report docs/transcript_cleanup_report.json]
"""
import argparse
import csv
import json
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent
MIN_RUN = 3
MAX_CHARS = 25
RAW = "Transcript-openai-whisper"
SETS = {"Master": "test_dataset.csv", "Teepa": "teepa.csv", "FTD": "ftd.csv",
        "GEMS": "gems.csv", "ODJ": "odj.csv", "SUP": "sup.csv"}


def collapse(segments):
    """Return (kept segments, list of removed runs)."""
    kept, runs, i = [], [], 0
    while i < len(segments):
        text = segments[i]["text"].strip()
        j = i
        while j + 1 < len(segments) and segments[j + 1]["text"].strip() == text:
            j += 1
        kept.append(segments[i])
        if j - i + 1 >= MIN_RUN and len(text) <= MAX_CHARS:
            runs.append({"text": text, "length": j - i + 1, "start": round(segments[i]["start"], 1),
                         "end": round(segments[j]["end"], 1)})
        else:
            kept.extend(segments[i + 1:j + 1])
        i = j + 1
    return kept, runs


def fmt(seconds):
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(path, segments):
    with open(path, "w", encoding="utf-8") as f:
        for k, seg in enumerate(segments, 1):
            f.write(f"{k}\n{fmt(seg['start'])} --> {fmt(seg['end'])}\n{seg['text'].strip()}\n\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--report", default=str(REPO / "docs" / "transcript_cleanup_report.json"))
    args = ap.parse_args()

    report = {"rule": f"collapse runs of >= {MIN_RUN} identical consecutive segments of <= {MAX_CHARS} characters "
                      "to the first segment", "videos": []}
    for base, sheet in SETS.items():
        for row in csv.DictReader(open(REPO / sheet, newline="", encoding="utf-8")):
            root = REPO / base / row["index"]
            raw_dir, out_dir = root / RAW, root / "Transcript"
            src = raw_dir if (raw_dir / "transcript-en.json").exists() else out_dir
            if not (src / "transcript-en.json").exists():
                report["videos"].append({"video": f"{base}/{row['index']}", "status": "missing"})
                continue
            raw = json.loads((src / "transcript-en.json").read_text(encoding="utf-8"))
            kept, runs = collapse(raw)
            if runs:
                if src == out_dir:
                    shutil.copytree(out_dir, raw_dir)  # keep the raw transcript before cleaning
                (out_dir / "transcript-en.json").write_text(json.dumps(kept, indent=2, ensure_ascii=False), encoding="utf-8")
                write_srt(out_dir / "transcript_en.srt", kept)
            elif src == raw_dir:
                # nothing to remove: Transcript/ should be the raw transcript itself
                shutil.copy2(raw_dir / "transcript-en.json", out_dir / "transcript-en.json")
            report["videos"].append({"video": f"{base}/{row['index']}", "segments_raw": len(raw),
                                     "segments_clean": len(kept), "removed": len(raw) - len(kept), "runs": runs})

    done = [v for v in report["videos"] if "removed" in v]
    report["summary"] = {
        "videos": len(done), "missing": [v["video"] for v in report["videos"] if v.get("status") == "missing"],
        "segments_raw": sum(v["segments_raw"] for v in done), "removed": sum(v["removed"] for v in done),
        "runs": sum(len(v["runs"]) for v in done),
        "videos_changed": sum(1 for v in done if v["removed"]),
    }
    Path(args.report).write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    s = report["summary"]
    print(f"{s['videos']} videos: removed {s['removed']} of {s['segments_raw']} segments "
          f"({100 * s['removed'] / max(s['segments_raw'], 1):.2f}%) in {s['runs']} runs across {s['videos_changed']} videos")
    for base in SETS:
        vs = [v for v in done if v["video"].startswith(base + "/")]
        print(f"  {base:6s} {sum(v['removed'] for v in vs):5d} removed of {sum(v['segments_raw'] for v in vs):6d}")
    if s["missing"]:
        print("missing:", ", ".join(s["missing"]))


if __name__ == "__main__":
    main()
