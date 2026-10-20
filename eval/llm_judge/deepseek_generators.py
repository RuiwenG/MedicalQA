"""DeepSeek V4 Pro's verdicts on the three SingleAgent generator sets.

Qwen3.5-Plus, Qwen3-14B and Qwen2.5-7B each generated pairs from the v1, v2
and v3 prompts. This summarises, per generator and prompt version, DeepSeek's
pass rates (mean and range over its runs), 4-level rating distributions,
recommendation, error labels and run-to-run consistency.

usage: python3 eval/llm_judge/deepseek_generators.py [OUT.json]   (from the repo root)
"""
import csv
import itertools
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
RES = HERE.parent / "results"
B = "Master-Teepa_deepseek-v4-pro_full_Eval_"


def manifest_runs(name):
    m = json.loads((HERE / name).read_text(encoding="utf-8"))
    ds = next(j for j in m["judges"] if j["k"] == "ds")
    return m["arms"], [HERE.parent.parent / p for p in ds["runs"]]


arms_14, runs_14 = manifest_runs("judges_qwen3-14b_manifest.json")
arms_7, runs_7 = manifest_runs("judges_qwen2.5-7b_manifest.json")

# Qwen3.5-Plus: run 1 was judged one prompt version at a time (three CSVs).
SETS = {
    "plus": {"label": "Qwen3.5-Plus", "model": "qwen3.5-plus",
             "arms": {"SingleAgent-v1": "v1", "SingleAgent-v2": "v2", "SingleAgent": "v3"},
             "runs": [[(RES / (B + "20260909_154820.csv"), "SingleAgent-v1"),
                       (RES / (B + "20260909_005804.csv"), "SingleAgent-v2"),
                       (RES / (B + "20260909_131051.csv"), "SingleAgent")],
                      [(RES / (B + "20260909_173408.csv"), None)],
                      [(RES / (B + "20260911_152538.csv"), None)]]},
    "q14": {"label": "Qwen3-14B", "model": "qwen/qwen3-14b",
            "arms": arms_14, "runs": [[(p, None)] for p in runs_14]},
    "q7": {"label": "Qwen2.5-7B", "model": "qwen/qwen-2.5-7b-instruct",
           "arms": arms_7, "runs": [[(p, None)] for p in runs_7]},
}
METRICS = [
    ("Trustworthiness", "Q&A Trustworthiness", ["Excellent", "Good", "Fair", "Poor"], "Yes"),
    ("Clarity", "Q&A Clarity", ["Very easy to understand", "Easy", "Somewhat difficult", "Difficult"], "Yes"),
    ("Usefulness", "Q&A Usefulness", ["Highly useful", "Useful", "Limited useful", "Not useful"], "Yes"),
    ("Care Safety", "Q&A Care Safety", None, "No"),
    ("Standalone", "Q&A Standalone", ["Fully standalone", "Mostly standalone", "Somewhat dependent", "Not standalone"], "Yes"),
]
REC = ["Yes", "Yes, but with minor edits (meaning unchanged)", "No, it needs major edits", "No"]
VERSIONS = ("v1", "v2", "v3")


def load(parts, arms):
    rows = {}
    for path, keep in parts:
        for r in csv.DictReader(open(path, encoding="utf-8")):
            if r["Approach"] in arms and (keep is None or r["Approach"] == keep):
                rows[r["QA ID"]] = r
    return rows


def summ(v):
    return {"mean": sum(v) / len(v), "min": min(v), "max": max(v), "n": len(v)}


def kappa(a, b):
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(a) | set(b)) / (n * n)
    return None if pe >= 0.999999 else (po - pe) / (1 - pe)


out = {"sets": {}, "versions": VERSIONS, "metrics": [m[0] for m in METRICS], "rec_scale": REC}
for key, cfg in SETS.items():
    runs = [load(parts, cfg["arms"]) for parts in cfg["runs"]]
    uids = sorted(set.intersection(*[set(r) for r in runs]))
    version_of = {u: cfg["arms"][runs[0][u]["Approach"]] for u in uids}
    by_v = {v: [u for u in uids if version_of[u] == v] for v in VERSIONS}
    s = {"label": cfg["label"], "model": cfg["model"], "n_runs": len(runs), "n_pairs": len(uids),
         "n_by_version": {v: len(by_v[v]) for v in VERSIONS}, "metrics": {}}
    for name, col, scale, good in METRICS:
        md = {"scale": scale, "by_version": {}, "self_kappa": None}
        for v in VERSIONS:
            pool = by_v[v]
            e = {"pass": summ([100 * sum(r[u][col + " Yes/No"] == good for u in pool) / len(pool) for r in runs])}
            if scale:
                c = Counter(r[u][col + " Attribute"] for r in runs for u in pool)
                tot = sum(c[x] for x in scale)
                e["levels"] = [c[x] / tot for x in scale]
                e["mean_level"] = sum((4 - i) * c[x] for i, x in enumerate(scale)) / tot
            errs = Counter()
            for r in runs:
                for u in pool:
                    if r[u][col + " Yes/No"] != good:
                        for lab in (r[u].get(col + " Error Type") or "").split(";"):
                            lab = lab.strip()
                            if lab and lab.lower() not in ("no issue", "none"):
                                errs[lab] += 1
            e["errors"] = {k: 100 * n / (len(pool) * len(runs)) for k, n in errs.most_common()}
            md["by_version"][v] = e
        ks = [kappa([a[u][col + " Yes/No"] for u in uids], [b[u][col + " Yes/No"] for u in uids])
              for a, b in itertools.combinations(runs, 2)]
        ks = [k for k in ks if k is not None]
        md["self_kappa"] = summ(ks) if ks else None
        s["metrics"][name] = md
    rec = {}
    for v in VERSIONS:
        c = Counter(r[u]["Caregiver Recommendation"] for r in runs for u in by_v[v])
        tot = sum(c[x] for x in REC)
        rec[v] = [c[x] / tot for x in REC]
    s["recommendation"] = rec
    out["sets"][key] = s

dest = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "deepseek_generators.json"
dest.write_text(json.dumps(out, indent=1), encoding="utf-8")
for key, s in out["sets"].items():
    print(f"{s['label']:12s} runs={s['n_runs']} pairs={s['n_pairs']} {s['n_by_version']}")
    for m, md in s["metrics"].items():
        print(f"   {m:15s} pass " + " / ".join(f"{md['by_version'][v]['pass']['mean']:.1f}" for v in VERSIONS)
              + (f"   self-κ {md['self_kappa']['mean']:.2f}" if md["self_kappa"] else ""))
    print("   rec Yes      " + " / ".join(f"{100 * s['recommendation'][v][0]:.0f}" for v in VERSIONS))
