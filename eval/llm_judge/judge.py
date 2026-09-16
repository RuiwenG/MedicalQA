#!/usr/bin/env python3
"""LLM-as-a-judge over the generated QA pairs, using the website's exact form.

Rates QA pairs with the same five metrics, attribute labels, Yes/No decisions,
error taxonomy, and caregiver recommendation as ``eval/web/index.html``, so the
judge's output lands in ``eval/results/`` as just another annotator and the
notebook's aggregation cell picks it up unchanged.

Two run modes:

``--mode pilot40``
    The exact 40-pair session a human gets from the pinned link
    ``?batch=1`` on the live pilot site (seed 42, 8 batches, anchor 20,
    MAX_PAIRS 40, DualAgent/RAG excluded). The assignment RNG below is a
    bit-exact port of the site's mulberry32/Fisher-Yates, verified against
    the site's own JavaScript.
``--mode full``
    Every pair in qa_data.json — including DualAgent and RAG, which humans
    are not rating in the pilot. This is the only evaluation those two
    approaches get.

The judge sees the video transcript (for the Alignment metric) but never the
approach name — the same blinding humans get. Calls are grouped by video with
the transcript in a stable prompt prefix, so DeepSeek's context cache makes
every call after a video's first nearly free on input.

Cost is computed from each response's usage block (cache-hit / cache-miss /
output) and printed as a running total.

    python eval/llm_judge/judge.py --mode pilot40
    python eval/llm_judge/judge.py --mode full --workers 6
"""
import argparse
import csv
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[2]

# --- Pricing (USD per 1M tokens), from api-docs.deepseek.com, 2026-08-07 ---
PRICES = {
    "deepseek-v4-pro":   {"hit": 0.003625, "miss": 0.435, "out": 0.87},
    "deepseek-v4-flash": {"hit": 0.0028,   "miss": 0.14,  "out": 0.28},
}

# --- Live pilot site settings (eval-pilot-netlify-review/config.js) ---
SITE = {
    "seed": 42, "batches": 8, "anchor_size": 20, "max_pairs": 40,
    "exclude": {"DualAgent", "RAG"},
}

# =============================================================================
# The evaluation form — labels copied verbatim from eval/web/index.html METRICS
# =============================================================================
# Two per-metric shape flags, because the form is no longer uniform:
#   "attribute": None  -> the humans rate no graded scale for this metric
#   "error_trigger"    -> which binary answer opens the error list ("No" for the
#                         first three; "Yes" for Care Safety, whose binary asks
#                         whether a PROBLEM is present, so Yes is the bad answer)
METRICS = [
    {
        "key": "qa_alignment",
        "db_prefix": "qna_trustworthiness",
        "label": "Q&A Trustworthiness",
        "binary_question": "Does the Q&A align with the video?",
        "definition": "The Q&A accurately reflects the information presented in the source video. The answer is supported by the video, and does not introduce unsupported claims or contradictions.",
        "attribute": ["Excellent", "Good", "Fair", "Poor"],
        "errors": ["Source Misinterpretation", "Hallucinating", "Contradiction", "Missing Key Information"],
        "error_trigger": "No",
    },
    {
        "key": "qa_accessibility",
        "db_prefix": "qna_clarity",
        "label": "Q&A Clarity",
        "binary_question": "Is the Q&A easy for a caregiver to understand?",
        "definition": "The Q&A is specific, readable, and accessible for an 8th grade reader, without unexplained clinical or technical language.",
        "attribute": ["Very easy to understand", "Easy", "Somewhat difficult", "Difficult"],
        "errors": ["Difficult vocabulary", "Too long", "Ambiguous", "Poor organization"],
        "error_trigger": "No",
    },
    {
        "key": "qa_edu_actionable",
        "db_prefix": "qna_usefulness",
        "label": "Q&A Usefulness",
        "binary_question": "Does the Q&A provide useful or actionable guidance for caregivers?",
        "definition": "The Q&A teaches an important dementia-care idea and/or gives practical guidance a caregiver could apply.",
        "attribute": ["Highly useful", "Useful", "Limited useful", "Not useful"],
        "errors": ["Not actionable", "Missing Explanation", "Generic Advice", "Low Relevance to Caregiver Needs"],
        "error_trigger": "No",
    },
    {
        "key": "qa_mental_health",
        "db_prefix": "qna_care_safety",
        "label": "Q&A Care Safety",
        "binary_question": "Does the Q&A contain guidance that could lead to unsafe or inappropriate care?",
        "definition": "The Q&A should give safe and respectful guidance for caregivers and people living with dementia.",
        "attribute": None,          # humans rate no graded scale here
        "errors": ["Unsafe medical or health advice", "Physical safety risk", "Blaming or judgmental language", "Discourages professional care"],
        "error_trigger": "Yes",     # NOTE: inverted - "Yes" means a problem exists
    },
    {
        # Mirrors the Self-contained (Standalone) criterion added to the
        # SingleQA generation prompt. Appended last so the first four metrics
        # keep their CSV column positions across runs.
        "key": "qa_standalone",
        "db_prefix": "qna_standalone",
        "label": "Q&A Standalone",
        "binary_question": "Can the Q&A be understood on its own by a caregiver who has not seen the video and cannot see any other Q&A pair?",
        "definition": "The pair names everything it refers to. It does not mention the video, transcript, or speaker, and uses no pronoun or \"this/these\" reference whose meaning depends on the video or on another Q&A pair.",
        "attribute": ["Fully standalone", "Mostly standalone", "Somewhat dependent", "Not standalone"],
        "errors": ["Refers to the video, transcript or speaker",
                   "Undefined pronoun",
                   "Undefined this/these reference",
                   "Depends on another Q&A pair"],
        "error_trigger": "No",
    },
]
NO_ISSUE = "No issue"
RECOMMENDATION = [
    "Yes",
    "Yes, but with minor edits (meaning unchanged)",
    "No, it needs major edits",
    "No",
]

# =============================================================================
# Bit-exact port of the site's assignment RNG (mulberry32 + Fisher-Yates).
# All arithmetic stays in uint32, matching JS Math.imul / >>> semantics; the
# final division by 2^32 is exact in a double, so shuffles match the browser.
# =============================================================================
M32 = 0xFFFFFFFF


def mulberry32(seed):
    state = {"a": seed & M32}

    def rnd():
        state["a"] = (state["a"] + 0x6D2B79F5) & M32
        a = state["a"]
        t = ((a ^ (a >> 15)) * (1 | a)) & M32
        t = ((t + (((t ^ (t >> 7)) * (61 | t)) & M32)) ^ t) & M32
        return ((t ^ (t >> 14)) & M32) / 4294967296

    return rnd


def shuffled(arr, seed):
    out = list(arr)
    rnd = mulberry32(seed)
    for i in range(len(out) - 1, 0, -1):
        j = int(rnd() * (i + 1))
        out[i], out[j] = out[j], out[i]
    return out


def pilot40_pairs(all_pairs, batch_index=0):
    """The site's auto-mode session for a pinned batch, capped at 40."""
    pool = [p for p in all_pairs if p["approach"] not in SITE["exclude"]]
    deck = shuffled(pool, SITE["seed"])
    anchor = deck[: SITE["anchor_size"]]
    rest = deck[SITE["anchor_size"]:]
    mine = [p for i, p in enumerate(rest) if i % SITE["batches"] == batch_index]
    session = shuffled(anchor + mine, SITE["seed"] + batch_index + 1)
    return session[: SITE["max_pairs"]]


# =============================================================================
# Prompts
# =============================================================================
def load_transcript(dataset, video):
    path = REPO / dataset / str(video) / "Transcript" / "transcript-en.json"
    segs = json.loads(path.read_text(encoding="utf-8"))
    lines = []
    for s in segs:
        t = int(s.get("start", 0))
        text = str(s.get("text", "")).strip()
        if text:
            lines.append(f"[{t // 60}:{t % 60:02d}] {text}")
    return "\n".join(lines)


def form_text():
    parts = []
    for i, m in enumerate(METRICS, 1):
        block = [f"{i}. {m['label']}", f"   Definition: {m['definition']}"]
        if m["attribute"]:
            block.append(
                f"   attribute — pick exactly one (strongest first): {json.dumps(m['attribute'])}"
            )
        else:
            block.append(
                "   attribute — this metric has NO graded scale; omit the field entirely"
            )
        trig = m["error_trigger"]
        other = "No" if trig == "Yes" else "Yes"
        block.append(
            f"   binary — answer the question \"{m['binary_question']}\" with \"Yes\" or \"No\""
        )
        block.append(
            f"   errors — ONLY if binary is \"{trig}\": pick one or more from "
            f"{json.dumps(m['errors'])}; if binary is \"{other}\", use an empty list"
        )
        parts.append("\n".join(block))
    return "\n".join(parts)


def system_prompt(dataset, video, transcript):
    schema = {}
    for m in METRICS:
        entry = {"binary": "Yes|No", "errors": ["..."]}
        if m["attribute"]:
            entry = {"attribute": "...", **entry}
        schema[m["key"]] = entry
    schema["caregiver_recommendation"] = " | ".join(RECOMMENDATION)
    return f"""You are an expert evaluator of educational question-answer (QA) pairs that were automatically generated from a dementia-caregiving training video. The QA pairs are meant to teach caregivers of people living with dementia. You rate each QA pair with the exact form human annotators use, judging the question and answer together, as one unit, from a caregiver's perspective.

THE FORM — five metrics, then an overall recommendation:

{form_text()}

Overall caregiver recommendation — "Would you confidently recommend this Q&A to a caregiver?" Pick exactly one: {json.dumps(RECOMMENDATION)}

RULES:
- For Q&A Trustworthiness, verify the answer's claims against the video transcript below. A claim the transcript does not support is "Hallucinating"; a claim the transcript contradicts is a "Contradiction". Judge grounding strictly against this transcript.
- The other metrics are judged on the QA text itself.
- For Q&A Standalone, set the transcript ASIDE and judge the QA text alone. A
  reference such as "this activity", "these techniques", "the speaker", or "they"
  counts as undefined whenever the QA pair itself never names it — even if the
  transcript makes the referent obvious to you. Ask only this: would a caregiver
  who has never seen the video, reading this ONE pair with no others in front of
  them, know what every reference points to? If not, the binary is "No".
- Q&A Care Safety is phrased as a PROBLEM check: answer "Yes" only if the Q&A contains guidance that could lead to unsafe or inappropriate care, and list the concerns. A safe Q&A is "No" with an empty errors list. It has no attribute field.
- Answers may contain Markdown (## headings, **bold**). Formatting noise that hurts readability counts against Q&A Clarity.
- Use the label strings EXACTLY as given, character for character.
- Respond with STRICT JSON only, no prose, in exactly this shape:
{json.dumps(schema, indent=2)}

VIDEO TRANSCRIPT ({dataset} dataset, video {video}; [m:ss] = start time of each line):

{transcript}"""


def user_prompt(pair):
    return f"Rate this QA pair.\n\nQuestion: {pair['question']}\n\nAnswer: {pair['answer']}"


# =============================================================================
# Validation — enforce the site's vocabulary exactly
# =============================================================================
def validate(obj):
    problems = []
    if not isinstance(obj, dict):
        return ["response is not a JSON object"]
    for m in METRICS:
        block = obj.get(m["key"])
        if not isinstance(block, dict):
            problems.append(f"{m['key']}: missing object")
            continue
        if m["attribute"] is not None and block.get("attribute") not in m["attribute"]:
            problems.append(f"{m['key']}.attribute must be one of {m['attribute']}")
        if block.get("binary") not in ("Yes", "No"):
            problems.append(f"{m['key']}.binary must be \"Yes\" or \"No\"")
        errs = block.get("errors", [])
        if not isinstance(errs, list) or any(e not in m["errors"] for e in errs):
            problems.append(f"{m['key']}.errors must be a list drawn from {m['errors']}")
        elif block.get("binary") == m["error_trigger"] and not errs:
            problems.append(
                f"{m['key']}: binary is \"{m['error_trigger']}\" so errors must name at least one problem"
            )
    if obj.get("caregiver_recommendation") not in RECOMMENDATION:
        problems.append(f"caregiver_recommendation must be one of {RECOMMENDATION}")
    return problems


# =============================================================================
# DeepSeek API
# =============================================================================
def read_env_key(name):
    for line in (REPO / ".env").read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(f"{name} not found in .env")


class CostMeter:
    def __init__(self, prices):
        self.prices = prices
        self.hit = self.miss = self.out = self.calls = 0
        self.lock = threading.Lock()

    def add(self, usage):
        with self.lock:
            details = usage.get("prompt_tokens_details") or {}
            hit = usage.get("prompt_cache_hit_tokens", details.get("cached_tokens", 0)) or 0
            miss = usage.get("prompt_cache_miss_tokens") or 0
            if not miss:  # server didn't split: treat non-cached prompt as miss
                miss = (usage.get("prompt_tokens", 0) or 0) - hit
            self.hit += hit
            self.miss += miss
            self.out += usage.get("completion_tokens", 0) or 0
            self.calls += 1

    def usd(self):
        p = self.prices
        return (self.hit * p["hit"] + self.miss * p["miss"] + self.out * p["out"]) / 1e6

    def line(self):
        return (f"{self.calls} calls | in: {self.miss:,} miss + {self.hit:,} cached | "
                f"out: {self.out:,} | ${self.usd():.4f}")


def judge_one(session, model, sys_prompt, pair, meter, temperature=0.0):
    messages = [{"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt(pair)}]
    feedback = None
    for attempt in range(4):
        body = {
            "model": model,
            "messages": messages if not feedback else messages + feedback,
            "temperature": temperature,
            # v4-pro reasons by default and its thinking counts toward this cap;
            # 900 truncated the JSON mid-object. The final JSON itself is ~300.
            "max_tokens": 6000,
            "response_format": {"type": "json_object"},
        }
        t0 = time.time()
        try:
            r = session.post("https://api.deepseek.com/chat/completions",
                             json=body, timeout=300)
            r.raise_for_status()
        except requests.RequestException as error:
            if attempt == 3:
                raise
            time.sleep(min(2 ** attempt * 2, 20))
            continue
        data = r.json()
        meter.add(data.get("usage", {}))
        text = data["choices"][0]["message"]["content"] or ""
        try:
            obj = json.loads(re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M))
        except json.JSONDecodeError:
            obj, problems = None, ["response was not valid JSON"]
        else:
            problems = validate(obj)
        if not problems:
            return obj, time.time() - t0
        # One structured repair round-trip, then give up on this pair.
        if feedback is not None:
            raise ValueError(f"invalid after repair: {problems}")
        feedback = [
            {"role": "assistant", "content": text},
            {"role": "user", "content": "Your response was invalid: "
             + "; ".join(problems) + ". Reply again with corrected STRICT JSON only."},
        ]
    raise RuntimeError("unreachable")


# =============================================================================
# Output — CSV identical to the website download, JSONL for the raw record
# =============================================================================
def _metric_cols():
    """Column names per metric. Care Safety has no attribute, so it emits two
    columns where the others emit three — csv_row() must stay in step."""
    cols = []
    for m in METRICS:
        if m["attribute"]:
            cols.append(f"{m['label']} Attribute")
        cols += [f"{m['label']} Error Type", f"{m['label']} Yes/No"]
    return cols


CSV_COLS = (["Dataset", "Video Index", "Approach", "QA ID", "Question", "Answer"]
            + _metric_cols()
            + ["Caregiver Recommendation", "Annotator", "Session ID", "Batch", "Seconds"])


def csv_row(pair, obj, model, session_id, batch, seconds):
    row = [pair["dataset"], pair["video"], pair["approach"], pair["uid"],
           pair["question"], pair["answer"]]
    for m in METRICS:
        block = obj[m["key"]]
        # Errors are listed when binary == error_trigger; otherwise "No issue".
        # Care Safety inverts that trigger, so keying off "Yes" here would flip it.
        flagged = block["binary"] == m["error_trigger"]
        if m["attribute"]:
            row.append(block["attribute"])
        row += [NO_ISSUE if not flagged else "; ".join(block["errors"]),
                block["binary"]]
    row += [obj["caregiver_recommendation"], model, session_id, batch, round(seconds)]
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["pilot40", "full"], required=True)
    ap.add_argument("--model", default="deepseek-v4-pro", choices=sorted(PRICES))
    ap.add_argument("--batch", type=int, default=1, help="pilot40: pinned batch (1-based), default 1")
    ap.add_argument("--workers", type=int, default=6, help="parallel videos (full mode)")
    ap.add_argument("--limit", type=int, default=0, help="stop after N pairs (debug)")
    ap.add_argument("--resume", metavar="JSONL",
                    help="Path to a previous run's JSONL. Pairs already judged there are "
                         "reused instead of re-judged, and the new run appends to a fresh "
                         "file; the CSV is written from the merged set. Use it after a run "
                         "is interrupted, so the completed pairs are not paid for twice.")
    ap.add_argument(
        "--approaches",
        nargs="+",
        default=None,
        help="Only judge these approach folder names, e.g. "
             "--approaches MultiAgent-LLMChunking MultiAgent-LLMChunking-v3. "
             "Default: every approach in qa_data.json.",
    )
    args = ap.parse_args()

    data = json.loads((REPO / "eval" / "web" / "qa_data.json").read_text(encoding="utf-8"))
    all_pairs = data["pairs"]

    if args.approaches:
        wanted = set(args.approaches)
        found = {p["approach"] for p in all_pairs}
        missing = wanted - found
        if missing:
            raise SystemExit(
                f"No pairs for approach(es): {', '.join(sorted(missing))}. "
                f"Available: {', '.join(sorted(found))}"
            )
        all_pairs = [p for p in all_pairs if p["approach"] in wanted]
        print(f"Filtered to {len(all_pairs)} pairs from: {', '.join(sorted(wanted))}")

    if args.mode == "pilot40":
        pairs = pilot40_pairs(all_pairs, batch_index=(args.batch - 1) % SITE["batches"])
        session_id = f"llm-{args.model}-pilot40-b{args.batch}"
        batch_label = f"batch-{args.batch}-of-{SITE['batches']}"
    else:
        pairs = list(all_pairs)
        session_id = f"llm-{args.model}-full"
        batch_label = "llm-full"
    if args.limit:
        pairs = pairs[: args.limit]

    # Judgments carried over from an interrupted run, keyed by uid. These are
    # merged into the CSV at the end but never re-sent to the API.
    resumed = {}
    if args.resume:
        resume_path = Path(args.resume)
        if not resume_path.is_absolute():
            resume_path = REPO / resume_path
        with open(resume_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    row = json.loads(line)
                    resumed[row["uid"]] = (row["judgment"], row.get("seconds", 0))
        before = len(pairs)
        pairs = [p for p in pairs if p["uid"] not in resumed]
        # Only pairs actually in this run's selection count as carried over.
        wanted_uids = {p["uid"] for p in all_pairs}
        resumed = {u: v for u, v in resumed.items() if u in wanted_uids}
        print(f"Resuming from {resume_path.name}: {len(resumed)} pairs carried over, "
              f"{len(pairs)} of {before} left to judge")

    key = read_env_key("DEEPSEEK_API_KEY")
    meter = CostMeter(PRICES[args.model])

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = REPO / "eval" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"Master-Teepa_{args.model}_{args.mode}_Eval_{stamp}.csv"
    runs_dir = REPO / "eval" / "llm_judge" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = runs_dir / f"{args.model}_{args.mode}_{stamp}.jsonl"

    # Group by video so every pair of a video reuses the same cached prefix.
    by_video = {}
    for p in pairs:
        by_video.setdefault((p["dataset"], p["video"]), []).append(p)

    print(f"Judging {len(pairs)} pairs across {len(by_video)} videos "
          f"with {args.model} ({args.mode})")

    results, failures = {}, []
    write_lock = threading.Lock()
    jsonl_file = open(jsonl_path, "w", encoding="utf-8")

    def run_video(dv):
        dataset, video = dv
        sys_prompt = system_prompt(dataset, video, load_transcript(dataset, video))
        session = requests.Session()
        session.headers.update({"Authorization": f"Bearer {key}",
                                "Content-Type": "application/json"})
        out = []
        for pair in by_video[dv]:
            try:
                obj, secs = judge_one(session, args.model, sys_prompt, pair, meter)
                out.append((pair, obj, secs))
                with write_lock:
                    jsonl_file.write(json.dumps(
                        {"uid": pair["uid"], "judgment": obj, "seconds": round(secs, 2)},
                        ensure_ascii=False) + "\n")
                    jsonl_file.flush()
                    done = sum(len(v) for v in results.values()) + len(out)
                    print(f"  [{done}/{len(pairs)}] {pair['uid']:<44} {meter.line()}")
            except Exception as error:
                with write_lock:
                    failures.append((pair["uid"], str(error)))
                    print(f"  FAILED {pair['uid']}: {error}", file=sys.stderr)
        return dv, out

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for future in as_completed(pool.submit(run_video, dv) for dv in sorted(by_video)):
            dv, out = future.result()
            results[dv] = out
    jsonl_file.close()

    # Fold the carried-over judgments back in so the CSV covers the whole
    # selection, not just what this invocation re-judged.
    if resumed:
        by_uid = {p["uid"]: p for p in all_pairs}
        for uid, (obj, secs) in resumed.items():
            pair = by_uid[uid]
            results.setdefault((pair["dataset"], pair["video"]), []).append((pair, obj, secs))

    written = 0
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLS)
        for dv in sorted(results):
            for pair, obj, secs in sorted(results[dv], key=lambda r: r[0]["uid"]):
                writer.writerow(csv_row(pair, obj, args.model, session_id, batch_label, secs))
                written += 1

    print("\n================ SUMMARY ================")
    if resumed:
        print(f"Resumed  : {len(resumed)} pairs carried over from a previous run")
    print(f"CSV rows : {written}")
    print(f"Rated    : {sum(len(v) for v in results.values())}/{len(pairs)} pairs")
    if failures:
        print(f"Failed   : {len(failures)} -> {[u for u, _ in failures]}")
    print(f"Tokens   : {meter.miss:,} input (cache miss) + {meter.hit:,} input (cache hit) "
          f"+ {meter.out:,} output")
    p = PRICES[args.model]
    print(f"Cost     : ${meter.usd():.4f}   "
          f"(miss ${meter.miss * p['miss'] / 1e6:.4f} + hit ${meter.hit * p['hit'] / 1e6:.4f} "
          f"+ out ${meter.out * p['out'] / 1e6:.4f})")
    print(f"CSV      : {csv_path.relative_to(REPO)}")
    print(f"Raw JSONL: {jsonl_path.relative_to(REPO)}")


if __name__ == "__main__":
    main()
