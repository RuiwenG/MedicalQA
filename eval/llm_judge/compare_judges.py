"""Agreement statistics for any number of LLM judges, each with three runs.

usage: python3 compare_judges.py OUT.json [GEMMA_CSV1 GEMMA_CSV2 GEMMA_CSV3]
       python3 compare_judges.py OUT.json --manifest MANIFEST.json
Run from the repo root. With no Gemma CSVs it compares DeepSeek and gpt-oss only.
A manifest names the arms ({"approach": "v1", ...}) and the judges with their
run CSVs ({"k", "name", "model", "runs": [...]}) for any other generated set.
"""
import csv, json, sys, statistics as st, itertools, collections

B = "eval/results/Master-Teepa_deepseek-v4-pro_full_Eval_"
G = "eval/results/Master-Teepa_openai-gpt-oss-120b_full_Eval_"
ARMS = {"SingleAgent-v1": "v1", "SingleAgent-v2": "v2", "SingleAgent": "v3"}

def load(path, keep=None):
    return {r["QA ID"]: r for r in csv.DictReader(open(path, encoding="utf-8"))
            if r["Approach"] in ARMS and (keep is None or r["Approach"] in keep)}

MANIFEST = None
if len(sys.argv) > 3 and sys.argv[2] == "--manifest":
    MANIFEST = json.load(open(sys.argv[3], encoding="utf-8"))
    ARMS = MANIFEST["arms"]

ds_r1 = {}
ds_r1.update(load(B + "20260909_154820.csv", {"SingleAgent-v1"}))
ds_r1.update(load(B + "20260909_005804.csv", {"SingleAgent-v2"}))
ds_r1.update(load(B + "20260909_131051.csv", {"SingleAgent"}))
JUDGES = [
    {"k": "ds", "name": "DeepSeek", "model": "deepseek-v4-pro",
     "runs": [ds_r1, load(B + "20260909_173408.csv"), load(B + "20260911_152538.csv")]},
    {"k": "go", "name": "gpt-oss", "model": "openai/gpt-oss-120b",
     "runs": [load(G + "20261001_214603.csv"), load(G + "20261001_223931.csv"), load(G + "20261001_224310.csv")]},
]
if MANIFEST:
    JUDGES = [{"k": j["k"], "name": j["name"], "model": j["model"],
               "runs": [load(p) for p in j["runs"]]} for j in MANIFEST["judges"]]
elif len(sys.argv) > 2:
    JUDGES.append({"k": "gm", "name": "Gemma 4", "model": "google/gemma-4-31b-it",
                   "runs": [load(p) for p in sys.argv[2:]]})

ALL = [r for J in JUDGES for r in J["runs"]]
U = sorted(set.intersection(*[set(r) for r in ALL]))
mism = sum(1 for u in U if len({r[u]["Question"] for r in ALL}) > 1)
print("run sizes:", {J["k"]: [len(r) for r in J["runs"]] for J in JUDGES})
print(f"common pairs: {len(U)} | question-text mismatches: {mism}")
assert mism == 0

M = [("Trustworthiness", "Q&A Trustworthiness", ["Excellent", "Good", "Fair", "Poor"], "Yes"),
     ("Clarity", "Q&A Clarity", ["Very easy to understand", "Easy", "Somewhat difficult", "Difficult"], "Yes"),
     ("Usefulness", "Q&A Usefulness", ["Highly useful", "Useful", "Limited useful", "Not useful"], "Yes"),
     ("Care Safety", "Q&A Care Safety", None, "No"),
     ("Standalone", "Q&A Standalone", ["Fully standalone", "Mostly standalone", "Somewhat dependent", "Not standalone"], "Yes")]
REC = ["Yes", "Yes, but with minor edits (meaning unchanged)", "No, it needs major edits", "No"]
arm_of = {u: ARMS[JUDGES[0]["runs"][1][u]["Approach"]] for u in U}
IDX = {a: [i for i, u in enumerate(U) if arm_of[u] == a] for a in ("v1", "v2", "v3")}

def passv(run, col, good): return [run[u][col + " Yes/No"] == good for u in U]
def attrv(run, col, scale):
    return [len(scale) - scale.index(run[u][col + " Attribute"]) if run[u][col + " Attribute"] in scale else None for u in U]
def recv(run): return [4 - REC.index(run[u]["Caregiver Recommendation"]) if run[u]["Caregiver Recommendation"] in REC else None for u in U]

def pairs(a, b): return [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
def agree(a, b): p = pairs(a, b); return sum(x == y for x, y in p) / len(p)
def kappa(a, b):
    p = pairs(a, b); n = len(p); po = sum(x == y for x, y in p) / n
    pa = sum(x for x, _ in p) / n; pb = sum(y for _, y in p) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return None if pe >= 0.999999 else (po - pe) / (1 - pe)
def ac1(a, b):
    p = pairs(a, b); n = len(p); po = sum(x == y for x, y in p) / n
    pi = (sum(x for x, _ in p) + sum(y for _, y in p)) / (2 * n); pe = 2 * pi * (1 - pi)
    return None if pe >= 0.999999 else (po - pe) / (1 - pe)
def wkappa(a, b, k=4):
    p = pairs(a, b); n = len(p); O = [[0] * k for _ in range(k)]
    for x, y in p: O[x - 1][y - 1] += 1
    ra = [sum(O[i]) for i in range(k)]; cb = [sum(O[i][j] for i in range(k)) for j in range(k)]
    num = den = 0.0
    for i in range(k):
        for j in range(k):
            w = ((i - j) ** 2) / ((k - 1) ** 2); num += w * O[i][j]; den += w * ra[i] * cb[j] / n
    return None if den == 0 else 1 - num / den
def _rank(v):
    s = sorted(range(len(v)), key=lambda i: v[i]); r = [0] * len(v); i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and v[s[j + 1]] == v[s[i]]: j += 1
        for t in range(i, j + 1): r[s[t]] = (i + j) / 2 + 1
        i = j + 1
    return r
def spearman(a, b):
    p = pairs(a, b); x = _rank([q for q, _ in p]); y = _rank([q for _, q in p])
    mx, my = st.mean(x), st.mean(y)
    sx = sum((v - mx) ** 2 for v in x) ** .5; sy = sum((v - my) ** 2 for v in y) ** .5
    return None if sx == 0 or sy == 0 else sum((p1 - mx) * (q1 - my) for p1, q1 in zip(x, y)) / (sx * sy)
def within1(a, b): p = pairs(a, b); return sum(abs(x - y) <= 1 for x, y in p) / len(p)
def fleiss(raters):
    """Fleiss' kappa. raters: one aligned vector per rater (None = no rating).
    Nominal: a one-level miss on an ordinal scale counts as full disagreement."""
    m = len(raters)
    subj = [t for t in zip(*raters) if all(x is not None for x in t)]
    n = len(subj)
    if m < 2 or n == 0: return None
    tot = collections.Counter(); p_sum = 0.0
    for t in subj:
        c = collections.Counter(t); tot.update(c)
        p_sum += (sum(v * v for v in c.values()) - m) / (m * (m - 1))
    p_bar = p_sum / n
    p_e = sum((v / (n * m)) ** 2 for v in tot.values())
    return None if p_e >= 0.999999 else (p_bar - p_e) / (1 - p_e)
def summ(vals):
    v = [x for x in vals if x is not None]
    return None if not v else {"mean": st.mean(v), "min": min(v), "max": max(v), "n": len(v)}

KEYS = [J["k"] for J in JUDGES]
PAIRKEYS = [f"{a}|{b}" for i, a in enumerate(KEYS) for b in KEYS[i:]]
def by_pair(vec_of, fn):
    """fn over run pairings: within a judge (3 pairings) and between judges (9)."""
    out = {}
    for i, A in enumerate(JUDGES):
        for B_ in JUDGES[i:]:
            va = [vec_of(r) for r in A["runs"]]; vb = [vec_of(r) for r in B_["runs"]]
            combos = (itertools.combinations(range(len(A["runs"])), 2) if A is B_
                      else itertools.product(range(len(A["runs"])), range(len(B_["runs"]))))
            out[f"{A['k']}|{B_['k']}"] = summ([fn(va[x], vb[y]) for x, y in combos])
    return out

# Majority verdict; with an even number of runs a tie counts as a pass.
def majority(runs, col, good): return [sum(r[u][col + " Yes/No"] == good for r in runs) >= len(runs) / 2 for u in U]
def medattr(runs, col, scale):
    out = []
    for u in U:
        vs = sorted(len(scale) - scale.index(r[u][col + " Attribute"]) for r in runs if r[u][col + " Attribute"] in scale)
        out.append(vs[len(vs) // 2] if vs else None)
    return out

def fleiss_block(vec_of, cons_of):
    """Fleiss' kappa four ways: judges together (one run from each, all run
    combinations), judges' consensus, every run as its own rater, and within
    each judge across its own three runs."""
    vecs = {J_["k"]: [vec_of(r) for r in J_["runs"]] for J_ in JUDGES}
    return {
        "judges_by_run": summ([fleiss([vecs[J_["k"]][ix] for J_, ix in zip(JUDGES, combo)])
                               for combo in itertools.product(*[range(len(J_["runs"])) for J_ in JUDGES])]),
        "judges_consensus": fleiss([cons_of(J_) for J_ in JUDGES]),
        "all_runs": fleiss([v for J_ in JUDGES for v in vecs[J_["k"]]]),
        "within": {J_["k"]: fleiss(vecs[J_["k"]]) for J_ in JUDGES},
    }

res = {"n_pairs": len(U), "arm_n": {a: len(IDX[a]) for a in IDX},
       "judges": [{"k": J["k"], "name": J["name"], "model": J["model"], "n_runs": len(J["runs"])} for J in JUDGES],
       "pairkeys": PAIRKEYS, "metrics": {}}

for lab, col, scale, good in M:
    pb = lambda r, col=col, good=good: passv(r, col, good)
    d = {"scale": scale, "stats": {
        "agreement": by_pair(pb, agree), "kappa": by_pair(pb, kappa), "ac1": by_pair(pb, ac1)}}
    if scale:
        pa = lambda r, col=col, scale=scale: attrv(r, col, scale)
        d["stats"].update({"exact": by_pair(pa, agree), "within1": by_pair(pa, within1),
                           "wkappa": by_pair(pa, wkappa), "spearman": by_pair(pa, spearman)})
    arms = {}
    for a, idx in IDX.items():
        e = {}
        for J in JUDGES:
            e[J["k"]] = {"pass": summ([sum(pb(r)[i] for i in idx) / len(idx) * 100 for r in J["runs"]])}
            if scale:
                e[J["k"]]["attr"] = summ([st.mean([x for x in (attrv(r, col, scale)[i] for i in idx) if x is not None]) for r in J["runs"]])
        e["pair_agree"] = {}
        for i, A in enumerate(JUDGES):
            for B_ in JUDGES[i + 1:]:
                e["pair_agree"][f"{A['k']}|{B_['k']}"] = summ(
                    [sum(pb(A["runs"][x])[k] == pb(B_["runs"][y])[k] for k in idx) / len(idx)
                     for x, y in itertools.product(range(len(A["runs"])), range(len(B_["runs"])))])
        arms[a] = e
    d["arms"] = arms
    cons = {J["k"]: majority(J["runs"], col, good) for J in JUDGES}
    med = {J["k"]: medattr(J["runs"], col, scale) for J in JUDGES} if scale else {}
    d["consensus"] = {}
    for i, A in enumerate(JUDGES):
        for B_ in JUDGES[i + 1:]:
            ca, cb = cons[A["k"]], cons[B_["k"]]
            c = {"a_fail_b_pass": sum((not x) and y for x, y in zip(ca, cb)),
                 "a_pass_b_fail": sum(x and (not y) for x, y in zip(ca, cb)),
                 "both_fail": sum((not x) and (not y) for x, y in zip(ca, cb)),
                 "both_pass": sum(x and y for x, y in zip(ca, cb)),
                 "agreement": agree(ca, cb), "kappa": kappa(ca, cb), "ac1": ac1(ca, cb)}
            if scale:
                ma, mb = med[A["k"]], med[B_["k"]]
                ct = [[0] * 4 for _ in range(4)]
                for x, y in pairs(ma, mb): ct[4 - x][4 - y] += 1
                c.update({"attr_exact": agree(ma, mb), "attr_wkappa": wkappa(ma, mb), "crosstab": ct})
            d["consensus"][f"{A['k']}|{B_['k']}"] = c
    d["fail_counts"] = {J["k"]: sum(not x for x in cons[J["k"]]) for J in JUDGES}
    d["fleiss"] = {"verdict": fleiss_block(pb, lambda J_: cons[J_["k"]])}
    if scale:
        d["fleiss"]["rating"] = fleiss_block(pa, lambda J_: med[J_["k"]])
    def errs(runs):
        c = collections.Counter()
        for r in runs:
            for u in U:
                if r[u][col + " Yes/No"] != good:
                    for e in r[u][col + " Error Type"].split("; "):
                        if e and e != "No issue": c[e] += 1
        return {k: v / len(runs) / len(U) * 100 for k, v in c.items()}
    d["errors"] = {J["k"]: errs(J["runs"]) for J in JUDGES}
    res["metrics"][lab] = d

rv = lambda r: recv(r)
rec = {"stats": {"exact": by_pair(rv, agree), "within1": by_pair(rv, within1),
                 "wkappa": by_pair(rv, wkappa), "spearman": by_pair(rv, spearman)},
       "arms": {}}
for a, idx in IDX.items():
    rec["arms"][a] = {}
    for J in JUDGES:
        dist = [st.mean([sum(1 for i in idx if recv(r)[i] == lvl) / len(idx) * 100 for r in J["runs"]]) for lvl in (4, 3, 2, 1)]
        rec["arms"][a][J["k"]] = {"dist": dist,
                                  "yes": summ([sum(1 for i in idx if recv(r)[i] == 4) / len(idx) * 100 for r in J["runs"]])}
def rec_median(J_):
    out = []
    for i in range(len(U)):
        vs = sorted(x for x in (recv(r)[i] for r in J_["runs"]) if x is not None)
        out.append(vs[len(vs) // 2] if vs else None)
    return out
rec["fleiss"] = fleiss_block(rv, rec_median)
res["recommendation"] = rec
json.dump(res, open(sys.argv[1], "w"), indent=1)

f = lambda s: "  —  " if s is None else f"{s['mean']:.3f}"
print(f"\nStandalone pass, mean of 3: " + "  ".join(
    f"{J['name']} " + "/".join(f"{res['metrics']['Standalone']['arms'][a][J['k']]['pass']['mean']:.1f}" for a in ('v1', 'v2', 'v3'))
    for J in JUDGES))
print(f"\n{'κ binary':<16}" + "".join(f"{p:>9}" for p in PAIRKEYS))
for lab, *_ in M:
    print(f"{lab:<16}" + "".join(f"{f(res['metrics'][lab]['stats']['kappa'][p]):>9}" for p in PAIRKEYS))
