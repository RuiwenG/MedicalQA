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
            f"using only the provided video transcript context. {lang_guard}"
        )

        prompt = f"""**Context:**
        {context}

        ---

        **Question:**
        {question}

        ---

        Write an answer that meets ALL of these requirements:

        1. GROUNDED: use only information in the context above. Do not add outside knowledge or speculate. If the context only partially answers the question, answer the part it covers.
        2. CLEAR: plain, conversational {lang_name} a caregiver with no medical background can understand; define any clinical term the context uses. Complete but concise.
        3. ACTIONABLE: where the context provides them, include the concrete steps, strategies, or signs the caregiver should know.
        4. SUPPORTIVE: warm, non-judgmental tone; preserve any reassurance or empathy-building insight the context offers. Never blaming or alarming.

        Output only the answer text — no headings, no restating the question."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        answer = self.model_handler.chat(messages, max_tokens=1024)
        return answer.strip()
