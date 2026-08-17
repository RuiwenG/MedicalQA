import os
import sys
import time
import argparse
import random
import re, json
from pathlib import Path
from typing import List, Dict

# custom packages
from models.model_handler import QAModel
from processors.time_utils import format_hhmmss

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
_LANG_SUFFIX_RE = re.compile(r"-(cn|zh|en|hi|ro)(?:-|\.|$)", re.IGNORECASE)


class MultiAgent:
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

    def __init__(self, model_path=qwen_model_path, transcript_file=None):
        self.gpu_monitor = GPUMemoryMonitor()
        self.model_path = model_path
        self.model_handler = None
        self.agents = {}
        self.transcript_file = transcript_file

    def initialize_agents(self):
        """Initialize all agents with the model handler"""
        from agents.agent1_architect import Architect
        from agents.agent2_inquisitor import Inquisitor
        from agents.agent3_scorer_single import Scorer
        from agents.agent4_justifier import Justifier
        from agents.agent5_synthesizer import Synthesizer

        self.agents = {
            "architect": Architect(self.model_handler),
            "inquisitor": Inquisitor(self.model_handler),
            "scorer": Scorer(self.model_handler),
            "justifier": Justifier(self.model_handler),
            "synthesizer": Synthesizer(self.model_handler),
        }

    def run_multiagent(self):
        print("=" * 70)
        print("DECOMPOSED MULTI-AGENT QA EXTRACTION PIPELINE")
        print("=" * 70)

        # Initial GPU State
        print("\n[INITIAL GPU STATE]")
        self.gpu_monitor.print_gpu_memory()

        # Load Model
        print("\n⏳ Loading Qwen3.5-9B for all agent roles...")
        self.model_handler = QAModel(qwen_model_path).load_model()

        # Post-load GPU memory
        print("\n[MODEL LOADED GPU STATE]")
        self.gpu_monitor.print_gpu_memory()

        # Get Input (JSON only)
        from processors.file_handler import FileHandler

        file_handler = FileHandler()
        if self.transcript_file:
            print(f"\n📂 Using transcript file from flag: {self.transcript_file}")
            file_name = self.transcript_file
        else:
            file_name = input("\n📝 Enter transcript filename (.json): ").strip()

        # Adding lang feautre
        lang = self.detect_language_from_filename(file_name)
        self.set_global_language(*lang)
        print(
            f"🌐 Detected transcript language: {config.LANGUAGE_CODE} ({config.LANGUAGE_NAME})"
        )
        # Moved initialize agents to here so that the language is included
        self.initialize_agents()
        print("✅ Model loaded.")

        segments_json = file_handler.read_transcript(file_name)
        base_name = os.path.splitext(file_name)[0]

        # Build numbered transcript and time index from JSON
        print("🔢 Building numbered transcript from JSON segments...")
        numbered_lines = []
        line_to_time = {}
        for i, seg in enumerate(segments_json, 1):
            text = (seg.get("text") or "").strip()
            start_sec = float(seg.get("start", 0.0))
            end_sec = float(seg.get("end", 0.0))
            numbered_lines.append(f"{i}: {text}")
            line_to_time[i] = (start_sec, end_sec)

        # --- PHASE 1 & 2: ARCHITECT & INQUISITOR (UNCHANGED) ---
        # from processors.transcript_processor import TranscriptProcessor
        # transcript_processor = TranscriptProcessor()
        # numbered_lines = transcript_processor.number_transcript_lines(transcript)

        # Agent 1 time
        agent1_start = time.time()
        blueprint = self.agents["architect"].run_agent1_architect(
            "\n".join(numbered_lines)
        )
        agent1_end = time.time() - agent1_start

        if not blueprint:
            return

        segments = []
        seg_by_id = {}
        line_dict = {i + 1: line for i, line in enumerate(numbered_lines)}

        for bp in blueprint:
            content_lines = [
                line_dict.get(i, "")
                for i in range(bp["start_line"], bp["end_line"] + 1)
            ]
            seg_obj = {
                "topic": bp["title"],
                "segment-number": bp["id"],
                "content": "\n".join(content_lines),
                # Timestamp mapping (from first/last line in the range)
                "time_start_sec": line_to_time.get(bp["start_line"], (0.0, 0.0))[0],
                "time_end_sec": line_to_time.get(bp["end_line"], (0.0, 0.0))[1],
                "time_start_hhmmss": format_hhmmss(
                    line_to_time.get(bp["start_line"], (0.0, 0.0))[0]
                ),
                "time_end_hhmmss": format_hhmmss(
                    line_to_time.get(bp["end_line"], (0.0, 0.0))[1]
                ),
            }
            segments.append(seg_obj)
            seg_by_id[seg_obj["segment-number"]] = seg_obj

        print(f"Assembled {len(segments)} content segments.")
        # SAVE the segments to chunks.json:
        segment_file = file_handler.save_output(segments, file_name, "chunks")
        print(f"💾 The segment chunks are saved to: {segment_file}")

        all_potential_questions = []
        # Agent 2 time
        agent2_start = time.time()
        for segment in segments:
            questions = self.agents["inquisitor"].run_agent2_inquisitor(
                segment["content"]
            )
            for q in questions:
                all_potential_questions.append(
                    {
                        "question": q,
                        "source_segment": {
                            "id": segment["segment-number"],
                            "topic": segment["topic"],
                        },
                    }
                )
        agent2_end = time.time() - agent2_start

        # shuffle the questions to aviod positional bias
        random.shuffle(all_potential_questions)

        # --- BETWEEN PHASES: PROGRAMMATIC NUMBERING ---
        numbered_questions_list = []
        for i, item in enumerate(all_potential_questions):
            item["question_number"] = i + 1
            numbered_questions_list.append(
                f"{i + 1}. (From Segment {item['source_segment']['id']}) {item['question']}"
            )

        # --- PHASE 3: SELECTION ---
        """
        selected_numbers = self.agents['scorer'].run_agent3_selector("\n".join(numbered_questions_list))
        selected_numbers_set = set(selected_numbers)

        # --- BETWEEN PHASES: PROGRAMMATIC STATUS UPDATE ---
        curation_log = all_potential_questions
        for item in curation_log:
            item['status'] = "Selected" if item['question_number'] in selected_numbers_set else "Rejected"
        """
        scored_pool = []
        # Agent 3 time
        agent3_start = time.time()
        for i, item in enumerate(all_potential_questions):
            # context hint for scoring helps consistency
            hint = f"Segment {item['source_segment']['id']}: {item['source_segment']['topic']}. If this question relates to the introduction or greetings of the speaker, lower its rating."
            s = self.agents["scorer"].run_agent3_scorer_single(item["question"], hint)
            scored_pool.append(
                {
                    "id": item["question_number"],
                    "segment_id": item["source_segment"]["id"],
                    "score": float(s),
                    "text": item["question"],
                }
            )
        agent3_end = time.time() - agent3_start

        # the target number of questions
        K = 20
        from processors.selection_algorithm import SelectionAlgorithm

        selection_algorithm = SelectionAlgorithm()
        selected_numbers = selection_algorithm.select_proportionally_distributed(
            scored_pool, K, skip_topics={"Introduction"}
        )
        selected_numbers_set = set(selected_numbers)

        # --- BETWEEN PHASES: PROGRAMMATIC STATUS UPDATE ---
        curation_log = all_potential_questions
        for item in curation_log:
            if item["question_number"] in selected_numbers_set:
                item["status"] = "Selected"
            else:
                item["status"] = "Rejected"

        # Note: Feel free to comment back the following to see the justification that LLM gives
        # # --- PHASE 4: JUSTIFICATION ---
        # print("\n🧠 Running Agent 4 (Justifier) for all questions (one by one)...")
        # for i, item in enumerate(curation_log):
        #     print(f"   - Justifying question {i+1}/{len(curation_log)}...")
        #     reason = self.agents['justifier'].run_agent4_justifier(item)
        #     item['reason'] = reason

        # print(f"✅ Justification complete.")
        # curation_file = file_handler.save_output(curation_log, file_name, "Intermediate")
        # print(f"💾 Full Curation log saved to: {curation_file}")

        # --- PHASE 5: SYNTHESIS ---
        print(
            "\n🧠 Running Agent 5 (Synthesizer) for selected questions (one by one)..."
        )
        final_qa_pairs = []
        selected_questions = [
            item for item in curation_log if item.get("status") == "Selected"
        ]

        # Enforce 20 question rule after selection
        if len(selected_questions) != 20:
            print(
                f"⚠️ Selector returned {len(selected_questions)} questions, not 20. The list will be truncated/padded if needed."
            )
        # Agent 5 time
        agent5_start = time.time()
        for i, item in enumerate(selected_questions):
            print(f"   - Answering question {i + 1}/{len(selected_questions)}...")
            context = ""
            src_id = item["source_segment"]["id"]
            # fetch context
            for seg in segments:
                if seg["segment-number"] == src_id:
                    context = seg["content"]
                    break
            if context:
                answer = self.agents["synthesizer"].run_agent5_synthesizer(
                    item["question"], context
                )
                # stamp timestamps from the source segment
                src_seg = seg_by_id.get(src_id, {})
                final_qa_pairs.append(
                    {
                        "question": item["question"],
                        "answer": answer,
                        "source_segment": item["source_segment"],
                        "time_start_sec": src_seg.get("time_start_sec"),
                        "time_end_sec": src_seg.get("time_end_sec"),
                        "time_start_hhmmss": src_seg.get("time_start_hhmmss"),
                        "time_end_hhmmss": src_seg.get("time_end_hhmmss"),
                    }
                )
        agent5_end = time.time() - agent5_start

        # Add all agent times and print to the console. run.py will read from it
        total_time = agent1_end + agent2_end + agent3_end + agent5_end
        print(json.dumps({"agent_seconds": total_time}), flush=True)

        # --- FINAL OUTPUT ---
        print("\n--- FINAL OUTPUT ---")
        output_file = file_handler.save_output(final_qa_pairs, file_name, "finalQA")
        print(f"🎉 Process complete! Generated {len(final_qa_pairs)} final QA pairs.")
        print(f"💾 Final output saved to: {output_file}")

        # Final GPU State
        # self.gpu_monitor.print_gpu_memory()

        self.model_handler.cleanup()

        print("\n[GPU STATE AFTER CLEANUP]")
        self.gpu_monitor.print_gpu_memory()


def get_args():
    parser = argparse.ArgumentParser(description="Multi-Agent QA Pipeline")
    parser.add_argument(
        "--id",
        type=int,
        help="Transcript ID (if provided, transcript file will be loaded from predefined path)",
    )
    parser.add_argument(
        "--base",
        default="Master",
        help=(
            "Folder holding the per-video directories, i.e. <base>/<id>/Transcript. "
            "Relative paths resolve against the repository root."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = get_args()
    transcript_file = None
    # searching for transcript files
    if args.id is not None:
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

    pipeline = MultiAgent(
        transcript_file=str(transcript_file) if transcript_file else None
    )
    pipeline.run_multiagent()
