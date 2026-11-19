"""Mixed decks for the Qwen3.5-Plus judge experiment.

Every deck holds all the normal (good) Qwen3.5-Plus pairs plus one fifth of the bad
pairs. The bad pairs are split into five disjoint folds, so the five decks together
use each bad pair exactly once. Each deck is shuffled, so pairs reach the judge in
random order, not grouped by video and not good-then-bad. Seeds are fixed so the
decks can be rebuilt exactly.
"""
import json, random
from pathlib import Path

H = Path(__file__).parent
LJ = H.parent
SEED = 20261118

good = json.loads((LJ / "qa_data_qwen35plus-v3.json").read_text())["pairs"]
bad = json.loads((LJ / "qa_data_qwen3.5-plus-bad-v3.json").read_text())["pairs"]

order = sorted(p["uid"] for p in bad)
random.Random(SEED).shuffle(order)
folds = [order[i::5] for i in range(5)]
fold_of = {u: i + 1 for i, f in enumerate(folds) for u in f}

manifest = {"seed": SEED, "good": len(good), "bad": len(bad), "decks": []}
for k in range(1, 6):
    deck = good + [p for p in bad if fold_of[p["uid"]] == k]
    random.Random(SEED + k).shuffle(deck)
    out = H / f"qa_data_qwen35plus-mix-fold{k}.json"
    out.write_text(json.dumps({"pairs": deck}, ensure_ascii=False))
    nb = sum(fold_of.get(p["uid"]) == k for p in deck)
    manifest["decks"].append({"fold": k, "file": out.name, "shuffle_seed": SEED + k,
                              "pairs": len(deck), "good": len(deck) - nb, "bad": nb,
                              "bad_uids": sorted(u for u in folds[k - 1])})
    print(out.name, len(deck), "good", len(deck) - nb, "bad", nb, "first 5:", [p["uid"].split("_SingleAgent")[0] + ("*" if "bad" in p["approach"] else "") for p in deck[:5]])
(H / "manifest.json").write_text(json.dumps(manifest, indent=1))
