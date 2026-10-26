"""Label every QA pair with a topic from the codebook, using a model behind a vLLM server.

Passes, chosen with --level:
  flat  one of the 12 top-level topics (F01-F12)
  sub   one of the 33 subtopics (S01-S33)
  sub2  round 2: one of the 33 subtopics, listed under their topics, with a stricter
        secondary rule and two worked examples; the topic is read off the subtopic

Each call asks for a primary label and an optional secondary one. The answer is
constrained by a JSON schema whose fields are enums of the valid ids, so a small
model cannot invent a label. Temperature 0. Results go to
runs/llm_labels/<model>_<level>.csv (--pairs and --out-dir label another set, such as
the bad pairs); rerunning skips pairs already labelled.

Usage (on a GPU node, with a vLLM server up; slurm/run_label_vllm.sh does both):
  VLLM_URL=http://127.0.0.1:8001/v1/chat/completions \
      python llm_label.py --model phi-4 --level flat --workers 32
"""
import argparse
import csv
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
PAIRS = HERE / "pairs_qwen3-14b-v3.jsonl"
CODEBOOK = HERE / "codebook_v1.json"
OUT_DIR = HERE / "runs" / "llm_labels"
FIELDS = ["pair_id", "label", "secondary", "model", "level", "attempts", "seconds"]


def options(codebook, level):
    return codebook["topics"] if level == "flat" else codebook["subtopics"]


# Round 2 examples: one pair with no secondary, one with. Both are real pairs (Master 10 and Master 1).
SUB2_EXAMPLES = """Examples:
Question: How can caregivers help someone with dementia during mealtime?
Reply: {"label": "S22", "secondary": "none"}
Question: How can a caregiver help a person with dementia start eating or drinking using the hand-under-hand technique?
Reply: {"label": "S22", "secondary": "S23"}"""


def system_prompt_sub2(codebook):
    groups = []
    for t in codebook["topics"]:
        subs = "\n".join(f"  {s['id']}: {s['name']}. {s['definition']}"
                         for s in codebook["subtopics"] if s["parent"] == t["id"])
        groups.append(f"{t['name']} ({t['definition']})\n{subs}")
    return ("You sort question-answer pairs from dementia caregiver education videos by subtopic.\n\n"
            "Pick the subtopic for what the question asks the caregiver to understand or do, using the answer "
            "to settle it. Read the topic headings first, then choose the subtopic under the right one.\n\n"
            "Most pairs have one subtopic: give \"none\" as the secondary. Give a secondary subtopic only when "
            "the answer also gives separate, substantial advice on a second subtopic. Overlapping or related "
            "themes are not enough.\n\n"
            "The subtopics, grouped by topic:\n" + "\n\n".join(groups) + "\n\n" + SUB2_EXAMPLES +
            '\n\nReply with JSON only: {"label": "<id>", "secondary": "<id or none>"}.')


def system_prompt(codebook, level):
    if level == "sub2":
        return system_prompt_sub2(codebook)
    kind = "topic" if level == "flat" else "subtopic"
    lines = "\n".join(f"{o['id']}: {o['name']}. {o['definition']}" for o in options(codebook, level))
    return (f"You sort question-answer pairs from dementia caregiver education videos by {kind}.\n\n"
            f"{codebook['rule']}\n\nThe {kind}s:\n{lines}\n\n"
            f'Reply with JSON only: {{"label": "<id>", "secondary": "<id or none>"}}.')


def schema(codebook, level):
    ids = [o["id"] for o in options(codebook, level)]
    return {"type": "object", "additionalProperties": False, "required": ["label", "secondary"],
            "properties": {"label": {"type": "string", "enum": ids},
                           "secondary": {"type": "string", "enum": ids + ["none"]}}}


def label_one(url, model, sys_prompt, json_schema, pair, valid):
    body = {
        "model": model, "temperature": 0, "max_tokens": 60, "seed": 0,
        "messages": [{"role": "system", "content": sys_prompt},
                     {"role": "user", "content": f"Question: {pair['question']}\nAnswer: {pair['answer']}"}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "label", "schema": json_schema}},
    }
    # Qwen3 thinking models would spend the token budget reasoning. Only they get the flag:
    # vLLM rejects chat_template_kwargs outright for models served with Mistral's tokenizer.
    if model.startswith("qwen3"):
        body["chat_template_kwargs"] = {"enable_thinking": False}
    start = time.time()
    for attempt in range(1, 4):
        try:
            r = requests.post(url, json=body, timeout=300)
            r.raise_for_status()
            obj = json.loads(r.json()["choices"][0]["message"]["content"])
            if obj.get("label") in valid:
                sec = obj.get("secondary")
                sec = "" if sec in (None, "none", obj["label"]) or sec not in valid else sec
                return obj["label"], sec, attempt, time.time() - start
        except (requests.RequestException, ValueError, KeyError, IndexError):
            time.sleep(2 * attempt)
    return "", "", 3, time.time() - start


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="served model name; also names the output file")
    ap.add_argument("--level", choices=["flat", "sub", "sub2"], required=True)
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--limit", type=int, help="label only the first N pairs (smoke test)")
    ap.add_argument("--pairs", type=Path, default=PAIRS, help="JSONL of pairs to label (default: the v3 pairs)")
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR, help="where the label CSVs go")
    args = ap.parse_args()
    url = os.environ.get("VLLM_URL", "http://127.0.0.1:8000/v1/chat/completions")

    codebook = json.loads(CODEBOOK.read_text())
    pairs = [json.loads(line) for line in args.pairs.read_text().splitlines()][:args.limit]
    out = args.out_dir / f"{args.model}_{args.level}.csv"
    args.out_dir.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        done = {r["pair_id"] for r in csv.DictReader(out.open()) if r["label"]}
    todo = [p for p in pairs if p["pair_id"] not in done]
    print(f"{args.model} {args.level}: {len(todo)} to label ({len(done)} already done)", flush=True)

    sys_prompt, json_schema = system_prompt(codebook, args.level), schema(codebook, args.level)
    valid = {o["id"] for o in options(codebook, args.level)}
    new_file = not out.exists()
    failed = 0
    with out.open("a", newline="") as f, ThreadPoolExecutor(args.workers) as pool:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        futures = {pool.submit(label_one, url, args.model, sys_prompt, json_schema, p, valid): p for p in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            label, sec, attempts, secs = fut.result()
            failed += not label
            w.writerow({"pair_id": futures[fut]["pair_id"], "label": label, "secondary": sec, "model": args.model,
                        "level": args.level, "attempts": attempts, "seconds": round(secs, 2)})
            if i % 200 == 0:
                f.flush()
                print(f"  {i}/{len(todo)}", flush=True)
    print(f"{args.model} {args.level}: done, {failed} failed -> {out}", flush=True)


if __name__ == "__main__":
    main()
