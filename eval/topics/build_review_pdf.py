"""PDF of the 100-pair human review set (5 groups of 20), from select_review_set.py.

Each pair shows its source (video, title, timestamp, link), its new topic (codebook v2,
6 topics), its old topic and subtopic (codebook v1, 12 topics) with how many of the nine
labelling models chose them, and every judge's ratings
(criteria passed out of 3 runs, and "recommend as is" out of 3). Bad pairs also show
their intended failure and how many judge runs flagged it; until they are chosen,
each group shows a placeholder.

Usage: python eval/topics/build_review_pdf.py   (writes runs/review_set/review_set.pdf)
"""
import json
import re
from datetime import date
from collections import Counter
from html import escape
import csv
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

import select_review_set as S

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUT = HERE / "runs" / "review_set"
INK, MUTED, RULE, ACCENT = colors.HexColor("#1f2328"), colors.HexColor("#5f6670"), colors.HexColor("#d7dce0"), \
    colors.HexColor("#1f5fae")
CLASS_LABEL = {"high": "Normal pair, highly rated", "low": "Normal pair, low rated", "bad": "Deliberately bad pair"}
SHORT = {"Trustworthiness": "Trust", "Clarity": "Clarity", "Usefulness": "Useful", "Care Safety": "Safe",
         "Standalone": "Standalone"}

base = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=base["Title"], fontName="Helvetica-Bold", fontSize=20, leading=24,
                    alignment=TA_LEFT, textColor=INK, spaceAfter=6)
H2 = ParagraphStyle("h2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=15, leading=19,
                    textColor=INK, spaceBefore=0, spaceAfter=4)
BODY = ParagraphStyle("body", parent=base["BodyText"], fontName="Helvetica", fontSize=9.5, leading=13, textColor=INK)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8.5, leading=11, textColor=MUTED)
TAG = ParagraphStyle("tag", parent=BODY, fontName="Helvetica-Bold", fontSize=9, textColor=ACCENT)
Q = ParagraphStyle("q", parent=BODY, fontName="Helvetica-Bold")


def video_meta():
    """(dataset, video) -> (title, url), from video_titles.json (the dataset card's register of the 76 kept videos)."""
    titles = json.loads((HERE / "video_titles.json").read_text())
    return {(k.split()[0], int(k.split()[1])): (v["title"], f"https://www.youtube.com/watch?v={v['youtube_id']}")
            for k, v in titles.items()}


def timestamps(pair_id):
    ds, v, approach, q = re.match(r"(\w+?)_v(\d+)_(.+)_q(\d+)$", pair_id).groups()
    rows = json.loads((REPO / ds / v / approach / "QA results" / "finalQA.json").read_text())
    r = rows[int(q) - 1]
    stamp = r.get("timestamp") or (f"{r['time_start_sec'] // 60}:{r['time_start_sec'] % 60:02d}" if r.get("time_start_sec") is not None else "no timestamp")
    return stamp, r.get("time_start_sec")


def ratings_table(ratings):
    head = ["Judge"] + [SHORT[c] for c in S.CRITERIA] + ["Recommend as is"]
    rows = [head]
    notes = []
    for k, (name, _) in S.JUDGES.items():
        runs = ratings.get(k, [])
        if not runs:
            rows.append([name] + ["not run"] + [""] * len(S.CRITERIA))
            continue
        n = len(runs)
        cells = [f"{sum(r['criteria'][c] for r in runs)}/{n}" for c in S.CRITERIA]
        rec = f"{sum(r['recommendation'] == 'Yes' for r in runs)}/{n}"
        rows.append([name] + cells + [rec])
        for c in S.CRITERIA:
            errs = {e.strip() for r in runs if not r["criteria"][c] for e in r["errors"][c].split(";")
                    if e.strip() not in ("", "No issue")}
            if errs:
                notes.append(f"{name}, {SHORT[c].lower()}: {'; '.join(sorted(errs))}")
    t = Table(rows, colWidths=[1.25 * inch] + [0.66 * inch] * len(S.CRITERIA) + [1.15 * inch], hAlign="LEFT")
    style = [("FONT", (0, 0), (-1, -1), "Helvetica", 8), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
             ("TEXTCOLOR", (0, 0), (-1, -1), INK), ("LINEBELOW", (0, 0), (-1, 0), 0.5, RULE),
             ("ALIGN", (1, 0), (-1, -1), "CENTER"), ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("TOPPADDING", (0, 0), (-1, -1), 2)]
    for i, row in enumerate(rows[1:], 1):
        if row[1] == "not run":
            style += [("SPAN", (1, i), (-1, i)), ("TEXTCOLOR", (1, i), (-1, i), MUTED), ("ALIGN", (1, i), (-1, i), "LEFT")]
            continue
        for j, cell in enumerate(row[1:], 1):
            got, of = map(int, cell.split("/"))
            if got < of:
                style.append(("TEXTCOLOR", (j, i), (j, i), colors.HexColor("#b3261e")))
                style.append(("FONT", (j, i), (j, i), "Helvetica-Bold", 8))
    t.setStyle(TableStyle(style))
    return t, notes


def new_topic_text(votes, names2):
    """The codebook v2 topic the most models chose, or every topic tied for most."""
    ranked = Counter(votes).most_common()
    top = [t for t, c in ranked if c == ranked[0][1]]
    shown = " / ".join(f"{t} {escape(names2[t])}" for t in top)
    return shown + (f" (tie, {ranked[0][1]}/9 models each)" if len(top) > 1 else f" ({ranked[0][1]}/9 models)")


def pair_block(n, item, pair, ratings, flat, sub, names, meta, new_votes, names2):
    title, url = meta.get((pair["dataset"], pair["video"]), ("", ""))
    stamp, start = timestamps(item["pair_id"])
    link = f"{url}&t={int(start)}s" if url and start is not None else url
    f_id, f_share = flat[item["pair_id"]]
    s_id, s_share = sub[item["pair_id"]]
    f_votes, s_votes = round(f_share * 9), round(s_share * 9)
    parts = [
        Paragraph(f"{n}. {CLASS_LABEL[item['class']]}", TAG),
        Paragraph(f"<b>Source:</b> {pair['dataset']} {pair['video']}, {escape(title)} · {escape(stamp)} · "
                  f"<link href='{escape(link)}' color='#1f5fae'>video</link> · <font color='#5f6670'>{item['pair_id']}</font>", SMALL),
        Paragraph(f"<b>New topic:</b> {new_topic_text(new_votes[item['pair_id']], names2)}", SMALL),
        Paragraph(f"<b>Old topic:</b> {f_id} {escape(names[f_id])} ({f_votes}/9 models) · "
                  f"<b>Subtopic:</b> {s_id} {escape(names[s_id])} ({s_votes}/9)", SMALL),
    ]
    if item["class"] == "bad":
        failure = item["intended_failure"].replace("_", " ")
        parts.append(Paragraph(f"<b>Intended failure:</b> {failure} · the judges flagged {failure} in "
                               f"{item['flagged_runs']} of {item['judged_runs']} gpt-oss and Gemma runs", SMALL))
    parts += [
        Spacer(1, 4),
        Paragraph(f"Q: {escape(pair['question'])}", Q),
        Paragraph(f"A: {escape(pair['answer'])}", BODY),
        Spacer(1, 4),
    ]
    table, notes = ratings_table(ratings)
    parts.append(table)
    if notes:
        parts.append(Paragraph("<b>Judge notes:</b> " + escape(" · ".join(notes)), SMALL))
    parts += [Spacer(1, 3),
              Paragraph("<b>Reviewer:</b> [ ] keep &nbsp;&nbsp; [ ] edit &nbsp;&nbsp; [ ] replace &nbsp;&nbsp; "
                        "Notes: ______________________________________________", SMALL),
              Spacer(1, 6),
              Table([[""]], colWidths=[7 * inch], rowHeights=[1], style=[("LINEABOVE", (0, 0), (-1, -1), 0.5, RULE)]),
              Spacer(1, 6)]
    return KeepTogether(parts)


def main():
    sel = json.loads((OUT / "selection_normal.json").read_text())
    bad_file = OUT / "selection_bad.json"
    bad = json.loads(bad_file.read_text())["groups"] if bad_file.exists() else None
    codebook = json.loads((HERE / "codebook_v1.json").read_text())
    names = {o["id"]: o["name"] for o in codebook["topics"] + codebook["subtopics"]}
    codebook2 = json.loads((HERE / "codebook_v2.json").read_text())
    names2 = {t["id"]: t["name"] for t in codebook2["topics"]}
    sub_to_new = {x["id"]: x["parent"] for x in codebook2["subtopics"]}
    pairs = {json.loads(l)["pair_id"]: json.loads(l) for l in (HERE / "pairs_qwen3-14b-v3.jsonl").open()}
    files = S.judge_files("qwen3-14b-run*")
    for k in ("go", "gm", "qw"):
        files[k] += S.judge_files("qwen3-14b-new69-run*")[k]
    ratings = S.read_ratings(files, set(pairs))
    flat, sub = S.majority("flat2"), S.majority("sub2")
    meta = video_meta()
    if bad:
        # Bad pairs: their own text, judge runs and the nine models' labels (topic read off each model's subtopic).
        ids = {c["pair_id"] for g in bad for c in g}
        pairs.update({json.loads(l)["pair_id"]: json.loads(l) for l in S.BAD_PAIRS.open()
                      if json.loads(l)["pair_id"] in ids})
        ratings.update(S.read_ratings(S.judge_files("qwen3-14b-bad-review-run*"), ids))
        parent = {x["id"]: x["parent"] for x in codebook["subtopics"]}
        votes = {p: [] for p in ids}
        for f in S.BAD_LABELS.glob("*_sub2.csv"):
            for r in csv.DictReader(f.open()):
                if r["pair_id"] in ids and r["label"]:
                    votes[r["pair_id"]].append(r["label"])
        for p, v in votes.items():
            top_sub = Counter(v).most_common(1)[0]
            top_topic = Counter(parent[x] for x in v).most_common(1)[0]
            sub[p], flat[p] = (top_sub[0], top_sub[1] / 9), (top_topic[0], top_topic[1] / 9)

    # New topics: each model's round 2 subtopic mapped to its codebook v2 topic, normal and bad pairs alike.
    review_ids = {c["pair_id"] for g in sel["groups"] for c in g} | ({c["pair_id"] for g in bad for c in g} if bad else set())
    new_votes = {p: [] for p in review_ids}
    for d in (HERE / "runs" / "llm_labels", S.BAD_LABELS):
        for f in sorted(d.glob("*_sub2.csv")):
            for r in csv.DictReader(f.open()):
                if r["pair_id"] in new_votes and r["label"] in sub_to_new:
                    new_votes[r["pair_id"]].append(sub_to_new[r["label"]])

    story = [Paragraph("MedicalQA human review set", H1),
             Paragraph(f"100 Qwen3-14B v3 QA pairs in 5 groups of 20 · draft of {date.today():%d %B %Y}", SMALL),
             Spacer(1, 10)]
    intro = [
        "<b>What each group holds.</b> 16 normal pairs (12 highly rated and 4 low rated by the LLM judges) and 4 "
        "deliberately bad pairs, one for each intended failure: trustworthiness, clarity, usefulness and care safety.",
        "<b>How the normal pairs were chosen.</b> At random (seed 42) from the 76 kept videos, so each group covers all "
        "12 topics once plus 4 extra topics, with at most one pair per video in a group. Highly rated: every criterion "
        "passed and \"recommend as is\" in all 6 runs of gpt-oss-120b and Gemma 4 (3 runs each). Low rated: a criterion "
        "failed and not \"recommend as is\" in at least 4 of those 6 runs.",
        "<b>Topics.</b> The majority label of nine local models (round 2 prompt), with how many of the nine chose it. "
        "Each pair shows two topic schemes. <i>New topic</i> is the advisor's 6-topic codebook v2: T1 Understanding "
        "Dementia (old F05, F08, F10, F12), T2 Caregiving Support and Resources (F02, F03), T3 Communication and "
        "Behavior (F04), T4 Physical Care and Assistance (F06), T5 Daily Living and Independence (F01), T6 Social "
        "Engagement and Activities (F07, F09, F11); the dignity subtopic (S14) moves from F03 to T3. <i>Old topic</i> "
        "is the 12-topic codebook v1 the pairs were selected with, so the groups are balanced over the old topics, "
        "not the new ones. A tie lists every topic tied for most votes.",
        "<b>Ratings.</b> Criteria passed out of the runs each judge made (Safe = no care-safety problem found). A number "
        "in red means at least one run failed it. DeepSeek rated only the original 25 videos (Master, Teepa).",
        "<b>How the bad pairs were chosen.</b> They come from Ruiwen's inverted prompt (Qwen3-14B asked to fail one "
        "criterion per pair). The generator often made a different failure from the one asked for, so for each slot "
        "2 to 6 candidates with a clear topic were judged by gpt-oss-120b and Gemma 4 (3 runs each), and the one "
        "flagged most for its intended failure was kept. Each shows how many of its 6 runs flagged that failure. "
        "DeepSeek did not rate the bad pairs.",
        "<b>For reviewers.</b> Mark each pair keep, edit or replace, and add a note on anything wrong with it.",
    ]
    story += [Paragraph(p, BODY) for p in intro]
    story.append(PageBreak())

    for g, group in enumerate(sel["groups"], 1):
        order = sorted(group, key=lambda c: (c["class"] != "high", c["topic"]))
        story.append(Paragraph(f"Group {g}", H2))
        story.append(Paragraph(f"{sum(c['class'] == 'high' for c in group)} highly rated and "
                               f"{sum(c['class'] == 'low' for c in group)} low rated normal pairs"
                               + ("; then 4 deliberately bad pairs" if bad else
                                  "; the 4 bad pairs are still being generated"), SMALL))
        members = [c["pair_id"] for c in group] + ([c["pair_id"] for c in bad[g - 1]] if bad else [])
        per_new = Counter(new_topic_text(new_votes[p], names2).split(" ")[0] for p in members)
        story.append(Paragraph("New topics in this group: " + " · ".join(
            f"{t} {per_new[t]}" for t in names2) + " (a tie counts under the first topic listed)", SMALL))
        story.append(Spacer(1, 8))
        n = 0
        for item in order:
            n += 1
            story.append(pair_block(n, item, pairs[item["pair_id"]], ratings[item["pair_id"]], flat, sub, names, meta,
                                    new_votes, names2))
        if bad:
            for item in sorted(bad[g - 1], key=lambda c: S.FAILURES.index(c["intended_failure"])):
                n += 1
                story.append(pair_block(n, item, pairs[item["pair_id"]], ratings[item["pair_id"]], flat, sub, names,
                                        meta, new_votes, names2))
        else:
            story.append(Paragraph("<b>17–20. Deliberately bad pairs:</b> pending. They are being generated with "
                                   "Ruiwen's inverted prompt and will be judged and labelled before they are added.",
                                   SMALL))
        story.append(PageBreak())

    OUT.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(OUT / "review_set.pdf"), pagesize=letter, leftMargin=0.75 * inch,
                            rightMargin=0.75 * inch, topMargin=0.7 * inch, bottomMargin=0.7 * inch,
                            title="MedicalQA human review set", author="MedicalQA")
    doc.build(story, onLaterPages=lambda c, d: (c.setFont("Helvetica", 8), c.setFillColor(MUTED),
                                                 c.drawRightString(7.75 * inch, 0.45 * inch, f"Page {d.page}")))
    print("wrote", OUT / "review_set.pdf")


if __name__ == "__main__":
    main()
