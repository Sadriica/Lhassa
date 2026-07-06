# Lhassa — narrative & voice book

The source of truth for how Lhassa is *told*: the flag claim, the pillars, the
tone, and the vocabulary. Every public text (README, docs, dashboard copy, site,
release notes) derives from this file. When a text and this file disagree, fix
the text.

Inspired by the *craft* of projects like Hermes Agent (one hammered claim,
features written as promises, friction-killer sections) — never by their form.
Lhassa's narrative devices are its own.

---

## The name and the mythology

**Lhassa** is named for **Lhasa** (ལྷ་ས, "place of the gods") — the city at the
roof of the world. The mark is a **gold stupa with a monk-red finial**; the
palette is the palette of the place: gold, monk-red, bone-white, prayer-flag
green, on warm ink.

Rules for using the mythology (this is what keeps it *mítico-sobrio*, never
kitsch):

1. **At most one mythological touch per section.** A name, an image, a single
   sentence — then back to plain language.
2. **Mythology never replaces a technical noun.** The heartbeat is called the
   heartbeat; the metaphor may *accompany* it ("routine turns of the wheel cost
   nothing"), never rename it.
3. **It is an homage to a place, not a doctrine.** No Buddhist teachings, no
   spiritual claims. Mountains, pilgrims, the wheel, the stupa, altitude,
   patience — imagery of the landscape only.
4. **Warmth over grandeur.** Lhassa's own persona is humble and human; the
   narrative voice matches it. Prefer "it lives quietly on your machine" to
   "an omniscient intelligence".

## The flag (el claim único)

> **Build the brain, rent the body.**

One phrase, hammered verbatim everywhere — README, docs landing, dashboard
about, release notes. The durable thing is *who Lhassa is* (identity, memory,
judgment, trust); models, harnesses and tools are interchangeable muscle, rented
by the hour and swapped without surgery.

Spanish canonical form: **«Construye el cerebro, alquila el cuerpo.»**

The supporting identity sentence (the second thing every text says):

> Lhassa is a persistent personal AI **brain** — an identity that lives awake
> all day for $0, orchestrates rented models and tools, earns its autonomy
> through verified trust, and grows from one brain into an organization.

Spanish: «Lhassa es un **cerebro** personal y persistente — una identidad que
vive despierta todo el día a $0, orquesta modelos y herramientas alquiladas,
se gana su autonomía con confianza verificada, y crece de un cerebro a una
organización.»

## The pillars (in priority order — the owner's priorities lead)

1. **A routine day costs $0.** Do-and-sleep: the heartbeat wakes the brain every
   couple of minutes; a deterministic plane (watchlist, reflexes, actions,
   monitor, digest, routing) answers almost everything without touching a
   model. Thinking is the exception, not the rule.
2. **Subscriptions before APIs.** When it does think, it walks the owner's CLI
   subscriptions (claude / codex / opencode — already paid for) before spending
   a metered API cent. A budget router, a soft brake and a kill-switch guard
   whatever remains.
3. **It forges its own hands.** Repetition is detected with $0 SQL; the brain
   writes the tool (and its test) that replaces the repeated spend; the
   candidate runs only in quarantine until it passes the four gates and a human
   approves its graduation. Tools that stop earning their keep are retired.
4. **Sovereign by construction.** Deny-by-default allowlist, Ed25519-signed
   tasks, trust that only degrades, HITL approvals with an autonomy knob,
   and an append-only episode log: every decision it ever made, auditable.
5. **No lock-in, ever.** Two swap axes (`ModelProvider` to think,
   `HarnessAdapter` to act) behind registries; every capability drops in as a
   plugin. Swap the model, keep the brain.
6. **From one brain to an organization.** The same core scales to a hierarchy
   of brains — departments, command chains, a monitor role — with deterministic
   routing and a full audit trail.

## Narrative devices that are OURS (not Hermes')

- **"A day in the life"** — narrate one real day of the brain tick by tick
  (07:58 the watchlist sees nothing, $0 · 08:00 the digest arrives in the
  owner's voice · 10:14 a Telegram question recalls two past episodes · …).
  This is the do-and-sleep story told as a story; Hermes cannot tell it.
- **The ledger as proof** — end economic claims with the actual number
  ("yesterday: 412 wake-ups, 3 thinks, $0.011"). Honesty is the aesthetic.
- **The gates as a rite of passage** — self-written code *earns* trust:
  quarantine → correcta → mejor → graduation. Tell it as one.

## Tone

Between **mítico-sobrio** and **cálido-personal**: the imagery of the high
plateau, the warmth of Lhassa's own persona, and measurable claims underneath.
Never corporate, never hyped, never cute. Numbers beat adjectives. If a sentence
could appear in any agent's README, sharpen it until it couldn't.

**Honesty rules** (audience is "public from day one, owner as first user"):
claims describe what runs today; roadmap items say so; "the only agent that…"
style superlatives are allowed only when literally verifiable in the code.

## Canonical vocabulary

| Term | Use | Never |
|---|---|---|
| brain / cerebro | what Lhassa *is* | "assistant", "chatbot", "copilot" |
| do-and-sleep | the execution model (hace y duerme) | "polling loop" |
| heartbeat | the tick; routine beats are $0 | "cron job" |
| think / pensar | a model call — always the exception | "query the LLM" |
| the grande / the chica | the two memories | generic "vector store" |
| árbitro | objective verification (never an LLM) | "evaluator model" |
| episodes | the append-only decision log | "logs" |
| the gates | quarantine → graduation for self-written code | "CI" |
| body | rented models/harnesses/tools | — |
| org | the 1..N brain hierarchy | "multi-agent swarm" |
| it | Lhassa's pronoun (no gender) | she/he |

## Deliverable map

| Surface | Text | Derives |
|---|---|---|
| `README.md` (EN) + `README.es.md` | flag → identity → pillars table → a day in the life → economics → quickstart | this file |
| Dashboard "about" / CLI `--help` header | flag + identity sentence | this file |
| Docs landing (future site) | identity + pillar cards + get-started | this file |
| Release notes | plain, numbers-first, one myth-touch max | tone rules |
