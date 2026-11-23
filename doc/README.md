# Lhassa docs

Lhassa is a persistent personal AI brain; build the brain, rent the body. It
lives on one machine, thinks only when it is worth the spend, and orchestrates
rented models and tools instead of being one. These docs are organized by what
you are trying to do, not by subsystem.

## Start here

- [`getting-started.md`](getting-started.md); install it, wake it, wire Telegram, open the dashboard.

## Understand it

- [`concepts.md`](concepts.md); the core ideas: do-and-sleep, the two memories, the árbitro, the org.
- [`security.md`](security.md); trust, approvals, the allowlist, quarantine and the gates.
- [`architecture.md`](architecture.md); current high-level overview (layers, data dir, phases, diagrams).
- [`architecture-layers.md`](architecture-layers.md); the modularity guard: layers + forbidden import directions.
- [`../packages/README.md`](../packages/README.md); the capability-pack contract: what a pack is, how it registers, how to install one.
- [`boundaries.md`](boundaries.md); what is public code and what stays the owner's (read before committing anything).
- [`research.md`](research.md); Lhassa's research line: the bench as tribunal, drafted notes.
- [`integrity.md`](integrity.md); the integrity watcher: executable architecture and sovereignty contracts.
- [`narrative.md`](narrative.md); the narrative & voice book (the flag, the pillars, tone, vocabulary).
- [`lhassa-brief.md`](lhassa-brief.md); the implementation spec, in Spanish.
- [`decisions.md`](decisions.md); the running decisions log, phase by phase.
- [`roadmap.md`](roadmap.md); the CURRENT plan: what's done (pointers into `decisions.md`), what's in progress, what's next.
- [`plan.md`](plan.md) / [`plan-4.md`](plan-4.md); superseded, absorbed into `roadmap.md` on 2026-08-04; kept only so old links don't die.

## Use it

- [`cli.md`](cli.md); every `lhassa` subcommand, flags and examples.
- [`telegram.md`](telegram.md); the bot: commands, the digest, approvals from chat.
- [`dashboard.md`](dashboard.md); the React dashboard: sections, WebSocket, auth.
- [`memory.md`](memory.md); the grande, the chica, and semantic (layer 4) facts.
- [`tools.md`](tools.md); self-written tools: quarantine, the gates, graduation.
- [`agents.md`](agents.md); background/agentic execution (`lhassa code`, the harnesses).
- [`scheduler.md`](scheduler.md); recurring tasks, the heartbeat, deferred retries.
- [`calendar.md`](calendar.md); connecting a calendar: ICS feeds or Microsoft Graph.
- [`outlook.md`](outlook.md); Outlook email/calendar setup (Azure app registration).
- [`examples.md`](examples.md); worked end-to-end scenarios.
- [`voice-interaction.md`](voice-interaction.md); voice, an opt-in surface (off by default).

## Operate it

- [`install.md`](install.md); the always-on Linux install (systemd user units, boot lingering).
- [`configuration.md`](configuration.md); providers, voice, tools, agents and the main settings (the README's configuration table lists the `LHASSA_*` knobs by group).
- [`coverage.md`](coverage.md); the coverage baseline and what is deliberately left out.
- [`deployment.md`](deployment.md); the Docker image, Compose roles, secrets.
- [`portability.md`](portability.md); the VPS-portability audit: couplings, status, blockers.
