"""Check the 100-pair review set before it goes to annotators. Prints every problem found.

Checks, for each of the 100 pairs:
  - every judge has all 3 runs (DeepSeek only for normal pairs from the original 25 videos)
  - the timestamp passes timestamp_check.py (exists, inside the video, points at where the pair came from)
  - the video has a title and link (video_titles.json)
  - at least 5 of the 9 labelling models agree on its topic
  - bad pairs: the judges flagged the intended failure in most runs
and, for each group: 12 high, 4 low and 4 bad pairs, no video twice, no near-duplicate questions.

Usage: python eval/topics/audit_review_set.py   (exit code 1 if anything is wrong)
"""
import itertools
import json
import re
import sys
from collections import Counter

import select_review_set as S
import timestamp_check as T

STOP = set("""the a an and or of to in on for with is are be can how what why when does do it its this that their they
them person people dementia caregiver caregivers help living someone who may might should""".split())


def words(text):
    return {w for w in re.findall(r"[a-z']+", text.lower()) if len(w) > 3 and w not in STOP}


def main():
    normal = json.loads((S.OUT / "selection_normal.json").read_text())["groups"]
    bad = json.loads((S.OUT / "selection_bad.json").read_text())["groups"]
    pairs = {json.loads(l)["pair_id"]: json.loads(l) for l in (S.HERE / "pairs_qwen3-14b-v3.jsonl").open()}
    pairs.update({json.loads(l)["pair_id"]: json.loads(l) for l in S.BAD_PAIRS.open()})
    files = S.judge_files("qwen3-14b-run*")
    for k in ("go", "gm", "qw"):
        files[k] += S.judge_files("qwen3-14b-new69-run*")[k]
    ratings = S.read_ratings(files, {c["pair_id"] for g in normal for c in g})
    ratings.update(S.read_ratings(S.judge_files("qwen3-14b-bad-review-run*"), {c["pair_id"] for g in bad for c in g}))
    titles = json.loads((S.HERE / "video_titles.json").read_text())
    flat = S.majority("flat2")
    problems = []
    for gi, (gn, gb) in enumerate(zip(normal, bad), 1):
        group = gn + gb
        shape = Counter(c["class"] for c in group)
        if shape != Counter({"high": S.HIGH, "low": S.LOW, "bad": S.BAD}):
            problems.append(f"group {gi}: {dict(shape)}")
        if len({(pairs[c['pair_id']]['dataset'], pairs[c['pair_id']]['video']) for c in group}) < len(group):
            problems.append(f"group {gi}: a video appears twice")
        for a, b in itertools.combinations(group, 2):
            wa, wb = words(pairs[a["pair_id"]]["question"]), words(pairs[b["pair_id"]]["question"])
            if wa and wb and len(wa & wb) / len(wa | wb) >= 0.5:
                problems.append(f"group {gi}: near-duplicate questions {a['pair_id']} / {b['pair_id']}")
        for c in group:
            p, d = c["pair_id"], pairs[c["pair_id"]]
            judges = ("go", "gm", "qw") + (("ds",) if c["class"] != "bad" and d["dataset"] in ("Master", "Teepa") else ())
            for k in judges:
                if len(ratings[p].get(k, [])) != 3:
                    problems.append(f"{p}: {S.JUDGES[k][0]} has {len(ratings[p].get(k, []))} of 3 runs")
            ok, why, *_ = T.check(p)
            if not ok:
                problems.append(f"{p}: timestamp, {why}")
            if f"{d['dataset']} {d['video']}" not in titles:
                problems.append(f"{p}: no video title")
            votes = c["subtopic_votes"] if c["class"] == "bad" else round(flat[p][1] * 9)
            if votes < S.MIN_TOPIC_VOTES:
                problems.append(f"{p}: topic chosen by only {votes} of 9 models")
            if c["class"] == "bad" and c["flagged_runs"] <= c["judged_runs"] / 2:
                problems.append(f"{p}: intended failure confirmed in only {c['flagged_runs']} of {c['judged_runs']} runs")
    total = sum(len(gn) + len(gb) for gn, gb in zip(normal, bad))
    print(f"{total} pairs audited: " + ("no problems" if not problems else f"{len(problems)} problems"))
    for p in problems:
        print("  " + p)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
