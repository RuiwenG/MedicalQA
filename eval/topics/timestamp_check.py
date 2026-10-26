"""Check that a QA pair's timestamp points at the part of the transcript it came from.

The pair (question + answer) and every 60-second window of its transcript (10-second
steps) are embedded with all-mpnet-base-v2, the topic model's encoder. The cited
range, padded by PAD seconds each side, scores the best similarity of the windows it
overlaps. A timestamp is trusted when it exists, lies inside the video, scores at least
MIN_SIM, and no window more than NEAR seconds away scores BETTER times as much: the
pair is grounded where it says and not plainly somewhere else.

(A first version counted shared words; answers paraphrase too much for that to work.)
"""
import glob
import json
import re
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PAD, WINDOW, STEP = 20, 60, 10
MIN_SIM, BETTER, NEAR = 0.35, 1.25, 90
STOP = set("""the a an and or of to in on for with is are be can how what why when does do it its this that their they
them person people dementia caregiver caregivers help helps living someone who may might should your you as by from at
more about into than also other others way ways important when while which there these those because could would""".split())


@lru_cache(maxsize=None)
def transcript(dataset, video):
    segs = json.loads(open(glob.glob(str(REPO / dataset / str(video) / "Transcript" / "transcript*.json"))[0]).read())
    return [(s.get("start", 0.0), s.get("end") or s.get("start", 0.0), s.get("text", "")) for s in segs]


_ENCODER = None


def encoder():
    global _ENCODER
    if _ENCODER is None:
        from sentence_transformers import SentenceTransformer
        _ENCODER = SentenceTransformer("sentence-transformers/all-mpnet-base-v2")
    return _ENCODER


@lru_cache(maxsize=None)
def windows(dataset, video):
    """(starts, normalised embeddings) of the transcript's 60-second windows."""
    segs = transcript(dataset, video)
    duration = max(e for _, e, _ in segs)
    starts = list(range(0, max(1, int(duration) - WINDOW // 2), STEP))
    texts = [" ".join(t for s, e, t in segs if s <= a + WINDOW and e >= a) or "." for a in starts]
    return starts, encoder().encode(texts, normalize_embeddings=True)


def check(pair_id):
    """(ok, reason, cited similarity, best similarity) for one pair, read from its finalQA.json."""
    ds, v, approach, q = re.match(r"(\w+?)_v(\d+)_(.+)_q(\d+)$", pair_id).groups()
    row = json.loads((REPO / ds / v / approach / "QA results" / "finalQA.json").read_text())[int(q) - 1]
    segs = transcript(ds, int(v))
    duration = max(e for _, e, _ in segs)
    s, e = row.get("time_start_sec"), row.get("time_end_sec")
    if s is None or e is None or not row.get("timestamp"):
        return False, "no timestamp", 0.0, 0.0
    if not 0 <= s < e or e > duration + 1:
        return False, f"range {s}-{e} invalid for a {duration:.0f}s video", 0.0, 0.0
    starts, emb = windows(ds, int(v))
    sims = emb @ encoder().encode([row["question"] + " " + row["answer"]], normalize_embeddings=True)[0]
    best = float(sims.max())
    cited = float(max(sim for a, sim in zip(starts, sims) if a <= e + PAD and a + WINDOW >= s - PAD))
    if cited < MIN_SIM:
        return False, f"cited window similarity {cited:.2f} (floor {MIN_SIM})", cited, best
    # A clearly better match far from the cited range means the pair comes from elsewhere; a better match next
    # to it only means the range starts a little early or late, and a topic discussed twice is no error.
    far = [(sim, a) for a, sim in zip(starts, sims) if a > e + NEAR or a + WINDOW < s - NEAR]
    if far and max(far)[0] > cited * BETTER:
        sim, a = max(far)
        return False, f"cited window {cited:.2f}; a better match ({sim:.2f}) at {a // 60}:{a % 60:02d}", cited, best
    return True, "ok", cited, best
