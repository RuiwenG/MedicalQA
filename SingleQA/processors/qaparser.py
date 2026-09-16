import re

# A clock time inside a model-written range: "4:15", and also "4: 15". The
# model sometimes leaves a space after the colon when the minutes digit runs
# into the "Timestamp N:" label it has just written. Seconds are validated
# ([0-5]\d) so "2:60" is rejected rather than silently misread; hours are not
# handled because the corpus tops out at 23-minute videos.
_TIME = r"(\d{1,3}):\s?([0-5]\d)"
TIME_RANGE = re.compile(
    _TIME + r"\s*(?:-|–|—|to|until|through)\s*" + _TIME, re.IGNORECASE
)
TIME_START = re.compile(_TIME)
# A pair number introducing the range — but only ever matched against what is
# left in FRONT of the range, never against the range itself.
PAIR_INDEX = re.compile(r"^\s*(\d{1,3})\s*[:.)]\s*(?:\*{1,3}|_+)?\s*$")


def parse_timestamp_line(rest):
    """Pull a pair number and a second range out of the text after "Timestamp".

    The range is located FIRST and the pair number is only whatever is left in
    front of it, so a start time can never be eaten as a number. Reading left
    to right, "Timestamp 4:07-5:09" looks like pair 4 followed by an
    unparsable "07-5:09"; it is really 4:07-5:09 with no pair number at all,
    and the model writes it both ways in the same response.

    Returns ``(number, display, start_sec, end_sec)`` with ``number`` and
    ``end_sec`` possibly None, or None when the line holds no clock time.
    """
    match = TIME_RANGE.search(rest)
    if match:
        a, b, c, d = (int(g) for g in match.groups())
        start, end = a * 60 + b, c * 60 + d
        if end <= start:  # "8:15-8:15", or a range the model wrote backwards
            end = None
    else:
        # Open-ended ranges like "7:31-End" still pin down where to start.
        match = TIME_START.search(rest)
        if not match:
            return None
        a, b = (int(g) for g in match.groups())
        start, end = a * 60 + b, None
    number = PAIR_INDEX.match(rest[: match.start()])
    return (
        int(number.group(1)) if number else None,
        rest[match.start() :].strip().strip("*_ "),
        start,
        end,
    )


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
        # Matches a "Timestamp 3: 4:15-6:40" line and hands everything after
        # the word to parse_timestamp_line, which separates the pair number
        # from the range. Nothing is stripped here: the old pattern captured
        # the number itself and so ate the leading minutes digit whenever the
        # model omitted the number.
        timestamp_pattern = re.compile(
            r"^\s*(?:[-*>]\s*)?(?:\*{1,3}|_+)?\s*timestamps?\b\s*[:.]?\s*(.*)",
            re.IGNORECASE,
        )

        # Collect timestamp lines up front so placement (before or after the
        # answer) doesn't matter, then attach them to pairs by number below.
        ts_by_num = {}
        ts_in_order = []
        for raw in lines:
            m = timestamp_pattern.match(raw.strip())
            if not m:
                continue
            parsed = parse_timestamp_line(m.group(1))
            if not parsed:
                continue
            number, display, start, end = parsed
            entry = {"timestamp": display, "start": start, "end": end}
            ts_in_order.append(entry)
            if number is not None:
                ts_by_num[number] = entry

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
        numbered = len(ts_by_num) == len(ts_in_order) and len(ts_by_num) >= len(qa_pairs)
        for k, pair in enumerate(qa_pairs, start=1):
            if numbered:
                entry = ts_by_num.get(k)
            elif k <= len(ts_in_order):
                entry = ts_in_order[k - 1]
            else:
                entry = None
            pair["timestamp"] = entry["timestamp"] if entry else None
            # Also expose numeric seconds under the field names DualAgent and
            # MultiAgent already use, so eval/web/extract_qa_data.py picks the
            # timestamps up as t/te without any changes. An open-ended range
            # contributes a start only; the site then plays on from there.
            if entry:
                pair["time_start_sec"] = entry["start"]
                if entry["end"] is not None:
                    pair["time_end_sec"] = entry["end"]

        # Validate count against the k requested in the prompt (when known)
        if expected is not None and len(qa_pairs) < expected:
            print(f"⚠️ Warning: Only found {len(qa_pairs)} of {expected} expected QA pairs")

        return qa_pairs
