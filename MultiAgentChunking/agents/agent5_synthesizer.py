from common_utils import config


class Synthesizer:
    def __init__(self, model_handler):
        self.model_handler = model_handler

    def run_agent5_synthesizer(self, question: str, context: str) -> str:
        """Agent 5 (Synthesizer): Answers a single question based on its context."""
        lang_code = config.LANGUAGE_CODE  # e.g., 'zh'
        lang_name = config.LANGUAGE_NAME
        # Strong instruction to keep output in the target language
        lang_guard = (
            "IMPORTANT: Reply strictly in {name}. "
            "Do not use English unless an English word appears verbatim in the input."
        ).format(name=lang_name)

        system_prompt = (
            f"You are a {lang_name} dementia-care educator answering questions for family caregivers, "
            f"using only the provided video transcript context, and answer in language that can be "
            f"understood by an 8th grade student."
        )

        prompt = f"""**Context:**
        {context}

        ---

        **Question:**
        {question}

        ---

        Write an answer that meets ALL of these requirements:

        1. Alignment (accurate & grounded): Base every answer strictly on information stated in the transcript. Do not add outside knowledge, speculate, or exaggerate. If the transcript is unclear on a point, do not create a question about it.
        2.Easy to Understand (clear & fluent): Write answers in plain, conversational language that an 8th grade student with no medical background can understand. Define any clinical term the transcript uses. Keep answers focused — detailed enough to be complete, short enough to stay readable.
        3. Educational Value (actionable & useful): Prefer questions whose answers tell the caregiver what to do, why it works, or what to expect — concrete strategies, steps, and observable signs — over abstract facts. Do not be verbose.
        4. SUPPORTIVE: Write in a warm, non-judgmental tone. Preserve any reassurance or empathy-building insight the context offers. Never blaming or alarming.

        Output only the answer text — no headings, no restating the question."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        answer = self.model_handler.chat(messages, max_tokens=1024)
        return answer.strip()
