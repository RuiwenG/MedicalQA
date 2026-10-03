"""Observed vs chance agreement, Fleiss kappa, Gwet AC1 and quadratic-weighted kappa for the three judges.

usage: python3 kappa_paradox.py [OUT.json] [--manifest MANIFEST.json]
Without a manifest it uses the Qwen3.5-Plus runs listed in fleiss_judges.py.
"""
import csv, sys, json, itertools, statistics as st
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fleiss_judges as F

argv = [a for a in sys.argv[1:]]
MANIFEST = None
if "--manifest" in argv:
    i = argv.index("--manifest"); MANIFEST = json.load(open(argv[i + 1], encoding="utf-8")); del argv[i:i + 2]
if MANIFEST:
    ARM_OF = MANIFEST["arms"]
    def _load(path):
        return {r["QA ID"]: r for r in csv.DictReader(open(path, encoding="utf-8")) if r["Approach"] in ARM_OF}
    runs = {j["name"]: [_load(p) for p in j["runs"]] for j in MANIFEST["judges"]}
else:
    ARM_OF = {"SingleAgent-v1": "v1", "SingleAgent-v2": "v2", "SingleAgent": "v3"}
    runs = {j: [F.load_run(x) for x in p] for j, p in F.RUNS.items()}
judges = list(runs)
common = sorted(set.intersection(*[set(r) for rs in runs.values() for r in rs]))
Q = 4

def level(r, u, col, scale):
    v = r[u][col]; return scale.index(v) if v in scale else None

def subjects(metric, combo, pool):
    col, scale = F.METRICS[metric]; out = []
    for u in pool:
        t = tuple(level(runs[j][i], u, col, scale) for j, i in zip(judges, combo))
        if None not in t: out.append(t)
    return out

def stats(subj):
    m = len(subj[0]); n = len(subj); tot = Counter(); p = 0.0
    for t in subj:
        c = Counter(t); tot.update(c); p += (sum(v*v for v in c.values()) - m) / (m*(m-1))
    po = p / n
    pi = {k: tot[k]/(n*m) for k in range(Q)}
    pe = sum(v*v for v in pi.values())
    fk = (po-pe)/(1-pe) if pe < 0.999999 else None
    pe_g = sum(v*(1-v) for v in pi.values())/(Q-1)
    ac1 = (po-pe_g)/(1-pe_g)
    # mean pairwise quadratic-weighted Cohen kappa
    qws = []
    for a, b in itertools.combinations(range(m), 2):
        x = [t[a] for t in subj]; y = [t[b] for t in subj]
        w = lambda i, j: ((i-j)/(Q-1))**2
        do = sum(w(i, j) for i, j in zip(x, y))/n
        px = Counter(x); py = Counter(y)
        de = sum(px[i]*py[j]*w(i, j) for i in range(Q) for j in range(Q))/(n*n)
        qws.append(1-do/de if de > 0 else None)
    qws = [q for q in qws if q is not None]
    qw = st.mean(qws) if qws else None
    all3 = sum(len(set(t)) == 1 for t in subj)/n
    w1 = sum(max(t)-min(t) <= 1 for t in subj)/n
    return dict(po=po, pe=pe, fleiss=fk, ac1=ac1, qwk=qw, all3=all3, within1=w1)

def summarise(pool):
    out = {}
    for metric in F.METRICS:
        per = [stats(subjects(metric, c, pool)) for c in itertools.product(*[range(len(runs[j])) for j in judges])]
        out[metric] = {k: {"mean": st.mean([p[k] for p in per if p[k] is not None]),
                           "min": min(p[k] for p in per if p[k] is not None),
                           "max": max(p[k] for p in per if p[k] is not None)} for k in per[0]}
    return out

def dist(pool):
    out = {}
    for metric, (col, scale) in F.METRICS.items():
        out[metric] = {}
        for j in judges:
            c = Counter(); n = 0
            for r in runs[j]:
                for u in pool:
                    lv = level(r, u, col, scale)
                    if lv is not None: c[lv] += 1; n += 1
            out[metric][j] = [c[k]/n for k in range(Q)]
    return out

first = next(iter(runs.values()))[0]
v3 = [u for u in common if ARM_OF[first[u]["Approach"]] == "v3"]
by_arm = {a: [u for u in common if ARM_OF[first[u]["Approach"]] == a] for a in sorted(set(ARM_OF.values()))}
res = {"n_v3": len(v3), "n_all": len(common), "v3": summarise(v3), "all": summarise(common), "dist_v3": dist(v3),
       "dist_by_arm": {a: dist(p) for a, p in by_arm.items()}, "n_by_arm": {a: len(p) for a, p in by_arm.items()},
       "scales": {m: s for m, (_, s) in F.METRICS.items()}}
out = argv[0] if argv else str(Path(__file__).resolve().parent / "kappa_paradox.json")
json.dump(res, open(out, "w"), indent=1)
for m, d in res["v3"].items():
    print(f"{m:26s} obs {d['po']['mean']:.2f} chance {d['pe']['mean']:.2f} fleiss {d['fleiss']['mean']:.2f} AC1 {d['ac1']['mean']:.2f} QWK {d['qwk']['mean']:.2f} all3 {d['all3']['mean']:.0%} w1 {d['within1']['mean']:.0%}")
print("all arms")
for m, d in res["all"].items():
    print(f"{m:26s} obs {d['po']['mean']:.2f} chance {d['pe']['mean']:.2f} fleiss {d['fleiss']['mean']:.2f} AC1 {d['ac1']['mean']:.2f} QWK {d['qwk']['mean']:.2f} all3 {d['all3']['mean']:.0%} w1 {d['within1']['mean']:.0%}")
