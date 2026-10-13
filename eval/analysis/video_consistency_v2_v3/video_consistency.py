"""Read saved MedicalQA ratings; write a small, reproducible analysis outside the repo.

Run: python3 -B video_consistency.py --repo /path/to/MedicalQA --output /path/to/analysis_outputs
Requires numpy and Pillow. No generation, API calls, or repository writes.
The fixed retrospective analysis specification is in plan.json.
"""
import argparse
import csv
import hashlib
import json
import math
import subprocess
from collections import defaultdict
from pathlib import Path

import numpy as np


HERE = Path(__file__).resolve().parent
VERSIONS = ("v2", "v3")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def holm(pvalues):
    """Holm step-down adjusted p-values, returned in the original order."""
    values = np.asarray(pvalues, dtype=float)
    order = np.argsort(values, kind="stable")
    adjusted = np.empty(len(values))
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, min(1.0, (len(values) - rank) * values[index]))
        adjusted[index] = running
    return adjusted


def bootstrap_indices(videos, iterations, seed):
    """Draw whole matched video blocks within each source collection."""
    rng = np.random.default_rng(seed)
    blocks = []
    for dataset in sorted({dataset for dataset, _ in videos}):
        indices = np.array([i for i, (base, _) in enumerate(videos) if base == dataset])
        blocks.append(rng.choice(indices, size=(iterations, len(indices)), replace=True))
    return np.concatenate(blocks, axis=1)


def interval(differences, draws, confidence=0.95):
    means = np.asarray(differences)[draws].mean(axis=1)
    tail = (1 - confidence) / 2
    return np.quantile(means, [tail, 1 - tail], method="linear")


def permutation_p(differences, signs):
    """Two-sided paired label swaps. +1 correction for Monte Carlo sampling."""
    differences = np.asarray(differences, dtype=float)
    observed = abs(differences.mean())
    null = np.abs((signs * differences).mean(axis=1))
    return float((1 + np.count_nonzero(null >= observed - 1e-12)) / (len(signs) + 1))


def leave_one_out(differences):
    differences = np.asarray(differences, dtype=float)
    if len(differences) < 2:
        raise ValueError("At least two matched videos are needed")
    return (differences.sum() - differences) / (len(differences) - 1)


def require_equal(actual, expected, message):
    if actual != expected:
        raise ValueError(message)


def read_model(repo, model, plan, provenance):
    manifest_path = repo / f"eval/llm_judge/judges_{model}_manifest.json"
    qa_path = repo / f"eval/llm_judge/qa_data_{model}.json"
    for path in (manifest_path, qa_path):
        provenance[path.relative_to(repo).as_posix()] = sha256(path)
    manifest = json.loads(manifest_path.read_text())
    payload = json.loads(qa_path.read_text())
    arm_map = manifest["arms"]
    canonical = {}
    for pair in payload["pairs"]:
        if arm_map.get(pair["approach"]) in VERSIONS:
            if pair["uid"] in canonical:
                raise ValueError(f"Duplicate canonical QA ID: {pair['uid']}")
            canonical[pair["uid"]] = pair
    if not canonical:
        raise ValueError(f"No ordinary v2/v3 Q&As for {model}")
    expected_videos = {(base, i) for base, count in (("Master", 10), ("Teepa", 15))
                       for i in range(1, count + 1)}
    for version in VERSIONS:
        videos = {(p["dataset"], int(p["video"])) for p in canonical.values()
                  if arm_map[p["approach"]] == version}
        require_equal(videos, expected_videos, f"Missing/unexpected source videos in {model} {version}")
    ordered_videos = sorted(expected_videos)
    video_map = {(base, int(number)): meta["url"]
                 for base, rows in payload["videos"].items() for number, meta in rows.items()}

    # Verify logged generator settings, without claiming unrecorded settings were controlled.
    suffix = "qwen-2.5-7b-instruct" if model == "qwen2.5-7b" else "qwen3-14b"
    settings_fields = ("model", "backend", "endpoint", "provider", "seed", "thinking", "prompt_format")
    settings = []
    for base in ("Master", "Teepa"):
        pair_meta = []
        for version in VERSIONS:
            path = repo / base / f"SingleAgent-{suffix}-{version}_generation_meta.json"
            provenance[path.relative_to(repo).as_posix()] = sha256(path)
            meta = json.loads(path.read_text())
            require_equal(meta["prompt_version"], version, f"Wrong version in {path.name}")
            recorded = {field: meta.get(field) for field in settings_fields}
            if any(field not in meta for field in ("model", "backend", "provider", "seed", "prompt_format")):
                raise ValueError(f"Missing essential generation metadata: {path.name}")
            pair_meta.append(recorded)
        require_equal(pair_meta[0], pair_meta[1], f"Recorded generator settings changed in {model}/{base}")
        settings.append({"dataset": base, "recorded_settings": pair_meta[0]})

    metric_names = list(plan["outcomes"])
    groups = []
    audit = []
    for judge in manifest["judges"]:
        ratings = defaultdict(list)
        for run_number, relative in enumerate(judge["runs"], 1):
            path = repo / relative
            provenance[relative] = sha256(path)
            with path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                required = {"QA ID", "Dataset", "Video Index", "Approach", "Question", "Answer"}
                required.update(spec["column"] for spec in plan["outcomes"].values())
                if not required.issubset(reader.fieldnames or []):
                    raise ValueError(f"Missing rating columns: {relative}")
                rows = list(reader)
            seen = set()
            all_seen = set()
            for row in rows:
                uid = row["QA ID"].strip()
                if uid in all_seen:
                    raise ValueError(f"Duplicate rating ID in {relative}: {uid}")
                all_seen.add(uid)
                if arm_map.get(row["Approach"]) not in VERSIONS:
                    continue
                if uid not in canonical:
                    raise ValueError(f"Unknown v2/v3 rating ID in {relative}: {uid}")
                pair = canonical[uid]
                for column, key in (("Approach", "approach"), ("Question", "question"), ("Answer", "answer"), ("Dataset", "dataset")):
                    require_equal(row[column].strip(), str(pair[key]).strip(), f"{column} mismatch in {relative}: {uid}")
                require_equal(int(row["Video Index"]), int(pair["video"]), f"Video mismatch: {uid}")
                scores = []
                for name in metric_names:
                    spec = plan["outcomes"][name]
                    value = row[spec["column"]].strip()
                    if value not in spec["allowed"]:
                        raise ValueError(f"Invalid/missing {name} rating in {relative}: {uid}: {value!r}")
                    scores.append(float(value in spec["positive"]))
                ratings[uid].append(scores)
                seen.add(uid)
            require_equal(seen, set(canonical), f"Incomplete v2/v3 rating coverage in {relative}")
            audit.append({"model": model, "judge": judge["name"], "run": run_number,
                          "file": relative, "input_rows": len(rows), "included_rows": len(seen)})
        qa_means = {uid: np.mean(values, axis=0) for uid, values in ratings.items()}
        scores = np.zeros((2, len(ordered_videos), len(metric_names)))
        counts = np.zeros((2, len(ordered_videos)), dtype=int)
        for version_index, version in enumerate(VERSIONS):
            for video_index, video in enumerate(ordered_videos):
                values = [qa_means[uid] for uid, pair in canonical.items()
                          if (pair["dataset"], int(pair["video"])) == video and arm_map[pair["approach"]] == version]
                counts[version_index, video_index] = len(values)
                scores[version_index, video_index] = np.mean(values, axis=0)
        groups.append({"model": model, "judge": judge["name"], "judge_key": judge["k"],
                       "judge_model": judge["model"], "runs": len(judge["runs"]),
                       "videos": ordered_videos, "video_urls": video_map,
                       "scores": scores, "counts": counts})
    return groups, audit, settings


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def table(rows, primary=False):
    columns = ["Model", "Judge", "Outcome", "v2 (%)", "v3 (%)", "Difference (pp)", "95% CI (pp)"]
    if primary:
        columns += ["Holm p", "LOO range (pp)", "Videos + / = / −"]
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        cells = [row["model"], row["judge"], row["outcome"], f"{row['v2_percent']:.2f}",
                 f"{row['v3_percent']:.2f}", f"{row['difference_pp']:+.2f}",
                 f"[{row['ci_low_pp']:+.2f}, {row['ci_high_pp']:+.2f}]"]
        if primary:
            cells += [f"{row['p_holm']:.4f}", f"[{row['loo_min_pp']:+.2f}, {row['loo_max_pp']:+.2f}]",
                      f"{row['videos_improved']} / {row['videos_tied']} / {row['videos_declined']}"]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def draw_plot(path, rows):
    """Small static scientific figure using Pillow (no extra plotting dependencies)."""
    from PIL import Image, ImageDraw, ImageFont
    width, height = 1400, 1120
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font_path = "/System/Library/Fonts/Helvetica.ttc"
    def font(size):
        try:
            return ImageFont.truetype(font_path, size)
        except OSError:
            return ImageFont.load_default(size=size)
    title, text, small = font(32), font(23), font(20)
    draw.text((40, 25), "Qwen3-14B: v3 minus v2 by source video", font=title, fill="#172a43")
    draw.text((40, 72), "DeepSeek ratings. Each point is one video's average change, not an individual Q&A.", font=small, fill="#526175")
    metrics = ("Standalone", "Trustworthiness")
    for panel, metric in enumerate(metrics):
        points = [row for row in rows if row["model"] == "qwen3-14b" and row["judge"] == "DeepSeek" and row["outcome"] == metric]
        left, right = 240 + panel * 680, 625 + panel * 680
        bound = max(10, math.ceil(max(abs(row["difference_pp"]) for row in points) / 10) * 10)
        def x(value):
            return left + (value + bound) / (2 * bound) * (right - left)
        draw.text((left, 122), metric, font=text, fill="#172a43")
        for tick in np.linspace(-bound, bound, 5):
            xx = x(tick)
            draw.line((xx, 175, xx, 997), fill="#e1e6ee", width=1)
            draw.text((xx, 1015), f"{tick:+.0f}", anchor="mt", font=small, fill="#526175")
        draw.line((x(0), 175, x(0), 997), fill="#64748b", width=2)
        for index, row in enumerate(points):
            yy = 191 + index * 32
            draw.text((left - 18, yy), f"{row['dataset']} {row['video']}", anchor="rm", font=small, fill="#344256")
            color = "#2563a6" if row["dataset"] == "Master" else "#b35c21"
            xx = x(row["difference_pp"])
            draw.ellipse((xx - 5, yy - 5, xx + 5, yy + 5), fill=color)
        draw.text(((left + right) / 2, 1050), "Difference (percentage points)", anchor="mt", font=small, fill="#344256")
    image.save(path)


def write_report(out, summary, audits):
    primary = [row for row in summary if row["primary"]]
    secondary = [row for row in summary if not row["primary"]]
    standalone = next(row for row in primary if row["outcome"] == "Standalone")
    trust = next(row for row in primary if row["outcome"] == "Trustworthiness")
    statements = []
    for row in primary:
        direction = "increased" if row["difference_pp"] > 0 else "decreased" if row["difference_pp"] < 0 else "did not change"
        evidence = "The Holm-adjusted test detects a difference" if row["p_holm"] < 0.05 else "The Holm-adjusted test does not establish a difference"
        robustness = "The estimated direction is unchanged after removing any one video" if row["loo_sign_reversals"] == 0 else f"The estimated direction reverses after removing {row['loo_sign_reversals']} video(s)"
        statements.append(f"- **{row['outcome']} {direction} by {abs(row['difference_pp']):.2f} percentage points** "
                          f"(95% CI {row['ci_low_pp']:+.2f} to {row['ci_high_pp']:+.2f}; Holm p = {row['p_holm']:.4f}). "
                          f"{evidence}. {robustness}. It improved on {row['videos_improved']} videos, "
                          f"was unchanged on {row['videos_tied']}, and declined on {row['videos_declined']}.")
    conclusion = "The two outcomes do not support a simple claim that v3 is better overall."
    if standalone["p_holm"] < 0.05 and standalone["difference_pp"] > 0:
        conclusion = "The primary analysis supports an improvement in Standalone quality."
        if trust["difference_pp"] < 0:
            conclusion += " Trustworthiness moves in the opposite direction, so this should be reported as a potential trade-off, not an overall quality improvement."
    report = f"""# Video-level consistency analysis

## Main finding

{conclusion}

{chr(10).join(statements)}

## Primary comparison

Ordinary Qwen3-14B outputs, **v3 versus v2**, judged by DeepSeek. There are **25 matched source videos: 10 Master and 15 Teepa**, with {standalone['qas_v2']} Q&As in v2 and {standalone['qas_v3']} in v3. Each Q&A has three saved DeepSeek ratings. Positive differences favour v3.

{table(primary, primary=True)}

These averages can differ from earlier pooled Q&A averages because every video, rather than every Q&A, receives equal weight. The leave-one-video-out (LOO) range describes how the average changes when each video is omitted. It is **not** a confidence interval. A stable average direction does not mean every video improved. Confidence intervals are ordinary, unadjusted 95% intervals. Holm correction applies to the two primary p-values, not to these intervals.

![Differences for each source video](video_differences.png)

## Supporting checks

Other judges on 14B and all judges on 7B are descriptive checks, not extra primary hypothesis tests. Every row uses the same 25 videos and 226 Q&As per version. The 7B DeepSeek data have two repeats; the other model/judge combinations have three. Judge ratings are never pooled into independent observations.

{table(secondary)}

These intervals are unadjusted. Secondary judge and model comparisons are exploratory and do not replace the primary comparison.

## Analysis methods

1. Read only the run files named in the existing 14B and 7B manifests. Excluded v1, Plus, human ratings, and deliberately bad challenge examples.
2. Checked every included QA ID, question, answer, dataset, and video against its canonical Q&A file. Required complete repeated ratings, valid outcome labels, no duplicate IDs, and all 25 videos in both versions. Logged generator settings matched between v2 and v3 within each model/source collection. All {len(audits)} saved judge runs passed these checks.
3. **Standalone:** the saved Yes/No rating is positive when it is Yes. **Trustworthiness:** the saved four-level rating is positive when it is Excellent or Good. Trustworthiness here is not the separate Yes/No field, nor a mean 1–4 score.
4. Average repeats within each Q&A, Q&As within each video/version, then matched video differences. Every video has equal weight, regardless of its Q&A count. Versions are paired by source video, not by question number.
5. Resample whole paired videos **10,000 times**, with replacement within Master and Teepa, retaining 10 and 15 source videos respectively in each draw. Use the 2.5th and 97.5th percentiles of the average differences for the 95% interval. All Q&As and repeat ratings stay in their original video block. Seed is 42.
6. For the two primary outcomes, use **10,000 two-sided paired video-block label swaps** under the null assumption that version labels are exchangeable within a video. Use the plus-one Monte Carlo p-value correction and Holm adjustment across these two outcomes. Seed is 43 for label swaps.
7. Remove each video once and recalculate the remaining-video average, without changing the scoring rule. Count positive, tied, and negative per-video differences.

## Interpretation limits

- This is a **retrospective robustness analysis**, not a preregistered experiment: aggregate results had already been viewed before this plan was fixed.
- The uncertainty describes variability across these source videos, conditional on the observed generated outputs and saved judges. It does not measure variability from regenerating answers, choosing new judge models, or sampling new human annotators.
- There are 25 source-video clusters, not hundreds of independent videos. This collection is not a random sample of all dementia-care videos. Related videos or shared speakers may add dependence not captured by video-only clustering.
- These are LLM quality judgements, not independent clinical verification. Lack of a detected Trustworthiness decline is not proof that Trustworthiness is preserved.
- No generation setting beyond the recorded metadata was independently verified. Because the prompt changed as a bundle, this is not an ablation proving which instruction caused an effect.
- Human-rating analyses involving repeated annotators across videos require additional handling of annotator dependence. Human evaluation and challenge-item validation are outside the scope of this analysis.

## Files and reproducibility

- `summary.csv`: estimates, confidence intervals, primary p-values, LOO ranges, and Master/Teepa descriptive differences.
- `per_video.csv`: version means, Q&A counts, and differences for each video, model, judge, and outcome.
- `leave_one_video_out.csv`: every omitted-video result.
- `provenance.json`: exact input file names and SHA-256 hashes, recorded generator settings, validation counts, software version, repository commit, and plan/script hashes. Source file hashes were unchanged after the analysis.
- `plan.json`, `video_consistency.py`, and `test_video_consistency.py`: the fixed specification, runnable analysis, and tests. With NumPy and Pillow available, run `python3 -B video_consistency.py --repo /path/to/MedicalQA --output /path/outside/the/repository`.

The analysis reused existing generated Q&As and saved judge ratings. It did not involve new generation, judge inference, or human annotation.

## Method reference

Med-PaLM 2 kept repeated answer ratings together during clustered resampling and permutation testing. This analysis applies that dependency-preserving principle to source-video blocks. Video-level LOO and Holm correction across the two primary tests are additional methods, not methods attributed to that paper. [Singhal et al., *Toward expert-level medical question answering with large language models*, Nature Medicine (2025)](https://www.nature.com/articles/s41591-024-03423-7).
"""
    (out / "report.md").write_text(report, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=HERE)
    args = parser.parse_args()
    repo, out = args.repo.resolve(), args.output.resolve()
    if out == repo or repo in out.parents:
        raise ValueError("Analysis outputs must be outside the project folder")
    plan = json.loads((HERE / "plan.json").read_text())
    input_hashes, groups, audits, generation_metadata = {}, [], [], {}
    for model in plan["models"]:
        model_groups, model_audit, settings = read_model(repo, model, plan, input_hashes)
        groups.extend(model_groups)
        audits.extend(model_audit)
        generation_metadata[model] = settings
    for group in groups[1:]:
        require_equal(group["videos"], groups[0]["videos"], "Source videos differ across generators/judges")
        require_equal(group["video_urls"], groups[0]["video_urls"], "Source URLs differ across generators")
    draws = bootstrap_indices(groups[0]["videos"], plan["bootstrap_iterations"], plan["seed"])
    signs = np.random.default_rng(plan["seed"] + 1).choice([-1, 1], size=(plan["permutation_iterations"], len(groups[0]["videos"])))
    summary, per_video, loo_rows = [], [], []
    for group in groups:
        for metric_index, metric in enumerate(plan["outcomes"]):
            a, b = group["scores"][0, :, metric_index], group["scores"][1, :, metric_index]
            difference = b - a
            ci = interval(difference, draws, plan["confidence_level"])
            loo = leave_one_out(difference)
            primary = group["model"] == plan["primary_model"] and group["judge_key"] == plan["primary_judge"]
            label = {"model": group["model"], "judge": group["judge"], "outcome": metric}
            row = dict(label, primary=primary, videos=len(a), judge_runs=group["runs"],
                       qas_v2=int(group["counts"][0].sum()), qas_v3=int(group["counts"][1].sum()),
                       v2_percent=float(a.mean() * 100), v3_percent=float(b.mean() * 100),
                       difference_pp=float(difference.mean() * 100), ci_low_pp=float(ci[0] * 100), ci_high_pp=float(ci[1] * 100),
                       p_raw=permutation_p(difference, signs) if primary else "", p_holm="",
                       loo_min_pp=float(loo.min() * 100), loo_max_pp=float(loo.max() * 100),
                       loo_sign_reversals=int(np.count_nonzero(loo * difference.mean() < -1e-12)),
                       videos_improved=int(np.count_nonzero(difference > 1e-12)),
                       videos_tied=int(np.count_nonzero(np.abs(difference) <= 1e-12)),
                       videos_declined=int(np.count_nonzero(difference < -1e-12)))
            for base in ("Master", "Teepa"):
                selection = [i for i, (dataset, _) in enumerate(group["videos"]) if dataset == base]
                row[f"{base.lower()}_difference_pp"] = float(difference[selection].mean() * 100)
            summary.append(row)
            for i, (dataset, video) in enumerate(group["videos"]):
                per_video.append(dict(label, dataset=dataset, video=video, source_url=group["video_urls"][(dataset, video)],
                                      qas_v2=int(group["counts"][0, i]), qas_v3=int(group["counts"][1, i]),
                                      v2_percent=float(a[i] * 100), v3_percent=float(b[i] * 100), difference_pp=float(difference[i] * 100)))
                loo_rows.append(dict(label, omitted_dataset=dataset, omitted_video=video,
                                     remaining_videos=len(a) - 1, difference_pp=float(loo[i] * 100)))
    primary_rows = [row for row in summary if row["primary"]]
    require_equal(len(primary_rows), 2, "Expected exactly two primary tests")
    for row, adjusted in zip(primary_rows, holm([row["p_raw"] for row in primary_rows])):
        row["p_holm"] = float(adjusted)
    for relative, expected in input_hashes.items():
        require_equal(sha256(repo / relative), expected, f"Input changed during analysis: {relative}")
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "summary.csv", summary)
    write_csv(out / "per_video.csv", per_video)
    write_csv(out / "leave_one_video_out.csv", loo_rows)
    draw_plot(out / "video_differences.png", per_video)
    metadata = {"source_sha256": input_hashes, "generation_metadata": generation_metadata, "rating_validation": audits,
                "numpy_version": np.__version__, "repository_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
                "plan_sha256": sha256(HERE / "plan.json"), "script_sha256": sha256(Path(__file__)), "source_hashes_unchanged_after_analysis": True}
    (out / "provenance.json").write_text(json.dumps(metadata, indent=2) + "\n")
    write_report(out, summary, audits)
    print(json.dumps({"primary_results": primary_rows, "validated_judge_runs": len(audits), "output": str(out)}, indent=2))


if __name__ == "__main__":
    main()
