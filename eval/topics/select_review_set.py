"""Choose the 100 pairs for human review: 5 groups of 20 Qwen3-14B v3 pairs.

Each group holds 4 deliberately bad pairs (one per intended failure: trustworthiness,
clarity, usefulness, care safety) and 16 normal pairs, 12 highly rated and 4 low
rated by the LLM judges. The 16 normal pairs cover all 12 topics once plus 4
extra topics, spread so each topic gets 6 or 7 of the 80 normal pairs overall.

Ratings come from gpt-oss-120b and Gemma 4 31B, the two judges that rated every
pair, 3 runs each (6 runs):
  high  every criterion passed and "recommend as is" in all 6 runs
  low   at least one criterion failed and not "recommend as is" in at least 4 of 6
Care Safety is a problem check in the judge CSVs: "No" means safe.

Topics are the round-2 majority labels of the nine labelling models
(runs/llm_labels, label_agreement.py). Only unique pairs (no near-duplicate) from
the 76 kept videos are used, and a group never takes two pairs from one video.

Usage, in order:
  python eval/topics/select_review_set.py                         normal pairs -> runs/review_set/selection_normal.json
  python eval/topics/select_review_set.py --stage bad-candidates  2 bad candidates per slot, and the judge input
  (judge the candidates: judge.py --qa-data eval/llm_judge/qa_data_qwen3-14b-bad-review.json --tag qwen3-14b-bad-review-runN)
  python eval/topics/select_review_set.py --stage bad-final       keep the candidate flagged most for its failure
  python eval/topics/select_review_set.py --stage swap-normal     replace normal picks whose timestamp fails
Both selections only keep pairs that pass timestamp_check.py (the timestamp points at where the pair came from).
"""
import csv
import glob
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
from exclusions import is_excluded  # noqa: E402

SEED = 42
GROUPS, BAD, HIGH, LOW = 5, 4, 12, 4
CRITERIA = ["Trustworthiness", "Clarity", "Usefulness", "Care Safety", "Standalone"]
FAILURES = ["trustworthiness", "clarity", "usefulness", "care_safety"]
JUDGES = {"ds": ("DeepSeek V4 Pro", "deepseek-v4-pro"), "go": ("gpt-oss-120b", "openai-gpt-oss-120b"),
          "gm": ("Gemma 4 31B", "google-gemma-4-31b-it"), "qw": ("Qwen2.5-VL-72B", "qwen2.5-vl-72b-awq")}
OUT = HERE / "runs" / "review_set"


def passed(row, criterion):
    value = row[f"Q&A {criterion} Yes/No"].strip().lower() == "yes"
    return not value if criterion == "Care Safety" else value


def judge_files(tag_glob):
    """{judge key: [csv, ...]} for one generator tag pattern."""
    return {k: sorted(glob.glob(str(REPO / "eval" / "results" / f"Master-Teepa_{slug}_{tag_glob}_full_Eval_*.csv")))
            for k, (_, slug) in JUDGES.items()}


def read_ratings(files, wanted):
    """{pair_id: {judge: [run rows]}} for the pairs in wanted."""
    out = defaultdict(lambda: defaultdict(list))
    for k, fs in files.items():
        for f in fs:
            for r in csv.DictReader(open(f)):
                if r["QA ID"] in wanted:
                    out[r["QA ID"]][k].append({"criteria": {c: passed(r, c) for c in CRITERIA},
                                               "errors": {c: r[f"Q&A {c} Error Type"] for c in CRITERIA},
                                               "recommendation": r["Caregiver Recommendation"].strip()})
    return out


def rating_class(runs):
    allpass = sum(all(r["criteria"].values()) for r in runs) / len(runs)
    rec = sum(r["recommendation"] == "Yes" for r in runs) / len(runs)
    if allpass == 1 and rec == 1:
        return "high"
    if allpass <= 2 / 6 and rec <= 2 / 6:
        return "low"
    return "mid"


def majority(level):
    rows = csv.DictReader((HERE / "runs" / "llm_labels" / "analysis" / f"majority_{level}.csv").open())
    return {r["pair_id"]: (r["majority"], float(r["share"])) for r in rows}


def topic_quotas(rng, topics, roomy):
    """Per group: every topic once plus 4 extras, with each topic extra in at most 2 groups overall.
    Extras go only to roomy topics (pairs from at least 3 videos): a one- or two-video topic such as
    Down syndrome cannot fill two slots of a group from different videos."""
    extras_left = Counter({t: 0 for t in topics})
    plan = []
    for _ in range(GROUPS):
        pool = [t for t in topics if extras_left[t] < 2 and t in roomy]
        rng.shuffle(pool)
        pool.sort(key=lambda t: extras_left[t])
        extra = pool[:HIGH + LOW - len(topics)]
        for t in extra:
            extras_left[t] += 1
        plan.append(list(topics) + extra)
    return plan


def select_normal():
    rng = random.Random(SEED)
    codebook = json.loads((HERE / "codebook_v1.json").read_text())
    topics = [t["id"] for t in codebook["topics"]]
    pairs = {json.loads(line)["pair_id"]: json.loads(line) for line in (HERE / "pairs_qwen3-14b-v3.jsonl").open()}
    dup = {r["pair_id"]: r["dup_of"] for r in csv.DictReader(
        (HERE / "runs" / "hier_qa_mc10_p10_dd95" / "assignments.csv").open())}
    flat, sub = majority("flat2"), majority("sub2")

    files = judge_files("qwen3-14b-run*")
    for k in ("go", "gm", "qw"):
        files[k] += judge_files("qwen3-14b-new69-run*")[k]
    ratings = read_ratings(files, set(pairs))
    pool = defaultdict(list)  # (topic, class) -> pair ids
    for p in pairs:
        if dup[p] or is_excluded(pairs[p]["dataset"], pairs[p]["video"]):
            continue
        cls = rating_class(ratings[p]["go"] + ratings[p]["gm"])
        if cls != "mid":
            pool[(flat[p][0], cls)].append(p)
    for v in pool.values():
        rng.shuffle(v)

    # Normal pairs: fill each group's topic slots, putting the 4 low slots on topics that still have low pairs.
    roomy = {t for t in topics if len({(pairs[p]["dataset"], pairs[p]["video"]) for p in pool[(t, "high")]}) >= 3}
    plan = topic_quotas(rng, topics, roomy)
    used, groups = set(), []
    video = lambda p: (pairs[p]["dataset"], pairs[p]["video"])  # noqa: E731

    def free(t, cls, videos):
        return [p for p in pool[(t, cls)] if p not in used and video(p) not in videos]

    for slots in plan:
        chosen, videos, remaining = [], set(), list(slots)

        def take(p, t, cls):
            used.add(p)
            videos.add(video(p))
            chosen.append({"pair_id": p, "kind": "normal", "class": cls, "topic": t})
            remaining.remove(t)

        # Low slots first, on 4 different topics that still have a usable low pair. Topics are drawn at
        # random, weighted by how many low pairs they have left, so small pools are not used up early.
        options = sorted(set(remaining))
        while sum(c["class"] == "low" for c in chosen) < LOW:
            options = [t for t in options if free(t, "low", videos)]
            t = rng.choices(options, weights=[len(free(t, "low", videos)) ** 0.5 for t in options])[0]
            take(free(t, "low", videos)[0], t, "low")
            options.remove(t)
        for t in list(remaining):
            cands = free(t, "high", videos) or free(t, "high", set())  # one-video topics may repeat a video
            take(cands[0], t, "high")
        groups.append(chosen)

    selection = {"seed": SEED, "rule": __doc__.split("\n\n")[1], "groups": groups}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "selection_normal.json").write_text(json.dumps(selection, indent=2))
    counts = Counter((c["class"]) for g in groups for c in g)
    per_topic = Counter(c["topic"] for g in groups for c in g)
    print("normal pairs by class:", dict(counts), "| per group:", [Counter(c["class"] for c in g) for g in groups])
    print("per topic:", dict(sorted(per_topic.items())))
    print("pool sizes:", {f"{t}/{c}": len(v) for (t, c), v in sorted(pool.items())})


BAD_LABELS = HERE / "runs" / "llm_labels_bad"
BAD_PAIRS = HERE / "pairs_qwen3-14b-bad-v3.jsonl"
CRITERION_OF = {"trustworthiness": "Trustworthiness", "clarity": "Clarity", "usefulness": "Usefulness",
                "care_safety": "Care Safety"}
MIN_TOPIC_VOTES = 5  # normal and bad picks need a topic at least 5 of the 9 labelling models agree on
CANDIDATES = 2  # bad candidates judged per slot; the one the judges flag most for its intended failure is kept


def bad_majority():
    """{pair_id: (subtopic, votes)} from the nine models' round-2 labels of the bad pairs."""
    votes = defaultdict(Counter)
    for f in BAD_LABELS.glob("*_sub2.csv"):
        for r in csv.DictReader(f.open()):
            if r["label"]:
                votes[r["pair_id"]][r["label"]] = votes[r["pair_id"]][r["label"]] + 1
    return {p: c.most_common(1)[0] for p, c in votes.items()}


def bad_candidates():
    """Two candidates per bad slot (group x intended failure), spread over topics and videos."""
    rng = random.Random(SEED + 1)
    codebook = json.loads((HERE / "codebook_v1.json").read_text())
    parent = {s["id"]: s["parent"] for s in codebook["subtopics"]}
    normal = json.loads((OUT / "selection_normal.json").read_text())["groups"]
    bad = {json.loads(l)["pair_id"]: json.loads(l) for l in BAD_PAIRS.open()}
    label = bad_majority()
    video = lambda p: (bad[p]["dataset"], bad[p]["video"])  # noqa: E731
    # Only pairs whose topic most models agree on (at least 5 of 9), so the topic shown is meaningful.
    pool = [p for p in bad if p in label and label[p][1] >= 5]
    rng.shuffle(pool)
    topic_use, used, groups = Counter(), set(), []
    for g, group in enumerate(normal):
        videos = {(c["pair_id"].split("_")[0], int(c["pair_id"].split("_")[1][1:])) for c in group}
        slots = []
        for failure in FAILURES:
            picks = []
            for _ in range(CANDIDATES):
                cands = [p for p in pool if p not in used and bad[p]["intended_failure"] == failure
                         and video(p) not in videos]
                # Prefer the topics the bad pairs have used least so far.
                cands.sort(key=lambda p: topic_use[parent[label[p][0]]])
                p = cands[0]
                used.add(p)
                videos.add(video(p))
                topic_use[parent[label[p][0]]] += 1
                picks.append({"pair_id": p, "topic": parent[label[p][0]], "subtopic": label[p][0],
                              "subtopic_votes": label[p][1], "intended_failure": failure})
            slots.append(picks)
        groups.append(slots)
    (OUT / "bad_candidates.json").write_text(json.dumps({"seed": SEED + 1, "groups": groups}, indent=2))
    # Judge input in qa_data.json format, with the video URLs the judge prompt needs.
    videos = {}
    for f in ("qa_data_qwen3-14b.json", "qa_data_qwen3-14b-new69.json"):
        for ds, vids in json.loads((REPO / "eval" / "llm_judge" / f).read_text())["videos"].items():
            videos.setdefault(ds, {}).update(vids)
    chosen = [c for g in groups for slot in g for c in slot]
    data = {"pairs": [{"uid": c["pair_id"], "dataset": bad[c["pair_id"]]["dataset"], "video": bad[c["pair_id"]]["video"],
                       "approach": "SingleAgent-qwen3-14b-bad-v3", "question": bad[c["pair_id"]]["question"],
                       "answer": bad[c["pair_id"]]["answer"], "t": bad[c["pair_id"]]["time_start_sec"],
                       "te": bad[c["pair_id"]]["time_end_sec"], "ts": "model"} for c in chosen],
            "videos": {ds: {v: u for v, u in vids.items() if any(c["pair_id"].startswith(f"{ds}_v{v}_") for c in chosen)}
                       for ds, vids in videos.items()}}
    out = REPO / "eval" / "llm_judge" / "qa_data_qwen3-14b-bad-review.json"
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    print(f"{len(chosen)} bad candidates -> {out.relative_to(REPO)}; topics: {dict(sorted(topic_use.items()))}")


def bad_final():
    """Keep, per slot, the candidate the judges flag most for its intended failure."""
    rng = random.Random(SEED + 2)
    cand = json.loads((OUT / "bad_candidates.json").read_text())["groups"]
    ids = {c["pair_id"] for g in cand for slot in g for c in slot}
    ratings = read_ratings(judge_files("qwen3-14b-bad-review-run*"), ids)
    import timestamp_check as T
    groups = []
    for g in cand:
        chosen = []
        for slot in g:
            slot = [c for c in slot if T.check(c["pair_id"])[0]]
            for c in slot:
                runs = ratings[c["pair_id"]]["go"] + ratings[c["pair_id"]]["gm"]
                crit = CRITERION_OF[c["intended_failure"]]
                c["flagged_runs"] = sum(not r["criteria"][crit] for r in runs)
                c["judged_runs"] = len(runs)
                c["any_failure_runs"] = sum(not all(r["criteria"].values()) for r in runs)
            best = max(slot, key=lambda c: (c["flagged_runs"], c["any_failure_runs"], rng.random()))
            chosen.append({**best, "kind": "bad", "class": "bad"})
        groups.append(chosen)
    (OUT / "selection_bad.json").write_text(json.dumps({"seed": SEED + 2, "groups": groups}, indent=2))
    flagged = sum(c["flagged_runs"] > c["judged_runs"] / 2 for g in groups for c in g)
    print(f"bad pairs chosen: {sum(len(g) for g in groups)}; intended failure flagged by most judge runs: {flagged}")


def swap_normal():
    """Replace selected normal pairs whose timestamp fails timestamp_check, or whose topic fewer than
    MIN_TOPIC_VOTES of the nine models chose, keeping every other pick.
    A replacement has the same group, topic and class, passes the check, and comes from a video not
    already in the group."""
    import timestamp_check as T
    rng = random.Random(SEED + 4)
    sel = json.loads((OUT / "selection_normal.json").read_text())
    pairs = {json.loads(line)["pair_id"]: json.loads(line) for line in (HERE / "pairs_qwen3-14b-v3.jsonl").open()}
    dup = {r["pair_id"]: r["dup_of"] for r in csv.DictReader(
        (HERE / "runs" / "hier_qa_mc10_p10_dd95" / "assignments.csv").open())}
    flat = majority("flat2")
    files = judge_files("qwen3-14b-run*")
    for k in ("go", "gm", "qw"):
        files[k] += judge_files("qwen3-14b-new69-run*")[k]
    ratings = read_ratings(files, set(pairs))
    video = lambda p: (pairs[p]["dataset"], pairs[p]["video"])  # noqa: E731
    used = {c["pair_id"] for g in sel["groups"] for c in g}
    # Videos of each group's bad pairs, when those are chosen: a normal pair must not share one.
    bad_file = OUT / "selection_bad.json"
    bad_videos = [{(c["pair_id"].split("_")[0], int(c["pair_id"].split("_")[1][1:])) for c in g}
                  for g in json.loads(bad_file.read_text())["groups"]] if bad_file.exists() else [set()] * GROUPS
    swaps = []
    for gi, g in enumerate(sel["groups"]):
        for i, c in enumerate(g):
            ok, why, *_ = T.check(c["pair_id"])
            if ok and video(c["pair_id"]) in bad_videos[gi]:
                ok, why = False, "same video as one of the group's bad pairs"
            if ok and round(flat[c["pair_id"]][1] * 9) < MIN_TOPIC_VOTES:
                ok, why = False, f"topic chosen by only {round(flat[c['pair_id']][1] * 9)} of 9 models"
            if ok:
                continue
            videos = {video(x["pair_id"]) for x in g if x is not c} | bad_videos[gi]
            def candidates(topic):
                cands = [p for p in pairs if p not in used and not dup[p] and flat[p][0] == topic
                         and not is_excluded(*video(p)) and video(p) not in videos
                         and rating_class(ratings[p]["go"] + ratings[p]["gm"]) == c["class"]]
                cands = [p for p in cands if round(flat[p][1] * 9) >= MIN_TOPIC_VOTES]
                rng.shuffle(cands)
                return next((p for p in cands if T.check(p)[0]), None)

            topic, new = c["topic"], candidates(c["topic"])
            if new is None:
                # The topic has no usable pair left (animal fluency: one video); take the least-picked topic instead.
                counts = Counter(x["topic"] for gg in sel["groups"] for x in gg)
                for topic in sorted(counts, key=lambda t: (counts[t], rng.random())):
                    in_group = sum(x["topic"] == topic for x in g if x is not c)
                    if in_group < 2 and (new := candidates(topic)):
                        break
            if new is None:
                raise SystemExit(f"no replacement for {c['pair_id']} ({c['topic']}, {c['class']})")
            used.add(new)
            swaps.append({"old": c["pair_id"], "new": new, "reason": why})
            g[i] = {**c, "pair_id": new, "topic": topic, "replaced": c["pair_id"], "replaced_because": why}
    sel["timestamp_check"] = "every pair passes eval/topics/timestamp_check.py"
    (OUT / "selection_normal.json").write_text(json.dumps(sel, indent=2))
    print(f"{len(swaps)} normal pairs replaced")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["normal", "bad-candidates", "bad-final", "swap-normal"], default="normal")
    stage = ap.parse_args().stage
    {"normal": select_normal, "bad-candidates": bad_candidates, "bad-final": bad_final,
     "swap-normal": swap_normal}[stage]()


if __name__ == "__main__":
    main()
