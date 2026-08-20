import re


class Scorer:
    def __init__(self, model_handler):
        self.model_handler = model_handler

    def run_agent3_scorer_single(self, question: str, context_hint: str) -> float:
        """
        Score a single question 1-10 across three quality dimensions
        (alignment, clarity, educational value).
        Returns the average of the three sub-scores.
        Keep it short so we can call it per-question (reduces cross-item bias).
        """
        print("🧠 Running Agent 3 (Scorer) to rating every questions...")

        system_prompt = (
            "You are an expert evaluator of dementia caregiver-education QA content. "
            "Return ONLY three numbers in the format: A=n C=n E=n"
        )
        prompt = f"""Rate this question on three dimensions, each 1-10:
        A (Alignment): can it be answered accurately from the source topic, without outside knowledge?
        C (Clear): is it clear and understandable to a non-medical family caregiver?
        E (Educational value): would the answer give actionable, useful guidance?

        Question: {question}
        Source topic: {context_hint}

        Return only: A=n C=n E=n"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        response = self.model_handler.chat(messages, max_tokens=30)

        # Parse three sub-scores (A=alignment, C=clear, E=educational)
        subs = re.findall(r"[ACE]\s*=\s*(\d+)", response)
        if len(subs) == 3:
            scores = [min(10.0, max(1.0, float(s))) for s in subs]
            return sum(scores) / len(scores)
        # Fallback: any single number found
        m = re.search(r"(\d+)", response)
        return min(10.0, max(1.0, float(m.group(1)))) if m else 5.0
