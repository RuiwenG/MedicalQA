from typing import Dict, List
from agents.base_agent import QwenModelHandler
from common_utils import config

class AnswerSynthesizer:
    
    def __init__(self, model_handler):
        self.model_handler = model_handler

    def run_agent2_answer_synthesis(self, question: str, context_chunks: List[str]) -> Dict:
        """Agent 2: Use retrieved chunks to answer a single question."""

        # Language settings
        lang_code = config.LANGUAGE_CODE   # e.g., 'zh'
        lang_name = config.LANGUAGE_NAME
        # Strong instruction to keep output in the target language
        lang_guard = (
            "IMPORTANT: Reply strictly in {name}. "
            "Do not use English unless an English word appears verbatim in the input."
        ).format(name=lang_name)

        system_prompt = f"You are an expert {lang_name} content analyst. Your task is to read a long transcript and provide answers in {lang_name} that cover the most important educational and mentorship value for the mentioned questions. {lang_guard}"
        
        # Join the chunks to form the context
        context = "\n\n---\n\n".join(context_chunks)
        
        prompt = f"""Please answer the following question in {lang_name} using ONLY the information from the context provided below. {lang_guard}

        Question:
        {question}

        Context:
        {context}

        Answer:"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        answer = self.model_handler.chat(messages, max_tokens=1024).strip()


        # Basic validation to ensure the model didn't refuse to answer
        if "not available in the provided context" in answer.lower():
            return None

        return {"question": question, "answer": answer}