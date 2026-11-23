# Lhassa; roadmap vigente

Consolidado el 2026-08-04 desde `doc/plan.md` + `doc/plan-4.md` + el stub
previo de este archivo (que apuntaba a `lhassa-brief.md`/`decisions.md`; esa
indirección se pliega aquí). `doc/decisions.md` sigue siendo la fuente de
verdad histórica y NO se toca; este archivo solo dice **qué está hecho**
(una línea + pointer), **qué está a medias** y **qué sigue**. `plan.md` y
`plan-4.md` quedan como stubs de superseded (el contenido vive en git).

> **Status note, 2026-10-15 (English).** Two things changed after this file was
> written. (1) The 2026-08-30 strategy change (see `decisions.md`) froze Phase 7:
> the org layer is maintained, not extended, so "Stream 6 Fase B" (per-actor auth,
> general RBAC) and any new org primitive below need the owner's go-ahead first.
> (2) Part of Stream 2 shipped on 2026-08-03; the "Siguiente" section marks which
> steps. The rest of this file is in Spanish and was not retranslated.

Una contradicción real apareció al consolidar y se resuelve aquí contra el
código y `decisions.md`, no a ojo: `plan-4.md` listaba el Stream 0 (allowlist
en el heartbeat) como bloqueante-pendiente y el Stream 6 Fase A (multi-user)
como dependiente de él; ambos ya están **hechos** (`decisions.md`
2026-08-03, "Security sweep closure" y "Stream 6 Phase A" respectivamente).
`plan-4.md` no se había actualizado tras esas sesiones.

---

## Hecho; kilometraje (2026-07-21 a 2026-07-28)

Voz y detalle originales en `doc/plan.md` (ahora superseded); pointer completo
en `doc/decisions.md`.

- **Ola 0**: percepción encendida (`knowledge_roots`/`protected_roots`),
  diagnóstico del answer cache, telemetría de kilometraje en el digest. ✅
- **Ola 1**: embeddings reales (`RemoteEmbedder`/Ollama), continuidad de
  hilo, knowledge en el contexto (capa 6 de `memory/load.py`). ✅
- **Ola 2**: sensibilidad a bajo volumen del detector de repetición,
  feedback → autonomía, simulacro completo del forjado (author → cuarentena →
  gates → approve → producción). ✅
- **Ola 3**: cero surface-direct sends (todo por `org/deliver.py`); piloto
  `LHASSA_ORG_LIVE` corrido una vez, **el flag sigue OFF por defecto**. ✅
- **Plan 2** (2026-07-28); Projects, standing brains por kind, plataforma
  (API/dashboard/evidencia prepared→observed→verified), knowledge graph
  LightRAG-lite (`kg_sync` + retrieval, flag OFF), `lhassa tui`, TOON
  (construido y archivado). Detalle: `decisions.md` 2026-07-28. ✅
- **Plan 3** (2026-07-28); Loops (la forja itera; `/goal --verify`),
  context packs, grafo real (triples opt-in + `kg_resolve` + retrieval
  citado), `/moa`, LhassaBench, `/no-ai-slop`. Detalle: `decisions.md`
  2026-07-28 (tarde). ✅

**Pendientes nombrados que quedaron abiertos de esas dos olas** (verificado
en código, siguen sin encender/consumir a 2026-08-04): `LHASSA_MEMORY_GRAPH`
y `LHASSA_KG_TRIPLES` siguen OFF por defecto (`lhassa/config.py`); nada en
`llm/router.py` consume `bench_runs` todavía (solo lo leen
`actionpack/_research_note.py` y el CLI/digest); pantalla TUI de
mensajes/org; proyectos en el digest; VISION.md autorado por modelo. Todos
pasan a "Siguiente" más abajo.

---

## Hecho. Plan 4 (2026-08-03/04)

Voz y detalle de diseño originales en `doc/plan-4.md` (ahora superseded).

- **Stream 0; foundations.** El branch de acciones fijas del heartbeat ya
  cruza `allow()` con Task firmada; `sync_repo` fuera de
  `LHASSA_ALLOWLIST_REPOS` se refusa con episodio honesto. Cerrado junto con
  el resto del security sweep (kill switch global, pseudonimización en el
  fallback chain, self-approval, SSRF en calendar feeds, traversal en skills).
  `decisions.md`: "Security sweep closure" + "Round two: the traversal that
  could delete the brain" (2026-08-03). ✅
- **Stream 1; the versatile brain.** `notify/intent.py:classify_work_type()`
  (question/research/build/review/maintenance/decision), `notify/modes.py`
  (registry de modos), `core/effort.py` (minimal/standard/deep/exhaustive),
  compuesto en `notify/task_router.py`. `decisions.md`: "Plan 4, Stream 1"
  (2026-08-03). ✅; remainder abajo (no todo se integró).
- **Stream 3; install.** `lhassa setup` (6 pasos, $0, permisos por scope,
  perfiles → seeds, smoke check). `decisions.md`: "`lhassa setup`: one
  command closes the eight-manual-step gap" (2026-08-03). ✅
- **Stream 4; a real voice.** `persona.LEAD_PHRASES` como única fuente,
  `notify/phrasebook.py` con test de paridad, identidad en el plano $0,
  bypass de `org/deliver.py` cerrado. `decisions.md`: "A real voice: the $0
  plane finally speaks as Lhassa" (2026-08-03). ✅
- **Stream 5; the four interfaces.** `lhassa flow` con ciclo de vida
  completo (create/edit/validate/run/watch/logs/runs/delete, siempre vía
  `delegate_flow`), TUI de 4 a 8 tabs con `TableScreen` común, dashboard con
  `lib/apiClient.js` como única puerta. `decisions.md`: "Stream 5: four
  interfaces, one capability" (2026-08-03). ✅; remainder abajo (se cortó
  explícitamente).
- **Stream 6 Fase A; multi-user, un cerebro por persona.** `actor_id` como
  columna real (migration 11) en `approvals`/`facts`/`knowledge`;
  `org/sharing.py` (tipos compartibles cerrados: skill/flow/template/
  knowledge; nunca fact/episode); edge `shares` en
  `org/relationships.py`; `lhassa share *` + `/api/share/*`. `decisions.md`:
  "Stream 6 Phase A: one brain per person, shared resources" (2026-08-03). ✅
  Fase B (auth por actor, RBAC, "approver ≠ proposer" como regla general) NO
  empezada; pasa a "Siguiente".

**Otros cierres de la misma ventana que endurecen lo anterior, sin ser un
Stream propio:** licencia AGPL-3.0-or-later, macOS como tier 2 (host layer +
CI real que corrigió supuestos de la auditoría), y el cierre 2026-08-04 sobre
el gate de firmas que no debe matar una recurrencia al refusar.

---

## En curso / parcial

- **Stream 1 remainder**: `EffortProfile` resuelto pero no conectado:
  `loop_turns`/`verifier_count`/`moa` todavía no alimentan `core/step.py`'s
  `_judge()` ni la decisión de abrir panel en `/moa`. Deliberadamente fuera
  de alcance de esa sesión, sin diseño adicional pendiente; solo falta
  cablear.
- **Stream 5 remainder** (cortado explícitamente en esa sesión); los node
  kinds v2 del canvas (pertenecen al Stream 2, no a una superficie),
  `lhassa init`, y la migración mecánica de ~una docena de paneles del
  dashboard que todavía llaman `fetch` directo en vez de `apiClient.js`.

---

## Siguiente

**Stream 2; flow engine v2 ("graph of loops").** Parcial (verificado en
código el 2026-10-15): ya están los pasos 1 a 3 de abajo (registries
`TASK_ROUTERS`/`CONTEXT_PROVIDERS`/`OUTPUT_HANDLERS` en `flows/registries.py`,
commit 2d8b0b7; retries y timeouts por nodo en `flows/engine.py`, a0006bb; los
node kinds `LOOP`/`WAIT`/`SUBFLOW` en `flows/model.py`, 456e36b). Faltan el
paralelismo (`MAP`/`JOIN`, paso 4) y el I/O tipado y versionado (paso 5): no hay
`MAP`/`JOIN` ni `input_schema`/`output_schema` en `lhassa/flows/`. La durabilidad
de base (CAS/leases/fencing/checkpoints/idempotencia) ya era sólida. Diseño
completo, sin recortar, movido tal cual desde `plan-4.md`:

1. **De-Lhassa-ify primero (riesgo de comportamiento cero).** Tres costuras
   se vuelven registries cuyo default ES el comportamiento de hoy: el
   ruteo de `task` → `TASK_ROUTERS` (default `org_topology`); `{base_cv}`/
   `{company}` → context providers (default `cv`); `output: tex` → `OUTPUT_
   HANDLERS` (default `tex` → `lhassa.cv.export`). Los flujos de CV e
   investigación siguen corriendo **sin cambios**.
2. **Retries y timeouts.** `WAITING_RETRY` deja de ser scaffolding muerto: el
   estado, la transición, el `claim_run` que ya despierta por
   `next_wakeup_at` y la tabla `flow_step_attempts` **ya existen**. Solo
   falta el cableado en `engine.py`. El timeout por nodo viaja por el mismo
   camino (`retry_on: timeout`).
3. **Vocabulario nuevo.** `LOOP` (acotado, terminado por verificador;
   `max_iterations` obligatorio, como `agents/author.py`), `WAIT` (evento
   externo; generaliza el park/resume de approval), `SUBFLOW` (composición).
4. **Paralelismo.** `MAP`/`JOIN`. El cursor pasa de
   `frontier: list[(node, data)]` a entradas con `branch_id` y `attempt`;
   `visited` se vuelve claves compuestas. Todo vive en blobs JSON que la DB
   nunca interpreta: **cero migración de esquema**.
5. **I/O tipado y versionado.** `input_schema`/`output_schema` opcionales
   validados por un chequeo estructural mínimo (no un sistema de tipos), más
   `version` en el flow. Las corridas en vuelo ya están pinneadas por
   `flow_snapshot`.

DoD: un investigador construye un flow que itera hasta que un verificador
pasa, corre en paralelo sobre N inputs, se cae a mitad de camino, y `resume`
lo retoma exacto.

**Riesgos de diseño reales (no tareas de build), sin resolver todavía:**
1. Loops vs. validación acíclica; qué back-edges son legales cuando un
   `LOOP` contiene un `BRANCH` o un `LOOP` anidado.
2. Granularidad del checkpoint bajo paralelismo; per-nodo vs. per-batch
   dentro de una región paralela; exige o un contrato de idempotencia en
   cada executor, o una política de que un batch parcialmente completo hace
   fallar la corrida en vez de re-correr silenciosamente.
3. `SUBFLOW` y la jerarquía; si la delegación del padre absorbe la del hijo
   o el hijo registra su propio episodio anidado. Decisión de gobernanza.
4. `JOIN` bajo fallo parcial; en modo `any`/`count`, qué pasa con las ramas
   que nunca llegaron: cancelar, huérfano o descartar. Una rama huérfana que
   sigue llamando a un cerebro pagado es una fuga del invariante $0.

**Stream 7; the three packages.** No empezado. Presets sobre el core; un
seed, plantillas de flow y una pantalla cada uno, no un fork:
- **Research**: flows de experimento, bench como gate, evidence custody,
  corpus citado, exports (la mayor parte ya existe; falta el empaquetado).
- **Dev**: leer repo/sistema, corridas agénticas, tests, automatizaciones,
  context packs.
- **Knowledge**: ingestión, grafo, notas con fuentes, publicación revisada.

**Pendientes nombrados heredados de las olas de kilometraje** (ver sección
anterior): encender `LHASSA_MEMORY_GRAPH` y `LHASSA_KG_TRIPLES` tras rodaje;
el router consumiendo `bench_runs`; pantalla TUI de mensajes/org; proyectos
en el digest; VISION.md autorado por modelo.

**Stream 6 Fase B** (posterior, depende de que Fase A tenga tráfico real);
auth por actor más allá de `require_local_or_token`, RBAC por actor
reusando `org/rbac.py`, "approver ≠ proposer" como regla general en vez de
chequeo defensivo puntual.

---

## Secuencia y verificación (siguen vigentes para todo lo de "Siguiente")

```
Stream 2 (interno: 2.1→2.2→2.3→2.4→2.5) ──┬─→ Stream 5 remainder (canvas v2)
Stream 7 (packages, sobre Stream 2)       ─┘
Stream 6 Fase B (después de rodaje de Fase A)
```

- Suite completa en verde por stream.
- Tests de invariante, no solo happy paths.
- **Un live test es obligatorio antes de cada merge**: no es ceremonia: en
  una sola sesión la suite no vio un stutter de mensaje entregado, corridas
  de test disparando toasts de escritorio reales al dueño, y un path
  traversal por nombre de skill que podía borrar `~/.lhassa`. Tests en verde
  prueban que alguien pensó en chequear algo, nada más.
- Una entrada fechada en `doc/decisions.md`, en inglés.
- Una branch por stream; commits en inglés.
