import os
import re
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))
from common_utils import config

# Prompt versions of the SingleAgent ablation, verbatim from git history:
#   v1  86869bd  four criteria (no Self-contained)
#   v2  447a972  adds Self-contained as one line
#   v3  c7450ab  splits Self-contained and bans dangling openers ("this", "the" + noun)
# Choose with SINGLEQA_PROMPT=v1|v2|v3 (default v3, the current prompt).
_STANDALONE = {
    "v1": "",
    "v2": (
        "Self-contained (Standalone): every pair should be read on its own, without any order, by a caregiver who has not seen the video before and can’t see the other pairs. Don’t mention the video, transcript or speaker. Never mention any pronouns before defining them first.\n\n        "
    ),
    "v3": (
        "Self-contained (Standalone): every pair should be read on its own, without any order, by a caregiver who has not seen the video before and can’t see the other pairs.\n"
        "        Don’t mention the video, transcript or speaker.\n"
        "        Name the subject in the question itself. Do not open with \"this\", \"these\", \"the\" + a noun the pair never names, or references that point at another pair.\n"
        "        Never mention any pronouns before naming them in the same pair first.\n\n        "
    ),
}


# The format line as the v1-v3 runs used it, and a variant that shows what goes
# after each label. Qwen3-14B reads the bare labels as text to copy and leaves
# the question out (218 of 226 v1 pairs, every seed tried); the filled variant
# fixes that without touching the quality criteria. SINGLEQA_FORMAT=filled.
_FORMAT_LABELS = '("Question 1:", "Timestamp 1:", "Answer 1:" on separate lines, in that order)'
_FORMAT_FILLED = '("Question 1: <question text>", "Timestamp 1: <range>", "Answer 1: <answer text>" on separate lines, in that order)'


class Settings:
    prompt_version = os.getenv("SINGLEQA_PROMPT", "v3").strip().lower()
    prompt_format = os.getenv("SINGLEQA_FORMAT", "labels").strip().lower()
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

        if self.prompt_version not in _STANDALONE:
            raise ValueError(f"SINGLEQA_PROMPT must be one of {sorted(_STANDALONE)}, got {self.prompt_version!r}")
        standalone = _STANDALONE[self.prompt_version]
        n_criteria = 4 if self.prompt_version == "v1" else 5
        n_word = {4: "FOUR", 5: "FIVE"}[n_criteria]

        # Build prompt
        system_prompt = (
            f"You are a {lang_name} dementia-care education specialist. Your task is to read a "
            f"caregiver-education video transcript and generate question-answer pairs that are "
            f"faithful to the video, easy to understand, practically useful, and emotionally "
            f"supportive for dementia caregivers. {lang_guard}"
        )

        prompt = f"""Read the following transcript carefully and generate the {k} most valuable question-answer pairs in {lang_name}. Every QA pair must satisfy ALL {n_word} quality criteria:

        Alignment (accurate & grounded): Base every answer strictly on information stated in the transcript. Do not add outside knowledge, speculate, or exaggerate. If the transcript is unclear on a point, do not create a question about it.

        Easy to Understand (clear & fluent): Write questions and answers in plain, conversational language that an 8th grade student with no medical background can understand. Define any clinical term the transcript uses. Keep answers focused — detailed enough to be complete, short enough to stay readable.

        Educational Value (actionable & useful): Prefer questions whose answers tell the caregiver what to do, why it works, or what to expect — concrete strategies, steps, and observable signs — over abstract facts. Avoid trivial or overly narrow questions.

        Supportive (empathetic): Use a warm, non-judgmental tone that normalizes the caregiver's struggles. Where the transcript offers reassurance, coping strategies, or empathy-building insight into the person with dementia's experience, capture it. Never phrase answers in a blaming or alarming way.

        {standalone}Coverage rules:
        - Draw questions from across the ENTIRE transcript, not just one section.
        - Ensure the pairs do not overlap significantly in content.
        - QA needs to satisfy all of the {n_criteria} criteria.

        Strictly format your response as a list of question-answer pairs, with each pair clearly marked ("Question 1:", "Timestamp 1:", "Answer 1:" on separate lines, in that order). Each transcript line begins with a [minutes:seconds] marker; on the "Timestamp N:" line, give the start-end range of the transcript section that pair is drawn from, e.g. "Timestamp 3: 4:15-6:40". Do not mention timestamps inside the question or answer text itself. Output only the structured pairs — no preamble, no closing remarks.

        Transcript:
        {transcript}"""

        if self.prompt_format == "filled":
            assert _FORMAT_LABELS in prompt
            prompt = prompt.replace(_FORMAT_LABELS, _FORMAT_FILLED, 1)
        elif self.prompt_format != "labels":
            raise ValueError(f"SINGLEQA_FORMAT must be 'labels' or 'filled', got {self.prompt_format!r}")

        return system_prompt, prompt, k
