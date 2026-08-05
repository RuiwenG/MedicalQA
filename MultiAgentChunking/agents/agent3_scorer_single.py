import re
import torch


class Scorer:
    def __init__(self, model_handler):
        self.model_handler = model_handler

    def run_agent3_scorer_single(self, question: str, context_hint: str) -> float:
        """
        Score a single question 1-10 across four quality dimensions
        (alignment, accessibility, educational value, mental health value).
        Returns the average of the four sub-scores.
        Keep it short so we can call it per-question (reduces cross-item bias).
        """
        print("🧠 Running Agent 3 (Scorer) to rating every questions...")

        system_prompt = (
            "You are an expert evaluator of dementia caregiver-education QA content. "
            "Return ONLY four numbers in the format: A=n C=n E=n M=n"
        )
        prompt = f"""Rate this question on four dimensions, each 1-10:
        A (Alignment): can it be answered accurately from the source topic, without outside knowledge?
        C (Accessibility): is it clear and understandable to a non-medical family caregiver?
        E (Educational value): would the answer give actionable, useful guidance?
        M (Mental health value): does it invite a supportive, empathy-building, or resilience-promoting answer?

        Question: {question}
        Source topic: {context_hint}

        Return only: A=n C=n E=n M=n"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        text = self.model_handler.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )

        inputs = self.model_handler.tokenizer(
            text, return_tensors="pt", truncation=True, max_length=131072
        ).to(self.model_handler.model.device)

        with torch.no_grad():
            outputs = self.model_handler.model.generate(
                **inputs,
                max_new_tokens=30,
                pad_token_id=self.model_handler.tokenizer.eos_token_id,
            )

        response = self.model_handler.tokenizer.decode(
            outputs[0][inputs.input_ids.shape[1] :], skip_special_tokens=True
        )

        # Parse four sub-scores (A=alignment, C=accessibility, E=educational, M=mental health)
        subs = re.findall(r"[ACEM]\s*=\s*(\d+)", response)
        if len(subs) == 4:
            scores = [min(10.0, max(1.0, float(s))) for s in subs]
            return sum(scores) / len(scores)
        # Fallback: any single number found
        m = re.search(r"(\d+)", response)
        return min(10.0, max(1.0, float(m.group(1)))) if m else 5.0
