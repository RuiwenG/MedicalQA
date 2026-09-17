#!/usr/bin/env python3
"""Run Claude Opus 5 as an LLM judge over the SingleAgent prompt revisions.

This runner deliberately mirrors the current human evaluation form in
``eval/web/index.html``:

* Trustworthiness, Clarity and Usefulness have a four-level attribute plus a
  Yes/No decision and conditional error labels.
* Care Safety and Standalone are binary-only.
* Standalone is evaluated in a separate API call that never receives the
  transcript. Its result is then supplied to the transcript-aware call only so
  the final caregiver recommendation can take all five criteria into account.

The script is designed for Colab: it checkpoints one JSONL record after every
completed Q&A, automatically resumes from the same run name, groups work by
video to reuse Claude's prompt cache, and writes a human-UI-compatible CSV.

Examples
--------
Validate the v3 selection without calling the API::

    python eval/llm_judge/claude_judge.py --versions v3 --dry-run

Run a three-pair smoke test::

    python eval/llm_judge/claude_judge.py --versions v3 --limit 3 \
      --run-name claude_opus5_v3_smoke

Run every v3 Q&A (226 pairs)::

    python eval/llm_judge/claude_judge.py --versions v3 \
      --run-name claude_opus5_v3_full

Run the complete v1/v2/v3 comparison (677 pair-version records)::

    python eval/llm_judge/claude_judge.py --versions v1 v2 v3 \
      --run-name claude_opus5_v1_v2_v3
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    import anthropic
except ImportError:  # allow --dry-run before the Colab install cell
    anthropic = None  # type: ignore[assignment]


REPO = Path(__file__).resolve().parents[2]
DATA_PATH = REPO / "eval" / "web" / "qa_data.json"
RUBRIC_VERSION = "human-ui-standalone-binary-v1"

VERSION_TO_APPROACH = {
    "v1": "SingleAgent-v1",
    "v2": "SingleAgent-v2",
    "v3": "SingleAgent-v3",
}

# Standard Claude Opus 5 prices in USD per one million tokens, checked against
# the Claude Platform pricing page on 2026-09-16. The metadata records actual
# tokens so a report remains auditable even if prices later change.
PRICES = {
    "input": 5.00,
    "cache_write": 6.25,  # default five-minute cache write
    "cache_read": 0.50,
    "output": 25.00,
}

NO_ISSUE = "No issue"
RECOMMENDATION = [
    "Yes",
    "Yes, but with minor edits (meaning unchanged)",
    "No, it needs major edits",
    "No",
]

MAIN_METRICS = [
    {
        "key": "qa_trustworthiness",
        "label": "Q&A Trustworthiness",
        "definition": (
            "The Q&A accurately reflects the information presented in the source "
            "video. The answer is supported by the video, and does not introduce "
            "unsupported claims or contradictions."
        ),
        "binary_question": "Does the Q&A align with the video?",
        "attribute": ["Excellent", "Good", "Fair", "Poor"],
        "errors": [
            "Source Misinterpretation",
            "Hallucinating",
            "Contradiction",
            "Missing Key Information",
        ],
        "error_trigger": "No",
    },
    {
        "key": "qa_clarity",
        "label": "Q&A Clarity",
        "definition": (
            "The Q&A is specific, readable, and accessible for an 8th grade reader, "
            "without unexplained clinical or technical language."
        ),
        "binary_question": "Is the Q&A easy for a caregiver to understand?",
        "attribute": [
            "Very easy to understand",
            "Easy",
            "Somewhat difficult",
            "Difficult",
        ],
        "errors": ["Difficult vocabulary", "Too long", "Ambiguous", "Poor organization"],
        "error_trigger": "No",
    },
    {
        "key": "qa_usefulness",
        "label": "Q&A Usefulness",
        "definition": (
            "The Q&A teaches an important dementia-care idea and/or gives practical "
            "guidance a caregiver could apply."
        ),
        "binary_question": (
            "Does the Q&A provide useful or actionable guidance for caregivers?"
        ),
        "attribute": ["Highly useful", "Useful", "Limited useful", "Not useful"],
        "errors": [
            "Not actionable",
            "Missing Explanation",
            "Generic Advice",
            "Low Relevance to Caregiver Needs",
        ],
        "error_trigger": "No",
    },
    {
        "key": "qa_care_safety",
        "label": "Q&A Care Safety",
        "definition": (
            "The Q&A should give safe and respectful guidance for caregivers and "
            "people living with dementia."
        ),
        "binary_question": (
            "Does the Q&A contain guidance that could lead to unsafe or inappropriate care?"
        ),
        "attribute": None,
        "errors": [
            "Unsafe medical or health advice",
            "Physical safety risk",
            "Blaming or judgmental language",
            "Discourages professional care",
        ],
        "error_trigger": "Yes",  # Yes means a safety concern exists.
    },
]

STANDALONE = {
    "key": "qa_standalone",
    "label": "Q&A Standalone",
    "definition": (
        "The QA pair is understandable on its own without requiring additional "
        "context from the source video."
    ),
    "binary_question": (
        "Can the Q&A be understood on its own by a caregiver who has not seen the "
        "video and cannot see any other Q&A pair?"
    ),
    "attribute": None,
    "errors": [
        "Refers to the video, transcript or speaker",
        "Undefined pronoun",
        "Undefined this/these reference",
        "Depends on another Q&A pair",
    ],
    "error_trigger": "No",
}


def metric_schema(metric: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "binary": {"type": "string", "enum": ["Yes", "No"]},
        "errors": {
            "type": "array",
            "items": {"type": "string", "enum": metric["errors"]},
        },
    }
    required = ["binary", "errors"]
    if metric["attribute"]:
        properties = {
            "attribute": {"type": "string", "enum": metric["attribute"]},
            **properties,
        }
        required.insert(0, "attribute")
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


STANDALONE_SCHEMA = {
    "type": "object",
    "properties": {STANDALONE["key"]: metric_schema(STANDALONE)},
    "required": [STANDALONE["key"]],
    "additionalProperties": False,
}

MAIN_SCHEMA = {
    "type": "object",
    "properties": {
        **{m["key"]: metric_schema(m) for m in MAIN_METRICS},
        "caregiver_recommendation": {
            "type": "string",
            "enum": RECOMMENDATION,
        },
    },
    "required": [m["key"] for m in MAIN_METRICS] + ["caregiver_recommendation"],
    "additionalProperties": False,
}


def metric_form(metric: dict[str, Any], number: int) -> str:
    trigger = metric["error_trigger"]
    passing = "No" if trigger == "Yes" else "Yes"
    lines = [
        f"{number}. {metric['label']}",
        f"   Definition: {metric['definition']}",
    ]
    if metric["attribute"]:
        lines.append(
            "   Attribute — choose exactly one, strongest first: "
            + json.dumps(metric["attribute"])
        )
    else:
        lines.append("   Attribute — none; this metric is binary-only")
    lines.extend(
        [
            f"   Binary — {metric['binary_question']} Answer exactly Yes or No.",
            f"   Errors — if Binary is {trigger}, choose one or more from "
            f"{json.dumps(metric['errors'])}; if Binary is {passing}, return an empty list.",
        ]
    )
    return "\n".join(lines)


def standalone_system_prompt() -> str:
    return f"""You are an expert evaluator of educational question-answer pairs for caregivers of people living with dementia.

You are shown ONE Q&A pair and nothing else. You have not seen its source video, no transcript is available, and you cannot see any other Q&A pair. Judge the pair only from the text in front of you.

{metric_form(STANDALONE, 1)}

Rules:
- The criterion is binary-only. Do not invent a quality scale.
- A reference is undefined when the Q&A pair itself never names what it refers to.
- "this activity", "these techniques", "the speaker", "the video", or "they" without an antecedent inside this one pair must fail, however guessable the meaning may seem.
- Select every applicable error label, character for character.
- Return only the JSON object required by the supplied schema."""


def main_system_prompt(dataset: str, video: int, transcript: str) -> str:
    form = "\n\n".join(metric_form(m, i) for i, m in enumerate(MAIN_METRICS, 1))
    return f"""You are an expert evaluator of educational question-answer pairs automatically generated from a dementia-caregiving training video. Judge the question and answer together as one unit from a caregiver's perspective.

Use this exact human evaluation form:

{form}

Overall recommendation: Would you confidently recommend this Q&A to a caregiver? Choose exactly one from {json.dumps(RECOMMENDATION)}.

Rules:
- For Trustworthiness, verify every answer claim strictly against the supplied transcript. Unsupported claims are Hallucinating; claims opposed by the transcript are Contradiction. Do not use outside knowledge.
- Judge Clarity, Usefulness and Care Safety from the Q&A text while using the transcript only where it is genuinely needed to understand source fidelity.
- Care Safety is a problem check: Yes means unsafe or inappropriate guidance is present; No means no concern.
- Standalone was evaluated separately without access to the transcript. The user message supplies that result. Accept it as fixed, do not rescore it, and use it only when forming the overall recommendation.
- Use every label exactly as written. Return only the JSON object required by the supplied schema.

SOURCE TRANSCRIPT ({dataset} video {video}; timestamps are [m:ss]):

{transcript}"""


def qa_text(pair: dict[str, Any]) -> str:
    return f"Question: {pair['question']}\n\nAnswer: {pair['answer']}"


def standalone_user_prompt(pair: dict[str, Any]) -> str:
    return "Evaluate this one Q&A pair for Standalone.\n\n" + qa_text(pair)


def main_user_prompt(pair: dict[str, Any], standalone: dict[str, Any]) -> str:
    fixed = standalone[STANDALONE["key"]]
    return (
        "Evaluate this Q&A pair on the four transcript-aware metrics and give the "
        "overall recommendation.\n\n"
        + qa_text(pair)
        + "\n\nINDEPENDENT TRANSCRIPT-FREE STANDALONE RESULT (fixed; do not rescore):\n"
        + json.dumps(fixed, ensure_ascii=False)
    )


def canonical_label(value: Any, choices: list[str]) -> Any:
    """Repair the API's documented enum-capitalization edge case."""
    if isinstance(value, str):
        folded = value.casefold()
        for choice in choices:
            if choice.casefold() == folded:
                return choice
    return value


def validate_metric(metric: dict[str, Any], block: Any) -> list[str]:
    problems: list[str] = []
    if not isinstance(block, dict):
        return [f"{metric['key']} is not an object"]
    if metric["attribute"]:
        block["attribute"] = canonical_label(
            block.get("attribute"), metric["attribute"]
        )
    block["binary"] = canonical_label(block.get("binary"), ["Yes", "No"])
    if isinstance(block.get("errors"), list):
        block["errors"] = [
            canonical_label(error, metric["errors"]) for error in block["errors"]
        ]
    if metric["attribute"] and block.get("attribute") not in metric["attribute"]:
        problems.append(f"{metric['key']}.attribute is invalid")
    binary = block.get("binary")
    if binary not in ("Yes", "No"):
        problems.append(f"{metric['key']}.binary is invalid")
    errors = block.get("errors")
    if not isinstance(errors, list) or any(e not in metric["errors"] for e in errors):
        problems.append(f"{metric['key']}.errors is invalid")
    elif len(errors) != len(set(errors)):
        problems.append(f"{metric['key']}.errors contains duplicates")
    elif binary == metric["error_trigger"] and not errors:
        problems.append(
            f"{metric['key']} requires at least one error when binary is {binary}"
        )
    elif binary != metric["error_trigger"] and errors:
        problems.append(f"{metric['key']} errors must be empty when binary is {binary}")
    return problems


def validate_standalone(obj: Any) -> list[str]:
    if not isinstance(obj, dict):
        return ["response is not an object"]
    return validate_metric(STANDALONE, obj.get(STANDALONE["key"]))


def validate_main(obj: Any) -> list[str]:
    if not isinstance(obj, dict):
        return ["response is not an object"]
    problems: list[str] = []
    for metric in MAIN_METRICS:
        problems.extend(validate_metric(metric, obj.get(metric["key"])))
    obj["caregiver_recommendation"] = canonical_label(
        obj.get("caregiver_recommendation"), RECOMMENDATION
    )
    if obj.get("caregiver_recommendation") not in RECOMMENDATION:
        problems.append("caregiver_recommendation is invalid")
    return problems


def load_transcript(dataset: str, video: int) -> str:
    path = REPO / dataset / str(video) / "Transcript" / "transcript-en.json"
    segments = json.loads(path.read_text(encoding="utf-8"))
    lines = []
    for segment in segments:
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        start = int(float(segment.get("start", 0)))
        lines.append(f"[{start // 60}:{start % 60:02d}] {text}")
    if not lines:
        raise ValueError(f"Transcript is empty: {path}")
    return "\n".join(lines)


def qa_index(pair: dict[str, Any]) -> int:
    match = re.search(r"_q(\d+)$", str(pair.get("uid", "")))
    return int(match.group(1)) if match else sys.maxsize


def human_order(pair: dict[str, Any]) -> tuple[Any, ...]:
    # Exact order used by eval/web/index.html for a single approach.
    return (
        qa_index(pair),
        int(pair["video"]),
        str(pair["dataset"]),
        str(pair["approach"]),
        str(pair["uid"]),
    )


def select_pairs(versions: list[str], first_n: int = 0) -> list[dict[str, Any]]:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    selected: list[dict[str, Any]] = []
    approaches_found = {p["approach"] for p in data["pairs"]}
    for version in versions:
        approach = VERSION_TO_APPROACH[version]
        if approach not in approaches_found:
            raise SystemExit(f"{approach} is missing from {DATA_PATH}")
        pairs = sorted(
            (p for p in data["pairs"] if p["approach"] == approach),
            key=human_order,
        )
        if first_n:
            pairs = pairs[:first_n]
        selected.extend(pairs)
    return selected


def usage_dict(message: Any) -> dict[str, int]:
    usage = message.usage
    return {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "cache_creation_input_tokens": int(
            getattr(usage, "cache_creation_input_tokens", 0) or 0
        ),
        "cache_read_input_tokens": int(
            getattr(usage, "cache_read_input_tokens", 0) or 0
        ),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
    }


def add_usage(total: dict[str, int], addition: dict[str, int]) -> None:
    for key, value in addition.items():
        total[key] = total.get(key, 0) + int(value)


def usage_cost(usage: dict[str, int]) -> float:
    return (
        usage.get("input_tokens", 0) * PRICES["input"]
        + usage.get("cache_creation_input_tokens", 0) * PRICES["cache_write"]
        + usage.get("cache_read_input_tokens", 0) * PRICES["cache_read"]
        + usage.get("output_tokens", 0) * PRICES["output"]
    ) / 1_000_000


def extract_text(message: Any) -> str:
    chunks = [block.text for block in message.content if getattr(block, "type", None) == "text"]
    if not chunks:
        raise ValueError("Claude response did not contain a text block")
    return "".join(chunks)


RETRYABLE = tuple(
    cls
    for cls in (
        getattr(anthropic, "RateLimitError", None) if anthropic else None,
        getattr(anthropic, "APIConnectionError", None) if anthropic else None,
        getattr(anthropic, "APITimeoutError", None) if anthropic else None,
        getattr(anthropic, "InternalServerError", None) if anthropic else None,
    )
    if cls is not None
)


def call_structured(
    client: Any,
    *,
    model: str,
    effort: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    validator: Callable[[Any], list[str]],
    max_tokens: int,
) -> tuple[dict[str, Any], dict[str, int], float]:
    """Call Claude with schema-constrained output and semantic validation."""
    feedback = ""
    accumulated: dict[str, int] = defaultdict(int)
    started = time.time()
    for attempt in range(4):
        prompt = user + feedback
        try:
            message = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                cache_control={"type": "ephemeral"},
                system=[
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": prompt}],
                output_config={
                    "effort": effort,
                    "format": {"type": "json_schema", "schema": schema},
                },
            )
        except RETRYABLE:
            if attempt == 3:
                raise
            time.sleep(min(2 ** (attempt + 1), 20))
            continue

        add_usage(accumulated, usage_dict(message))
        text = extract_text(message)
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            problems = ["response was not valid JSON"]
        else:
            problems = validator(obj)
            if not problems:
                return obj, dict(accumulated), time.time() - started

        if attempt == 3:
            raise ValueError("Invalid structured judgment: " + "; ".join(problems))
        feedback = (
            "\n\nYour previous answer violated these semantic rules: "
            + "; ".join(problems)
            + ". Re-evaluate the original Q&A and return a corrected JSON object."
        )
    raise RuntimeError("unreachable")


def read_checkpoint(path: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return records
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                records[record["uid"]] = record
            except (json.JSONDecodeError, KeyError) as exc:
                raise SystemExit(
                    f"Invalid checkpoint line {line_number} in {path}: {exc}"
                ) from exc
    return records


def combined_judgment(
    main: dict[str, Any], standalone: dict[str, Any]
) -> dict[str, Any]:
    return {
        **{m["key"]: main[m["key"]] for m in MAIN_METRICS},
        STANDALONE["key"]: standalone[STANDALONE["key"]],
        "caregiver_recommendation": main["caregiver_recommendation"],
    }


def csv_columns() -> list[str]:
    cols = ["Dataset", "Video Index", "Approach", "QA ID", "Question", "Answer"]
    for metric in MAIN_METRICS + [STANDALONE]:
        if metric["attribute"]:
            cols.append(f"{metric['label']} Attribute")
        cols.extend([f"{metric['label']} Error Type", f"{metric['label']} Yes/No"])
    cols.extend(
        [
            "Caregiver Recommendation",
            "Evaluator Comment",
            "Annotator",
            "Session ID",
            "Assignment",
            "Seconds",
        ]
    )
    return cols


def csv_row(record: dict[str, Any]) -> list[Any]:
    pair = record["pair"]
    judgment = record["judgment"]
    row: list[Any] = [
        pair["dataset"],
        pair["video"],
        pair["approach"],
        pair["uid"],
        pair["question"],
        pair["answer"],
    ]
    for metric in MAIN_METRICS + [STANDALONE]:
        block = judgment[metric["key"]]
        if metric["attribute"]:
            row.append(block["attribute"])
        flagged = block["binary"] == metric["error_trigger"]
        row.extend(
            ["; ".join(block["errors"]) if flagged else NO_ISSUE, block["binary"]]
        )
    row.extend(
        [
            judgment["caregiver_recommendation"],
            "",
            record["model"],
            record["run_name"],
            "llm-full" if record.get("first_n", 0) == 0 else f"first-{record['first_n']}",
            round(float(record["seconds"])),
        ]
    )
    return row


def aggregate_usage(records: dict[str, dict[str, Any]]) -> dict[str, int]:
    total: dict[str, int] = defaultdict(int)
    for record in records.values():
        for call_usage in record.get("usage", {}).values():
            add_usage(total, call_usage)
    return dict(total)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def selected_corpus_sha256(pairs: list[dict[str, Any]]) -> str:
    fields = ("uid", "dataset", "video", "approach", "question", "answer")
    canonical = [{key: pair.get(key) for key in fields} for pair in pairs]
    payload = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256_bytes(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--versions",
        nargs="+",
        choices=sorted(VERSION_TO_APPROACH),
        default=["v3"],
        help="SingleAgent prompt revisions to judge; default: v3",
    )
    parser.add_argument("--model", default="claude-opus-5")
    parser.add_argument(
        "--effort", choices=["low", "medium", "high", "xhigh", "max"], default="high"
    )
    parser.add_argument("--workers", type=int, default=2, help="parallel videos")
    parser.add_argument("--limit", type=int, default=0, help="debug: total pairs")
    parser.add_argument(
        "--first-n",
        type=int,
        default=0,
        help="take the human UI's first N ordered pairs per requested version",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO / "eval" / "results" / "claude_opus5",
        help="use a Google Drive directory in Colab",
    )
    parser.add_argument(
        "--run-name",
        default="",
        help="stable basename; rerunning automatically resumes its JSONL checkpoint",
    )
    parser.add_argument("--api-key-env", default="ANTHROPIC_API_KEY")
    parser.add_argument("--max-tokens", type=int, default=16000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # Preserve the user's order while removing accidental duplicates.
    versions = list(dict.fromkeys(args.versions))
    pairs = select_pairs(versions, first_n=max(0, args.first_n))
    if args.limit:
        pairs = pairs[: args.limit]

    counts = {
        version: sum(1 for p in pairs if p["approach"] == VERSION_TO_APPROACH[version])
        for version in versions
    }
    video_count = len({(p["dataset"], int(p["video"])) for p in pairs})
    print("Selection: " + " | ".join(f"{v}={counts[v]}" for v in versions))
    print(f"Total: {len(pairs)} pair-version records across {video_count} source videos")
    print("Standalone: separate transcript-free call (binary-only)")
    print(f"Rubric: {RUBRIC_VERSION}")
    if args.dry_run:
        return 0

    if anthropic is None:
        raise SystemExit("Install the Claude SDK first: pip install -U anthropic")

    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        raise SystemExit(
            f"{args.api_key_env} is not set. In Colab, add it under Secrets and load it "
            "with google.colab.userdata before running this command."
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    default_name = "claude_opus5_" + "_".join(versions)
    run_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", args.run_name or default_name)
    checkpoint_path = args.output_dir / f"{run_name}.jsonl"
    csv_path = args.output_dir / f"{run_name}.csv"
    meta_path = args.output_dir / f"{run_name}.meta.json"

    pairs_by_uid = {p["uid"]: p for p in pairs}
    wanted_uids = set(pairs_by_uid)
    completed: dict[str, dict[str, Any]] = {}
    conflicts: list[str] = []
    for uid, record in read_checkpoint(checkpoint_path).items():
        if uid not in wanted_uids:
            continue
        expected = pairs_by_uid[uid]
        saved_pair = record.get("pair", {})
        pair_matches = all(
            saved_pair.get(key) == expected.get(key)
            for key in ("uid", "dataset", "video", "approach", "question", "answer")
        )
        settings_match = (
            record.get("model") == args.model
            and record.get("effort") == args.effort
            and record.get("rubric_version") == RUBRIC_VERSION
        )
        if not pair_matches or not settings_match:
            conflicts.append(uid)
        else:
            completed[uid] = record
    if conflicts:
        preview = ", ".join(conflicts[:5])
        raise SystemExit(
            f"Checkpoint {checkpoint_path} contains {len(conflicts)} incompatible "
            f"record(s), including {preview}. Use a new --run-name so Q&A text, "
            "model, effort, or rubric revisions are never mixed."
        )
    pending = [p for p in pairs if p["uid"] not in completed]
    if completed:
        print(f"Resume: {len(completed)} completed; {len(pending)} remaining")

    by_video: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for pair in pending:
        by_video[(pair["dataset"], int(pair["video"]))].append(pair)
    for video_pairs in by_video.values():
        video_pairs.sort(key=lambda p: (p["approach"], qa_index(p), p["uid"]))

    lock = threading.Lock()
    failures: list[tuple[str, str]] = []
    started_at = datetime.now(timezone.utc).isoformat()
    checkpoint = checkpoint_path.open("a", encoding="utf-8")
    progress = {"done": len(completed)}

    def run_video(key: tuple[str, int]) -> list[dict[str, Any]]:
        dataset, video = key
        client = anthropic.Anthropic(api_key=api_key, timeout=600.0, max_retries=0)
        transcript_prompt = main_system_prompt(dataset, video, load_transcript(dataset, video))
        produced: list[dict[str, Any]] = []
        for pair in by_video[key]:
            pair_started = time.time()
            try:
                standalone_obj, standalone_usage, _ = call_structured(
                    client,
                    model=args.model,
                    effort=args.effort,
                    system=standalone_system_prompt(),
                    user=standalone_user_prompt(pair),
                    schema=STANDALONE_SCHEMA,
                    validator=validate_standalone,
                    max_tokens=args.max_tokens,
                )
                main_obj, main_usage, _ = call_structured(
                    client,
                    model=args.model,
                    effort=args.effort,
                    system=transcript_prompt,
                    user=main_user_prompt(pair, standalone_obj),
                    schema=MAIN_SCHEMA,
                    validator=validate_main,
                    max_tokens=args.max_tokens,
                )
                record = {
                    "uid": pair["uid"],
                    "pair": {
                        key: pair.get(key)
                        for key in (
                            "uid",
                            "dataset",
                            "video",
                            "approach",
                            "question",
                            "answer",
                            "t",
                            "te",
                            "ts",
                        )
                    },
                    "judgment": combined_judgment(main_obj, standalone_obj),
                    "standalone_judgment": standalone_obj,
                    "transcript_aware_judgment": main_obj,
                    "usage": {"standalone": standalone_usage, "transcript_aware": main_usage},
                    "seconds": round(time.time() - pair_started, 2),
                    "model": args.model,
                    "effort": args.effort,
                    "rubric_version": RUBRIC_VERSION,
                    "run_name": run_name,
                    "first_n": max(0, args.first_n),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                with lock:
                    completed[pair["uid"]] = record
                    checkpoint.write(json.dumps(record, ensure_ascii=False) + "\n")
                    checkpoint.flush()
                    progress["done"] += 1
                    total_usage = aggregate_usage(completed)
                    print(
                        f"[{progress['done']}/{len(pairs)}] {pair['uid']:<42} "
                        f"${usage_cost(total_usage):.2f}",
                        flush=True,
                    )
                produced.append(record)
            except Exception as exc:  # preserve completed work; rerun resumes
                with lock:
                    failures.append((pair["uid"], f"{type(exc).__name__}: {exc}"))
                    print(f"FAILED {pair['uid']}: {exc}", file=sys.stderr, flush=True)
        return produced

    try:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = [pool.submit(run_video, key) for key in sorted(by_video)]
            for future in as_completed(futures):
                future.result()
    finally:
        checkpoint.close()

    # Always rebuild the CSV from the checkpoint so a resumed run produces one
    # complete, deduplicated export rather than a collection of partial files.
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(csv_columns())
        by_uid = {p["uid"]: p for p in pairs}
        for uid in sorted(completed, key=lambda item: human_order(by_uid[item])):
            writer.writerow(csv_row(completed[uid]))

    total_usage = aggregate_usage(completed)
    meta = {
        "run_name": run_name,
        "model": args.model,
        "effort": args.effort,
        "rubric_version": RUBRIC_VERSION,
        "versions": versions,
        "approaches": [VERSION_TO_APPROACH[v] for v in versions],
        "selected_pairs": len(pairs),
        "completed_pairs": len(completed),
        "source_videos": video_count,
        "qa_data_sha256": sha256_bytes(DATA_PATH.read_bytes()),
        "selected_corpus_sha256": selected_corpus_sha256(pairs),
        "first_n_per_version": max(0, args.first_n),
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "usage": total_usage,
        "estimated_standard_cost_usd": round(usage_cost(total_usage), 6),
        "pricing_usd_per_million_tokens": PRICES,
        "checkpoint": str(checkpoint_path),
        "csv": str(csv_path),
        "failures": [{"uid": uid, "error": error} for uid, error in failures],
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print("\n================ CLAUDE JUDGE SUMMARY ================")
    print(f"Completed : {len(completed)}/{len(pairs)}")
    print(f"Input     : {total_usage.get('input_tokens', 0):,} regular")
    print(f"Cache     : {total_usage.get('cache_creation_input_tokens', 0):,} write + "
          f"{total_usage.get('cache_read_input_tokens', 0):,} read")
    print(f"Output    : {total_usage.get('output_tokens', 0):,}")
    print(f"Est. cost : ${usage_cost(total_usage):.2f}")
    print(f"CSV       : {csv_path}")
    print(f"Checkpoint: {checkpoint_path}")
    print(f"Metadata  : {meta_path}")
    if failures:
        print(f"Failures  : {len(failures)} — rerun the identical command to resume")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
