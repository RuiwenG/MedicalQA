# Preprocessing & Scaling Notes

_Started 2026-08-17. Working notes for the corpus expansion from the current
~25-video pilot to hundreds of hours of dementia-caregiver content._

## Scope decision (2026-08-17)

**Only two approaches are in scope: `SingleQA` (single-agent) and
`MultiAgentChunking` (multi-agent).**

RAG and LLMChunking/DualAgent are **dropped** — not evaluated, not run, not
reported. All cost and throughput numbers below assume two approaches.

Two things follow from this:

- **The GPU is no longer needed for generation.** RAG was the only component
  requiring local BGE-M3 embeddings and ChromaDB. The 3090 is now needed only
  for Whisper transcription (and for local Qwen, if `QWEN_BACKEND=local`).
- **This aligns the LLM judge with the human study.** The pilot annotation site
  already excludes DualAgent and RAG (`SITE["exclude"]`, `eval/llm_judge/judge.py:57`),
  and `judge.py`'s docstring notes that `--mode full` was "the only evaluation
  those two approaches get." Dropping them removes a divergence rather than
  creating one. `--mode full` should be updated to skip them.

Dead code to leave alone for now but exclude from the paper: `RAG/`,
`LLMChunking/`, and the `DualAgent` / `RAG` output directories under `Teepa/`.

## Corpus construction — decisions and open questions

### Transcription: normalize, don't filter

Original plan was to keep only videos with human-uploaded captions and discard
auto-caption videos. **Recommended change: transcribe everything with Whisper
instead.**

Reasons:

- **Current provenance is unknown.** `download_transcripts.py` passes
  `--write-subs` and `--write-auto-subs` together, and `subtitle_file()` selects
  by language code only — yt-dlp names manual and ASR tracks identically
  (`caption.en.json3`). Nothing in `Teepa/` or `Master/` records which it got.
- **The preference order may actively select ASR.** `LANGUAGE_TRACKS["english"]`
  is `("en-orig", "en")`, and `en-orig` is YouTube's original-language
  auto-dub track, which lives under automatic captions. **TODO: verify with
  `yt-dlp --list-subs` on a known video.**
- **Caption availability is a weak selection criterion.** It correlates with
  channel and upload era, not content quality — an uncontrolled selection effect
  in the paper.
- Whisper large-v3 beats YouTube auto-captions and gives uniform quality across
  the whole corpus, which is a defensible one-line methods statement.

**Free validation set:** on videos that *do* have manual captions, WER of
Whisper against those captions is a reportable number.

### Visual dependence: filter at segment level, not video level

Original plan was to keep only podcasts, to avoid content that needs visual
context. Right motivation — Teepa's demonstration content ("watch my hand here",
"see how she pulls back") produces transcripts that don't carry their own
meaning — but "podcast" is a lossy proxy.

**Recommended:** score *segments* for deictic/visual-reference language
(*here, this, like so, you can see, watch, notice how, as I'm showing*), then
drop or mask those segments while keeping the rest of the video. Keeps the ~70%
of a demo video that is verbal explanation. Describable in the paper as a
"visual-grounding filter."

Caveat either way: skewing toward podcasts shifts the corpus toward anecdote and
interview and away from procedural how-to, which is often where the caregiver
value is.

### Cross-video topic concatenation: do NOT do this as preprocessing

Original plan was to cut sections from different videos on the same topic,
concatenate them, and generate QA over the merged document. **Recommended
against**, for four reasons in order of severity:

1. **It subsumes the variable being measured.** MultiAgentChunking's architect
   agent *is* a topic segmenter. Handing it a pre-grouped, topically homogeneous
   document does its job by hand and collapses the Single-vs-Multi comparison.
2. **It breaks grounding.** `finalQA.json` carries `timestamp`,
   `time_start_sec`, `time_end_sec` (commit 86869bd), and the methodology
   section promises segment-grounded answers. Merging makes those offsets
   meaningless unless a segment→(video_id, original_start) map is threaded
   through every pipeline and remapped on output.
3. **Cross-video redundancy becomes cross-video blending.** The same expert
   restates material with drift across videos. Merged, the generator emits
   near-duplicates or fuses two variants into an answer neither source made —
   a safety problem in health content, and exactly what a faithfulness judge
   should catch.
4. It is the least reversible and most labour-intensive step in the plan.

**Do this instead:** keep the video as the generation unit, then group by topic
*after* generation — embed and cluster the QA pairs, or label them against a
caregiver topic taxonomy (wandering, sundowning, bathing refusal, driving,
hospice transitions). Same deliverable ("every QA about bathing refusal, across
40 videos"), full provenance intact, cheap and reversible.

If cross-video merging is still wanted, run it as a **separate condition**
against the per-video corpus so it is a measured question, not a baked-in
assumption.

### Metadata sidecar (do this now, it's cheap)

Transcript JSON is a bare segment list with no video identity, and
`FileHandler.read_transcript` asserts `isinstance(list)`. Add a sidecar
`transcript-<lang>.meta.json` (video_id, url, title, caption_type, transcription
model, duration) rather than wrapping the list — same information, nothing
downstream breaks.

## Cost model — 300 hours of audio, two approaches

### 1. Whisper transcription — ~10 GPU-hours, effectively $0

Currently pinned to the slow implementation (`openai-whisper==20250625`,
`preprocess.py:14`).

| Implementation | ~Speed vs realtime | GPU-hours for 300h |
| --- | --- | --- |
| `openai-whisper` large-v3 (current) | ~5x | ~60 |
| `faster-whisper` large-v3, fp16 | ~12-15x | ~20-25 |
| `faster-whisper` large-v3, **batched** | ~30x | **~10** |
| `large-v3-turbo`, batched | ~60x | ~5 |

Electricity for 10 GPU-hours at ~350 W is under a dollar. The real spend is
GPU-hours on a card **time-sliced five ways** with jupyter/ollama/plex — budget
2-3x the wall-clock under contention.

Switching to `faster-whisper` with `BatchedInferencePipeline` is a small change
to `transcribe()` and buys 5-6x. **Highest-leverage edit in this document.**

Operational notes:
- Don't hold the GPU with local Qwen (16.7 GiB) while transcribing — the two
  won't co-exist in 24 GB. Sequence the phases, or set `QWEN_BACKEND=api`
  during transcription.
- Run as a **checkpointed job, skip-if-exists per video**. The k3s API server
  restarts under heavy I/O; losing state at hour 8 of a 10-hour run is the
  failure mode to design against.
- Storage is a non-issue: ~34 GB of 16 kHz mono for 300h, against 7.8 TB free
  on the PVC.

Hosted ASR, for comparison, would be roughly $10-110 for 300 hours depending on
provider and tier. Verify current pricing before relying on that — but
transcription is a rounding error either way.

#### Model choice (2026-08-17): `large-v3` via faster-whisper, float16, batched

As of mid-2026 OpenAI has not shipped a Whisper v4; `large-v3` and
`large-v3-turbo` remain the current checkpoints.

| Option | VRAM (CT2 fp16) | ~Speed | Verdict |
| --- | --- | --- | --- |
| **`large-v3`** | **~3.1 GB** | ~30x batched | **Chosen** |
| `large-v3-turbo` | ~1.6 GB | ~60x batched | Defensible; validate on pilot |
| `distil-large-v3.5` | ~1.5 GB | ~70x | English-only, more WER drift |
| `openai-whisper` large-v3 (current pin) | ~10 GB | ~5x | Drop |

Rationale:

- **The speed saving is worthless here.** Turbo cuts 300h from ~10 to ~5
  GPU-hours, against a project measured in days. Spend the slack on accuracy.
- **The corpus is rare-word heavy, which is turbo's weak spot.** Turbo is
  large-v3 with the decoder pruned 32 layers -> 4. The "within 0.2-0.3 points"
  benchmarks are aggregate WER dominated by common words. The QA content lives
  in the uncommon ones: *Lewy body*, *anosognosia*, *sundowning*, *donepezil*,
  *aphasia*, plus Teepa's own vocabulary (*Positive Approach to Care*, the
  *GEMS* states). A mangled drug name propagates into a generated answer.
- **The 3 GB footprint matters on this card.** The 3090 is time-sliced five ways
  without memory partitioning; a smaller footprint materially lowers the chance
  an unattended 10-hour run is OOM'd by a neighbour waking up.

**Still to validate:** turbo's best case is clean single-speaker English podcast
audio, which describes much of the corpus. Run both over the 10-hour pilot and
diff on the manual-caption subset (the free validation set) before locking in.

Rejected: NVIDIA Canary-Qwen 2.5B (tops Open ASR leaderboard, 5.63% avg WER) and
Parakeet TDT. Better on paper, but NeMo's dependency tree is a bad risk in an
environment that has already been burned twice by library shadowing.

#### Decoding config — matters more than the checkpoint

```python
segments, info = pipe.transcribe(
    audio, batch_size=16, language="en",
    vad_filter=True,                    # #1 anti-hallucination lever
    condition_on_previous_text=False,   # stops repetition-loop drift on long files
    initial_prompt="Teepa Snow, Positive Approach to Care, GEMS states, "
                   "Lewy body dementia, sundowning, anosognosia, aphasia, ...",
)
```

- `vad_filter` — Whisper hallucinates fluent, confident text during silence and
  music. Over 300 hours this **will** accumulate fabricated segments, and they
  are invisible downstream because they read as normal sentences.
- `condition_on_previous_text=False` — costs some cross-sentence coherence, buys
  robustness against repetition loops on long files. Correct trade for an
  unattended batch.
- `initial_prompt` — free rare-word accuracy from domain vocabulary biasing.

#### Open question: speaker diarization

Podcasts are multi-speaker and Whisper emits no speaker labels. With the corpus
steering toward interview content, expert-vs-host attribution matters — the
host's question text should not generate an "expert answer." WhisperX +
pyannote adds diarization at the cost of a gated HF model and more GPU.
**Decide before the big run, not after.**

#### Environment traps for faster-whisper on this pod

- `requirements.txt:71` pins `nvidia-cudnn-cu13`, which mismatches the installed
  torch (`2.13.0+cu126`, cuDNN 9.2). CTranslate2 needs **cuDNN 9 for CUDA 12**
  (`nvidia-cudnn-cu12`). Symptom: `libcudnn_ops.so.9: cannot open shared object
  file`. (Note: as of 2026-08-19 the driver is **580.173.02**, the CUDA 13.0
  branch, so `cu13` is no longer wrong for the *driver* — only for this torch
  build. `+cu128` is the better-matched torch if the env is rebuilt.)
- The venv was built with `--system-site-packages`, so install with
  `--ignore-installed --no-deps` or conda's copies shadow it silently.

### 2. QA generation (SingleQA + MultiAgentChunking)

#### Which backend is actually in use

Three modes exist in the current config:

| Mode | Selected by | GPU | Where set |
| --- | --- | --- | --- |
| **A. transformers in-process** | `QWEN_BACKEND=local` | **yes, 16.7 GiB** | `k8s/workspace-deployment.yaml:43` |
| **B. vLLM on the pod GPU, over HTTP** | `QWEN_BACKEND=api` + `VLLM_POD_ENDPOINT` | **yes** | `.env` -> `http://localhost:8001/v1` |
| C. hosted Qwen | `QWEN_BACKEND=api` + dashscope | no | `.env` -> `QWEN_API_KEY` |

`QWEN_BACKEND` defaults to `api` in code (`llm_client.py:286`), but the k8s
manifest sets `local` as a container env var, which wins — so **on the pod today
it is mode A**. Off the pod, `_resolve_base_url()` falls through
`QWEN_BASE_URL` -> `QWEN_PROVIDER` -> `VLLM_POD_ENDPOINT`, and `.env` sets the
last one, so anywhere the k8s env is absent it lands on mode B against
`localhost:8001` (works only if vLLM is up).

#### Mode A does not scale to 300 hours

MultiAgent is ~186 calls/video -> ~248,000 calls at 300 hours, ~68M output
tokens. HF `generate` is **single-stream** — no batching across calls — so every
token re-reads all 18 GB of weights at batch size 1. The 3090's 936 GB/s gives a
~52 tok/s ceiling; transformers overhead lands at 20-30 in practice.

| Mode | Throughput | 300h wall-clock | Cash |
| --- | --- | --- | --- |
| A. transformers bf16 (current) | ~20-30 tok/s | **~1 month** | $0 |
| B. vLLM bf16 | ~300-600 tok/s | ~2 days | $0 |
| **B'. vLLM + INT4 (AWQ/GPTQ)** | ~1000+ tok/s | **~15-20 h** | $0 |
| C. hosted API | rate-limit bound | ~4 days | ~$17 |

Continuous batching is the entire difference — one card serving 16-64 concurrent
requests amortizes the weight read across all of them. INT4 helps twice: weights
drop ~17 GB -> ~6 GB, leaving ~15 GB for KV cache instead of ~5 (far more
concurrent sequences), and leaving headroom on a card ollama/plex/jupyter also
touch. **AWQ or GPTQ, not FP8** — the 3090 is Ampere and has no FP8 path.

**Recommendation: mode B' (vLLM + INT4) for the corpus run.** Keep mode A for
small reproducibility spot-checks.

#### Reproducibility trade-off — decide deliberately

`llm_client.py:180` calls `set_seed(SEED)` before every generation; the comment
at line 44 explains why (without it the same transcript yielded between 9 and 19
QA pairs across runs). **That bit-identical guarantee exists only in mode A.**
vLLM's continuous batching varies batch composition run to run, and
floating-point reduction order with it; the `seed` parameter is best-effort,
as `llm_client.py:154` already notes for the hosted API.

So: bitwise reproducibility, or a corpus that finishes this month — not both.
Suggested resolution: take vLLM, and pin the **generated dataset** as the
released artifact rather than the generation procedure (what most published work
does), but state it explicitly in the paper.

#### If mode C (hosted API) is used — measured projection for 300 h

> Supersedes an earlier estimate built on "~186 calls/video" from
> `PROJECT_STATUS.md` and a flat K=20. Both are obsolete. Everything below is
> derived from the **v3 25-video run** (measured 2026-08-20), not extrapolated
> from the older pilot.

**Measured per-video structure** (25 videos, 1,797 total agent calls):

| Agent | Calls/video | Driver |
| --- | --- | --- |
| 1 Architect | 1.0 | per video |
| 2 Inquisitor | 10.4 | per segment |
| **3 Scorer** | **51.4** | **per candidate question** |
| 5 Synthesizer | 9.0 | per selected pair (= K) |
| **Total** | **71.9** | |

**The length-scaled K barely changed the call count.** Agent 5 fell from 20 to
9 calls per video, but agent 3 dominates and its count depends on how many
questions agent 2 generates, not on K. Total went 82.8 -> 71.9 calls/video,
about 13% — not the 55% the pair-count drop suggests.

**300 hours = 1,286 videos** (at the measured 14.0 min average, 9,506
words/content-hour, 1.10 tokens/word):

| | Calls | Input | Output |
| --- | --- | --- | --- |
| SingleAgent | 1,286 | 3.65 M | 1.49 M |
| MultiAgent | 92,386 | **25.09 M** | 3.73 M |
| **Combined** | **93,672** | **28.74 M** | **5.21 M** |

Per video MultiAgent is ~19,500 input / ~2,900 output tokens — a **8x
amplification** of the 2,440-token transcript, because the corpus is re-read
across four agent stages.

Where MultiAgent's input actually goes:

| Agent | Share of MA input |
| --- | --- |
| 3 Scorer | **40%** |
| 2 Inquisitor | 23% |
| 5 Synthesizer | 23% |
| 1 Architect | 14% |

**Agent 3 is the cost centre, not agent 5.** An earlier note claimed the
synthesizer dominated; that was true at K=20 and is no longer. If input cost
needs cutting, batch the scorer (rate several questions per call) rather than
trimming K further — though note the docstring's reason for one-at-a-time
scoring was to reduce cross-item bias, so that is a quality trade.

**Cost for the full 300 h across both approaches:**

| Model | Cost |
| --- | --- |
| `qwen/qwen3.5-plus-20260420` (OpenRouter, pinned) | **$18.00** |
| `qwen3.5-plus` (DashScope) | $24.01 |
| `qwen3.5-flash` | $4.96 |

**Wall-clock is still the binding constraint, not price.** At the measured
2.0 min/video, 1,286 videos is **~43 hours serial**. Parallelising needs
blocker #4 (the CWD collision) fixed first.

Local Qwen serving does **not** scale to this: ~92k generation calls on one 9B
model at single-stream throughput is weeks of GPU time. Would need vLLM with
continuous batching, or stay on the API backend.

### 3. LLM judge (DeepSeek) — ~$23 per pass

**Measured baseline** (`eval/llm_judge/runs/full_run.log`, 2026-08-07,
`deepseek-v4-pro`, `--mode full`):

```
Rated    : 1587/1587 pairs
Tokens   : 405,170 input (cache miss) + 8,336,896 input (cache hit) + 2,039,549 output
Cost     : $1.9809   (miss $0.1762 + hit $0.0302 + out $1.7744)
```

→ **$0.00125 per pair, i.e. $1.25 per 1,000 pairs.**

This rate is robust to scaling because **output is 90% of the cost**
($1.77 of $1.98) at ~1,285 output tokens/pair, and output volume depends on the
form, not on transcript length. Input is nearly free thanks to the per-video
cached transcript prefix (8.3M cached tokens cost $0.03).

Projection for 300 hours:

| Quantity | Value |
| --- | --- |
| Pairs per approach (~1 per 250 words, ~130 wpm) | ~9,400 |
| Pairs for two approaches | **~18,700** |
| One judge pass | **~$23** |
| Five passes (prompt iteration, re-runs) | **~$120** |
| Same on `deepseek-v4-flash` (out $0.28 vs $0.87) | ~$8/pass |

> Supersedes an earlier estimate of $300-500 per pass, which assumed frontier
> model pricing. DeepSeek is roughly an order of magnitude cheaper, and the
> measured rate is what to plan against.

### 4. Total

| Line item | 300 hours, two approaches |
| --- | --- |
| Whisper (phase 1) | ~10 GPU-hours, ~$0 |
| QA generation, local vLLM + INT4 (phase 2) | ~15-20 GPU-hours, ~$0 |
| QA generation, hosted API instead (**measured**) | **$18** (OpenRouter pinned), no GPU |
| LLM judge, 1 pass over ~23,200 pairs | ~$29 |
| LLM judge, 5 passes | ~$145 |
| Storage | free (34 GB of 7.8 TB) |
| **All-in (API generation, 1 judge pass)** | **~$47** |
| **All-in (local generation, 1 judge pass)** | **~$29, ~30 GPU-hours** |

Judge figure uses the measured DeepSeek rate of $1.25 per 1,000 pairs against
the projected pair count: 11,610 (SingleAgent) + 11,626 (MultiAgent at 9.04
pairs/video) ≈ **23,200 pairs**. Note this is *higher* than the earlier 18,700
estimate because MultiAgent now produces pairs at SingleAgent's density rather
than a flat 20/video — the K change lowered pair count on this short-video
pilot but raises it on a corpus with longer videos.

Phases 1 and 2 **both** need the GPU under local generation. Whisper (~3 GB) plus
Qwen bf16 (~17 GB) is ~20 GB of 24 on a card time-sliced without memory
partitioning and shared with ollama/plex/jupyter — they will OOM each other.
Sequence them: finish phase 1, tear down Whisper, bring up vLLM, run phase 2.

**Money is not the constraint.** The constraints are:

1. **Wall-clock** — days of downloading, plus ~4 days of MultiAgent API calls.
2. **YouTube throttling / IP blocks** during bulk download — the highest risk of
   hard failure.
3. **Human annotation capacity** — scales with pairs, and is the one cost that
   doesn't fall with cheap models.

## The question underneath the cost question

Judge and human-eval cost scale with **QA pairs**, not corpus hours — and an
expert educator repeats themselves heavily across videos. 300 hours will not
yield 133x the unique content of 2.25 hours.

**Before committing to the full download, pilot ~10 hours end-to-end** and
measure two things:

1. Real Whisper throughput on the contended card.
2. Near-duplicate rate across the resulting QA pairs.

If redundancy is high, a curated 100 hours may beat 300 hours at a third of the
eval cost.

## Pipeline architecture

### What runs today

`run.py` is a **strictly serial per-video loop**. Per video, in order:

1. `build_video_root()` → `<base>/<idx>/{Audio,Transcript,SingleAgent,...}/`
2. Transcript acquisition, one of two paths:
   - `--preprocess` (opt-in, **off by default**, `run.py:318`) → subprocess
     `preprocess.py` → yt-dlp to `Audio/audio.wav` → `whisper.load_model()` →
     `model.transcribe()` → `Transcript/transcript-<lang>.json` + `.srt`
   - default → `find_existing_transcript()` globs `Transcript/transcript*.json`;
     video is skipped entirely if none found
3. For each `--app` id → subprocess `<Approach>/main.py --id <idx> --base <abs>`.
   The child re-globs the transcript dir itself and writes `finalQA.json` /
   `Intermediate.json` / `chunks.json` **into the current working directory**;
   `run.py` then `move_if_exists()` them into
   `<base>/<idx>/<Approach>/{QA results,intermediate}/`.
4. Parse `{"agent_seconds": ...}` from child stdout, rewrite the entire input CSV.

So GPU work and API work are **interleaved per video** — but only under
`--preprocess`.

### Decision: fully decouple the phases (already supported)

`--preprocess` is off by default, and `preprocess.py` has its own batch mode
(`process_sheet`, via `--sheet`/`--base`/`--only`) that walks a whole CSV without
`run.py`. So the phase-separated form works today:

```bash
python preprocess.py --sheet corpus.csv --base Corpus      # phase 1 — GPU
python run.py --v corpus.csv --base Corpus --app 1 3       # phase 2 — API only
python eval/llm_judge/judge.py --mode full                 # phase 3 — API only
```

**Why it matters here:** the GPU is needed only in phase 1. Once transcripts are
on disk, phases 2-3 are pure HTTP. Interleaved, the run holds a slot on a card
time-sliced five ways for the whole ~4-day generation run while using it 0% of
the time. Decoupled, the slot is released after ~10 GPU-hours.

### Target phase layout

| Phase | Bound by | GPU? | Parallel? |
| --- | --- | --- | --- |
| 0 — Inventory -> `corpus.csv` | network | no | n/a |
| 1a — Download audio | network, throttling | no | few workers |
| 1b — Transcribe | GPU | **yes** | batch internally |
| 2 — QA generation | API latency (~43 h serial) | only in mode A/B | needs blocker #4 fixed |
| 3 — Judge + eval | DeepSeek API | no | already threaded |

Phase 2 is GPU-bound under the current pod config (mode A) and under the
recommended vLLM setup (mode B') — it is API-bound only if mode C is chosen.
Either way it must not overlap phase 1 on this card.

Non-obvious split: **1a from 1b**. Downloading an hour of audio and transcribing
an hour take comparable time, so interleaving leaves the GPU idle ~half of phase
1. Producer/consumer (downloader fills a queue, Whisper drains it) roughly
doubles phase-1 throughput and lets the downloader back off under throttling
without stalling the GPU.

### Blockers at hundreds-of-videos scale

Ordered by severity:

1. **Whisper reloads per video.** `whisper.load_model()` is inside
   `transcribe()` (`preprocess.py:126`), called per video by `process_single`.
   ~500 videos x ~20s ≈ 3 hours of pure model loading. Hoist it out of the loop
   (required for the faster-whisper swap anyway).
2. **No resume in phase 1.** `download_audio()` unconditionally
   `shutil.rmtree`s the audio dir (`preprocess.py:41`), and nothing checks for an
   existing `transcript-<lang>.json`. A crash means restarting from zero — and
   k3s restarts its API server under heavy I/O.
3. **One bad video kills the batch.** Approach failures are caught per-approach
   (`run.py:395`), but `run_preprocessing` uses `check=True` outside any
   per-video guard, so a geo-blocked/deleted/members-only video aborts the run.
   `process_sheet` has the same gap.
4. **Phase 2 cannot be parallelized.** Child scripts write `finalQA.json` to the
   **CWD** and `run.py` moves it after (`run.py:137`, `run.py:178`). Concurrent
   videos clobber each other. This blocks the actual wall-clock bottleneck
   (~248k MultiAgent calls, ~4 days serial). Fix: per-worker working directories,
   or have children write straight to their destination paths.
5. **Input CSV rewritten after every approach** (`writeback_times_canonical`,
   `run.py:391`) — ~1000 full-file rewrites, and concurrent runs against the same
   CSV are unsafe.
6. **Transcript provenance is ambiguous.** `download_transcripts.py` and
   `preprocess.py` both write `transcript-<lang>.json` to the same directory, and
   consumers glob `transcript*.json` and take `sorted(...)[0]` (`run.py:105`,
   `SingleQA/main.py:201`). With both present, selection is alphabetical
   accident. The metadata sidecar fixes this.

## Prompt versions and measured effects (25-video pilot)

All figures below are measured over the 25-video pilot corpus (Master 10 + Teepa
15, 5.84 h, 55,519 words), generated with `qwen3.5-plus`. This corpus is the
small subset we iterate on before the full dataset.

### What changed in each version

**MultiAgent v1 -> v2** (committed `33ec416`)

- Agent 2: criteria reworded to Alignment / Educational Value / Supportive /
  Easy to Understand, targeting an 8th-grade reading level.
- Agent 3: dropped the `M` (mental health value) dimension, leaving A/C/E, with
  `C` renamed Accessibility -> Clear. **The parser had to move with it**
  (`[ACEM]` -> `[ACE]`, `len(subs) == 4` -> `== 3`); without that, every score
  falls to the first-number fallback and silently becomes Alignment alone.
- Agent 5: new system prompt and four numbered requirements.
- Agent 4 (Justifier) stays disabled.
- **K changed from a flat 20 to `max(4, round(word_count / 250))`**, mirroring
  `SingleQA/config/settings.py`.

**v2 -> v3** (uncommitted at time of writing)

- Agent 1: removed the false claim "which has about 2000 lines". Measured line
  counts across the 25 videos are **min 36, median 380, max 650** — the claim
  was wrong for every video by roughly 5x.
- Agent 2: added "One idea per question: ask about a single thing. Avoid
  compound questions."
- Agents 2/3/5: "family caregiver" -> **"care partner"** (matches the Teepa
  Snow / Positive Approach to Care vocabulary used in the source corpus).
- Agent 5: replaced "detailed enough to be complete, short enough to stay
  readable" (which licensed length) with "focused with minimum details as
  needed", and added **"Use 60-80 words. Stop once the question is answered."**

### Measured results

| | v2 | **v3** | SingleAgent |
| --- | --- | --- | --- |
| Pairs | 226 | **226** | 226 |
| Grounded | 226/226 | **226/226** | 225/226 |
| Answer mean | 110.5 w | **76.2 w** | 63.3 w |
| Answer median | 103 | **76** | 61 |
| Answer p90 | 175 | **85** | 80 |
| Answer max | 256 | **100** | 108 |
| Sentences, mean (max) | 5.7 (**17**) | 5.4 (**9**) | 3.3 (7) |
| Within the 60-80 target | — | **76%** | — |
| Question mean | 18.6 w | 15.9 w | 16.6 w |

**Findings:**

1. **The length instruction worked without a token cap.** `max_tokens` in
   `agent5_synthesizer.py` is still 1024 and was never the binding constraint —
   the prompt alone moved the mean 110 -> 76.
2. **The tail mattered more than the mean.** p90 fell 175 -> 85, max 256 -> 100,
   worst-case sentence count 17 -> 9. The v2 outliers were enumerated step
   lists; the explicit word target removed them.
3. **Length parity with SingleAgent improved from ~47 words apart to ~13.**
   Answer length is a known confound for both LLM judges and human annotators,
   so this materially strengthens the approach comparison — the same reasoning
   that motivated the K change.
4. **Grounding was not traded away.** 100% timestamp coverage held across the
   added instructions.
5. **A 3-video probe predicts the full run well.** The probe over Master 1-3
   gave a 75.0-word mean against the full corpus's 76.2. At ~50 min per full
   25-video run versus ~6 min per probe, probe first when trialling prompts.

### Open questions from v3

- **Pronouns without antecedents.** Agent 2 sees only its own segment, so
  questions can refer to "them" when the person with dementia was introduced
  earlier in the transcript. The judge scores pairs "as one unit" without the
  transcript in view for some dimensions, and a human annotator reads them cold.
  Not yet measured for prevalence; a "name who the question is about rather than
  using pronouns" line in agent 2 would be the cheap fix.
- **Agent 3 no longer scores mental health value, but the judge still does.**
  `judge.py` scores `qa_mental_health` ("The QA pair is constructive and
  emotionally supportive…"). Agent 5's requirement 4 guards the severe error
  modes (`Dismissive`, `Potentially harmful`), but selection is now blind to
  whether a *question* invites a supportive answer — so the risk sits on the
  `Neutral / limited support` category. **Testable:** if MultiAgent tracks
  SingleAgent on A/C/E but sits lower specifically on `qa_mental_health`, the
  dropped dimension matters and restoring `M` is a one-line revert.
- **v3 changed four things at once** (length, one-idea, terminology, agent 1
  line count). If any single change turns out to matter for the paper, it needs
  its own ablation.
- **Terminology is not consistent end-to-end.** `agent2_inquisitor.py` system
  prompt still says "family caregiver", and `judge.py` says "family caregivers"
  / "from a family caregiver's perspective". The human annotation form
  (`eval/web/index.html`) uses plain **"caregiver"** throughout, so changing the
  judge to "caregiver" would fix both the drift from the human form and the
  mismatch with "care partner" in one edit.

### Provider note

MultiAgent v2 was generated twice — once via DashScope `qwen3.5-plus` (floating
alias) and once via OpenRouter `qwen/qwen3.5-plus-20260420` (pinned). Same
prompts, same K, same nominal model: **24/25 videos matched on pair count, 0/25
produced identical questions.** DashScope will not report which dated snapshot
served a request; OpenRouter echoes the pinned id. **Pin the dated model for the
production run** — "generated with qwen3.5-plus" is not a reproducible claim.

## Next actions

- [ ] **Inventory pass** over candidate videos → CSV of
      `video_id, title, duration, caption_type, format, has_visual_demo`.
      Nothing else is decidable without knowing the pool. Needs the playlist /
      channel URLs.
- [ ] Verify the `en-orig` caption-track question with `yt-dlp --list-subs`.
- [ ] Convert `preprocess.py` to batched `faster-whisper`; hoist the model load
      out of the per-video loop; wrap as a checkpointed k8s Job.
- [ ] Add skip-if-exists + per-video try/except to phase 1 (blockers 2 and 3).
- [ ] Split phase 1a (download) from 1b (transcribe) as producer/consumer.
- [ ] Fix the CWD collision so phase 2 can run parallel workers (blocker 4).
- [ ] Measure how often v3 questions use pronouns with no antecedent; if common,
      add a "name who the question is about" line to agent 2.
- [ ] After the next judge run, check whether MultiAgent sits lower than
      SingleAgent specifically on `qa_mental_health` — decides whether dropping
      `M` from agent 3 was a mistake.
- [ ] Align caregiver terminology end-to-end (`agent2_inquisitor.py` system
      prompt, `judge.py`) — the human form uses plain "caregiver".
- [ ] Pin the dated model (`qwen/qwen3.5-plus-20260420`) for the production run.
- [ ] Add the `transcript-<lang>.meta.json` sidecar.
- [ ] Update `judge.py --mode full` to skip DualAgent and RAG.
- [ ] Run the 10-hour end-to-end pilot; measure throughput and duplicate rate.
