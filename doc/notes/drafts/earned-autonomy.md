<!-- draft: evidence-cited, review before publishing -->
<!-- topic: earned-autonomy | drafted: 2026-07-30T17:40:39.472771+00:00 | model: claude/claude-cli -->

# Earned autonomy

**Thesis.** Autonomy in Lhassa is not a switch; it is a balance accrued from evidence the brain has already produced. The pieces are in place; an adaptive threshold that lets a young brain fire on three repeats while a mature one stays strict, objective verifiers that never call a model, and a spend ledger; but the evidence pack shows a system that is *cheap and quiet*, not yet a system that has *demonstrated* it earns trust. 192 of 200 sampled episodes cost nothing, benchmarks pass, and no approval is pending. That is a good floor and a thin record. Trust earned against a near-empty docket is not yet trust earned.

**Evidence.**

- *doc/decisions.md (excerpt 2.1)* records the mechanism: `effective_threshold(base, population, floor=2)` = `max(floor, min(base, ceil(population/base)))`, with full strictness only at population ≥ base². Findings carry `threshold_used`, and cadence may relax to two occurrences but never to one. The excerpt for **2.2 feedback → autonomy** is truncated in the pack; the link from feedback signal to the autonomy knob is named but not readable here.
- *doc/decisions.md (excerpt 3.A)* confirms the árbitro is objective: verifiers (`command/tests/lint/health/regex/metric`) import no provider, asserted in tests, wired into the judgment branch via `router.cascade(verify=…)`; a failed verification escalates cheap→smart once. Autonomy has a check that cannot flatter itself.
- *doc/decisions.md (files excerpt)* shows the frontier reasoning that autonomy should follow: `list_dir`/`read_file` are allowlisted without approval "because the frontier is the path, not the action": scoped to `knowledge_roots`, with a secret-shaped ignore list enforced even inside.
- *bench_runs (latest 6 rows)*: 6/6 passed, all subject `default`, all on 2026-07-29. Three ran at zero cost; three at ~1.2e-05–2.1e-05 USD. `metric` is null on every row; the fixtures record pass/fail only, no graded quality.
- *episodes (200 sampled)*: 14 thinks, 192 zero-cost acts. The judgment branch is the exception, as designed.
- *ledger (today, UTC)*: 3,167 output tokens, budget 0, 0.0% used.

**What we do not yet know.**

The pack's cost fields; `ledger_today.cost_usd` and `episodes_window.think_cost_usd`: arrived redacted, so today's spend and the 14 thinks' cost cannot be stated. `approvals_pending: 0` is a point-in-time reading with no history: we cannot tell whether that means nothing was proposed, everything was resolved, or the streak counter has any population at all. The pack carries no approval-outcome counts, no denial rate, no `threshold_used` distribution, and no record of an action that actually auto-promoted. The truncated 2.2 excerpt means the feedback→autonomy coupling is unverified from this pack. And with `metric` null throughout, bench passes say "did not fail," not "did well."

**Next measurement.**

Join resolved approvals to the actions that ran afterward: per action name, count approvals, denials, current streak, whether it crossed into automatic, and the verified/observed outcome of the first N post-promotion runs. That single table answers the only question that matters here; whether an action that earned autonomy then kept behaving like one that deserved it. Until it exists, the mechanism is sound and the record is absent.

---

## Appendix; evidence pack (verbatim, per-datum provenance)

```json
{
  "topic": "earned-autonomy",
  "gathered_at": "2026-07-30T17:40:39.472771+00:00",
  "sources": [
    "ledger: llm_gateway.spend_summary (today, UTC)",
    "bench_runs table (latest 6 rows)",
    "episodes (latest 200 rows sampled)",
    "doc/decisions.md (4 matching excerpts)"
  ],
  "ledger_today": {
    "day": "2026-07-30",
    "output_tokens": 3167,
    "budget": 0,
    "pct_used": 0.0,
    "cost_usd": 0.004024300000000001
  },
  "bench_runs": [
    {
      "fixture_id": "instruction_marker_wrap",
      "subject": "default",
      "passed": 1,
      "metric": null,
      "cost_usd": 0.0,
      "created_at": "2026-07-29T00:50:10.173206+00:00"
    },
    {
      "fixture_id": "extraction_email",
      "subject": "default",
      "passed": 1,
      "metric": null,
      "cost_usd": 0.0,
      "created_at": "2026-07-29T00:50:10.168391+00:00"
    },
    {
      "fixture_id": "factual_capital_france",
      "subject": "default",
      "passed": 1,
      "metric": null,
      "cost_usd": 0.0,
      "created_at": "2026-07-29T00:50:10.158661+00:00"
    },
    {
      "fixture_id": "instruction_marker_wrap",
      "subject": "default",
      "passed": 1,
      "metric": null,
      "cost_usd": 1.862e-05,
      "created_at": "2026-07-29T00:49:43.360727+00:00"
    },
    {
      "fixture_id": "extraction_email",
      "subject": "default",
      "passed": 1,
      "metric": null,
      "cost_usd": 2.0860000000000004e-05,
      "created_at": "2026-07-29T00:49:42.019895+00:00"
    },
    {
      "fixture_id": "factual_capital_france",
      "subject": "default",
      "passed": 1,
      "metric": null,
      "cost_usd": 1.218e-05,
      "created_at": "2026-07-29T00:49:40.785329+00:00"
    }
  ],
  "episodes_window": {
    "sampled": 200,
    "thinks": 14,
    "think_cost_usd": 0.004024,
    "zero_cost_acts": 192
  },
  "approvals_pending": 0,
  "decisions_excerpts": [
    "- **2.1 adaptive thresholds** (`9010114`): `effective_threshold(base, population,\n  floor=2)` = `max(floor, min(base, ceil(population/base)))`: full strictness\n  only at population ≥ base²; a young brain fires on 3 repeats, a mature one\n  stays strict; explicit args still win; findings carry `threshold_used`;\n  cadence can relax to 2 occurrences, never 1.\n- **2.2 feedback → autonomy**: `security/",
    "**Same day, follow-up; deterministic file hands + the protected tier + /config.**\n\"Reading a file needs no AI\" made real: `actionpack/_files.py` adds `list_dir` /\n`read_file`: read-only, $0, allowlisted without approval because the frontier is\nthe path, not the action: only inside `config.knowledge_roots`, hard ignore-list\n(secret-shaped names) enforced even there, traversal resolved before the ",
    "**Automated frontend net.** Ran the previously-unrun Playwright e2e (browsers already installed);\nit immediately earned its keep; a stale assertion failed because the toast system now also surfaces\nthe \"Import complete\" message (role=status) alongside the panel copy (role=main), so an unscoped\n`getByText` matched two nodes. Scoped the two assertions to `role=main`; **3 e2e green**, which also\ncon",
    "Built (2 slices, gates green):\n- **3.A** `agents/verify.py`: objective verifiers (`command/tests/lint/health/regex/\n  metric`), **never call an LLM** (no provider/gateway import; asserted in tests) +\n  `make_verifier`/`verify` dispatcher; wired into `core/step.py`'s judgment branch via\n  `router.cascade(verify=…)` (failed verification escalates cheap→smart once; the\n  episode records `verificado:"
  ]
}
```
