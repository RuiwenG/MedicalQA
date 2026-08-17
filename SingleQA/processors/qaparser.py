import re


class QAParser:
    def parse_qa_pairs(self, text, expected=None):
        """Robust QA pair parsing with validation.

        expected: the k requested in the prompt (word_count / 250). Parsing is
        capped there and a warning is printed if fewer come back. None means
        no cap (parse everything found).
        """
        qa_pairs = []
        lines = text.split("\n")
        i = 0

        # Regex to match "Question", "Q:", "1. Question 1:", "Q1:" etc.
        question_pattern = re.compile(
            r"^\s*(?:[-*>]\s*)?(?:\d+\s*:|(?:\d+\.\s*)?(?:\*{1,3}|_+)?\s*(?:question\s*\d*|q\d*)\s*(?:\*{1,3}|_+)?\s*:?)",
            re.IGNORECASE,
        )
        answer_pattern = re.compile(r"^(?:answer|a\d*)[:\s]", re.IGNORECASE)
        # Matches "Timestamp 3: 4:15-6:40" (number and value captured); the
        # number ties the range to its pair even if the model reorders lines.
        timestamp_pattern = re.compile(
            r"^\s*(?:[-*>]\s*)?(?:\*{1,3}|_+)?\s*timestamp\s*(\d*)\s*(?:\*{1,3}|_+)?\s*:\s*(.*)",
            re.IGNORECASE,
        )

        # Collect timestamp lines up front so placement (before or after the
        # answer) doesn't matter, then attach them to pairs by number below.
        ts_by_num = {}
        ts_in_order = []
        for raw in lines:
            m = timestamp_pattern.match(raw.strip())
            if m and m.group(2).strip():
                value = m.group(2).strip().strip("*_ ")
                ts_in_order.append(value)
                if m.group(1):
                    ts_by_num[int(m.group(1))] = value

        while i < len(lines) and (expected is None or len(qa_pairs) < expected):
            line = lines[i].strip()
            question = None
            print(line)

            # Match question line
            if question_pattern.match(line):
                # Extract question text after ":" if present
                question = re.sub(question_pattern, "", line, count=1).strip()

            # Look for answer
            answer_lines = []
            j = i + 1
            while j < len(lines):
                next_line = lines[j].strip()
                if answer_pattern.match(next_line):
                    # if next_line.lower().startswith(("answer", "a:")):
                    if ":" in next_line:
                        answer_lines.append(next_line.split(":", 1)[1].strip())
                    else:
                        answer_lines.append(
                            re.sub(answer_pattern, "", next_line, count=1).strip()
                        )
                        # answer_lines.append(next_line[next_line.find(" ")+1:].strip())
                    j += 1
                    # Continue until next question or end
                    while j < len(lines) and not question_pattern.match(
                        lines[j].strip()
                    ):
                        # lines[j].strip().lower().startswith(("question", "q:")):
                        # Skip empty lines and timestamp lines (captured above)
                        if lines[j].strip() and not timestamp_pattern.match(
                            lines[j].strip()
                        ):
                            answer_lines.append(lines[j].strip())
                        j += 1
                    break
                j += 1

            if answer_lines:
                answer = " ".join(answer_lines)
                qa_pairs.append({"question": question, "answer": answer})
                i = j - 1  # Skip processed lines
            i += 1

        # Attach source-section timestamps. Explicit numbering ("Timestamp 3:")
        # is only trustworthy when the numbers are actually distinct — the model
        # often writes "Timestamp 1:" above every pair, in which case position
        # in the response is the reliable signal.
        range_pattern = re.compile(r"(\d{1,3}):(\d{2})\s*(?:-|–|—|to)\s*(\d{1,3}):(\d{2})")
        numbered = len(ts_by_num) == len(ts_in_order) and len(ts_by_num) >= len(qa_pairs)
        for k, pair in enumerate(qa_pairs, start=1):
            if numbered:
                pair["timestamp"] = ts_by_num.get(k)
            elif k <= len(ts_in_order):
                pair["timestamp"] = ts_in_order[k - 1]
            else:
                pair["timestamp"] = None
            # Also expose numeric seconds under the field names DualAgent and
            # MultiAgent already use, so eval/web/extract_qa_data.py picks the
            # timestamps up as t/te without any changes.
            m = range_pattern.search(pair["timestamp"] or "")
            if m:
                a, b, c, d = (int(g) for g in m.groups())
                pair["time_start_sec"] = a * 60 + b
                pair["time_end_sec"] = c * 60 + d

        # Validate count against the k requested in the prompt (when known)
        if expected is not None and len(qa_pairs) < expected:
            print(f"⚠️ Warning: Only found {len(qa_pairs)} of {expected} expected QA pairs")

        return qa_pairs
