import re
from typing import List
from common_utils import config


class Inquisitor:
    def __init__(self, model_handler):
        self.model_handler = model_handler

    def run_agent2_inquisitor(self, segment_content: str) -> List[str]:
        """Agent 2 (Inquisitor): Brainstorms questions for a single text segment."""

        print("Running Agent 2 (inquisitor) to generate questions for each segment...")
        lang_code = config.LANGUAGE_CODE  # e.g., 'zh'
        lang_name = config.LANGUAGE_NAME
        # Strong instruction to keep output in the target language
        lang_guard = (
            "IMPORTANT: Reply strictly in {name}. "
            "Do not use English unless an English word appears verbatim in the input."
        ).format(name=lang_name)

        system_prompt = (
            f"You are a {lang_name} dementia-care education specialist. You read transcript segments "
            f"from caregiver-education videos and generate questions a family caregiver would ask. "
            f"{lang_guard}"
        )

        prompt = f"""Based on the following text segment, generate a numbered list of potential questions in {lang_name}. Good questions are:
        - Alignment: only ask what this text actually addresses.
        - Educational Value: the answer would tell a caregiver what to do, why it works, or what to expect.
        - Supportive: if the text offers reassurance, coping strategies, or insight into the experience of the person with dementia, include questions that surface it.
        - Easy to Understand: phrased in plain conversational language that an 8th grade student with no medical background can understand.
        Avoid duplicate questions with similar meanings, and avoid trivial or overly narrow questions. Format your output as a simple numbered list only.
        Text Segment:
        {segment_content}"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        response = self.model_handler.chat(messages, max_tokens=2048)

        questions = [
            line.strip() for line in re.split(r"\d+\.\s*", response) if line.strip()
        ]
        return questions
