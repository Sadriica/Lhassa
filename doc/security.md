# Security — the sovereignty model

This is the honest account of what protects the owner, and what doesn't, when Lhassa
acts on its own. For the reasoning behind the design read [`concepts.md`](concepts.md);
for the module map read [`architecture.md`](architecture.md); the phase-by-phase build
log ([`decisions.md`](decisions.md)) has the historical detail behind each mechanism
below. Every mechanism claim here was verified against the code that implements it —
no aspirational claims.

## The stance: autonomy is earned, deny-by-default

Lhassa's default posture is that **nothing runs unless it clears an explicit allowlist,
and nothing self-written runs at all until it has passed four gates in a sandbox**. This
is a deliberate contrast with the common posture in coding-agent tooling, where the
operating system permission boundary (a container, a VM, "don't run as root") *is* the
security model — the agent itself is trusted to decide what to do, and the sandbox exists
to contain the blast radius if it's wrong. Lhassa adds a layer in front of that: a task
has to be an allowlisted action, for an allowlisted target, from a sufficiently trusted
origin, cryptographically signed — before it is even considered eligible to run, and
separately, whether it still needs a human "OK" regardless. The OS-level containment
(quarantine, below) exists too, but it's the second wall, not the only one.

## The allowlist — deny-by-default, per action

`security/allowlist.py:ALLOWLIST` is a `dict[str, ActionSpec]`; the empty case denies.
Each `ActionSpec` carries:

```python
@dataclass
class ActionSpec:
    objetivos: "set[str] | str"   # allowed targets, or the WILDCARD "*"
    min_trust: TrustLevel
    requires_approval: bool
```

`allow(task)` checks, in order, the same sequence the spec's docstring gives as
pseudocode: the action is listed → its objetivo (target) is permitted → the task's
origin trust meets `min_trust` → the task's Ed25519 signature verifies. The first
failure denies with a reason; there is no partial pass.

Risk is **per action**, not a single global knob. Most of the ~20 built-in actions
(`notify`, `add_fact`, `sync_repo`, `check_health`, …) are `INTERNO` trust, no approval.
Two classes stand out precisely because they run arbitrary code:

- **`run_command`** — `requires_approval=True`. "High-risk: execute arbitrary shell code
  even if sandboxed."
- **`run_script`** — the PTC entry point — is `requires_approval=True` too, and its
  own comment is explicit about why: "one script drives MANY actions via RPC. The
  sandbox + per-call allowlist re-check still apply, but the entry ticket into PTC
  always crosses the approval frontier." Every individual action the script triggers
  is *separately* re-checked against this same allowlist (see PTC below) — the
  approval on `run_script` gates entry, not each subsequent call.

This allowlist is task-level (which signed `Task` actions may execute at all) and is
explicitly kept independent from `lhassa/tools/privileged.py`'s command-level (sudo)
allowlist over OS binaries — two layers, on purpose, never merged.

The gate is enforced at the **local execution boundary**, not only at remote/PTC
edges. `core/step.py` checks persisted tasks before fixed actions, judgment and
delegation; reflexes and sensor-produced fixed actions cross it as well.
`core/dispatch.py:run_task_now()` reloads the authoritative row instead of trusting
parameters supplied by its caller. A denial is terminal (`rechazada`) and produces
an episode rather than silently retrying forever. This makes the heartbeat, an early
runner and a remote worker different transports for the same policy contract.

## Signed tasks + trust that only degrades

Every `Task` that reaches the allowlist gate carries a `firma` (signature) and an
`Origin`. `security/signing.py` uses Ed25519 (via the `cryptography` library — "we do
not roll our own crypto") to sign a task's canonical bytes; the brain holds the private
key (`~/.lhassa/keys/ed25519`, generated on first use, `chmod 0600` best-effort), a
machine holds the public half. The point is authenticity, not secrecy — "firmar >
cifrar": the channel (TLS/WireGuard) already handles confidentiality; the signature
proves the task really came from the brain and wasn't tampered with. `signing.verify()`
never raises — bad signature, garbage input, wrong type all just return `False`, because
the allowlist gate has to be able to call it on hostile data.

Tasks created through `core/db.py:add_task()` are signed automatically when the caller
does not provide a signature. The original `creado_en` travels with the task because it
is part of the signed envelope; mutable lifecycle state (`estado`) deliberately is not.
Consequently pending → running → done transitions do not invalidate authenticity, but
changing action, target, parameters, origin or creation time does. Execution always
verifies the exact stored envelope, so a caller cannot replace signed parameters after
retrieval.

`security/trust.py:TrustLevel` is a three-level ordered enum: `MUNDO` (0, derived from
external/untrusted content) < `INTERNO` (1, the brain acting on itself) < `HUMANO` (2, a
human, directly). The one invariant that matters: **`degrade()` returns the minimum of
its inputs, never the maximum.** When several sources combine into one action, the
result is only as trusted as the least-trusted input — a human directive that quotes
content from the open web is, for that derived action, `MUNDO` trust. Trust is never
promoted by combination, only ever degraded by contagion.

## Approvals + the autonomy knob

Once a task is *allowed* to run, `security/approval.py:needs_approval()` decides whether
a human still has to say "OK" first — the HITL frontier, again per-flow rather than
global. It combines three inputs, and any one of them is enough to require approval:

1. the action's own `ActionSpec.requires_approval` flag;
2. the task's origin trust being below the action's `min_trust` — and **always** when
   the origin is `MUNDO` (world-derived content is never trusted to act on its own);
3. membership in `Config.always_approve_actions`, the high-risk set (move-money,
   delete-data-shaped actions) that **always** needs a human, "regardless of everything
   else — including the autonomy knob."

A task that needs approval is *parked* — `request_approval()` writes a pending row to
the `approvals` table and the caller reports `pendiente_aprobacion`; the task does not
execute until a human resolves it (`/approve <id>` / `/deny <id>` over Telegram, or the
dashboard). Do-and-sleep applies here too: nothing blocks waiting for the human, a later
tick just picks up the resolved state.

`security/approval.py:resolve()` also **releases** the minted task the approval was
parked for: approving flips it back to `pendiente` (the next heartbeat, or the
early runner, picks it up); denying closes it `rechazada` directly. Before this
fix a tap only resolved the *queue row* — the underlying task waited forever;
now an approve genuinely unblocks the work, and Telegram's inline-keyboard
handler additionally runs a resolved `mint_task` approval immediately (the
same early-runner+report path the `[[action: …]]` marker uses), rather than
waiting for the next beat.

Approval is checked again at the final execution boundary. A task whose policy requires
approval may execute only when autonomy legitimately applies or the database contains a
durable, approved `mint_task` approval for that exact task id. Reaching
`actions.run()` through a lower-level database or dispatch seam therefore cannot skip
the human decision.

The authority resolving an approval is a trusted principal supplied by the surface,
not arbitrary request data. API, CLI, TUI and Telegram use the canonical owner
principal; a `by_actor` value is retained only as audit context. Separation of duties
is stamped when the approval is requested and enforced when it is resolved for all
high-risk shapes: `always_approve` actions, actions whose allowlist spec requires
approval (including `run_command` and `run_script`), protected-root reads and
non-read-only project attachments. The proposer cannot approve their own request, and
a missing proposer fails closed rather than disabling the rule.

## Global model kill switch

`LHASSA_KILL_SWITCH` / the live `llm_kill_switch` setting is an emergency boundary,
not a routing preference. `llm/gateway.py` owns the process-wide state and every model
provider calls `assert_llm_live()` immediately before HTTP I/O or launching a CLI
subprocess. The heartbeat, fallback chain, Telegram and `/ai` also preserve
`KillSwitchActive` as a hard stop: it is never converted into a budget failure or a
reason to try another provider. Turning the switch on therefore prevents both paid API
calls and nominally free local/CLI inference until it is explicitly turned off.

## Skill-store filesystem boundary

`skills/store.py` treats the configured skill root as a hard boundary for list, get,
save and delete. Skill folders must be real directories, not symlink aliases; a
symlinked `SKILL.md` is rejected and symlinked resources are not exported. The folder
name—not editable frontmatter—is the canonical storage identity. This prevents both
out-of-root traversal and in-store aliasing where one skill could be read or deleted
under another name.

### The protected-roots tier — per-access approval on a path, not an action

A second, stricter tier sits on top of the ordinary allowlist for the
deterministic file-perception actions (`list_dir`/`read_file`,
`lhassa/actionpack/_files.py`): `LHASSA_PROTECTED_ROOTS` names a set of
directories the owner considers "careful" — separate from, and may overlap,
`LHASSA_KNOWLEDGE_ROOTS`. `needs_approval()`'s input (d) checks whether a file
action's `path` param resolves inside one:

- **Per access, not per action.** The frontier is the PATH being touched, not
  the action name — `read_file` against an ordinary knowledge root runs
  no-approval; the exact same action against a protected root always parks.
- **Fail-closed.** Any error resolving the path (a bad string, a symlink
  loop, …) counts as protected — "when we can't tell whether a path is
  protected, the human decides."
- **Never lowered by the autonomy knob.** Unlike an ordinary repeated action,
  a protected-root read is never auto-approved by a streak — "the tap IS the
  verification," same standing as the high-risk always-approve set.
- **`approval.describe()` surfaces the `path`** for these rows, so the owner
  approving one sees WHICH file is being read, never a bare action name.

### Standing-brains attach — a risk classification stamped upstream, on purpose

A project's standing review brains (`org/templates.py`'s use-case templates,
auto-attached by the `actionpack/_standing.py` watchlist sense) are a fifth
input to `needs_approval()`, input (e): an `attach_project_template` task is
gated **unless** the target template is PROVABLY read-only
(`org.templates.template_is_read_only` — every brain in it has
`limits.read_only is True`; unknown template, empty brain list, or any brain
missing the flag all fail-closed to "gate it"). The layering here is
deliberate, not incidental: `org` is a SHELL and `security/approval.py` is an
INTERFACE, and interfaces must never import a shell — not even lazily, since
the architecture guard (`tests/test_architecture.py`) walks every import
regardless of nesting. So the classification is computed **upstream**, in
`actionpack/_standing.py` (a shell, where the `org` import is legal), and
stamped onto the minted task's own `params` as `read_only`;
`security/approval.py:_attach_template_needs_gate` only ever reads that
pre-computed flag. A read-only attach (e.g. `code-review`, whose two brains
both carry `limits.read_only: True`) still mints via `INTERNO` origin — this
is an internal reflex, not world-derived content — but still needs no human
tap; a template that writes (or can't be proven not to) always parks, same
never-lowered-by-autonomy standing as the protected-roots tier above.

### The project detector — a MUNDO-origin proposal, never a silent create

The $0 project detector (`actionpack/_projects.py`'s watchlist sense) notices
a repo listed in `LHASSA_AUTO_SYNC_REPOS` with no matching project row and
mints a `create_project` task to propose one — but it mints it with a
**`MUNDO`** origin, not `INTERNO`. The repo's presence on disk is
world-derived content (something the detector observed, not something the
owner directly asked for in this moment), so input (b) alone is enough to
park it regardless of the action's own approval flag: the trust model itself
is what keeps detection from ever silently creating anything. The same
pattern governs the standing-brains sense's own attach mint (`INTERNO`,
above) — the difference in origin is the difference between "the brain
noticed something in the world" and "the brain reacting to its own state."

`security/autonomy.py` is the streak knob: `should_auto_approve()` tracks, **per
`(accion, actor_id)`** — never globally — the count of consecutive human approvals; once
it reaches `Config.autonomy_threshold` (default 3), that specific action from that
specific actor auto-approves. Three hard rules keep this from becoming a loophole:

- the streak is keyed on the exact action *and* the exact actor — trust earned drafting
  a document never leaks into moving money;
- the high-risk always-approve set is never auto-promoted, unconditionally, no matter
  how long the streak;
- a denial resets the streak to zero, and the knob never raises `TrustLevel` itself —
  it only lowers HITL friction for actions a human has *already* repeatedly approved.

### Feedback → autonomy — the streak also learns from lived behavior, not only explicit taps

`security/autonomy.apply_feedback()` feeds `core/feedback.py`'s inferred
signals (re-asks, corrections, silent acceptance) into the same streak, on a
daily sweep (idempotent per UTC day via `watch_state`):

- **≥2 negative signals** for an action in the window reset its streak to
  zero — treated exactly like an explicit denial.
- **Positive/silence evidence acts weakly**: it advances the streak by AT MOST
  **+1** per action per window, and only for actors who **already hold a
  streak ≥ 1** (a human has to have started the trust first — feedback alone
  never originates a streak from zero).
- **Negative always wins** a conflict within the same window.
- **High-risk actions and the protected-root file actions are untouchable** —
  `apply_feedback` excludes them the same way `needs_approval` does; lived
  feedback can never promote what a human tap can't lower.
- **Attribution is conservative, never guessed**: a signal names only the
  `responder` episode it was inferred from, so `apply_feedback` looks for the
  nearest concrete (non-"responder") action within ±30 minutes; if none is
  found the signal is skipped, not force-attributed to the wrong action.

Registered as the `apply_feedback` action (allowlist, no-approval, the
hygiene tier), scheduled daily by the `maintenance` seed, owned by the
`oracle` monk under the temple department.

`approval.describe()` is the one function every surface (Telegram `/approvals`, the
dashboard panels, the digest) renders the queue through, so "what am I approving?" reads
consistently everywhere — it understands the payload shapes of `mint_action` (making a
repeated pattern automatic), `tool_graduation` (activating a self-written tool),
`flow_gate` (a paused work-flow step), `org_task` (a delegated goal), and degrades to a
generic `tipo`/`accion` label for anything unrecognized. It never raises on a malformed
payload.

## Quarantine — the sandbox for self-written and scripted code

`security/quarantine.py:run_in_quarantine()` runs candidate code — self-written tool
candidates, and PTC scripts — under the strongest sandbox actually available on the
host, detected at runtime via `shutil.which` and probed before being trusted
(`_sandbox_works`):

1. `unshare -rn` — a rootless user+network namespace, network namespace with only a
   down loopback (no route out). **Preferred.**
2. `firejail --net=none` — a network-less firejail sandbox.
3. `bwrap --unshare-net` — a bubblewrap sandbox with no network namespace.
4. no tool available → a plain subprocess with a scrubbed env, a temp cwd, and a hard
   timeout; the module's own docstring flags this fallback `[SUPUESTO]`: "the network
   block is best-effort only — there is no namespace, so a determined candidate could
   still open a socket." `QuarantineResult.isolation` reports which level was actually
   achieved, so a caller can refuse to graduate anything that only ran best-effort.

Regardless of isolation level, every run gets:

- a **fresh temp directory** (`tempfile.mkdtemp(prefix="lhassa_q_")`), so the candidate
  cannot see the real filesystem or the brain's databases, removed afterward;
- a **scrubbed environment** built from scratch, not by stripping `os.environ` — only
  `PATH`/`LANG`/`LC_ALL`/`TZ` pass through, `HOME`/`TMPDIR`/`PWD` are pinned to the
  throwaway dir. Building the allowlist from nothing (rather than a denylist) means a
  *new* secret-shaped env var can never leak by being unknown to a denylist;
- a **hard timeout** (default 30s), with `os.killpg(pgid, SIGKILL)` on expiry — the
  whole process group is killed, not just the top-level PID, since the candidate runs
  `start_new_session=True`.

If the sandbox wrapper itself fails to set up isolation (detected from its own stderr
diagnostics), the run is treated as `escaped=True` and fails gate 1 outright — an
un-isolated run is never silently trusted as caged.

## The four gates — self-written code earns its trust

`agents/gates.py:run_gates()` is deny-by-default graduation: a candidate is rejected
unless every gate explicitly passes, and the pipeline **stops at the first failure** (no
point measuring "better" on something not even proven "correct"):

1. **Segura** — `run_in_quarantine()`, above.
2. **Correcta** — a `Verifier` from `agents/verify.py` (command/tests/lint/health/
   regex/metric) must exit 0. No `tests_spec` given → automatic fail: "cannot prove
   'correcta' (deny-by-default)."
3. **Mejor** — must beat a concrete numeric baseline on a concrete metric (tokens/time/
   steps) via `MetricVerifier`. No baseline/metric function → automatic fail: "cannot
   prove 'mejor' (need a baseline)."
4. **Retiro** — not inline, continuous: `should_retire()` demotes a graduated tool back
   to quarantine if it went unused (zero recorded uses) or its production failure rate
   crosses 50% over at least 3 uses. It can re-enter the pipeline and re-graduate later.
   The `review_tools` action runs this sweep weekly by default (the `maintenance` seed).

**The árbitro principle**: verifiers (gate 2, gate 3) are objective checks — a command
exit code, a test suite result, a numeric metric comparison — and **never call an LLM**.
`agents/verify.py` imports no provider/gateway module at all, and this is asserted, not
just documented: `tests/test_verify.py::test_verifiers_never_invoke_an_llm` monkeypatches
both `llm.provider.get_model_provider` and the gateway to explode on any call, then
confirms every verifier still runs to completion. An "AI judging AI" loop would let a
biased or manipulated verdict pass a candidate; a shell exit code cannot be talked into
lying.

Passing all three inline gates does not execute the tool — it only clears the way to
park a `tool_graduation` approval (`security/approval.py`, above). A human, or an earned
autonomy streak, still has to say yes before the candidate is trusted with real
invocations.

`propose_tool(..., moa_verdict=True)` can attach a Mixture-of-Agents panel
review (`llm/moa.py`) of the graduated candidate's code to that same approval
payload — a second opinion for the human to read alongside the gate results.
It changes nothing about the trust boundary: the review is advisory only, the
four gates remain the sole hard filter, and a missing or degraded panel never
blocks or auto-approves a graduation.

## PTC containment — one script, many re-checked actions

`agents/ptc.py:run_script()` is "Programmatic Tool Calling": a script that needs to
drive several actions in sequence without a model round-trip per action. Its
containment stacks three independent guards:

- **The script itself never touches anything real.** It runs inside
  `run_in_quarantine`'s isolation (temp workdir, scrubbed env, no network), and its
  *only* door out is a Unix-domain-socket RPC to the parent process — the generated
  stub's own docstring states it plainly: "the ONLY capability available to this
  script: calling one deterministic Lhassa action, dispatched by the PARENT process
  over a Unix domain socket... There is no other door out of this sandbox."
- **Every RPC call is re-checked against the full allowlist**, not trusted because the
  script itself was approved to run. Each call builds a signed `Task` (actor `"ptc"`,
  trust `INTERNO`) and calls the exact same `security/allowlist.py:allow()` gate a
  normal task dispatch uses; an unknown action, a disallowed target, or anything whose
  `AllowDecision.requires_approval` is true is refused outright — PTC cannot grant HITL
  synchronously from inside a sandboxed script, so those calls just fail.
- **Hard caps**, independent of the allowlist: a 300-second wall-clock timeout (the
  whole process group SIGKILLed on expiry via `_kill_group`), a 50-call cap enforced
  inside the RPC handler itself (further calls refused with an explicit
  "max_calls exceeded" message, not silently dropped), and truncated stdout (50,000
  chars) / stderr (10,000 chars) so a runaway script cannot bloat an episode.

Because one script can fan out into many actions, entering PTC at all — invoking
`run_script` — always requires human approval (`security/allowlist.py`, above), even
though the individual actions inside it are separately gated too. Defense in depth, not
either/or.

## Privacy: pseudonymization at the remote boundary

`llm/provider.py:PseudonymizingProvider` wraps any **non-local** `ModelProvider`
(`is_local=False`) when `config.pseudonymize_remote` is enabled — the local path is
never wrapped, because a local model never leaves the machine and there is nothing to
scrub. Before a request reaches the wrapped provider, `complete()` runs the messages
(and any system prompt) through `privacy.py`'s scrubber; after the reply comes back, it
runs `depseudonymize()` before handing the text onward.

The scrubber is a **pure-stdlib regex** pass — no model, no external library — applied
in a fixed order: email → IPv4 address → secret-shaped token (API-key patterns like
`sk-`, `ghp_`, `AKIA…`, or a generic 32+ char high-entropy string) → phone number. Each
literal value is swapped for a stable placeholder (`<email_1>`, `<phone_1>`, …); the same
value reuses its placeholder within one call so the reply can be de-pseudonymized
symmetrically. The module's own rule: "nothing here may ever raise. On any error it
degrades to passthrough" — a scrubber bug fails open to the *original* text rather than
silently corrupting or blocking the call.

What this means concretely: when the active provider is a metered cloud API or a CLI
subscription (all cloud-ish, all wrapped when the flag is on), emails, phone numbers, IP
addresses and secret-shaped strings in the prompt are replaced before they leave the
box, and restored in the reply the owner sees. When the active provider is `local`
(`LocalModelProvider`, an OpenAI-compatible local endpoint), nothing is scrubbed, because
nothing is sent anywhere.

## Honest limits — what this does not protect against

- **The owner's own approvals are the ceiling.** Every gate above ends in either a
  deterministic allow or a parked human approval — the model of trust assumes the human
  resolving that approval queue is not itself compromised or careless. If the owner
  rubber-stamps every `tool_graduation` and `mint_action` prompt without reading
  `approval.describe()`'s summary, the autonomy knob will happily convert that into
  unattended auto-approval within 3 repeats (`autonomy_threshold`).
- **A compromised host defeats everything.** The allowlist, the signing key, the
  quarantine sandbox, and the approval queue all live on the same machine as the brain.
  Root on the box (or write access to `~/.lhassa/keys/ed25519`, `~/.lhassa/brain.db`, or
  the config) can forge signatures, edit the allowlist table in the source, or simply
  bypass the whole apparatus by running code directly. This is a sovereignty model for
  *the brain's own decisions*, not an anti-tamper system against a compromised operator
  or a supply-chain attack on the host.
- **The best-effort quarantine fallback is real, not theoretical.** On a host with none
  of `unshare`/`firejail`/`bwrap` installed, `run_in_quarantine()` still runs the
  candidate — with a scrubbed env and a temp dir, but its own docstring admits the
  network block is best-effort only; a sufficiently motivated candidate could still open
  a socket. `QuarantineResult.isolation` exposes this so a stricter caller *could* refuse
  to graduate on `isolation="none"`, but `agents/gates.py` does not currently enforce that
  refusal itself — gate 1 only checks `ok`/`timed_out`/`escaped`, not the isolation level.
- **Pseudonymization is a regex, not an NLP entity scrubber.** It catches emails,
  phones, IPs and secret-shaped tokens — it does not catch a name, an address written in
  prose, or any PII that doesn't match one of those four shapes. It also only applies to
  *remote, non-local* providers, and only when `pseudonymize_remote` is explicitly
  enabled — it is not on by default for every cloud call.
- **The allowlist is a static table, not a learned policy.** `ALLOWLIST` is built once
  from `Config` at import time (`build_allowlist()`); it protects against a task with
  the wrong action/target/trust/signature, not against a *correctly signed, correctly
  targeted* task whose consequences the owner didn't anticipate when they wrote the
  spec. Deny-by-default keeps unknown actions out; it does not audit whether an allowed
  action's blast radius was scoped tightly enough.
- **Delegation into the org (`org_run`) inherits, rather than re-derives, trust.** Its
  own allowlist entry does not itself require approval — the high-risk gate for a
  delegated goal lives in the org layer's own multi-person approval (proposer ≠ approver
  for high-risk), which is a separate check that has to be wired correctly at that layer
  to hold; it is not redundantly enforced by the top-level allowlist a second time.
</content>
