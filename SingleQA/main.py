import re
import os
import sys
import time
import argparse
from pathlib import Path
import json

from models.model_handler import QAModel
from processors.file_handler import FileHandler
from processors.qaparser import QAParser
from config.settings import Settings
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from common_utils.gpu_utils import GPUMemoryMonitor
from common_utils.paths import qwen_model_path
from common_utils import config

# adding private variables for language detecting
_LANG_MAP = {
    "en": ("en", "English"),
    "cn": ("zh", "Chinese"),
    "zh": ("zh", "Chinese"),
    "hi": ("hi", "Hindi"),
    "ro": ("ro", "Romanian"),
}
_LANG_SUFFIX_RE = re.compile(
    r"-(cn|zh|en|hi|ro)(?:-|\.|$)", re.IGNORECASE
)

class QAExtractor:
    def detect_language_from_filename(self, path: str) -> tuple[str, str]:
        """
        Detect language from file name patterns like `transcript-cn-1.json`.
        Returns (iso_code, human_name). Defaults to ('en', 'English').
        """
        name = Path(path).name  # just the file name
        m = _LANG_SUFFIX_RE.search(name)
        if m:
            key = m.group(1).lower()
            if key in _LANG_MAP:
                return _LANG_MAP[key]
        return ("en", "English")
    
    def set_global_language(self, iso_code: str, human_name: str):
        """
        Set global language in config and (optionally) environment for tools that read it.
        """
        config.LANGUAGE_CODE = iso_code
        config.LANGUAGE_NAME = human_name
        # Optional: also export to env if any subprocess/agent reads env
        os.environ["TRANSCRIPT_LANGUAGE_CODE"] = iso_code
        os.environ["TRANSCRIPT_LANGUAGE_NAME"] = human_name

    def __init__(self, transcript_file=None):
        self.settings = Settings()
        self.gpu_monitor = GPUMemoryMonitor()
        self.file_handler = FileHandler()
        self.qa_parser = QAParser()
        self.transcript_file = transcript_file

    def run(self):
        # Initialize
        print("="*70)
        print("QA EXTRACTION AGENT - Qwen3.5-9B")
        print("="*70)

        # Initial GPU memory
        print("\n[INITIAL GPU STATE]")
        self.gpu_monitor.print_gpu_memory()

        # Load model
        print("\n⏳ Loading model...")
        start_load = time.time()

        model = QAModel(qwen_model_path)
        model.load_model()

        load_time = time.time() - start_load
        print(f"✅ Model loaded in {load_time:.2f} seconds")

        # Post-load GPU memory
        print("\n[MODEL LOADED GPU STATE]")
        self.gpu_monitor.print_gpu_memory()

        # Get transcript file
        if self.transcript_file:
            print(f"\n📂 Using transcript file from flag: {self.transcript_file}")
            transcript = self.file_handler.read_transcript(self.transcript_file)
            file_name = Path(self.transcript_file).name
        else:
            while True:
                try:
                    file_name = input("\n📝 Enter transcript filename: ").strip()
                    transcript = self.file_handler.read_transcript(file_name)
                    break
                except ValueError as e:
                    print(f"❌ Error: {str(e)}. Try again.")
        
        # Adding lang feautre
        lang = self.detect_language_from_filename(file_name)    
        self.set_global_language(*lang)
        print(f"🌐 Detected transcript language: {config.LANGUAGE_CODE} ({config.LANGUAGE_NAME})")

        # Generate QA pairs
        print("\n🧠 Generating QA pairs (this may take several minutes)...")
        start_gen = time.time()

        response = model.generate_qa_pairs(transcript)

        gen_time = time.time() - start_gen
        print(f"✅ Generation completed in {gen_time:.2f} seconds")
        # return the total time to run.py
        print(json.dumps({"agent_seconds": gen_time}), flush=True)

        # Keep the raw model output. run.py moves Intermediate.json into the
        # approach's intermediate/ folder, so a later parser fix can be applied
        # to existing runs without paying for regeneration.
        with open("Intermediate.json", "w", encoding="utf-8") as f:
            json.dump(
                {"raw_response": response, "expected_pairs": model.expected_pairs},
                f,
                indent=2,
                ensure_ascii=False,
            )

        # Parse QA pairs
        print("⏳ Parsing QA pairs...")
        qa_data = self.qa_parser.parse_qa_pairs(response, expected=model.expected_pairs)

        print("\n=== DEBUG INFO ===")

        # Normalize transcript to a single string for stats/preview
        if isinstance(transcript, str):
            transcript = transcript.strip()
        elif isinstance(transcript, list):
            parts = []
            for item in transcript:
                if isinstance(item, dict):
                    s = (item.get("text") or item.get("transcript") or "").strip()
                    if s:
                        parts.append(s)
                else:
                    s = str(item).strip()
                    if s:
                        parts.append(s)
            transcript = " ".join(parts)
        elif isinstance(transcript, dict):
            transcript = (transcript.get("text") or transcript.get("transcript") or str(transcript)).strip()
        else:
            transcript = str(transcript).strip()

        print(f"Transcript length: {len(transcript)} chars, {len(transcript.split())} words")
        print(f"Model response preview: {response[:200]}...")
        print(f"Parsed {len(qa_data)} QA pairs")
        print("=================\n")

        # Save JSON
        output_file = self.file_handler.save_qa_pairs(qa_data, file_name, "finalQA")

        print(f"\n🎉 Successfully generated {len(qa_data)} QA pairs")
        print(f"💾 Output saved to: {output_file}")

        # Cleanup
        model.cleanup()

        print("\n[GPU STATE AFTER CLEANUP]")
        self.gpu_monitor.print_gpu_memory()

def get_args():
    parser = argparse.ArgumentParser(description="Single QA Extraction Agent")
    parser.add_argument(
        "--id",
        type=int,
        help="Transcript ID (if provided, transcript file will be loaded from predefined path)"
    )
    parser.add_argument(
        "--base",
        default="Master",
        help=(
            "Folder holding the per-video directories, i.e. <base>/<id>/Transcript. "
            "Relative paths resolve against the repository root. run.py passes an "
            "absolute path so the category folder is honoured."
        ),
    )
    return parser.parse_args()

if __name__ == "__main__":
    args = get_args()

    transcript_file = None
    # searching for transcript files
    if args.id is not None:
        # Path(root) / base keeps absolute bases intact and resolves relative
        # ones against the repo root.
        transcript_dir = (
            Path(__file__).resolve().parent.parent / args.base / str(args.id) / "Transcript"
        )
        print(f"Looking for transcripts in: {transcript_dir}")
        # Look for both transcript_*.json and transcript-*.json patterns
        matches = list(transcript_dir.glob("transcript*.json"))
        if matches:
            # pick the first, or define your own selection rule
            transcript_file = matches[0]
            print(f"Found transcript file: {transcript_file}")
        else:
            print(f"No transcript files found in {transcript_dir}")

    app = QAExtractor(str(transcript_file) if transcript_file else None)
    app.run()