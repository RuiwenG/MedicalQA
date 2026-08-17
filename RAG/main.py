import os
import sys
import json
import time
import argparse
import re

from pathlib import Path
from typing import List, Dict
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from common_utils import config
from common_utils.gpu_utils import GPUMemoryMonitor
from vector_embeddings.chroma_db import ChromaManager
from processors.text_processor import TextProcessor
from processors.file_handler import FileHandler
from agents.base_agent import QwenModelHandler
from agents.agent1_question_generation import QuestionGenerator
from agents.agent2_answer_synthesis import AnswerSynthesizer
from processors.question_parser import QuestionParser

# adding private variables for language detecting
_LANG_MAP = {
    "en": ("en", "English"),
    "cn": ("zh", "Chinese"),
    "zh": ("zh", "Chinese"),
    "hi": ("hi", "Hindi"),
    "ro": ("ro", "Romanian"),
}
_LANG_SUFFIX_RE = re.compile(r"-(cn|zh|en|hi|ro)(?:-|\.|$)", re.IGNORECASE)


class RAGPipeline:
    # helpfer functions to detect languages
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
        self.gpu_monitor = GPUMemoryMonitor()
        self.text_processor = TextProcessor()
        self.file_handler = FileHandler()
        self.chroma_manager = ChromaManager()
        self.model_handler = QwenModelHandler()
        self.question_generator = QuestionGenerator(
            self.model_handler, QuestionParser()
        )
        self.answer_synthesizer = AnswerSynthesizer(self.model_handler)
        self.transcript_file = transcript_file

    def run(self):
        print("=" * 70)
        print("TRUE RAG PIPELINE FOR QA EXTRACTION")
        print("=" * 70)

        # Initial GPU state
        print("\n[INITIAL GPU STATE]")
        self.gpu_monitor.print_gpu_memory()

        # Get transcript file
        if self.transcript_file:
            print(f"\n📂 Using transcript file from flag: {self.transcript_file}")
            file_name = self.transcript_file
        else:
            file_name = input("\n📝 Enter transcript filename: ").strip()
        transcript = self.file_handler.get_transcript_file(file_name)

        # Adding lang feautre
        lang = self.detect_language_from_filename(file_name)
        self.set_global_language(*lang)
        print(
            f"🌐 Detected transcript language: {config.LANGUAGE_CODE} ({config.LANGUAGE_NAME})"
        )

        try:
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
                transcript = (
                    transcript.get("text")
                    or transcript.get("transcript")
                    or str(transcript)
                ).strip()
            else:
                transcript = str(transcript).strip()

            # ===== 1. EMBEDDING PHASE (INDEXING) =====
            print("\n--- PHASE 1: INDEXING ---")
            # Embedding time
            emb_start = time.time()
            chunks = self.text_processor.split_text(transcript)
            print(f"ℹ️ Created {len(chunks)} chunks.")

            vectordb = self.chroma_manager.initialize_db(chunks)
            print("✅ Vector DB created and saved")
            emb_end = time.time() - emb_start
            print(f"\n Embedding time is {emb_end}")

            # Post-load GPU memory
            print("\n[EMBEDDING MODEL LOADED GPU STATE]")
            self.gpu_monitor.print_gpu_memory()

            # Phase 2: Question Generation
            print("\n--- PHASE 2: QUESTION GENERATION ---")
            self.model_handler.load_model()

            # Query 1 running time
            q1_start = time.time()
            potential_questions = (
                self.question_generator.run_agent1_question_generation(transcript)
            )
            q1_end = time.time() - q1_start

            # Post-load GPU memory
            print("\n[QWEN MODEL LOADED GPU STATE]")
            self.gpu_monitor.print_gpu_memory()

            if not potential_questions:
                print("❌ Failed to generate questions. Exiting.")
                return

            # Phase 3: Retrieval and Answering
            print("\n--- PHASE 3: RETRIEVAL AND ANSWERING ---")
            # self.answer_synthesizer.prepare()

            print("\n[QWEN MODEL LOADED GPU STATE]")
            self.gpu_monitor.print_gpu_memory()

            final_qa_pairs = []
            # Query 2 time
            q2_start = time.time()
            retriever = vectordb.as_retriever(
                search_kwargs={"k": 5}
            )  # Retrieve top 5 chunks

            for i, question in enumerate(potential_questions):
                if len(final_qa_pairs) >= 20:
                    print("\nℹ️ Reached target of 20 QA pairs. Stopping.")
                    break

                print(f"\nProcessing Q{i + 1}/{len(potential_questions)}: {question}")

                # Step 1: Retrieve relevant chunks
                print("  🔍 Retrieving relevant context...")
                retrieved_docs = retriever.invoke(question)
                context_chunks = [doc.page_content for doc in retrieved_docs]

                # Step 2: Generate answer based on context
                print("  ✍️ Synthesizing answer...")
                qa_pair = self.answer_synthesizer.run_agent2_answer_synthesis(
                    question, context_chunks
                )

                if qa_pair:
                    final_qa_pairs.append(qa_pair)
                    print("  ✅ Answer synthesized successfully.")
                else:
                    print("  ⚠️ Answer could not be synthesized from context.")
            q2_end = time.time() - q2_start

            # return the time to run.py
            total_time = emb_end + q1_end + q2_end
            print(json.dumps({"agent_seconds": total_time}), flush=True)

            # ===== 4. SAVE RESULTS =====
            print("\n--- PHASE 4: SAVING RESULTS ---")
            output_file = self.file_handler.save_results(
                final_qa_pairs, file_name, "finalQA"
            )
            print(f"\n🎉 Successfully generated {len(final_qa_pairs)} QA pairs.")
            print(f"💾 Output saved to: {output_file}")

        finally:
            # Cleanup
            self.model_handler.unload_model()
            # self.question_generator.unload_model()
            # self.answer_synthesizer.unload_model()
            if "vectordb" in locals():
                del vectordb

            # The ChromaDB directory is NO LONGER deleted here, so it will persist.
            print("\n[GPU STATE AFTER CLEANUP]")
            self.gpu_monitor.print_gpu_memory()


def get_args():
    parser = argparse.ArgumentParser(description="True RAG Pipeline for QA Extraction")
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

    pipeline = RAGPipeline(
        transcript_file=str(transcript_file) if transcript_file else None
    )
    pipeline.run()
