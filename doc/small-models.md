# Small local models for Lhassa tasks (measured, November 2026)

Which small open models are good enough, today, for the jobs Lhassa wants to
keep on the machine: classify events, summarize logs and sessions, route a
request to a handler, flag policy violations, and extract decisions and
to-dos. This is research plus a reproducible measurement, not training.

Everything here is tagged **MEASURED** (we ran it on this machine, numbers
come from the JSONL files in `doc/small-models-results/`) or **INFERENCE**
(reasoned from sources or from measured numbers, not tested directly).

## Test machine

| Item | Value |
| --- | --- |
| CPU | Intel Core Ultra 5 125H (Meteor Lake), 14 cores / 14 threads |
| iGPU | Intel Arc Graphics (MTL), shared memory, Mesa Vulkan |
| NPU | Intel AI Boost (`/dev/accel/accel0`, `intel_vpu`) |
| RAM | 30 GiB (heavily used by other work during the runs) |
| OS / kernel | Arch Linux, 7.2.15-arch1-1 |
| Power | on battery for part of the runs, `power-mode` = performance |

The machine was **not idle and not cool**. Other agents ran test suites while
we measured, and the package sat at 86 to 100 C during the first sweep
(throttling). After that we gated every model on "no full pytest running and
package below 80 C" and recorded package temperature and 1 minute load with
each call (columns `pkg temp` and `load1` below; rows from the first sweep
have no temperature). Speed numbers are therefore pessimistic and noisy, but
the quality numbers do not depend on speed.

## Candidates (INFERENCE from sources, then filtered by what ran)

Selection used the official model cards and the Ollama library as of
2026-11-23; aggregator "best SLM" blog posts disagree with each other and were
used only to find names.

| Family | Size run | Released | License | Source |
| --- | --- | --- | --- | --- |
| Qwen3.5 | 0.8B, 2B, 4B | Feb 2026 | Apache-2.0 | https://huggingface.co/Qwen/Qwen3.5-4B (IFEval 89.8, BFCL-V4 50.3 on the card) |
| Qwen3 | 8B | Apr 2025 | Apache-2.0 | https://huggingface.co/Qwen/Qwen3-8B |
| Gemma 4 | E2B (2.3B effective, 5.1B with embeddings) | Mar 2026 | Apache-2.0 | https://huggingface.co/google/gemma-4-E2B-it |
| Granite 4.2 | 3B | Aug 2026 | Apache-2.0 | https://huggingface.co/ibm-granite/granite-4.2-3b |
| Ministral 3 | 3B | Dec 2025 | Apache-2.0 | https://huggingface.co/mistralai/Ministral-3-3B-Instruct-2512 (card lists text classification and data extraction as target uses) |
| LFM3 | 1.7B | Oct 2026 | LFM Open License | https://huggingface.co/LiquidAI/LFM3-1.7B and https://ollama.com/library/lfm3 |

Dates are the Hugging Face `createdAt` field. Seen but not run: Granite 4.2 8B,
LFM2.5 8B, Qwen3.5 9B (RAM budget and the 15 GB download limit), Phi-4-mini and
SmolLM3 (older; no newer Ollama build found). Library listing used:
https://ollama.com/library.

Runtimes considered:

- **Ollama** (system service is 0.17.5, too old: `ollama pull qwen3.5:4b`,
  `gemma4`, `lfm3` return HTTP 412). We ran Ollama 0.40.17 from a user-level
  tarball on port 11435 sharing `~/.ollama/models`, with `OLLAMA_IGPU_ENABLE=1`
  to use the Arc iGPU over Vulkan (it ignores integrated GPUs by default).
- **llama.cpp** b12651 prebuilt (CPU and Vulkan builds). Same GGUF files as
  Ollama, read from its blob store. SYCL was not tried: it needs the oneAPI
  runtime, which is not installed. The community SYCL thread reports SYCL wins
  on decode and loses badly on prefill versus Vulkan on an Xe3 iGPU
  (https://github.com/ggml-org/llama.cpp/discussions/23313, 2026-06-01).
- **OpenVINO GenAI** 2026.4.0 (CPU measured; NPU experiment below; iGPU not
  possible, see below). NPU guide:
  https://docs.openvino.ai/2026/openvino-workflow-generative/inference-with-genai/inference-with-genai-on-npu.html
  (symmetric weights required: `--sym`, group size 128 up to about 4-5B,
  channel-wise above).

## Method

- **Bench**: 60 synthetic fixtures, 12 per task, in
  `packages/lhassa-bench/src/lhassa/bench/fixtures/tasks/<task>/`. Run them
  with `lhassa bench run --suite tasks`. No personal data; all hosts are
  `example.test`, all names invented. About half of the cases per task are
  tagged `hard` (root cause vs symptom, path traversal, look-alike paths and
  hosts, reversed or negated decisions, relative dates, tentative ideas).
- **Scoring**: objective, no LLM judge, through the same
  `lhassa.agents.verify.verify` seam as every other fixture. New verifiers in
  `lhassa/bench/verifiers.py`: `label` (the whole normalised answer must equal
  the expected label), `facts` (required substrings or regexes, forbidden
  terms, word budget), `json_fields` (parse the JSON, then check values, list
  sizes and "owner and keyword in the same item"). Each fixture carries a
  `reference` answer that must pass and, for tricky cases, `bad` answers that
  must fail (`tests/test_task_fixtures.py`, no model needed).
- **Strictness**: a label task passes only if the answer is the label and
  nothing else. We also report a **lenient** column: the first non-empty line
  alone is scored. The gap between strict and lenient is format obedience
  (fixable by constrained decoding or trimming), not knowledge.
- **Settings**: temperature 0, fixed seed 20261123, thinking disabled
  (`think:false` on Ollama, `enable_thinking:false` on llama-server, `/no_think`
  for Qwen3 on OpenVINO), context 4096, `max_tokens` per fixture (32 for labels,
  200 summaries, 400 JSON). A short unique tag is put in front of every prompt so
  server prefix caches cannot help, so TTFT is a cold-prefill number everywhere.
- **Repeats**: rows say `+/-` where n>=2: population standard deviation of the
  per-run pass rate. n=3 for the iGPU contenders (qwen3.5:2b, gemma4:e2b,
  granite4.2:3b, ministral-3:3b, qwen3.5:4b), n=2 for llama.cpp Vulkan, n=1
  for everything else (qwen3.5:0.8b, lfm3:1.7b, qwen3:8b, CPU, OpenVINO).
  Because temperature is 0 the quality variance is mostly zero; the small
  `+/-` come from GPU numerics and the changed tag in the prompt.
- **Metrics**: TTFT (time to first streamed content), decode tokens/s (median
  over answers of 20 or more tokens, so label answers do not count), peak RSS of
  the server process tree (misleading on the iGPU: weights live in mapped or GPU
  memory, so RSS can be far below the model size), system battery power when
  discharging (whole machine, not the model: treat as order of magnitude, `n/a`
  when on AC). RAPL energy counters are root-only here, so there is **no
  per-model energy figure**.

Reproduce (from the repo root, one model at a time, never during a test run):

```bash
# start a recent Ollama as your user (do not touch the system service)
OLLAMA_HOST=127.0.0.1:11435 OLLAMA_IGPU_ENABLE=1 ollama serve &
uv run --all-packages --extra dev --no-sync python scripts/bench_small_models.py \
  --backend ollama --url http://127.0.0.1:11435 --model qwen3.5:4b \
  --label "qwen3.5:4b ollama-igpu" --server-pid <pid-of-serve> --runs 3 \
  --out results.jsonl
uv run --all-packages --extra dev --no-sync python scripts/bench_small_models.py --summary results.jsonl
```

`scripts/bench_small_models.py` also has `--backend openai` (llama-server, OVMS)
and `--backend openvino` (in-process, `--device CPU|GPU|NPU`, `--vlm` for
Gemma 4). It waits for the package to drop below `--max-temp` (default 80 C,
max 5 minutes) before each model.

## Results (MEASURED)

Percentages are pass rate on the 12 cases of each task. "all" is over all
cases run for that row.

### Ollama 0.40.17 on the Arc iGPU (Vulkan), quality and speed

| model | classify | extract | policy | route | summarize | all | label lenient | TTFT p50 s | decode tok/s p50 | pkg temp C p50 | load1 p50 | n |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| qwen3.5:4b | 100 | 92 | 92 | 100 | 83 | **93** | 97 | 2.73 | 4.5 | 78 | 4.0 | 3 |
| qwen3:8b | 100 | 92 | 75 | 100 | 100 | **93** | 92 | 2.62 | 3.6 | 74 | 3.8 | 1 |
| gemma4:e2b | 100 | 67 | 92 | 94 +/-4 | 69 +/-4 | 84 +/-1 | 95 | **0.78** | 8.6 | 78 | 5.0 | 3 |
| qwen3.5:2b | 100 | 42 | 75 | 92 | 100 | 82 | 89 | 1.52 | 5.1 | 78 | 7.8 | 3 |
| granite4.2:3b | 89 +/-4 | 53 +/-4 | 78 +/-4 | 92 | 83 | 79 +/-2 | 86 | 1.73 | 6.4 | 77 | 4.2 | 3 |
| ministral-3:3b | 67 | 83 | 75 | 92 +/-7 | 78 +/-4 | 79 +/-1 | 78 | 1.41 | 6.6 | 77 | 4.5 | 3 |
| qwen3.5:0.8b | 67 | 25 | 67 | 67 | 42 | 53 | 67 | 1.12 | 7.6 | n/a | 4.4 | 1 |
| lfm3:1.7b | 42 | 50 | 25 | 33 | 83 | 47 | 58 | 1.12 | 8.7 | n/a | 4.4 | 1 |

The decode speeds look low for models this size. We attribute that to a hot,
shared machine (about 77 C, load 4 to 8) and to the shared-memory iGPU; we did
not measure an idle, cool machine, so these are floors, not capabilities.

### Same GGUF files, other runtimes (quality should match, speed differs)

| model, runtime | classify | extract | policy | route | summarize | all | TTFT p50 s | decode tok/s p50 | n |
|---|---|---|---|---|---|---|---|---|---|
| qwen3.5:4b, llama.cpp Vulkan | 100 | 96 +/-4 | 92 | 100 | 83 | 94 +/-1 | 2.45 | 4.7 | 2 |
| gemma4:e2b, llama.cpp Vulkan | 100 | 67 | 92 | 96 +/-4 | 67 | 84 +/-1 | 1.30 | 4.2 | 2 |
| granite4.2:3b, llama.cpp Vulkan | 88 +/-4 | 50 | 79 +/-4 | 92 | 83 | 78 +/-2 | 1.57 | 6.8 | 2 |
| gemma4:e2b, Ollama CPU (classify, route, policy only) | 100 | n/a | 92 | 92 | n/a | 94 (3 tasks) | 1.93 | n/a | 1 |
| qwen3.5:2b, Ollama CPU (3 tasks) | 100 | n/a | 75 | 92 | n/a | 89 (3 tasks) | 5.06 | 9.2 | 1 |
| granite4.2:3b, Ollama CPU (3 tasks) | 100 | n/a | 75 | 92 | n/a | 89 (3 tasks) | 8.90 | n/a | 1 |

Reading: quality is the model's, not the runtime's (same files, same pass
rates within noise). The runtime decides TTFT: CPU prefill is slow for the 3B
Granite (8.9 s vs 1.7 s on the iGPU). A direct `llama-bench` probe of Qwen3.5
4B Q4_K_M under load gave pp200 = 111 tok/s on Vulkan versus 13 to 22 tok/s on
CPU (a quick probe, n=2, load 6 to 16; indicative only).

### OpenVINO GenAI 2026.4.0 (experiment, 36 label-task calls each, n=1)

| model | device | label tasks pass | TTFT p50 s | decode tok/s | result |
|---|---|---|---|---|---|
| Qwen3-4B int4 (`OpenVINO/Qwen3-4B-int4-ov`) | CPU | 92 | 3.6 | about 12 | works (needs `/no_think`) |
| Gemma-4-E2B int4 (`OpenVINO/gemma-4-E2B-it-int4-ov`) | CPU | 89 | 2.3 | about 16 | works through `VLMPipeline` |
| Gemma-4-E2B int4-cw (`OpenVINO/gemma-4-E2B-it-int4-cw-ov`) | **NPU** | 17 | 2.6 | about 6.6 | loads and runs, **output is corrupted** |

NPU: the kernel driver and firmware are fine; user space was missing, so we
unpacked Intel's `linux-npu-driver` v1.39.1 and `level-zero` 1.36.0 `.deb`
files into `~/.local` (no root) and set `LD_LIBRARY_PATH`; OpenVINO then listed
`['CPU', 'NPU']` and compiled the model on the NPU, with the CPU about 10%
busy. The first generated token was usually right (`storage`, `network`,
`scheduler`) and the rest was garbage, so only 2 of 12 calls passed. We did not
dig further (not a prompt problem: the same model on CPU is fine). Cause not
established; candidates are this multimodal Gemma 4 checkpoint on the NPU
plugin, or the dynamic KV handling of the NPU pipeline. **INFERENCE**:
a plain text model exported for NPU (for example `OpenVINO/Qwen3-8B-int4-cw-ov`,
untested here because of size) is the more likely first success.

GPU through OpenVINO was **not possible**: no OpenCL/Level Zero GPU runtime is
installed (`intel-compute-runtime`, Intel graphics compiler). The Vulkan path
above already uses the iGPU.

System packages that would make these paths first-class (proposal, not
installed; ask before installing): `level-zero-loader`,
`intel-compute-runtime` (and `intel-graphics-compiler`) from the Arch repos, and
an NPU user-mode driver (`intel-npu-driver` is an AUR package; the version we
unpacked is Intel's v1.39.1). A current Ollama (at least the 0.40 line) is the
key upgrade: the installed 0.17.5 cannot even pull Qwen3.5 4B, Gemma 4 or LFM3.

## Findings

1. **Size still matters for extraction, not for routing.** Routing is at
   92 to 100% from 2B up (all of Qwen3.5 2B/4B, Gemma 4 E2B, Granite, Ministral).
   Extraction separates the models: 4B and 8B reach 92 to 96%, while every model
   of 3B or less sits at 42 to 83%.
2. **Qwen3.5 4B is the best all-rounder** in the 2 to 4B range (93 to 94%
   overall, 97% lenient on labels). Qwen3 8B ties it overall but is slower and
   was run once.
3. **Gemma 4 E2B is the fastest to first token** (0.78 s on the iGPU) with
   perfect classification and 94 to 96% routing, but weaker at JSON extraction
   (67%) and summaries (67 to 69%).
4. **No model handled the look-alike path** (`/vault-notes/...` is not
   `/vault/`): 0 of every run, all models answered DENY:P1. The same models
   also failed relative dates ("by Friday" given a stated Monday date) in every
   run. These are real, repeatable weaknesses of this size class on these
   inputs.
5. **Format obedience is a separate problem.** LFM3 1.7B (via Ollama, thinking
   off) leaks its reasoning ("auth? Wait, let's parse...") or repeats the label
   twice, giving 58% lenient but 47% strict; Ministral 3B and Granite also lose
   points to extra words (lenient is 78 to 86%). Constrained decoding or taking
   the first line would recover some of that. INFERENCE: not tested.
6. **Thinking mode must be off for these jobs.** Qwen3 on OpenVINO emitted
   `<think>` blocks and failed every label call until `/no_think` was added;
   scoring strips `<think>` blocks, but the tokens still cost latency.
7. **The iGPU beats the CPU for these prompt-heavy tasks** (TTFT 1.7 s vs
   8.9 s for Granite 3B; llama-bench probe about 5 to 8 times faster prefill),
   at a similar decode rate.

## Recommendation per task (what to use today)

| Task | Use today | Why / caveat |
| --- | --- | --- |
| Classify events | `gemma4:e2b` (fast) or `qwen3.5:2b` | 100% on 12 cases, 0.8 to 1.5 s TTFT. Re-check on real event types; our 12 are synthetic. |
| Route a request | `qwen3.5:4b` (100%), `gemma4:e2b` (94 to 96%) as the cheap tier | Every 2B+ model is at or above 92%. A 7-label closed set is easy at this size. |
| Summarize logs and sessions | `qwen3.5:2b` (100%) or `qwen3:8b` (100%); `qwen3.5:4b` 83% | Fact retention is what we scored; wording quality was not scored. Keep a word budget. |
| Detect policy violations | **Do not rely on a model.** Best was 92% (`gemma4:e2b`, `qwen3.5:4b`) | Fails on look-alike paths. Use deterministic path and host rules; use the model only as a second opinion that can add DENY, never remove it. |
| Extract decisions and to-dos | `qwen3.5:4b` (92 to 96%) | 3B and below are not reliable here. Validate the JSON and re-ask on failure; verify dates in code. |
| Runtime | Ollama 0.40+ with `OLLAMA_IGPU_ENABLE=1`, or llama.cpp Vulkan | Same quality; iGPU for prefill. NPU not usable yet (see above). |

Default local tier suggestion (INFERENCE from the table): `gemma4:e2b` for
classify and routing hot paths, `qwen3.5:4b` for summaries and extraction, no
model in the policy gate.

## Limits and what is missing

- 12 synthetic cases per task is a small sample: one case is 8 points. Pass
  rates differ by less than about 8 points between several models; treat those
  as ties.
- The fixtures are English, short (under 600 tokens) and single-turn. No long
  logs, no Spanish beyond one case, no tool-call formats.
- n is 1 or 2 for several rows (see the tables). Speed was measured on a hot,
  shared machine; the first sweep also ran throttled (86 to 100 C) and was
  re-measured only for the contenders.
- No energy figure per model (RAPL is root-only); battery power is whole-system.
- Quantization was whatever Ollama ships (Q4_K_M class for most; Qwen3.5
  0.8B and 2B are older 8-month-old builds). We did not compare quantizations
  or run Granite 4.2 8B, Qwen3.5 9B or Gemma 4 E4B.
- Ollama's `think:false` did not stop LFM3 from reasoning; that may be an
  Ollama template issue and not a property of the model.
- Missing: constrained decoding (JSON schema or GBNF) which would likely lift
  strict scores for label and JSON tasks; an NPU run with a text-only
  checkpoint; a cool-machine speed rerun; energy per task; fine-tuning (out of
  scope here).

Raw data: `doc/small-models-results/*.jsonl` (one JSON line per call, with
model, runtime, seed, load, package temperature and the answer text).
