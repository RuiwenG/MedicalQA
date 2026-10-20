# Usage:
# Single video:
#   python preprocess.py --url ... --audio_out Master/1/Audio/audio.wav --transcript_out Master/1/Transcript/transcript.txt
#
# Transcription engines (--engine), all writing the same transcript-<lang>.json/.srt:
#   openai   openai-whisper large-v3 from WHISPER_MODEL_PATH (the original setup; default)
#   faster   faster-whisper (CTranslate2) large-v3, the same model. Default settings
#            (no batching, no voice-activity detection) ran at 18x real time on the
#            RTX 3090 vs 4x for openai-whisper, with ~3 s segments and 96% of the same
#            words. --batch-size 16 is ~3x faster again but returns 30 s segments and
#            dropped real speech in testing; --vad skips silence but also cut speech.
#            Weights come from Hugging Face unless --fw-model is a local directory.
#   groq     Groq's hosted Whisper API (GROQ_API_KEY in the environment or .env).
#            Audio is re-encoded to 16 kHz mono and sent in 10-minute chunks.
#   openrouter  The same, through OpenRouter's speech-to-text endpoint (OPEN_ROUTER_API_KEY);
#            pass --groq-model openai/whisper-large-v3. The provider can't be pinned.
#   hf-deepinfra  DeepInfra's Whisper through Hugging Face (HF_API_KEY), pinned to DeepInfra;
#            pass --groq-model openai/whisper-large-v3.


import argparse
import json, csv
import shutil
import subprocess
from pathlib import Path
import sys, os
from typing import Dict, List, Optional
import re
import tempfile

COOKIES_FILE = "youtube_cookies.txt"
WHISPER_MODEL_PATH = (
    "/WAVE/projects/oignat_lab/Mentorship-VQA/whisper-model/large-v3.pt"
)

_SENT_FINAL = set(".?!…？！。") | {"।"}
_CONTINUATION = set(",;:，；：")

# ---------- IO helpers ----------


def ensure_parent(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)


def safe_mkdir(p: Path):
    p.mkdir(parents=True, exist_ok=True)


def download_audio(url: str, audio_out: Path, cookies: Optional[str] = None) -> Path:
    """
    Downloads the best quality audio from a YouTube URL as a WAV file.
    Returns the path to the downloaded audio file.
    """

    # remove the audio folder
    if os.path.exists(audio_out):
        shutil.rmtree(audio_out)
        print(f"🗑️  Removed existing '{audio_out}' folder.")

    os.makedirs(audio_out, exist_ok=True)

    audio_path = os.path.join(audio_out, "audio.wav")
    print(f"--- Downloading Audio from: {url} ---")
    cmd = ["yt-dlp"]
    # Cookies help with age-gated or rate-limited videos but are optional.
    cookies = cookies or (COOKIES_FILE if os.path.exists(COOKIES_FILE) else None)
    if cookies:
        cmd += ["--cookies", cookies]
    cmd += [
        "--no-playlist",
        "-f",
        "bestaudio",
        "--extract-audio",
        "--audio-format",
        "wav",
        "-o",
        audio_path,
        url,
    ]

    print("⏳ downloading audio:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    if not Path(audio_out).exists():
        raise RuntimeError(f"audio expected at {audio_out} but not found")
    print(f"✅ audio saved to {audio_out}")
    return audio_path


# ---------- Language helpers ----------


def _choose_end_mark(lang_code: str, text: str) -> str:
    # Prefer language-specific sentence enders where appropriate.
    if lang_code == "hi":
        return "।"
    if lang_code in ("zh", "zh-hans", "zh-hant", "ja", "ko"):
        return "。"
    # default .
    return "."


def _already_ended(s: str) -> bool:
    s = s.rstrip()
    if not s:
        return True
    # If ends with ellipsis '...' or '…', treat as ended
    if re.search(r"(\.\.\.|…)$", s):
        return True
    last = s[-1]
    # Skip if last char is sentence-final OR continuation punctuation
    return last in _SENT_FINAL or last in _CONTINUATION


def restore_punctuation_text(text: str, lang_code: str) -> str:
    """
    Restore punctuation on a piece of text using deepmultilingualpunctuation.
    Optionally map period to danda for Hindi.
    """
    if not text:
        return text
    t = re.sub(r"\s+", " ", text.strip())

    if _already_ended(t):
        return t
    mark = _choose_end_mark(lang_code, t)
    return t + mark


# ---------- Transcription engines ----------

_MODELS: Dict[str, object] = {}  # loaded once per process, reused across videos


def _openai_transcribe(audio_path, init_prompt):
    import torch
    import whisper

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if "openai" not in _MODELS:
        print(f"🧠 Using device: {device}")
        print(f"⏳ loading whisper model: {WHISPER_MODEL_PATH}")
        # The .pt path is the WAVE cluster's copy; elsewhere load (and cache) large-v3 by name.
        # WHISPER_DOWNLOAD_ROOT keeps that download on persistent storage (e.g. the pod's PVC).
        weights = WHISPER_MODEL_PATH if os.path.exists(WHISPER_MODEL_PATH) else "large-v3"
        _MODELS["openai"] = whisper.load_model(weights, device=device,
                                               download_root=os.getenv("WHISPER_DOWNLOAD_ROOT") or None)
    model = _MODELS["openai"]
    if init_prompt is None:
        result = model.transcribe(str(audio_path), task="transcribe")
    else:
        result = model.transcribe(str(audio_path), initial_prompt=init_prompt, task="transcribe")
    return result["segments"]


def _faster_transcribe(audio_path, init_prompt, lang_code, fw_model, batch_size, vad=True):
    from faster_whisper import BatchedInferencePipeline, WhisperModel

    try:
        import torch

        cuda = torch.cuda.is_available()
    except ImportError:
        cuda = False
    if "faster" not in _MODELS:
        device, compute = ("cuda", "float16") if cuda else ("cpu", "int8")
        print(f"🧠 faster-whisper on {device} ({compute}); loading {fw_model}")
        model = WhisperModel(fw_model, device=device, compute_type=compute)
        # Batching needs voice-activity detection to cut the audio; it only pays off on GPU.
        # --batch-size 1 turns batching off.
        _MODELS["faster"] = (model, BatchedInferencePipeline(model=model) if cuda and batch_size > 1 else None)
    model, batched = _MODELS["faster"]
    # Decode with ffmpeg (as openai-whisper does) instead of faster-whisper's PyAV
    # reader, which breaks on some PyAV versions ("metadata_errors").
    import numpy as np

    raw = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(audio_path),
                          "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                         check=True, capture_output=True).stdout
    audio = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    kwargs = dict(language=lang_code, initial_prompt=init_prompt, vad_filter=vad)
    if batched is not None:
        segments, _ = batched.transcribe(audio, batch_size=batch_size, **kwargs)
    else:
        segments, _ = model.transcribe(audio, **kwargs)
    return [{"start": s.start, "end": s.end, "text": s.text} for s in segments]


def _read_env_key(name):
    if os.getenv(name):
        return os.getenv(name)
    env = Path(__file__).resolve().parent / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


_STT_APIS = {
    "groq": ("Groq", "https://api.groq.com/openai/v1/audio/transcriptions", "GROQ_API_KEY"),
    "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1/audio/transcriptions", "OPEN_ROUTER_API_KEY"),
    # Hugging Face's router with the provider in the path, so every chunk goes to DeepInfra.
    "hf-deepinfra": ("HF/DeepInfra", "https://router.huggingface.co/deepinfra/v1/openai/audio/transcriptions", "HF_API_KEY"),
}


def _api_transcribe(engine, audio_path, init_prompt, lang_code, model, chunk_minutes=10):
    """Hosted Whisper through an OpenAI-compatible /audio/transcriptions endpoint."""
    import requests

    name, url, key_name = _STT_APIS[engine]
    key = _read_env_key(key_name)
    if not key:
        raise RuntimeError(f"{key_name} is not set (environment or .env)")
    segments, cost = [], 0.0
    with tempfile.TemporaryDirectory() as tmp:
        # 16 kHz mono FLAC keeps each 10-minute chunk well under the upload limit. Each
        # chunk is cut as its own file: the segment muxer's later FLAC chunks have a
        # header DeepInfra rejects ("Invalid or unsupported audio file").
        duration = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(audio_path)],
            check=True, capture_output=True, text=True).stdout.strip())
        chunks = []
        for i in range(int(duration // (chunk_minutes * 60)) + 1):
            start = i * chunk_minutes * 60
            if duration - start < 0.5:
                break
            chunk = Path(tmp) / f"chunk_{i:03d}.flac"
            subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", str(start), "-t", str(chunk_minutes * 60),
                            "-i", str(audio_path), "-ac", "1", "-ar", "16000", "-c:a", "flac", str(chunk)], check=True)
            chunks.append(chunk)
        print(f"☁️  {name} {model}: {len(chunks)} chunk(s) of up to {chunk_minutes} min")
        for i, chunk in enumerate(chunks):
            offset = i * chunk_minutes * 60
            data = {"model": model, "response_format": "verbose_json", "temperature": "0"}
            if lang_code:
                data["language"] = lang_code
            if init_prompt and engine != "openrouter":  # OpenRouter's endpoint has no prompt field
                data["prompt"] = init_prompt
            for attempt in range(5):
                with open(chunk, "rb") as fh:
                    r = requests.post(url,
                                      headers={"Authorization": f"Bearer {key}"},
                                      files={"file": (chunk.name, fh, "audio/flac")}, data=data, timeout=600)
                if r.status_code == 429 or r.status_code >= 500:
                    wait = int(r.headers.get("retry-after", 0) or 0) or 10 * (attempt + 1)
                    print(f"   chunk {i}: HTTP {r.status_code}, retrying in {wait}s")
                    import time
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                break
            else:
                raise RuntimeError(f"{name} failed on chunk {i} after retries")
            body = r.json()
            if not body.get("segments"):
                raise RuntimeError(f"{name} returned no segment timestamps for chunk {i}")
            cost += float((body.get("usage") or {}).get("cost") or 0)
            for seg in body["segments"]:
                segments.append({"start": seg["start"] + offset, "end": seg["end"] + offset, "text": seg["text"]})
    if cost:
        print(f"   {name} cost: ${cost:.4f}")
    return segments


def transcribe(audio_path: Path, transcript_out: Path, lang: str, engine: str = "openai",
               fw_model: str = "large-v3", groq_model: str = "whisper-large-v3", batch_size: int = 1,
               vad: bool = False):
    """
    Transcribes audio with the chosen engine and saves transcript-<lang>.json and .srt.
    """
    ensure_parent(transcript_out)

    # --- Map language code to filename suffix ---
    lang_map = {"chinese": "zh", "hindi": "hi", "romanian": "ro", "english": "en"}
    suffix = lang_map.get(lang.lower(), lang.lower())

    prompts = {
        "zh": "请使用标点符号。",
        "hi": "कृपया हिंदी में लिखें और '।' जैसे विराम चिह्नों का उपयोग करें",
        "ro": "Transcrie în limba română și folosește semne de punctuație.",
        "en": "Transcribe in English and use punctuation.",
    }

    init_prompt = prompts.get(suffix)
    if not init_prompt:
        init_prompt = "Please use punctuation."
    # Note: if the detected language is Hindi, we don't use initial prompt
    if suffix == "hi":
        init_prompt = None
    print(f"🔎 Using initial prompt: {repr(init_prompt)}")

    print(f"   -> Starting transcription with {engine} (this may take a while)...")
    if engine == "openai":
        segments = _openai_transcribe(audio_path, init_prompt)
    elif engine == "faster":
        segments = _faster_transcribe(audio_path, init_prompt, suffix, fw_model, batch_size, vad)
    elif engine in _STT_APIS:
        segments = _api_transcribe(engine, audio_path, init_prompt, suffix, groq_model)
    else:
        raise ValueError(f"unknown engine {engine!r}")
    result = {"segments": segments}

    # detected_lang = result.get("language", "unknown")
    print(f"🌐 Using language from argument: {suffix}")

    # Apply punctuation restoration to each segment
    # Restore punctuation for Chinese only
    print("   -> Restoring punctuation on segments...")
    if suffix in ("zh"):
        for seg in result.get("segments", []):
            seg_text = seg.get("text", "")
            seg["text"] = restore_punctuation_text(seg_text, suffix)

    # --- 1. Save the structured JSON output ---
    json_filename = f"transcript-{suffix}.json"
    json_path = transcript_out / json_filename

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result["segments"], f, indent=2, ensure_ascii=False)
    print(f"✅ Raw segment data saved to: {json_path}")

    # --- 2. Create and save the SRT subtitle file ---
    srt_path = os.path.join(transcript_out, f"transcript_{suffix}.srt")
    with open(srt_path, "w", encoding="utf-8") as srt_file:
        for i, segment in enumerate(result["segments"], 1):
            # Format start and end times to SRT format (HH:MM:SS,ms)
            start_time = format_timestamp(segment["start"])
            end_time = format_timestamp(segment["end"])

            # Write the segment number, timestamp, and text to the file
            srt_file.write(f"{i}\n")
            srt_file.write(f"{start_time} --> {end_time}\n")
            srt_file.write(f"{segment['text'].strip()}\n\n")

    print(f"✅ Subtitle file saved to: {srt_path}")
    print("-" * 30)


def format_timestamp(seconds):
    """Converts seconds to SRT time format HH:MM:SS,ms."""
    assert seconds >= 0, "non-negative timestamp expected"
    milliseconds = round(seconds * 1000.0)

    hours = milliseconds // 3_600_000
    milliseconds %= 3_600_000

    minutes = milliseconds // 60_000
    milliseconds %= 60_000

    seconds = milliseconds // 1_000
    milliseconds %= 1_000

    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


# ---------- Spreadsheet reading ----------


def read_csv(path: Path) -> List[Dict[str, str]]:
    """
    Expect columns: url (required), index (optional), title (optional).
    Returns sorted list by 'index' (auto-numbered if missing).
    """
    rows: List[Dict[str, str]] = []
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                rows.append({k.strip(): (v or "").strip() for k, v in r.items()})
    elif path.suffix.lower() in [".xlsx", ".xls"]:
        try:
            import pandas as pd  # pandas + openpyxl recommended for xlsx
        except ImportError as e:
            raise SystemExit(
                "Please `pip install pandas openpyxl` to read .xlsx files"
            ) from e
        df = pd.read_excel(path, dtype=str).fillna("")
        rows = df.to_dict(orient="records")
    else:
        raise ValueError("Unsupported sheet format. Use .csv or .xlsx")

    # normalize + auto index
    out: List[Dict[str, str]] = []
    autoinc = 1
    for r in rows:
        url = r.get("url", "").strip()
        if not url:
            continue  # skip blank rows
        idx_raw = r.get("index", "").strip()
        if idx_raw == "":
            idx = autoinc
            autoinc += 1
        else:
            try:
                idx = int(idx_raw)
            except ValueError:
                idx = autoinc
                autoinc += 1
        language = r.get("language", "").strip()
        out.append({"index": idx, "url": url, "language": language})
    out.sort(key=lambda x: x["index"])
    return out


# ---------- Batch driver ----------


def process_single(
    url: str,
    audio_out: Path,
    transcript_out: Path,
    lang: str,
    cookies: Optional[str],
    dry_run: bool = False,
    engine_opts: Optional[dict] = None,
):
    print(f"🎬 processing {url}")
    if dry_run:
        print(f"DRY-RUN would save audio → {audio_out}")
        print(f"DRY-RUN would save transcript → {transcript_out}")
        return
    audio = download_audio(url, audio_out, cookies)
    transcribe(audio, transcript_out, lang, **(engine_opts or {}))


def process_sheet(
    sheet_path: Path,
    base_dir: Path,
    only: Optional[List[int]],
    cookies: Optional[str],
    dry_run: bool = False,
    engine_opts: Optional[dict] = None,
):
    videos = read_csv(sheet_path)
    if only:
        only_set = set(only)
        videos = [v for v in videos if v["index"] in only_set]

    safe_mkdir(base_dir)

    for v in videos:
        idx, url, language = v["index"], v["url"], v["language"]
        print(f"\n===== Video {idx}: {language} =====")
        root = base_dir / f"{idx}"
        audio_dir = root / "Audio"
        transcript_dir = root / "Transcript"
        safe_mkdir(audio_dir)
        safe_mkdir(transcript_dir)

        process_single(
            url=url,
            audio_out=audio_dir,  # pass the folder to save audio
            transcript_out=transcript_dir,
            cookies=cookies,
            dry_run=dry_run,
            lang=language,
            engine_opts=engine_opts,
        )


# ---------- CLI ----------


def main():
    ap = argparse.ArgumentParser(
        description="Download audio (yt-dlp) and transcribe (Whisper) from either a single URL or a spreadsheet."
    )
    # mode: single
    ap.add_argument("--url", help="YouTube URL (single mode)")
    ap.add_argument("--audio_out", help="Where to write audio file (single mode)")
    ap.add_argument("--transcript_out", help="Where to write transcript (single mode)")
    ap.add_argument("--lang", help="The language of this video")

    # mode B: spreadsheet
    ap.add_argument(
        "--sheet", help="Path to CSV or XLSX with columns: index,url,[title]"
    )
    ap.add_argument(
        "--base",
        default="OUTPUT",
        help="Base output folder for sheet mode (default: OUTPUT)",
    )
    ap.add_argument(
        "--only", nargs="*", type=int, help="Only process these indices (sheet mode)"
    )

    # shared options
    # could remove this argument
    ap.add_argument(
        "--cookies", default=None, help="Path to youtube cookies.txt (optional)"
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="Print the plan without doing work"
    )
    ap.add_argument("--engine", choices=["openai", "faster", "groq", "openrouter", "hf-deepinfra"], default="openai",
                    help="Transcription engine (default: openai, the original openai-whisper setup)")
    ap.add_argument("--fw-model", default="large-v3",
                    help="faster-whisper model name or local CTranslate2 directory (default: large-v3)")
    ap.add_argument("--groq-model", default="whisper-large-v3",
                    help="Groq/OpenRouter model, e.g. whisper-large-v3 (OpenRouter: openai/whisper-large-v3)")
    ap.add_argument("--batch-size", type=int, default=1,
                    help="faster-whisper batch size on GPU (default 1 = no batching; batching needs --vad)")
    ap.add_argument("--vad", action="store_true", help="faster-whisper: skip silence with voice-activity detection")
    args = ap.parse_args()
    engine_opts = {"engine": args.engine, "fw_model": args.fw_model, "groq_model": args.groq_model,
                   "batch_size": args.batch_size, "vad": args.vad or args.batch_size > 1}

    # Validate mode
    single_mode = args.url is not None
    sheet_mode = args.sheet is not None
    if not single_mode and not sheet_mode:
        print(
            "❌ Single: --url --audio_out --transcript_out \n Sheet: --url --base",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        if single_mode:
            if not args.audio_out or not args.transcript_out or not args.lang:
                print(
                    "❌ Requires --audio_out, --lang and --transcript_out",
                    file=sys.stderr,
                )
                sys.exit(2)
            process_single(
                url=args.url,
                audio_out=Path(args.audio_out),
                transcript_out=Path(args.transcript_out),
                cookies=args.cookies,
                dry_run=args.dry_run,
                lang=args.lang,
                engine_opts=engine_opts,
            )
        else:
            process_sheet(
                sheet_path=Path(args.sheet),
                base_dir=Path(args.base),
                only=args.only,
                cookies=args.cookies,
                dry_run=args.dry_run,
                engine_opts=engine_opts,
            )
        print("\n🎉 done.")
    except subprocess.CalledProcessError as e:
        print("❌ external tool failed:", e, file=sys.stderr)
        sys.exit(e.returncode or 1)
    except Exception as e:
        print("❌ error:", e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
