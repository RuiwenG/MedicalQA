import torch
from typing import Dict
from common_utils import config

class Justifier:
    def __init__(self, model_handler):
        self.model_handler = model_handler
    
    def run_agent4_justifier(self, question_item: Dict) -> str:
        """Agent 4 (Justifier): Provides a reason for a single question's status."""
        
        lang_code = config.LANGUAGE_CODE   # e.g., 'zh'
        lang_name = config.LANGUAGE_NAME
        # Strong instruction to keep output in the target language
        lang_guard = (
            "IMPORTANT: Reply strictly in {name}. "
            "Do not use English unless an English word appears verbatim in the input."
        ).format(name=lang_name)
        
        system_prompt = f"You are a {lang_name} content analyst. Your task is to provide a clear and concise justification for a question's selection status in {lang_name}. {lang_guard}"
        
        prompt = f"""You will be given a question, its source topic, and its "Selected" or "Rejected" status. Explain *why* that status makes sense with respect to four quality criteria: alignment with the video, accessibility to caregivers, educational value, and mental health value.

        **Question:** {question_item['question']}
        **Source Topic:** {question_item['source_segment']['topic']}
        **Selection Status:** {question_item['status']}

        Provide one concise reason in {lang_name}. Use the following as **sample reasons, but you are not limited to them**:
        - **Good `Selected` reasons:**
            - "Gives caregivers a concrete, actionable strategy grounded in the video."
            - "Surfaces the video's reassurance for struggling caregivers — high mental health value."
            - "Clear, jargon-free question addressing a key concept from the source topic."
        - **Good `Rejected` reasons:**
            - "Cannot be answered from the source topic without outside knowledge (poor alignment)."
            - "Too basic to offer actionable guidance."
            - "Already covered by another, more specific selected question."
            - "Received a low quality score from the evaluator."

        **Reason:**"""
        messages = [{"role": "system", "content": system_prompt}, 
                    {"role": "user", "content": prompt}]

        text = self.model_handler.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True, enable_thinking=False,)

        inputs = self.model_handler.tokenizer(
            text, return_tensors="pt", truncation=True, max_length=131072).to(self.model_handler.model.device)

        with torch.no_grad():
            outputs = self.model_handler.model.generate(
                **inputs, max_new_tokens=128, pad_token_id=self.model_handler.tokenizer.eos_token_id)

        reason = self.model_handler.tokenizer.decode(
            outputs[0][inputs.input_ids.shape[1]:], skip_special_tokens=True)

        return reason.strip()