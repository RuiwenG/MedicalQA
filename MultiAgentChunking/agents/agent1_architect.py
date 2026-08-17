from typing import List, Dict
from processors.json_parser import JSONParser
from common_utils import config
import json


def merge_consecutive_segments(segments: List[Dict]) -> List[Dict]:
    merged = []
    current = None

    for segment in sorted(segments, key=lambda x: x["start_line"]):
        if current is None:
            current = segment
        elif current["title"] == segment["title"]:
            # Merge by extending the end line
            current["end_line"] = segment["end_line"]
        else:
            merged.append(current)
            current = segment

    if current:
        merged.append(current)

    # Reassign IDs sequentially
    for i, seg in enumerate(merged, 1):
        seg["id"] = i

    return merged


def deduplicate_segments(segments: List[Dict]) -> List[Dict]:
    seen_titles = set()
    cleaned = []
    for seg in segments:
        title = seg.get("title", "").strip()
        if title and title not in seen_titles:
            cleaned.append(seg)
            seen_titles.add(title)
    return cleaned


class Architect:
    def __init__(self, model_handler):
        self.model_handler = model_handler

    def run_agent1_architect(self, numbered_transcript_str: str) -> List[Dict]:
        """Agent 1 (Architect): Creates a structural blueprint of the transcript."""
        print("🧠 Running Agent 1 (Architect) to create semantic blueprint...")

        # Language settings
        lang_code = config.LANGUAGE_CODE  # e.g., 'zh'
        lang_name = config.LANGUAGE_NAME

        # Strong instruction to keep output in the target language
        lang_guard = (
            "IMPORTANT: Reply strictly in {name}. "
            "Do not use English unless an English word appears verbatim in the input. "
            "Ensure JSON structure is valid and complete."
        ).format(name=lang_name)

        # Enhanced system prompt with more specific instructions
        system_prompt = (
            f"You are a hardworking {lang_name} document analysis expert specialized in creating structured document summaries. "
            f"You excel at avoiding duplicates in topics and maintaining proper sequential order. "
            f"{lang_guard}"
        )
        prompt = f"""Based on the numbered transcript below, create a JSON list of topic-based segments in {lang_name}. Each segment represents a coherent topic, not just a few sentences.
        Follow these rules:

        1. Each dictionary must have these keys:
           - "id" (sequential integer)
           - "title" (concise topic title)
           - "start_line" (integer)
           - "end_line" (integer)
           
        2. Grouping & Granularity:
            - Cover the entire transcript, which has about 2000 lines.
            - Each segment should cover as much of consecutive lines as possible, unless there is a topic change.
            - Merge adjacent lines or short topics that describe the same or closely related ideas.
            - Treat emotionally supportive content (reassurance, coping advice, caregiver wellbeing) as distinct topics worth their own segment — do not bury them inside instructional segments.

        3. Semantic coherence:
            - Group by **topic or idea**, not by sentence.
            - Combine similar or repetitive ideas into one segment.
            - Avoid splitting just because of minor wording changes or pauses.
            - Keep the sequence continuous: no overlaps, no gaps.
        
        4. Output Guidelines:
            - Output ONLY valid JSON array
            - Ensure all lines are covered exactly once.
            - Keep titles meaningful and descriptive

        Transcript:
        {numbered_transcript_str}"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        response = self.model_handler.chat(messages, max_tokens=4096)

        print("testing", response)
        # saving intermediate chunks
        segments_file = "debug_chunk.json"
        with open(segments_file, "w", encoding="utf-8") as f:
            json.dump(response, f, indent=2, ensure_ascii=False)
        print(f"Debug: segment file saved to: {segments_file}")

        json_parser = JSONParser()
        blueprint = json_parser.extract_json_from_response(response)

        if blueprint is None:
            print("❌ No valid JSON extracted. Passing raw response downstream...")
            # Fallback: wrap raw response so the next agent still receives something
            blueprint = [
                {
                    "id": 0,
                    "title": "Raw Response",
                    "start_line": 0,
                    "end_line": 0,
                    "raw_text": response,
                }
            ]

        return blueprint
