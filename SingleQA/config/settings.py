import re
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))
from common_utils import config

class Settings:
    max_length = 131072
    max_new_tokens = 8192
    # Approximate character budget for the transcript (~4 chars/token), used
    # because token counting now happens server-side.
    max_input_chars = 400000
    # One QA pair per ~250 transcript words. Normal speech is 130-170 words per
    # minute, so 250 words is roughly 2 minutes of video. Tentative constant —
    # tune here if k comes out too sparse or too dense.
    words_per_qa = 250
    # Floor for short transcripts: the ratio alone gave a 3-minute video a
    # single pair, too thin to evaluate or to cover the video.
    min_qa_pairs = 4
    # Lowered from 0.7: at 0.7 the same transcript produced wildly varying pair
    # counts across runs, because the model was free to decide how many to
    # write. 0.3 keeps it closer to the "k questions" instruction. Combined
    # with QWEN_SEED in common_utils/llm_client.py this makes runs repeatable.
    generation_config = {
        "temperature": 0.3,
        "top_p": 0.9,
        "repetition_penalty": 1.05,
    }

    def get_prompt_template(self, transcript):
        # Language settings
        lang_code = config.LANGUAGE_CODE  # e.g., 'zh'
        lang_name = config.LANGUAGE_NAME
        # Strong instruction to keep output in the target language
        lang_guard = (
            "IMPORTANT: Reply strictly in {name}. "
            "Do not use English unless an English word appears verbatim in the input."
        ).format(name=lang_name)

        # Scale the number of pairs to transcript length: k = word_count / 250.
        # The [m:ss] markers are stripped first so they don't inflate the count.
        word_count = len(re.sub(r"\[\d+:\d{2}\]", " ", transcript).split())
        k = max(self.min_qa_pairs, round(word_count / self.words_per_qa))

        # Build prompt
        system_prompt = (
            f"You are a {lang_name} dementia-care education specialist. Your task is to read a "
            f"caregiver-education video transcript and generate question-answer pairs that are "
            f"faithful to the video, easy to understand, practically useful, and emotionally "
            f"supportive for dementia caregivers. {lang_guard}"
        )

        prompt = f"""Read the following transcript carefully and generate the {k} most valuable question-answer pairs in {lang_name}. Every QA pair must satisfy ALL FOUR quality criteria:

        Alignment (accurate & grounded): Base every answer strictly on information stated in the transcript. Do not add outside knowledge, speculate, or exaggerate. If the transcript is unclear on a point, do not create a question about it.

        Easy to Understand (clear & fluent): Write questions and answers in plain, conversational language that an 8th grade student with no medical background can understand. Define any clinical term the transcript uses. Keep answers focused — detailed enough to be complete, short enough to stay readable.

        Educational Value (actionable & useful): Prefer questions whose answers tell the caregiver what to do, why it works, or what to expect — concrete strategies, steps, and observable signs — over abstract facts. Avoid trivial or overly narrow questions.

        Supportive (empathetic): Use a warm, non-judgmental tone that normalizes the caregiver's struggles. Where the transcript offers reassurance, coping strategies, or empathy-building insight into the person with dementia's experience, capture it. Never phrase answers in a blaming or alarming way.

        Coverage rules:
        - Draw questions from across the ENTIRE transcript, not just one section.
        - Ensure the pairs do not overlap significantly in content.
        - QA needs to satisfy all of the 4 criteria.

        Strictly format your response as a list of question-answer pairs, with each pair clearly marked ("Question 1:", "Timestamp 1:", "Answer 1:" on separate lines, in that order). Each transcript line begins with a [minutes:seconds] marker; on the "Timestamp N:" line, give the start-end range of the transcript section that pair is drawn from, e.g. "Timestamp 3: 4:15-6:40". Do not mention timestamps inside the question or answer text itself. Output only the structured pairs — no preamble, no closing remarks.

        Transcript:
        {transcript}"""

        return system_prompt, prompt, k
