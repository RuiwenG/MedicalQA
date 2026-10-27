"""Choose the 100 pairs for human review: 5 groups of 20 v3 pairs of one generator (--generator).

Each group holds 4 deliberately bad pairs (one per intended failure: trustworthiness,
clarity, usefulness, care safety) and 16 normal pairs, 12 highly rated and 4 low
rated by the LLM judges. The 16 normal pairs cover all 12 topics once plus 4
extra topics, spread so each topic gets 6 or 7 of the 80 normal pairs overall.
A topic with highly rated pairs for fewer than 5 groups sits in only that many
groups, and its free slots become extras (Qwen3.5-9B: Down syndrome has no pairs,
its one video SUP/5 being a format failure; animal fluency has 3 highly rated pairs).

Ratings come from gpt-oss-120b and Gemma 4 31B, the two judges that rated every
pair, 3 runs each (6 runs):
  high  every criterion passed and "recommend as is" in all 6 runs
  low   at least one criterion failed and not "recommend as is" in at least 4 of 6
Care Safety is a problem check in the judge CSVs: "No" means safe.

Topics are the round-2 majority labels of the nine labelling models
(runs/llm_labels, label_agreement.py). Only unique pairs (no near-duplicate) from
the 76 kept videos are used, and a group never takes two pairs from one video.

--generator qwen3.5-9b does the same for the Qwen3.5-9B pairs: labels from
runs/llm_labels_qwen35-9b (majorities counted here), near-duplicates found here with the
topic model's encoder and cutoff (all-mpnet-base-v2, cosine >= 0.95), bad pairs from
pairs_qwen3.5-9b-bad-v3.jsonl labelled into runs/llm_labels_qwen35-9b-bad, and every
output in runs/review_set_qwen3.5-9b. Pass the same --generator to every stage.

--size 25 builds a smaller set after the 100-pair one, in runs/<that set's folder>-25:
5 groups of 5 (3 highly rated, 1 low rated, 1 bad), none of them in the 100-pair set.
Four normal slots cannot hold every topic, so each topic with pairs goes in at least
one group and the rest go to roomy topics, never twice in a group. The bad pairs are
the 100-pair set's judged candidates it did not keep (bad-reuse), so nothing new is
judged: one per intended failure plus a second trustworthiness, in random group order.
  select_review_set.py --size 25 [--stage normal | bad-reuse | swap-normal]   (in that order)

Usage, in order:
  python eval/topics/select_review_set.py                         normal pairs -> runs/review_set/selection_normal.json
  python eval/topics/select_review_set.py --stage bad-candidates  2 bad candidates per slot, and the judge input
  (judge the candidates: judge.py --qa-data eval/llm_judge/qa_data_<generator>-bad-review.json --tag <generator>-bad-review-runN)
  python eval/topics/select_review_set.py --stage bad-more        more candidates for slots with none usable yet
  (judge qa_data_<generator>-bad-review-r<round>.json under the same tags; repeat until every slot has one)
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
SIZES = {"100": (5, 4, 12, 4), "25": (5, 1, 3, 1)}  # GROUPS, BAD, HIGH, LOW
SIZE = "100"
EXCLUDE = set()  # pairs a smaller set must not reuse: everything in the 100-pair set
CRITERIA = ["Trustworthiness", "Clarity", "Usefulness", "Care Safety", "Standalone"]
FAILURES = ["trustworthiness", "clarity", "usefulness", "care_safety"]
JUDGES = {"ds": ("DeepSeek V4 Pro", "deepseek-v4-pro"), "go": ("gpt-oss-120b", "openai-gpt-oss-120b"),
          "gm": ("Gemma 4 31B", "google-gemma-4-31b-it"), "qw": ("Qwen2.5-VL-72B", "qwen2.5-vl-72b-awq")}
# Per generator: its pairs, label folder, judge tags of the normal pairs, bad pairs and their labels, output folder.
# dup: the topic-model run whose assignments.csv lists near-duplicates; None finds them here.
GENERATORS = {
    "qwen3-14b": {"name": "Qwen3-14B", "pairs": "pairs_qwen3-14b-v3.jsonl", "labels": "llm_labels", "dup": "hier_qa_mc10_p10_dd95",
                  "judged": ["qwen3-14b-run*", "qwen3-14b-new69-run*"], "bad_pairs": "pairs_qwen3-14b-bad-v3.jsonl",
                  "bad_labels": "llm_labels_bad", "out": "review_set"},
    "qwen3.5-9b": {"name": "Qwen3.5-9B", "pairs": "pairs_qwen3.5-9b-v3.jsonl", "labels": "llm_labels_qwen35-9b", "dup": None,
                   "judged": ["qwen3.5-9b-run*"], "bad_pairs": "pairs_qwen3.5-9b-bad-v3.jsonl",
                   "bad_labels": "llm_labels_qwen35-9b-bad", "out": "review_set_qwen3.5-9b",
                   # Intended failures kept even when the judges do not confirm them, with the reason.
                   "unconfirmed_ok": {"clarity": (
                       "Qwen3.5-9B does not produce clarity failures: over 40 clarity candidates in two rounds, "
                       "gpt-oss-120b and Gemma 4 flagged clarity in 7 of 240 runs (trustworthiness 208, "
                       "usefulness 206); its 'unclear' pairs come out clear but fabricated. The best candidate "
                       "per slot is kept for the human raters, and the failure is reported (decided 2026-10-27).")}},
}
GENERATOR = "qwen3-14b"
G = GENERATORS[GENERATOR]
OUT = HERE / "runs" / G["out"]
DEDUP = 0.95  # bertopic_run.py's default cutoff


def use(generator, size="100"):
    """Point the stages at one generator's files and one set size."""
    global GENERATOR, G, OUT, BAD_LABELS, BAD_PAIRS, SIZE, GROUPS, BAD, HIGH, LOW, EXCLUDE
    GENERATOR, G, SIZE = generator, GENERATORS[generator], size
    GROUPS, BAD, HIGH, LOW = SIZES[size]
    OUT = HERE / "runs" / (G["out"] + ("" if size == "100" else f"-{size}"))
    BAD_LABELS, BAD_PAIRS = HERE / "runs" / G["bad_labels"], HERE / G["bad_pairs"]
    EXCLUDE = set() if size == "100" else {
        c["pair_id"] for f in ("selection_normal.json", "selection_bad.json")
        for g in json.loads((HERE / "runs" / G["out"] / f).read_text())["groups"] for c in g}


def load_pairs():
    return {json.loads(line)["pair_id"]: json.loads(line) for line in (HERE / G["pairs"]).open()}


def normal_files():
    """Judge CSVs of the generator's normal pairs."""
    files = defaultdict(list)
    for tag in G["judged"]:
        for k, fs in judge_files(tag).items():
            files[k] += fs
    return files


def near_duplicates(pairs):
    """{pair_id: id of the earlier pair it nearly repeats, or ""}."""
    if G["dup"]:
        return {r["pair_id"]: r["dup_of"] for r in csv.DictReader(
            (HERE / "runs" / G["dup"] / "assignments.csv").open())}
    cache = HERE / "runs" / G["out"] / "near_duplicates.csv"
    if not cache.exists():
        # bertopic_run.py's rule: question + answer embedded, greedy in file order, an earlier kept pair at >= 0.95.
        import timestamp_check as T
        ids = list(pairs)
        emb = T.encoder().encode([f"{pairs[p]['question']} {pairs[p]['answer']}" for p in ids], normalize_embeddings=True)
        sims, keep, dup = emb @ emb.T, [], {}
        for i, p in enumerate(ids):
            hit = next((k for k in keep if sims[i, k] >= DEDUP), None)
            dup[p] = ids[hit] if hit is not None else ""
            if hit is None:
                keep.append(i)
        cache.parent.mkdir(parents=True, exist_ok=True)
        with cache.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["pair_id", "dup_of"])
            w.writerows(dup.items())
    return {r["pair_id"]: r["dup_of"] for r in csv.DictReader(cache.open())}


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
    """{pair_id: (label, share of the models)}: label_agreement.py's majority_<level>.csv where it exists,
    else counted here the same way from the round-2 labels (flat2 is each model's subtopic's parent)."""
    labels = HERE / "runs" / G["labels"]
    table = labels / "analysis" / f"majority_{level}.csv"
    if table.exists():
        return {r["pair_id"]: (r["majority"], float(r["share"])) for r in csv.DictReader(table.open())}
    parent = {s["id"]: s["parent"] for s in json.loads((HERE / "codebook_v1.json").read_text())["subtopics"]}
    votes = defaultdict(Counter)
    for f in sorted(labels.glob("*_sub2.csv")):
        for r in csv.DictReader(f.open()):
            if r["label"]:
                votes[r["pair_id"]][parent[r["label"]] if level == "flat2" else r["label"]] += 1
    return {p: (c.most_common(1)[0][0], c.most_common(1)[0][1] / sum(c.values())) for p, c in votes.items()}


def topic_quotas(rng, topics, roomy, supply):
    """Per group: every topic once plus extras up to 16 slots, with each topic extra in at most 2 groups
    overall (more only when the extras need it). Extras go only to roomy topics (pairs from at least
    3 videos): a one- or two-video topic such as Down syndrome cannot fill two slots of a group from
    different videos. A topic with highly rated pairs for fewer groups than GROUPS (supply) sits in
    that many groups, picked at random."""
    if HIGH + LOW < len([t for t in topics if supply[t]]):
        return spread_topics(rng, topics, roomy, supply)
    member = {t: range(GROUPS) if supply[t] >= GROUPS else rng.sample(range(GROUPS), supply[t]) for t in topics}
    extras = GROUPS * (HIGH + LOW) - sum(len(m) for m in member.values())
    cap = max(2, -(-extras // len(roomy)))
    extras_left = Counter({t: 0 for t in topics})
    plan = []
    for g in range(GROUPS):
        own = [t for t in topics if g in member[t]]
        pool = [t for t in topics if extras_left[t] < cap and t in roomy]
        rng.shuffle(pool)
        pool.sort(key=lambda t: extras_left[t])
        extra = pool[:HIGH + LOW - len(own)]
        for t in extra:
            extras_left[t] += 1
        plan.append(own + extra)
    return plan


def spread_topics(rng, topics, roomy, supply):
    """Per group, when its slots are fewer than the topics: every topic with pairs once over the whole set,
    the remaining slots to distinct roomy topics, dealt so no group holds a topic twice."""
    first = [t for t in topics if supply[t]]
    rng.shuffle(first)
    second = [t for t in topics if t in roomy]
    rng.shuffle(second)
    plan = [[] for _ in range(GROUPS)]
    for t in (first + second)[:GROUPS * (HIGH + LOW)]:
        g = min((g for g in range(GROUPS) if t not in plan[g]), key=lambda g: (len(plan[g]), rng.random()))
        plan[g].append(t)
    return plan


def rule_text():
    """The docstring paragraph describing this size of set."""
    paras = __doc__.split("\n\n")
    return paras[1] if SIZE == "100" else next(p for p in paras if p.startswith("--size 25"))


def select_normal():
    rng = random.Random(SEED)
    codebook = json.loads((HERE / "codebook_v1.json").read_text())
    topics = [t["id"] for t in codebook["topics"]]
    pairs = load_pairs()
    dup = near_duplicates(pairs)
    flat, sub = majority("flat2"), majority("sub2")

    ratings = read_ratings(normal_files(), set(pairs))
    pool = defaultdict(list)  # (topic, class) -> pair ids
    for p in pairs:
        if dup[p] or is_excluded(pairs[p]["dataset"], pairs[p]["video"]) or p in EXCLUDE:
            continue
        cls = rating_class(ratings[p]["go"] + ratings[p]["gm"])
        if cls != "mid":
            pool[(flat[p][0], cls)].append(p)
    for v in pool.values():
        rng.shuffle(v)

    # Normal pairs: fill each group's topic slots, putting the 4 low slots on topics that still have low pairs.
    roomy = {t for t in topics if len({(pairs[p]["dataset"], pairs[p]["video"]) for p in pool[(t, "high")]}) >= 3}
    plan = topic_quotas(rng, topics, roomy, {t: len(pool[(t, "high")]) for t in topics})
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

    selection = {"generator": GENERATOR, "size": SIZE, "seed": SEED, "rule": rule_text(), "groups": groups}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "selection_normal.json").write_text(json.dumps(selection, indent=2))
    counts = Counter((c["class"]) for g in groups for c in g)
    per_topic = Counter(c["topic"] for g in groups for c in g)
    print("normal pairs by class:", dict(counts), "| per group:", [Counter(c["class"] for c in g) for g in groups])
    print("per topic:", dict(sorted(per_topic.items())))
    print("pool sizes:", {f"{t}/{c}": len(v) for (t, c), v in sorted(pool.items())})


BAD_LABELS = HERE / "runs" / G["bad_labels"]
BAD_PAIRS = HERE / G["bad_pairs"]
CRITERION_OF = {"trustworthiness": "Trustworthiness", "clarity": "Clarity", "usefulness": "Usefulness",
                "care_safety": "Care Safety"}
MIN_TOPIC_VOTES = 5  # normal and bad picks need a topic at least 5 of the 9 labelling models agree on
CANDIDATES = 2  # bad candidates judged per slot; the one the judges flag most for its intended failure is kept
MORE = 6  # candidates bad-more adds to a slot with no usable one yet


def bad_majority():
    """{pair_id: (subtopic, votes)} from the nine models' round-2 labels of the bad pairs."""
    votes = defaultdict(Counter)
    for f in BAD_LABELS.glob("*_sub2.csv"):
        for r in csv.DictReader(f.open()):
            if r["label"]:
                votes[r["pair_id"]][r["label"]] = votes[r["pair_id"]][r["label"]] + 1
    return {p: c.most_common(1)[0] for p, c in votes.items()}


def bad_pool(rng):
    """The bad pairs, their majority subtopics, each subtopic's v1 topic, and the shuffled candidate pool:
    only pairs whose topic most models agree on (at least 5 of 9), so the topic shown is meaningful."""
    parent = {s["id"]: s["parent"] for s in json.loads((HERE / "codebook_v1.json").read_text())["subtopics"]}
    bad = {json.loads(l)["pair_id"]: json.loads(l) for l in BAD_PAIRS.open()}
    label = bad_majority()
    pool = [p for p in bad if p in label and label[p][1] >= 5]
    rng.shuffle(pool)
    return bad, label, parent, pool


def take_candidate(pool, bad, label, parent, used, videos, topic_use, failure):
    """The next unused pool pair for this failure from a video not in the group, preferring the topics the
    bad pairs have used least so far; None when the pool has none left."""
    cands = [p for p in pool if p not in used and bad[p]["intended_failure"] == failure
             and (bad[p]["dataset"], bad[p]["video"]) not in videos]
    if not cands:
        return None
    cands.sort(key=lambda p: topic_use[parent[label[p][0]]])
    p = cands[0]
    used.add(p)
    videos.add((bad[p]["dataset"], bad[p]["video"]))
    topic_use[parent[label[p][0]]] += 1
    return {"pair_id": p, "topic": parent[label[p][0]], "subtopic": label[p][0], "subtopic_votes": label[p][1],
            "intended_failure": failure}


def write_judge_input(chosen, bad, out):
    """Judge input in qa_data.json format, with the video URLs the judge prompt needs."""
    videos = {}
    for f in ("qa_data_qwen3-14b.json", "qa_data_qwen3-14b-new69.json"):
        for ds, vids in json.loads((REPO / "eval" / "llm_judge" / f).read_text())["videos"].items():
            videos.setdefault(ds, {}).update(vids)
    data = {"pairs": [{"uid": c["pair_id"], "dataset": bad[c["pair_id"]]["dataset"], "video": bad[c["pair_id"]]["video"],
                       "approach": "SingleAgent-" + BAD_PAIRS.stem.removeprefix("pairs_"), "question": bad[c["pair_id"]]["question"],
                       "answer": bad[c["pair_id"]]["answer"], "t": bad[c["pair_id"]]["time_start_sec"],
                       "te": bad[c["pair_id"]]["time_end_sec"], "ts": "model"} for c in chosen],
            "videos": {ds: {v: u for v, u in vids.items() if any(c["pair_id"].startswith(f"{ds}_v{v}_") for c in chosen)}
                       for ds, vids in videos.items()}}
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False))


def normal_videos(group):
    return {(c["pair_id"].split("_")[0], int(c["pair_id"].split("_")[1][1:])) for c in group}


def bad_candidates():
    """Two candidates per bad slot (group x intended failure), spread over topics and videos."""
    rng = random.Random(SEED + 1)
    normal = json.loads((OUT / "selection_normal.json").read_text())["groups"]
    bad, label, parent, pool = bad_pool(rng)
    topic_use, used, groups = Counter(), set(), []
    for group in normal:
        videos = normal_videos(group)
        groups.append([[take_candidate(pool, bad, label, parent, used, videos, topic_use, failure)
                        for _ in range(CANDIDATES)] for failure in FAILURES])
    (OUT / "bad_candidates.json").write_text(json.dumps({"seed": SEED + 1, "groups": groups}, indent=2))
    chosen = [c for g in groups for slot in g for c in slot]
    out = REPO / "eval" / "llm_judge" / f"qa_data_{GENERATOR}-bad-review.json"
    write_judge_input(chosen, bad, out)
    print(f"{len(chosen)} bad candidates -> {out.relative_to(REPO)}; topics: {dict(sorted(topic_use.items()))}")


def usable(c, ratings):
    """A candidate fit to keep: its timestamp passes and most gpt-oss/Gemma runs flag its intended failure."""
    import timestamp_check as T
    runs = ratings[c["pair_id"]]["go"] + ratings[c["pair_id"]]["gm"]
    flagged = sum(not r["criteria"][CRITERION_OF[c["intended_failure"]]] for r in runs)
    return bool(runs) and flagged > len(runs) / 2 and T.check(c["pair_id"])[0]


def bad_more():
    """Add MORE candidates to every slot with no usable candidate yet, and write a judge input holding only
    the new ones (qa_data_<generator>-bad-review-r<round>.json). Judge it under the same
    <generator>-bad-review-runN tags, then run bad-final (or bad-more again)."""
    data = json.loads((OUT / "bad_candidates.json").read_text())
    cand = data["groups"]
    rnd = data.get("rounds", 1) + 1
    rng = random.Random(SEED + 4 + rnd)
    normal = json.loads((OUT / "selection_normal.json").read_text())["groups"]
    bad, label, parent, pool = bad_pool(rng)
    ids = {c["pair_id"] for g in cand for slot in g for c in slot}
    ratings = read_ratings(judge_files(f"{GENERATOR}-bad-review-run*"), ids)
    used = set(ids)
    topic_use = Counter(c["topic"] for g in cand for slot in g for c in slot)
    added = []
    for group, slots in zip(normal, cand):
        videos = normal_videos(group) | {(bad[c["pair_id"]]["dataset"], bad[c["pair_id"]]["video"])
                                         for slot in slots for c in slot}
        for slot in slots:
            if any(usable(c, ratings) for c in slot):
                continue
            for _ in range(MORE):
                c = take_candidate(pool, bad, label, parent, used, videos, topic_use, slot[0]["intended_failure"])
                if c:
                    slot.append({**c, "round": rnd})
                    added.append(slot[-1])
    data.update(groups=cand, rounds=rnd)
    (OUT / "bad_candidates.json").write_text(json.dumps(data, indent=2))
    out = REPO / "eval" / "llm_judge" / f"qa_data_{GENERATOR}-bad-review-r{rnd}.json"
    write_judge_input(added, bad, out)
    print(f"round {rnd}: {len(added)} more candidates "
          f"({dict(Counter(c['intended_failure'] for c in added))}) -> {out.relative_to(REPO)}")


def bad_reuse():
    """Smaller set: one bad pair per group from the 100-pair set's judged candidates that it did not keep, so
    nothing new is judged. Each group's failure comes from the four failures plus a second trustworthiness,
    shuffled. The pick passes the timestamp check, has all 3 runs of every judge, comes from a video not in
    the group, and is the one flagged most for its failure (by most runs, except a failure the generator
    accepts unconfirmed)."""
    import timestamp_check as T
    rng = random.Random(SEED + 6)
    big = HERE / "runs" / G["out"]
    cand = [c for g in json.loads((big / "bad_candidates.json").read_text())["groups"] for slot in g for c in slot
            if c["pair_id"] not in EXCLUDE]
    ratings = read_ratings(judge_files(f"{GENERATOR}-bad-review-run*"), {c["pair_id"] for c in cand})
    failures = FAILURES + ["trustworthiness"]
    rng.shuffle(failures)
    normal = json.loads((OUT / "selection_normal.json").read_text())["groups"]
    used, groups = set(), []
    for group, failure in zip(normal, failures):
        videos = normal_videos(group)
        opts = []
        for c in cand:
            p = c["pair_id"]
            if (c["intended_failure"] != failure or p in used or normal_videos([c]) & videos
                    or any(len(ratings[p][k]) != 3 for k in ("go", "gm", "qw")) or not T.check(p)[0]):
                continue
            runs = ratings[p]["go"] + ratings[p]["gm"]
            opts.append({**c, "flagged_runs": sum(not r["criteria"][CRITERION_OF[failure]] for r in runs),
                         "judged_runs": len(runs), "any_failure_runs": sum(not all(r["criteria"].values()) for r in runs)})
        best = max(opts, key=lambda c: (c["flagged_runs"], c["any_failure_runs"], rng.random()))
        if best["flagged_runs"] <= best["judged_runs"] / 2 and failure not in G.get("unconfirmed_ok", {}):
            raise SystemExit(f"no judged candidate confirms {failure} for group {len(groups) + 1}")
        used.add(best["pair_id"])
        groups.append([{**best, "kind": "bad", "class": "bad"}])
    (OUT / "selection_bad.json").write_text(json.dumps(
        {"seed": SEED + 6, "source": f"unused judged candidates of runs/{G['out']}", "groups": groups,
         **({"unconfirmed_ok": G["unconfirmed_ok"]} if "unconfirmed_ok" in G else {})}, indent=2))
    print("bad pairs:", [(c["intended_failure"], f"{c['flagged_runs']}/{c['judged_runs']}") for g in groups for c in g])


def bad_final():
    """Keep, per slot, the candidate the judges flag most for its intended failure."""
    rng = random.Random(SEED + 2)
    cand = json.loads((OUT / "bad_candidates.json").read_text())["groups"]
    ids = {c["pair_id"] for g in cand for slot in g for c in slot}
    ratings = read_ratings(judge_files(f"{GENERATOR}-bad-review-run*"), ids)
    import timestamp_check as T
    groups = []
    for g in cand:
        chosen = []
        for slot in g:
            slot = [c for c in slot if T.check(c["pair_id"])[0]]
            if not slot:
                raise SystemExit("a slot has no candidate whose timestamp passes; run --stage bad-more")
            for c in slot:
                runs = ratings[c["pair_id"]]["go"] + ratings[c["pair_id"]]["gm"]
                crit = CRITERION_OF[c["intended_failure"]]
                c["flagged_runs"] = sum(not r["criteria"][crit] for r in runs)
                c["judged_runs"] = len(runs)
                c["any_failure_runs"] = sum(not all(r["criteria"].values()) for r in runs)
            best = max(slot, key=lambda c: (c["flagged_runs"], c["any_failure_runs"], rng.random()))
            chosen.append({**best, "kind": "bad", "class": "bad"})
        groups.append(chosen)
    (OUT / "selection_bad.json").write_text(json.dumps(
        {"seed": SEED + 2, "groups": groups, **({"unconfirmed_ok": G["unconfirmed_ok"]} if "unconfirmed_ok" in G else {})},
        indent=2))
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
    pairs = load_pairs()
    dup = near_duplicates(pairs)
    flat = majority("flat2")
    ratings = read_ratings(normal_files(), set(pairs))
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
                cands = [p for p in pairs if p not in used and p not in EXCLUDE and not dup[p] and flat[p][0] == topic
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
                    if in_group < (2 if SIZE == "100" else 1) and (new := candidates(topic)):
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
    ap.add_argument("--stage", choices=["normal", "bad-candidates", "bad-more", "bad-final", "bad-reuse", "swap-normal"],
                    default="normal")
    ap.add_argument("--size", choices=list(SIZES), default="100")
    ap.add_argument("--generator", choices=list(GENERATORS), default=GENERATOR)
    args = ap.parse_args()
    use(args.generator, args.size)
    stage = args.stage
    {"normal": select_normal, "bad-candidates": bad_candidates, "bad-more": bad_more, "bad-final": bad_final,
     "bad-reuse": bad_reuse,
     "swap-normal": swap_normal}[stage]()


if __name__ == "__main__":
    main()
