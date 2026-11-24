#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Measure small local models on the LhassaBench task suite.

Not a unit test and not imported by the test suite: it needs a running local
model server. For every task fixture (classify, summarize, route, policy,
extract) it sends the prompt, streams the answer, scores it with the SAME
objective verifier the bench uses (``lhassa.agents.verify.verify``; never an
LLM judge) and records latency, throughput, memory and load.

Backends (``--backend``):

- ``ollama``: native ``/api/chat`` streaming (exact token counts and
  durations come from the server's own final chunk).
- ``openai``: any OpenAI-compatible server (``llama-server`` from llama.cpp,
  OpenVINO Model Server...); throughput comes from the server's ``timings``
  block when present, else from chunk counting.
- ``openvino``: in-process ``openvino_genai.LLMPipeline`` on CPU, GPU or NPU
  (``--device``), for a model directory exported with ``optimum-cli``.

Settings are fixed for reproducibility: temperature 0, a fixed seed,
thinking disabled where the model supports it. One JSON line per call is
appended to ``--out``; ``--summary`` prints the aggregate table instead.

Examples (from the repo root, inside ``uv run --all-packages``)::

    python scripts/bench_small_models.py --backend ollama \\
        --url http://127.0.0.1:11435 --model qwen3.5:4b --label ollama-cpu \\
        --server-pid $(pgrep -f 'ollama serve') --runs 3 --out results.jsonl
    python scripts/bench_small_models.py --summary results.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
from pathlib import Path

import httpx

SEED = 20261123
BATTERY = Path("/sys/class/power_supply")


# ── system sampling ─────────────────────────────────────────────────────────

def _descendants(root: int) -> list[int]:
    children: dict[int, list[int]] = {}
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            stat = (p / "stat").read_text().rsplit(")", 1)[1].split()
            children.setdefault(int(stat[1]), []).append(int(p.name))
        except (OSError, IndexError, ValueError):
            continue
    out, stack = [], [root]
    while stack:
        pid = stack.pop()
        out.append(pid)
        stack.extend(children.get(pid, []))
    return out


def _rss_mb(pids: list[int]) -> float:
    total = 0
    for pid in pids:
        try:
            for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1])
        except OSError:
            continue
    return total / 1024.0


def _cpu_times() -> tuple[int, int]:
    f = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
    vals = [int(x) for x in f]
    return sum(vals) - vals[3] - vals[4], sum(vals)  # busy, total (idle+iowait excluded from busy)


def _power_w() -> float | None:
    """Whole-system battery discharge power in watts, if on battery."""
    for bat in sorted(BATTERY.glob("BAT*")):
        try:
            if (bat / "status").read_text().strip() != "Discharging":
                return None
            return int((bat / "power_now").read_text()) / 1e6
        except (OSError, ValueError):
            continue
    return None


def package_temp_c() -> float | None:
    """Package temperature in C from the x86_pkg_temp thermal zone, else None."""
    for z in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
        try:
            if (z / "type").read_text().strip() == "x86_pkg_temp":
                return int((z / "temp").read_text()) / 1000.0
        except (OSError, ValueError):
            continue
    return None


def wait_cool(limit_c: float, max_wait_s: float = 300.0) -> None:
    """Block (short sleeps) until the package is below ``limit_c`` or the wait
    expires, so throttled silicon does not skew speed numbers."""
    t0 = time.time()
    while time.time() - t0 < max_wait_s:
        t = package_temp_c()
        if t is None or t < limit_c:
            return
        time.sleep(5)


class Sampler:
    """Background sampler: peak RSS of the server tree, CPU busy %, battery power."""

    def __init__(self, pid: int | None):
        self.pid = pid
        self.peak_rss = 0.0
        self.power: list[float] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self._cpu0 = _cpu_times()
        self._thread.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            if self.pid:
                self.peak_rss = max(self.peak_rss, _rss_mb(_descendants(self.pid)))
            w = _power_w()
            if w is not None:
                self.power.append(w)
            self._stop.wait(0.25)

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()
        b1, t1 = _cpu_times()
        b0, t0 = self._cpu0
        self.cpu_busy_pct = 100.0 * (b1 - b0) / max(1, t1 - t0)
        self.mean_power_w = statistics.fmean(self.power) if self.power else None


# ── backends ────────────────────────────────────────────────────────────────

def call_ollama(url: str, model: str, prompt: str, max_tokens: int, num_ctx: int) -> dict:
    body = {
        "model": model, "stream": True, "think": False,
        "messages": [{"role": "user", "content": prompt}],
        "options": {"temperature": 0, "seed": SEED, "num_predict": max_tokens, "num_ctx": num_ctx},
    }
    for attempt in (0, 1):
        t0 = time.perf_counter()
        text, ttft, last = [], None, {}
        with httpx.stream("POST", url + "/api/chat", json=body, timeout=600) as r:
            if r.status_code == 400 and attempt == 0 and "think" in r.read().decode():
                body.pop("think")  # model has no thinking switch
                continue
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                d = json.loads(line)
                piece = (d.get("message") or {}).get("content") or ""
                if piece and ttft is None:
                    ttft = time.perf_counter() - t0
                text.append(piece)
                if d.get("done"):
                    last = d
        wall = time.perf_counter() - t0
        ev, evd = last.get("eval_count", 0), last.get("eval_duration", 0)
        pe, ped = last.get("prompt_eval_count", 0), last.get("prompt_eval_duration", 0)
        return {
            "text": "".join(text), "ttft_s": ttft, "wall_s": wall,
            "gen_tokens": ev, "gen_tps": ev / (evd / 1e9) if evd else None,
            "prompt_tokens": pe, "prefill_tps": pe / (ped / 1e9) if ped else None,
            "load_s": last.get("load_duration", 0) / 1e9,
        }
    raise RuntimeError("unreachable")


def call_openai(url: str, model: str, prompt: str, max_tokens: int, num_ctx: int) -> dict:
    body = {
        "model": model, "stream": True, "temperature": 0, "seed": SEED, "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "chat_template_kwargs": {"enable_thinking": False},
        "stream_options": {"include_usage": True},
        "cache_prompt": False,
    }
    t0 = time.perf_counter()
    text, ttft, timings, usage, n_chunks = [], None, {}, {}, 0
    with httpx.stream("POST", url + "/v1/chat/completions", json=body, timeout=600) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.startswith("data:") or line.strip() == "data: [DONE]":
                continue
            d = json.loads(line[5:])
            for ch in d.get("choices") or []:
                piece = (ch.get("delta") or {}).get("content") or ""
                if piece:
                    n_chunks += 1
                    if ttft is None:
                        ttft = time.perf_counter() - t0
                    text.append(piece)
            timings = d.get("timings") or timings
            usage = d.get("usage") or usage
    wall = time.perf_counter() - t0
    gen = usage.get("completion_tokens") or timings.get("predicted_n") or n_chunks
    gen_tps = timings.get("predicted_per_second")
    if gen_tps is None and ttft is not None and wall > ttft and gen > 1:
        gen_tps = (gen - 1) / (wall - ttft)
    return {
        "text": "".join(text), "ttft_s": ttft, "wall_s": wall, "gen_tokens": gen, "gen_tps": gen_tps,
        "prompt_tokens": usage.get("prompt_tokens") or timings.get("prompt_n"),
        "prefill_tps": timings.get("prompt_per_second"), "load_s": 0.0,
    }


class OpenVinoBackend:
    def __init__(self, model_dir: str, device: str, vlm: bool = False):
        import openvino_genai as ov

        t0 = time.perf_counter()
        # Multimodal checkpoints (Gemma 4) load through VLMPipeline, text only.
        self.vlm = vlm
        self.pipe = (ov.VLMPipeline if vlm else ov.LLMPipeline)(model_dir, device)
        self.load_s = time.perf_counter() - t0
        self.ov = ov

    def __call__(self, url, model, prompt, max_tokens, num_ctx) -> dict:
        cfg = self.ov.GenerationConfig()
        cfg.max_new_tokens = max_tokens
        cfg.do_sample = False
        chunks, ttft, t0 = [], None, time.perf_counter()

        def streamer(piece):
            nonlocal ttft
            if ttft is None:
                ttft = time.perf_counter() - t0
            chunks.append(piece)
            return False

        self.pipe.start_chat()  # applies the model's chat template
        try:
            if self.vlm:
                res = self.pipe.generate(prompt, generation_config=cfg, streamer=streamer)
            else:
                res = self.pipe.generate([prompt], cfg, streamer)  # a list returns DecodedResults
        finally:
            self.pipe.finish_chat()
        wall = time.perf_counter() - t0
        pm = res.perf_metrics
        return {
            "text": "".join(chunks), "ttft_s": ttft, "wall_s": wall,
            "gen_tokens": pm.get_num_generated_tokens(),
            "gen_tps": pm.get_throughput().mean,
            "prompt_tokens": pm.get_num_input_tokens(), "prefill_tps": None, "load_s": self.load_s,
        }


# ── driver ──────────────────────────────────────────────────────────────────

def load_fixtures(tasks: list[str] | None):
    from lhassa.bench.loader import load_task_fixtures

    out = []
    for f in load_task_fixtures():
        if not tasks or f["task"] in tasks:
            out.append(f)
    return out


def score(fixture: dict, text: str):
    from lhassa.agents import verify as verify_agent
    from lhassa.bench.loader import resolve_verify_spec

    return verify_agent.verify(text, spec=resolve_verify_spec(fixture["verify"]))


def run(args) -> None:
    fixtures = load_fixtures(args.tasks)
    if args.limit:
        fixtures = fixtures[: args.limit]
    if args.backend == "ollama":
        call = call_ollama
    elif args.backend == "openai":
        call = call_openai
    else:
        call = OpenVinoBackend(args.model_dir, args.device, args.vlm)
    pid = args.server_pid or (os.getpid() if args.backend == "openvino" else None)
    out = Path(args.out)
    load = os.getloadavg()
    meta = {
        "label": args.label, "backend": args.backend, "model": args.model, "quant": args.quant,
        "runtime": args.runtime, "seed": SEED, "temperature": 0, "num_ctx": args.num_ctx,
        "load_avg_start": [round(x, 2) for x in load], "host_threads": os.cpu_count(),
        "on_battery": _power_w() is not None,
    }
    wait_cool(args.max_temp)
    # Warm-up (not recorded): loads the model into memory.
    call(args.url, args.model, "Reply with the word ok.", 8, args.num_ctx)
    with out.open("a") as fh:
        for rep in range(args.runs):
            for idx, fx in enumerate(fixtures):
                max_tokens = int(fx.get("max_tokens") or 256)
                # A short unique tag in front defeats server-side prefix reuse
                # (every prompt in a task shares a long header), so ttft is a
                # cold-prefill number on every backend.
                prompt = f"[req {args.run_offset + rep}-{idx}]\n" + fx["prompt"] + args.prompt_suffix
                with Sampler(pid) as s:
                    try:
                        r = call(args.url, args.model, prompt, max_tokens, args.num_ctx)
                        err = None
                    except Exception as exc:  # record, keep going
                        r, err = {"text": ""}, repr(exc)[:200]
                res = score(fx, r["text"])
                dur = r.get("wall_s") or 0.0
                rec = {
                    **meta, "run": args.run_offset + rep, "load_1m": round(os.getloadavg()[0], 2), "pkg_temp_c": package_temp_c(), "task": fx["task"], "fixture": fx["id"],
                    "passed": bool(res.passed), "detail": res.detail[:160], "error": err,
                    "answer": r["text"][:2000],
                    **{k: r.get(k) for k in ("ttft_s", "wall_s", "gen_tokens", "gen_tps",
                                              "prompt_tokens", "prefill_tps", "load_s")},
                    "peak_rss_mb": round(s.peak_rss, 1), "cpu_busy_pct": round(s.cpu_busy_pct, 1),
                    "mean_system_power_w": round(s.mean_power_w, 2) if s.mean_power_w else None,
                    "energy_j_system": round(s.mean_power_w * dur, 2) if s.mean_power_w and dur else None,
                }
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
                print(f"{args.label} run{rep} {fx['id']:<48} {'PASS' if res.passed else 'FAIL'} "
                      f"ttft={rec['ttft_s'] and round(rec['ttft_s'], 2)} tps={rec['gen_tps'] and round(rec['gen_tps'], 1)}",
                      file=sys.stderr)


def _sd(xs):
    return statistics.pstdev(xs) if len(xs) > 1 else 0.0


def _lenient(fixture: dict, answer: str) -> bool:
    """Format-forgiving re-score for the closed-label tasks: judge only the
    first non-empty line. Separates "knows the answer" from "obeys the
    output format" (the first can be fixed by post-processing or constrained
    decoding; the second cannot)."""
    if fixture["verify"]["type"] != "label":
        return False
    lines = [ln for ln in answer.strip().splitlines() if ln.strip()]
    return bool(lines) and score(fixture, lines[0]).passed


def summary(path: str) -> None:
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    # A re-run of the same (label, run, fixture) replaces the earlier row.
    rows = list({(r["label"], r["run"], r["fixture"]): r for r in rows}.values())
    fixtures = {f["id"]: f for f in load_fixtures(None)}
    # Re-score from the stored answer with the CURRENT fixture spec (rows
    # written before an answer-length cap change keep their stored verdict if
    # the answer was truncated).
    for r in rows:
        if len(r.get("answer", "")) < 300 and r["fixture"] in fixtures:
            r["passed"] = bool(score(fixtures[r["fixture"]], r["answer"]).passed)
    labels = sorted({r["label"] for r in rows})
    tasks = sorted({r["task"] for r in rows})
    print("| label | " + " | ".join(tasks) + " | all | label-tasks lenient | ttft p50 (s) | gen tok/s p50 | peak RSS MB | power W | pkg temp C p50 | load1 p50 |")
    print("|---|" + "---|" * (len(tasks) + 8))

    def pct(sub):
        runs = sorted({r["run"] for r in sub})
        per_run = [100.0 * sum(r["passed"] for r in sub if r["run"] == k) / sum(1 for r in sub if r["run"] == k)
                   for k in runs]
        if not per_run:
            return "n/a"
        return f"{statistics.fmean(per_run):.0f}" + (f" +/-{_sd(per_run):.0f}" if len(runs) > 1 else "")

    for lab in labels:
        rs = [r for r in rows if r["label"] == lab]
        cells = [pct([r for r in rs if r["task"] == t]) for t in tasks] + [pct(rs)]
        lab_rows = [r for r in rs if fixtures[r["fixture"]]["verify"]["type"] == "label"]
        lenient = (f"{100.0 * sum(_lenient(fixtures[r['fixture']], r['answer']) for r in lab_rows) / len(lab_rows):.0f}"
                   if lab_rows else "n/a")
        ttft = [r["ttft_s"] for r in rs if r.get("ttft_s")]
        tps = [r["gen_tps"] for r in rs if r.get("gen_tps") and (r.get("gen_tokens") or 0) >= 20]  # short answers make tok/s noisy
        pw = [r["mean_system_power_w"] for r in rs if r.get("mean_system_power_w")]
        med = lambda xs, f: f"{statistics.median(xs):{f}}" if xs else "n/a"  # noqa: E731
        rss = max(r["peak_rss_mb"] for r in rs)
        pwr = f"{statistics.fmean(pw):.1f}" if pw else "n/a"
        temps = [r["pkg_temp_c"] for r in rs if r.get("pkg_temp_c")]
        loads = [r["load_1m"] for r in rs if r.get("load_1m") is not None]
        print(f"| {lab} | " + " | ".join(cells) + f" | {lenient} | {med(ttft, '.2f')} | {med(tps, '.1f')} | {rss:.0f} | {pwr} | {med(temps, '.0f')} | {med(loads, '.1f')} |")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", choices=["ollama", "openai", "openvino"])
    ap.add_argument("--url", default="http://127.0.0.1:11434")
    ap.add_argument("--model", default="")
    ap.add_argument("--model-dir", default="", help="openvino: exported model directory")
    ap.add_argument("--device", default="CPU", help="openvino: CPU, GPU or NPU")
    ap.add_argument("--vlm", action="store_true", help="openvino: use VLMPipeline (multimodal checkpoints, text only)")
    ap.add_argument("--label", default="", help="row label, e.g. 'qwen3.5-4b ollama-cpu'")
    ap.add_argument("--quant", default="", help="recorded only (e.g. Q4_K_M)")
    ap.add_argument("--runtime", default="", help="recorded only (e.g. ollama 0.40.17)")
    ap.add_argument("--server-pid", type=int, default=0, help="root pid of the server (RSS of its process tree)")
    ap.add_argument("--tasks", nargs="*")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--run-offset", type=int, default=0, help="added to the run index (interleaved sweeps)")
    ap.add_argument("--max-temp", type=float, default=80.0, help="wait (max 5 min) for package temp below this before starting")
    ap.add_argument("--prompt-suffix", default="", help="appended to every prompt (e.g. ' /no_think' for Qwen3 via OpenVINO)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--num-ctx", type=int, default=4096)
    ap.add_argument("--out", default="small-models-results.jsonl")
    ap.add_argument("--summary", metavar="JSONL", help="print the aggregate markdown table and exit")
    args = ap.parse_args()
    if args.summary:
        summary(args.summary)
        return
    if not args.backend or not args.label:
        ap.error("--backend and --label are required")
    run(args)


if __name__ == "__main__":
    main()
