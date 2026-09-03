#!/usr/bin/env python3
"""Transcript-free judge for the two metrics that are about the QA text alone.

``judge.py`` puts the whole video transcript in the system prompt, because
Q&A Trustworthiness cannot be scored without it. That transcript is a problem
for the two metrics that are *not* about the video:

Q&A Standalone
    asks whether a caregiver who has never seen the video can understand the
    pair on its own. A judge holding the transcript can resolve "this activity"
    or "they" effortlessly, so it is being asked to ignore knowledge it
    demonstrably has. ``judge.py`` handles this with an instruction ("set the
    transcript ASIDE"), which is exactly the kind of instruction models quietly
    fail to honour.

Q&A Clarity
    is defined over readability of the QA text, and the transcript can only add
    context that a real reader would not have.

This script removes the confound structurally rather than by instruction: the
model never receives the transcript at all, so it physically cannot resolve a
dangling reference from the source. Differences against ``judge.py``'s numbers
measure how much the transcript was leaking into those two metrics.

Everything else is deliberately identical to ``judge.py`` — the metric labels,
attribute scales, error taxonomies and Yes/No triggers are IMPORTED from it, not
copied, so the two scripts cannot drift apart.

    python eval/llm_judge/judge_no_transcript.py --approaches SingleAgent SingleAgent-prev
"""
import argparse
import csv
import importlib.util
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

# Reuse judge.py's definitions rather than restating them. It guards its entry
# point with __main__, so importing runs no work.
_spec = importlib.util.spec_from_file_location("judge", Path(__file__).with_name("judge.py"))
_judge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_judge)

PRICES = _judge.PRICES
CostMeter = _judge.CostMeter
read_env_key = _judge.read_env_key
NO_ISSUE = _judge.NO_ISSUE

# The two metrics judged on the QA text alone. Order follows judge.py.
KEYS = ["qa_accessibility", "qa_standalone"]
METRICS = [m for m in _judge.METRICS if m["key"] in KEYS]
if len(METRICS) != len(KEYS):
    raise SystemExit(f"judge.py no longer defines all of {KEYS}")


def form_text():
    parts = []
    for i, m in enumerate(METRICS, 1):
        trig = m["error_trigger"]
        other = "No" if trig == "Yes" else "Yes"
        parts.append("\n".join([
            f"{i}. {m['label']}",
            f"   Definition: {m['definition']}",
            f"   attribute — pick exactly one (strongest first): {json.dumps(m['attribute'])}",
            f'   binary — answer the question "{m["binary_question"]}" with "Yes" or "No"',
            f'   errors — ONLY if binary is "{trig}": pick one or more from '
            f'{json.dumps(m["errors"])}; if binary is "{other}", use an empty list',
        ]))
    return "\n".join(parts)


def system_prompt():
    """One prompt for every pair — no transcript, so it caches across all calls."""
    schema = {m["key"]: {"attribute": "...", "binary": "Yes|No", "errors": ["..."]}
              for m in METRICS}
    return f"""You are an expert evaluator of educational question-answer (QA) pairs written to teach caregivers of people living with dementia. You judge the question and answer together, as one unit, from a caregiver's perspective.

You are shown ONE QA pair and nothing else. You have NOT seen the video it came from, you cannot see any other QA pair, and no transcript is available to you. Judge only what is in front of you — this is the same position the caregiver reading this pair will be in.

THE FORM — two metrics:

{form_text()}

RULES:
- Judge the QA text itself. Do not speculate about what a source video might have said.
- For Q&A Standalone, a reference counts as undefined when THIS pair never names it. "this activity", "these techniques", "the speaker", or "they" with no antecedent inside the pair are undefined, however guessable they may seem. Ask only whether a caregiver reading this one pair would know what every reference points to.
- Answers may contain Markdown (## headings, **bold**). Formatting noise that hurts readability counts against Q&A Clarity.
- Use the label strings EXACTLY as given, character for character.
- Respond with STRICT JSON only, no prose, in exactly this shape:
{json.dumps(schema, indent=2)}"""


def user_prompt(pair):
    return f"Rate this QA pair.\n\nQuestion: {pair['question']}\n\nAnswer: {pair['answer']}"


def validate(obj):
    problems = []
    if not isinstance(obj, dict):
        return ["response is not a JSON object"]
    for m in METRICS:
        block = obj.get(m["key"])
        if not isinstance(block, dict):
            problems.append(f"{m['key']}: missing object")
            continue
        if block.get("attribute") not in m["attribute"]:
            problems.append(f"{m['key']}.attribute must be one of {m['attribute']}")
        if block.get("binary") not in ("Yes", "No"):
            problems.append(f'{m["key"]}.binary must be "Yes" or "No"')
        errs = block.get("errors", [])
        if not isinstance(errs, list) or any(e not in m["errors"] for e in errs):
            problems.append(f"{m['key']}.errors must be a list drawn from {m['errors']}")
        elif block.get("binary") == m["error_trigger"] and not errs:
            problems.append(
                f'{m["key"]}: binary is "{m["error_trigger"]}" so errors must name at least one problem'
            )
    return problems


def judge_one(session, model, sys_prompt, pair, meter, temperature=0.0):
    messages = [{"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt(pair)}]
    feedback = None
    for attempt in range(4):
        body = {
            "model": model,
            "messages": messages if not feedback else messages + feedback,
            "temperature": temperature,
            # v4-pro reasons by default and thinking counts toward this cap.
            "max_tokens": 6000,
            "response_format": {"type": "json_object"},
        }
        t0 = time.time()
        try:
            r = session.post("https://api.deepseek.com/chat/completions", json=body, timeout=300)
            r.raise_for_status()
        except requests.RequestException:
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
        if feedback is not None:
            raise ValueError(f"invalid after repair: {problems}")
        feedback = [
            {"role": "assistant", "content": text},
            {"role": "user", "content": "Your response was invalid: " + "; ".join(problems)
             + ". Reply again with corrected STRICT JSON only."},
        ]
    raise RuntimeError("unreachable")


CSV_COLS = (["Dataset", "Video Index", "Approach", "QA ID", "Question", "Answer"]
            + [c for m in METRICS
               for c in (f"{m['label']} Attribute",
                         f"{m['label']} Error Type",
                         f"{m['label']} Yes/No")]
            + ["Annotator", "Session ID", "Seconds"])


def csv_row(pair, obj, model, session_id, seconds):
    row = [pair["dataset"], pair["video"], pair["approach"], pair["uid"],
           pair["question"], pair["answer"]]
    for m in METRICS:
        block = obj[m["key"]]
        flagged = block["binary"] == m["error_trigger"]
        row += [block["attribute"],
                NO_ISSUE if not flagged else "; ".join(block["errors"]),
                block["binary"]]
    row += [model, session_id, round(seconds)]
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default="deepseek-v4-pro", choices=sorted(PRICES))
    ap.add_argument("--approaches", nargs="+", required=True,
                    help="Approach folder names, e.g. SingleAgent SingleAgent-prev")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="stop after N pairs (debug)")
    args = ap.parse_args()

    data = json.loads((REPO / "eval" / "web" / "qa_data.json").read_text(encoding="utf-8"))
    wanted = set(args.approaches)
    found = {p["approach"] for p in data["pairs"]}
    missing = wanted - found
    if missing:
        raise SystemExit(f"No pairs for: {', '.join(sorted(missing))}. "
                         f"Available: {', '.join(sorted(found))}")
    pairs = [p for p in data["pairs"] if p["approach"] in wanted]
    pairs.sort(key=lambda p: (p["approach"], p["dataset"], p["video"], p["uid"]))
    if args.limit:
        pairs = pairs[: args.limit]

    key = read_env_key("DEEPSEEK_API_KEY")
    meter = CostMeter(PRICES[args.model])
    sys_prompt = system_prompt()
    session_id = f"llm-{args.model}-notranscript"

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    runs_dir = REPO / "eval" / "llm_judge" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    out_dir = REPO / "eval" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = runs_dir / f"{args.model}_notranscript_{stamp}.jsonl"
    csv_path = out_dir / f"Master-Teepa_{args.model}_notranscript_Eval_{stamp}.csv"

    counts = {a: sum(1 for p in pairs if p["approach"] == a) for a in sorted(wanted)}
    print(f"Judging {len(pairs)} pairs WITHOUT transcript on "
          f"{', '.join(m['label'] for m in METRICS)}")
    print("  " + " | ".join(f"{a}={n}" for a, n in counts.items()))

    # No transcript means no per-video prefix to preserve, so pairs parallelise
    # directly. The single shared system prompt caches across every call.
    results, failures = {}, []
    lock = threading.Lock()
    jsonl_file = open(jsonl_path, "w", encoding="utf-8")
    done = 0

    def run_pair(pair):
        s = requests.Session()
        s.headers.update({"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        return pair, judge_one(s, args.model, sys_prompt, pair, meter)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_pair, p) for p in pairs]
        for fut in as_completed(futures):
            try:
                pair, (obj, secs) = fut.result()
            except Exception as error:
                with lock:
                    failures.append(str(error))
                    print(f"  FAILED: {error}", file=sys.stderr, flush=True)
                continue
            with lock:
                results[pair["uid"]] = (pair, obj, secs)
                jsonl_file.write(json.dumps(
                    {"uid": pair["uid"], "judgment": obj, "seconds": round(secs, 2)},
                    ensure_ascii=False) + "\n")
                jsonl_file.flush()
                done += 1
                print(f"  [{done}/{len(pairs)}] {pair['uid']:<44} {meter.line()}", flush=True)
    jsonl_file.close()

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLS)
        for uid in sorted(results):
            pair, obj, secs = results[uid]
            w.writerow(csv_row(pair, obj, args.model, session_id, secs))

    print("\n================ SUMMARY ================")
    print(f"Rated    : {len(results)}/{len(pairs)} pairs")
    if failures:
        print(f"Failed   : {len(failures)}")
    print(f"Tokens   : {meter.miss:,} input (cache miss) + {meter.hit:,} input (cache hit) "
          f"+ {meter.out:,} output")
    print(f"Cost     : ${meter.usd():.4f}")
    print(f"CSV      : {csv_path.relative_to(REPO)}")
    print(f"Raw JSONL: {jsonl_path.relative_to(REPO)}")


if __name__ == "__main__":
    main()
