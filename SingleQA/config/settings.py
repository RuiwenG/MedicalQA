import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))
from common_utils import config


class Settings:
    max_length = 131072
    max_new_tokens = 8192
    generation_config = {
        "temperature": 0.7,
        "top_p": 0.9,
        "repetition_penalty": 1.05,
        "do_sample": True,
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

        # Build prompt
        system_prompt = (
            f"You are a {lang_name} dementia-care education specialist. Your task is to read a "
            f"caregiver-education video transcript and generate question-answer pairs that are "
            f"faithful to the video, easy to understand, practically useful, and emotionally "
            f"supportive for dementia caregivers. {lang_guard}"
        )

        prompt = f"""Read the following transcript carefully and generate the 20 most valuable question-answer pairs in {lang_name}. Every QA pair must satisfy ALL FOUR quality criteria:

        1. ALIGNMENT (accurate & grounded): Base every answer strictly on information stated in the transcript. Do not add outside knowledge, speculate, or exaggerate. If the transcript is unclear on a point, do not create a question about it.

        2. ACCESSIBILITY (clear & fluent): Write questions and answers in plain, conversational language a family caregiver with no medical background can understand. Define any clinical term the transcript uses. Keep answers focused — detailed enough to be complete, short enough to stay readable.

        3. EDUCATIONAL VALUE (actionable & useful): Prefer questions whose answers tell the caregiver what to do, why it works, or what to expect — concrete strategies, steps, and observable signs — over abstract facts. Avoid trivial or overly narrow questions.

        4. MENTAL HEALTH VALUE (supportive & empathetic): Use a warm, non-judgmental tone that normalizes the caregiver's struggles. Where the transcript offers reassurance, coping strategies, or empathy-building insight into the person with dementia's experience, capture it. Never phrase answers in a blaming or alarming way.

        Coverage rules:
        - Draw questions from across the ENTIRE transcript, not just one section.
        - Ensure the pairs do not overlap significantly in content.
        - Aim for all: A good QA pair should coverage all 4 of the criteria.

        Before finalizing, silently verify each pair against all four criteria and replace any pair that fails one.

        Strictly format your response as a list of question-answer pairs, with each pair clearly marked ("Question 1:", "Answer 1:" on separate lines). Output only the structured pairs — no preamble, no closing remarks.

        Transcript:
        {transcript}"""

        return system_prompt, prompt
