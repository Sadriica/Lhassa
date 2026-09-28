# Lhassa. Brief de implementación

> **Núcleo con identidad propia (un JARVIS)** que orquesta modelos y herramientas
> sin depender de ninguno. Lo durable es *quién es*; los modelos, harnesses y
> scripts son músculos intercambiables. **Construye el cerebro, alquila el cuerpo.**
>
> **Orientación organizacional desde el día uno:** se diseña como *una instancia
> con un dueño*, no como "el cerebro de una persona". El uso personal es la
> primera instancia (dueño = tú); la meta final es un JARVIS organizacional
> (cerebro general + cerebros por departamento). Single-tenant **en la práctica**
> durante todo el desarrollo del núcleo, multi-tenant **en la forma** desde el
> esquema. Ver ganchos en §3-Fase 0 y la capa organizacional en §3-Fase 7.

---

## Cómo usar este documento (para la IA que implementa)

**Audiencia:** una IA que construye esto **adaptando un esqueleto Python ya
existente** (incluye reconocimiento de voz, y los módulos `agents/base.py`,
`llm/gateway.py`, `llm/provider.py`, `persona.py`, `tools/registry.py`,
`memory/`, `health/`, `scheduler.py`, `state.py`, `api/server.py`, `cli/`,
paneles de dashboard). **Extiende y adapta; no reescribas lo que ya existe.**

**Reglas de oro:**
1. Respeta los **invariantes** (§1) sobre cualquier conveniencia.
2. Construye en el **orden de fases** (§3). Cada fase es una rebanada vertical que
   corre, se prueba y se usa. No empieces una fase sin cerrar la DoD de la anterior.
3. **Observabilidad desde el primer commit** (el episodio estructurado).
4. Las interfaces de §2.2 y el `instance_id` de §2.3 son **costuras obligatorias**.
   Nada de cablear un proveedor/harness concreto ni de asumir un solo dueño.
5. `[CONFIRMADO]` se verificó contra documentación real; `[SUPUESTO]` debe
   validarse en un smoke run antes de confiarse. Diseña defensivo alrededor de lo
   `[SUPUESTO]`.
6. **NO construyas la capa organizacional (Fase 7) todavía.** Solo deja sus
   ganchos baratos (§3-Fase 0). Meter multi-tenancy/jerarquía/roles antes de tener
   el núcleo probado es la forma más segura de no terminar nunca.
7. Documento **vivo**: tras cada fase, corrige aquí lo que la realidad contradiga (§4).

---

## 0. Resumen ejecutivo

Un cerebro persistente en un VPS bajo el patrón **"hace y duerme"**: *estado en
disco + una función `step()` que lo avanza un paso*, despertada por un latido
(systemd timer) o por demanda (API). Tiene personalidad, memoria con continuidad,
criterio para elegir qué músculo usar, y un bucle de auto-mejora que escribe y
ejecuta sus propias herramientas dentro de una cuarentena. Alcanza tus máquinas
(PC; casa a futuro) mediante **agentes locales que hacen check-in** (no push).

**Empieza supervisado**; la autonomía se gana con evidencia.

**Diferenciador** (nadie lo centra): identidad desacoplada del modelo + autonomía
nativa segura (cuarentena + allowlist) + privacidad por pseudonimización. El
cuerpo (orquestación de harnesses, contabilidad) ya está poblado por terceros
maduros; **se usan, no se compite** (ccusage para contabilidad; OpenRig/ECC y
fleet-views nativos para orquestación).

**Validación de mercado** `[CONFIRMADO]`: la arquitectura jerárquica de Fase 7
(supervisor + workers) **es el patrón estándar de despliegue empresarial**, y las
piezas de "governance" que la industria cobra caro; audit plane, model gateway,
memoria con permisos, HITL por flujo; **ya están en este diseño** con otros
nombres (el episodio, el `gateway.py`, la memoria privada+común, la frontera de
aprobación). **Decisión:** núcleo **propio**, NO adoptar un framework (LangGraph/
CrewAI/AutoGen); adoptarlo rompería el invariante de "cerebro con identidad
desacoplada"; sirven solo como referencia de patrones para la Fase 7.

**Contexto económico** `[CONFIRMADO]`: Anthropic cerró el arbitraje de
suscripción (abril 2026 → *Agent SDK credits* medidos); Google apretó Gemini CLI.
La economía dejó de ser "exprime el plano"; es "gasta el crédito/API correcto en
la tarea correcta y no desperdicies tokens". El latido rutinario es determinista;
el modelo solo despierta para el criterio difícil.

---

## 1. Principios rectores (invariantes; no cambian)

1. **Construye el cerebro, alquila el cuerpo.** Solo desde cero: identidad,
   memoria, árbitro (router + verificación + frontera de aprobación), auto-mejora
   con cuarentena. Lo demás se enchufa (MCP, harnesses, ccusage, cron, scripts).
2. **Hace y duerme.** Disco = cerebro; RAM = desechable. La continuidad se finge
   con memoria, no con uptime.
3. **Dos ejes intercambiables, tras interfaz** (§2.2). El **modelo** (pensar
   directo) y el **harness** (código delegado) son enchufables. El cerebro nunca
   llama a un concreto; llama a una interfaz.
4. **Una instancia con un dueño, nunca un dueño único asumido.** Todo lo "del
   dueño" lleva `instance_id` desde el día uno. Single-tenant en la práctica,
   multi-tenant en la forma. (Habilita la Fase 7 sin cirugía posterior.)
5. **Determinismo en el piso, modelo solo para el criterio.**
6. **Seguridad en capas que fallan distinto:** cuarentena (código auto-escrito) +
   allowlist determinista (órdenes ejecutables) + conciencia de amenazas
   (probabilística, *encima*). Deny-by-default.
7. **La confianza se degrada por contagio, nunca se promueve.**
8. **No rodar cripto ni infra casera.** TLS/túnel, Ed25519, SQLite, systemd, git.
9. **La autonomía se gana con evidencia.** Supervisado primero; perilla que baja
   con aprobaciones repetidas.
10. **Rebanada vertical + revisión continua** (§4).

---

## 2. Arquitectura de referencia

### 2.1 Modelo de ejecución; "hace y duerme"

El cerebro es `estado + step()`. Despertadores: **systemd timer** (latido) y **API
REST/WS** (por demanda; durante conversación activa se mantiene vivo hasta
terminar, luego duerme).

```python
# core/step.py; forma de referencia
def step(state: State) -> None:
    triggers = watchlist.check(state)          # determinista, CERO tokens
    if not triggers:
        return
    for t in triggers:
        episode = Episode.begin(trigger=t, instance_id=state.instance_id)
        if t.maps_to_fixed_action():            # sin LLM
            result = actions.run(t.action, episode)
        else:
            ctx = memory.load(state, token_budget=cfg.budget)   # por capas
            decision = router.decide(t, ctx)    # eje 1: proveedor o harness
            result = executor.run(decision, episode)
            verify.check(result, episode)       # compuertas objetivas
        episode.finish(result)
        store.append(episode)                   # observabilidad
    state.persist()                             # un solo escritor (§2.7)
```

Tareas largas: `step()` **lanza** el trabajo como proceso aparte (harness con su
PID), registra "job PID X" en el estado, y duerme. El siguiente tick supervisa
asíncrono vía disco. Nunca espera bloqueado.

### 2.2 Las dos interfaces de intercambio (costuras obligatorias)

**Eje 1; `ModelProvider`: piensa directo, SIN harness** (latido, clasificación,
decisiones, consolidación de memoria).

```python
# llm/provider.py  (adaptar el existente)
class ModelProvider(Protocol):
    name: str
    zero_retention: bool          # ¿entrena con los datos?
    def complete(self, messages, *, model=None, max_tokens=1024, **opts) -> Completion: ...
# Implementaciones: OpenCodeZenProvider (ahora) -> LocalProvider (después, zero_retention=True)
```

**Eje 2; `HarnessAdapter`: trabajo agéntico de código delegado.** Soporta los
tres (opencode, Codex, Claude Code), músculos con capacidades distintas
(Claude Code atado a Claude; Codex a OpenAI; opencode flexible). Es el prototipo
ya construido (parseo `stream-json`).

```python
# harness/adapter.py
class HarnessAdapter(Protocol):
    name: str
    model_locked: bool
    billing: str                  # "metered" | "subscription" | "free"
    def build_command(self, prompt, **opts) -> list[str]: ...
    def parse_line(self, raw) -> RunEvent: ...
# Implementaciones: OpenCodeAdapter (primero) -> ClaudeCodeAdapter, CodexAdapter (Fase 4)
```

**El router elige en ambos ejes** (§2.4): "¿pienso (provider) o delego (harness)?";
si delega, "¿cuál harness?"; para el provider, "¿qué modelo por costo y
sensibilidad del dato?".

**Arranque** `[CONFIRMADO sobre opencode Zen]`: latido con opencode Zen ahora
(gratis/barato) → **migrar a modelo local** después (solo cambia la implementación
de `ModelProvider`). **Privacidad:** los modelos *gratis* de Zen pueden entrenar
con tus datos; por eso la pseudonimización (§2.6) es **gancho obligatorio**
mientras el latido use gratis (lo que toca la nube gratis debe ser lo menos
sensible posible hasta migrar a local). Código delegado por los gratis sin
problema (el dato es el repo, no tu vida).

### 2.3 Estado y memoria

Cuatro cosas distintas: **identidad** (config con peso, se carga entera; es un
*parámetro de la instancia*, no una constante), **hechos** (duraderos, pasan por
ti), **episodios** (qué pasó), **estado de trabajo** (ya serializado).

**Memoria dual** (patrón validado en empresa): *working memory* (la chica:
contexto de sesión) + *persistent memory* (la grande: conocimiento, decisiones
históricas, preferencias). La distinción es crítica para el aislamiento entre
instancias/departamentos.

**Hechos con tipo:**
```python
# memory/facts.py
@dataclass
class Fact:
    id: str; instance_id: str       # <- raíz multi-tenant desde el día uno
    tipo: str            # "dato" | "directiva"
    contenido: str; pinned: bool; tags: list[str]
    procedencia: str; confianza: float
    creado_en: str; actualizado_en: str
# dato:      "le gusta el chocolate con menta"
# dato:      "prefiere herramientas de código libre"
# directiva: "responder conciso por defecto"            -> pinned
# directiva: "optimizar; muy sensible a cualquier delay" -> pinned
# Las DIRECTIVAS cambian el CÓMO actúa (se cargan siempre como identidad).
```
Contradicción: gana el **más reciente y de mayor confianza**.

**Dos almacenes:**
- **La grande**: SQLite **append-only / inmutable**, fuente de verdad. Todo
  crudo. De aquí: reportes, proyecciones, auditoría, reconstrucción de la chica.
  Fila = campos consultables **+** `raw` (JSON completo) + `instance_id`.
  Artefactos pesados → archivo en disco + **puntero**.
- **La chica**: caché viva que se carga al despertar. Derivada de la grande.

*Olvidar mal nunca es perder* (el crudo sigue abajo).

**Subir (al despertar), por capas, hasta un presupuesto de tokens:**
```python
# memory/store.py
def load(state, token_budget) -> Context:
    ctx = []
    ctx += facts.all(instance_id, pinned=True)     # 1. pinned + TODAS las directivas
    ctx += episodes.recent(instance_id, n)         # 3. recencia (continuidad)
    ctx += by_tags(instance_id, state.active_context)  # 2. contexto activo (tags, NO semántica)
    # capa 4 (embeddings) OPCIONAL: solo si 1-3 se quedan cortas. Por defecto NO.
    return truncate(ctx, token_budget, priority=["pinned","recent","tags"])
```

**Bajar / consolidar** = `memory/consolidate.py`, por **umbral** ("se llenó la
chica"), no por reloj. ~80% mecánico (LRU, sin tokens) + LLM **barato** para lo
semántico (resumir parecidos, detectar contradicciones). **Solo toca la chica.**
Fase 1 arranca **sin** compresión semántica (solo pinned + tags + recientes); la
semántica se añade en Fase 2.

**Memoria entre agentes** `[a aterrizar]`: la chica lleva **apuntadores** a
agentes que corren y sus memorias, *para registro*. No comparte todo: cada
especialista tiene **lo suyo + un común**. Al terminar un agente, su entrada pasa
de "apuntador vivo" a "referencia archivada" (patrón del PID).

### 2.4 Economía y router

- **Watchlist** (`core/watchlist.py`): cada tick revisa en código (cero tokens):
  equipo se reportó · archivo/repo cambió · venció tarea · terminó job · cruzó
  umbral · mensaje que matchea regla. Nada → duerme.
- **Disparador → ¿acción fija o criterio?** Muchos mapean a acción determinista
  (PC reportó → `sync_repo`); el modelo solo despierta para juicio.
- **Router (`llm/router.py`)** decide en dos ejes; `pick_provider` por costo y
  **sensibilidad del dato** (sensible → zero-retention); `pick_harness` por costo,
  capacidad y plan disponible (evitando los caminos que la política encareció).
- **Cascada:** barato → verifica → escala. El log de cascada alimenta al router.
- **Comandos manuales** (solo presente): `$think` / `$justdo`. No reemplazan la
  watchlist autónoma.
- **Choke point** (`llm/gateway.py`): caché + presupuesto + ledger +
  **kill-switch**. Consume **ccusage** (JSON) para contabilidad cruzando harnesses.

### 2.5 Multi-máquina; check-in, no push

Cada equipo corre un **agente local delgado** (`remote/local_agent.py`) que se
conecta hacia afuera.

```
PC -> cerebro:  POST /checkin {machine_id, capabilities, current_state}
cerebro -> PC:  {tasks: [Task firmada, ...]}        # reconciliación
PC ejecuta cada task -> gate de allowlist (§2.6) -> POST /report {task_id, outcome, detail}
```

**Estado deseado + reconciliación:** el cerebro mantiene `desired_state` por
máquina; en cada check-in reconcilia real vs. deseado. `git pull` es reconciliación.
Disparado por **evento** (el PC se reportó), no por predicción.

```python
@dataclass
class Task:
    id: str; instance_id: str
    accion: str          # set CERRADO: "sync_repo", ...
    objetivo: str; params: dict
    origen: Origin       # quién + nivel de confianza (§2.6)
    estado: str; firma: str; creado_en: str
```

El agente local puede exponer sus capacidades como **herramientas MCP**.

**Tareas tipadas planificadas:** `sync_repo` (primera) → de tu uso real:
`calendar_event` ↔ crea task asociada · `agendar_fechas` (fechas de examen →
agenda) · `generar_documentacion` · `tracking_postulaciones` (hojas de vida /
internships). *(Segunda tarea a elegir; define la primera integración MCP de Fase 4.)*

### 2.6 Seguridad

**Tres muros (fallan distinto):**
1. **Cuarentena**: código auto-escrito (§2.8).
2. **Allowlist en el equipo** (`security/allowlist.py`); **deny-by-default**:
   ```python
   def allow(task) -> bool:
       return (task.accion in ALLOWLIST
               and task.objetivo in ALLOWLIST[task.accion].objetivos
               and task.origen.trust >= ALLOWLIST[task.accion].min_trust
               and signing.verify(task))
   ```
3. **Conciencia de amenazas**: filtro probabilístico, *encima*, nunca en lugar de.

**Confianza por origen + QUIÉN** (`security/trust.py`):
```python
class TrustLevel(IntEnum):
    MUNDO = 0      # derivado de contenido externo
    INTERNO = 1    # cerebro sin input externo
    HUMANO = 2     # un humano, directo
@dataclass
class Origin:
    trust: TrustLevel
    actor_id: str | None     # QUIÉN (no solo qué tan confiable); prepara roles de Fase 7
# La confianza se degrada por contagio, nunca se promueve.
```

**HITL por flujo, NO global** (lección de la práctica empresarial): el umbral de
confianza/aprobación se define **por tipo de acción según su riesgo**, no uno
global. Mover dinero o borrar datos exige más (p.ej. aprobación humana siempre);
redactar un borrador exige menos. La allowlist lleva, por acción, su umbral y si
requiere aprobación.

**Autenticidad por firma** (`security/signing.py`): canal TLS/túnel + firma
Ed25519 (privada en VPS, pública en el equipo). Firmar > cifrar para ejecución
remota.

**El VPS es el eslabón débil.** Defensa principal = el PC no confía del todo
(allowlist). **Decisión asumida:** secretos en el VPS (autonomía sobre privacidad),
latido por la nube. **Pseudonimización** = gancho obligatorio mientras el latido
use modelos gratis; se activa plena al migrar a local.

### 2.7 Concurrencia; trabajo paralelo, bookkeeping serial

- **Trabajo** (deploy, análisis) = **paralelo**, en procesos (PID); no toca el
  estado mientras corre.
- **Bookkeeping** (apuntar empecé/terminé/aprendí) = **serial**, un escritor.

**Mecanismo:** SQLite **WAL** (lecturas libres sobre la grande inmutable + un
escritor). El latido que quiera escribir mientras tú escribes: **se salta el ciclo
y reintenta**. **Controlador / doble buffer:** si el estado está ocupado, la
escritura va a un **buffer de staging** y se **reconcilia** al liberarse; mismo
patrón estado-deseado/reconciliación de §2.5, aplicado a la memoria.

### 2.8 Auto-mejora 2b; cuarentena + compuertas

```python
# agents/verify.py; objetivo, NUNCA LLM
class Verifier(Protocol):
    name: str
    def verify(self, artifact, context) -> VerifyResult: ...  # passed, detail, metric
# built-ins: command (exit 0), tests, lint, health, regex, metric
```

**Compuertas en orden (gradúa solo si pasa todas):**
1. **Segura**: cuarentena: sandbox sin red/datos.
2. **Correcta**: funciona (test/check, exit 0).
3. **Mejor**: supera una **línea base en métrica concreta** (tokens/tiempo/pasos).
4. **(continua) Retiro**: si una graduada deja de usarse o falla en producción,
   se desgradúa (vuelve a cuarentena).

**Especialistas por dominio = skills, NO fine-tuning** (mismo modelo base +
instrucciones + herramientas + criterios; así no dependes de ningún modelo).
Compuertas **modulares por dominio**; deny-by-default. **Primer dominio =
optimización** (métrica limpia) sobre calendario/tasks; los difusos después.

**Versionado/rollback gratis vía GitHub:** herramientas auto-creadas como
**repos/PRs**; aprobación = merge de PR. Candidato a sandbox ya hecho: *Managed
Agent Harness* de Anthropic.

### 2.9 Interfaces de usuario; clientes delgados sobre una API

El cerebro expone **API REST + WS** (`api/server.py`); todo lo demás es cliente
sin lógica.
- **CLI** (`cli/`): actuar/conversar rápido, REPL, comandos internos, `$think`/`$justdo`.
- **Dashboard web** (`dashboard/`): (1) **cola de aprobaciones**, (2) **sala de
  cuarentena**, (3) observabilidad del fleet + costo + reportes/proyecciones, (4)
  límites de costo.
- **Notificaciones:** portal + **Telegram**.

### 2.10 Observabilidad, fallos, backup, reinicio

```python
@dataclass
class Episode:                       # observabilidad = esta pieza bien estructurada
    id: str; instance_id: str
    despertar_por: str
    memoria_cargada: list[str]       # ids + motivo
    opciones: list[str]; decision: str; accion: str; resultado: str
    costo_usd: float; raw: str; creado_en: str
```
La observabilidad es una *vista* sobre la grande (timeline en el dashboard), no un
sistema aparte. (En la Fase 7 esta misma pieza es el *audit plane* legal.)
- **Fallos:** transitorio → reintento con backoff; persistente → "falló, necesito
  ayuda" a la cola de aprobaciones; herramienta mala → retiro.
- **Backup:** la grande se replica **incremental** al PC (rutas espejo,
  redundancia). **Verificar restauraciones.**
- **Reinicio:** systemd hace que servicio + timer + estado vuelvan solos.

### 2.11 Stack y layout de módulos

| Capa | Tecnología |
|---|---|
| Host/resiliencia | Linux VPS + **systemd** (unit + timer) |
| Lenguaje | **Python 3.11+** |
| Estado + memoria | **SQLite (WAL)** + archivos para blobs |
| API | **FastAPI** (REST + WS) |
| Dashboard | **React** |
| Red entre máquinas | **WireGuard / Tailscale** + TLS |
| Integraciones | **MCP** (SDK `mcp` de Python) |
| Código delegado | adaptadores `stream-json`: opencode → Codex/Claude |
| Contabilidad | **ccusage** (JSON) |
| Modelo del latido | opencode Zen (ahora) → local (después), tras `ModelProvider` |
| Firma | **Ed25519** (`cryptography`/PyNaCl) |
| Auto-mejora/versionado | git + **GitHub** |
| Notificaciones | **Telegram Bot API** + portal |
| Pruebas | **pytest** + replay offline |
| Voz (tardío) | esqueleto existente (faster-whisper + Piper/Kokoro) |
| **Coordinación (Fase 7)** | mensajes tipados entre instancias; común de solo-lectura |

```
lhassa/
  core/        step.py · watchlist.py · actions.py
  llm/         provider.py (ModelProvider + OpenCodeZen/Local) · gateway.py · router.py
  harness/     adapter.py (OpenCode→Codex/Claude) · runner.py (PID/stream)
  memory/      store.py (grande+chica) · facts.py (dato/directiva) · consolidate.py
  security/    allowlist.py · trust.py (Origin: quién+nivel) · signing.py · quarantine.py
  agents/      base.py (existente) · verify.py · specialists/ (skills por dominio)
  remote/      local_agent.py · checkin.py
  api/         server.py (REST+WS)
  cli/  ·  dashboard/  ·  persona.py (identidad parametrizada)  ·  voice/  ·  config.py
  org/         (Fase 7. NO construir aún) coordinator.py · messaging.py · commons.py
```

---

## 3. Roadmap por fases

Rebanada vertical por fase, con **Definición de terminado (DoD)** verificable.
Esfuerzo aproximado para una persona (semanas orientativas). No avanzar sin cerrar
la anterior.

### Fase 0. La columna (+ ganchos organizacionales) · ~2–3 sem
**Objetivo:** circuito completo mínimo, observable, con la forma multi-tenant
sembrada.
**Construye/adapta:** `core/step.py`, `core/watchlist.py` (1 disparador: PC
reportó), `state.py`→SQLite WAL, systemd timer + check-in/report en `api/server.py`,
`remote/local_agent.py` con `security/allowlist.py` (deny-by-default) +
`signing.py` (Ed25519), `OpenCodeAdapter` mínimo, **episodio estructurado desde el
primer commit**, memoria mínima, canal WireGuard/Tailscale + TLS.
**Los TRES ganchos organizacionales (baratos, obligatorios):**
1. `instance_id` en **toda** fila (memoria, hechos, tareas, episodios). En uso
   personal siempre el mismo valor; evita migración dolorosa después.
2. **Identidad parametrizada:** `persona.py` carga la identidad *de la instancia*
   al arrancar, no una constante.
3. **`Origin` con `actor_id`:** el origen de una acción puede identificar *quién*,
   no solo el nivel de confianza.
**NO construir aún:** multi-tenancy real, jerarquía, roles, coordinación, común.
**DoD:** lanzas el timer → el PC arranca, hace check-in, el cerebro reconcilia,
corre `sync_repo` (gate de allowlist) y reporta; el episodio explica *por qué*;
matas el VPS a media tarea y al revivir continúa; cada fila tiene `instance_id`.
`pytest` verde con replay offline.

### Fase 1. Memoria ("si llego aquí, valió la pena") · ~2–3 sem
**Construye:** `memory/store.py` (grande append-only + chica), `memory/facts.py`
(dato/directiva, procedencia/recencia, **aprobación pasando por ti**, pinned
híbrido), las **3 capas de subir** con presupuesto, `memory/consolidate.py`
**sin** compresión semántica (solo LRU), apuntadores entre agentes
(vivo→archivado), backup incremental al PC + prueba de restauración.
**DoD:** recupera algo de hace días por tag (explicable); la chica se llena →
consolida sin desbordar; matas la chica → se regenera de la grande; restauras un
backup y funciona.

### Fase 2. Economía y router · ~1–2 sem
**Construye:** `gateway.py` (caché+presupuesto+ledger+kill-switch), `router.py`
(dos ejes; provider por costo+sensibilidad; harness), latido por `ModelProvider`
(opencode Zen), watchlist filtra antes de invocar, `$think`/`$justdo`, **ccusage**,
límites desde el dashboard, **+ compresión semántica de memoria** (LLM barato).
**DoD:** un día de latidos cuesta centavos; rutina en el modelo barato; ledger
muestra el split; cruzar el tope detiene.

### Fase 3. El árbitro: verificación + aprobación · ~2 sem
**Construye:** `agents/verify.py`, **cola de aprobaciones**, `security/trust.py`
(niveles + `Origin` + **HITL por flujo**: umbral por acción según riesgo),
perilla de autonomía que baja con aprobaciones repetidas.
**DoD:** una tarea origen `mundo`/acción sensible espera tu OK; una `humano`
repetida se vuelve automática; mover-dinero exige aprobación aunque borrador no;
el verificador nunca llama a un LLM.

### Fase 4. Manos: MCP + los tres harnesses · ~2–3 sem
**Construye:** cliente **MCP** (carga `mcp.json`, tools namespaced),
`ClaudeCodeAdapter` + `CodexAdapter`, segunda tarea tipada + su integración MCP,
reconciliación multi-equipo madura.
**DoD:** una tool MCP invocable; el cerebro delega código a cualquiera de los tres
y supervisa PID/costo en vivo; añadir una integración es enchufar, no programar.

### Fase 5. Auto-mejora 2b con cuarentena · ~3–4 sem
**Construye:** `security/quarantine.py` (sandbox; evaluar Managed Agent Harness),
las 4 compuertas, `agents/specialists/` (skills; **primero optimización**),
herramientas auto-creadas como **repos/PRs GitHub**, sala de cuarentena.
**DoD:** el cerebro se escribe una herramienta, pasa compuertas (o se rechaza con
motivo), gradúa como PR; una que falla se retira sola; rollback con git; las
graduadas son *medibles-mejores*, no solo "pasan tests".

### Fase 6. Madurez: interfaces ricas, voz, casa · continuo
**Construye:** dashboard completo (fleet/reportes/proyecciones), CLI power
features, Telegram pulido, **voz** reutilizando el esqueleto existente,
integraciones de **casa** (MCP), capa 4 de memoria (si se ganó), **migración del
latido a modelo local** (nueva `ModelProvider`; la pseudonimización se relaja).

### Fase 7. Capa organizacional (JARVIS de empresa) · proyecto aparte
**Solo después de un cerebro probado solo.** Es la capa "30% nueva y difícil".
**Construye:** `org/`: un **cerebro general** (orquestador: coordina y rutea, NO
ejecuta el trabajo de los departamentos) + **cerebros de departamento**
(instancias con su identidad/memoria/permisos, simulando roles de gerencia);
**mensajes tipados entre instancias** (`messaging.py`: NO prosa libre; previene
colusión/contradicción, es la cola de tareas entre cerebros); **común de
solo-lectura** (`commons.py`: políticas, organigrama, datos compartidos; el
general es único curador; memoria privada por departamento + común leído por
todos, sin copias que diverjan); **roles/RBAC** (qué rol puede hacer qué sobre qué
datos; `actor_id` ya sembrado); **aprobación multi-persona** (separar quién
propone de quién aprueba para acciones de alto riesgo); el **audit plane** legal
(el episodio, ya existe).
**Reglas de oro de Fase 7:** el general coordina, no ejecuta (evita cuello de
botella y baja costo: la mayoría de decisiones se quedan en el departamento);
comunicación por mensajes tipados; una sola fuente de verdad transversal (común de
solo-lectura), no memorias que se sincronizan. **Empieza con DOS cerebros (un
general + un departamento), nunca con seis**: dos es el caso mínimo donde
aparecen todos los problemas de jerarquía.
**No adoptar un framework** (LangGraph/CrewAI/AutoGen) como cimiento; referencia
de patrones sí.

**Secuencia:** 0 → 1 → 2 → 3 → 4 → 5 → 6 → (7, proyecto aparte). Seguridad antes
que poder; la capa organizacional al final, sobre un núcleo probado.

---

## 4. Pruebas y reescritura continua

- **Núcleo determinista + replay offline.** `step()` lo más puro posible (estado +
  disparador → decisión), probado con fixtures grabados (como el adaptador de
  harness, probado offline con `stream-json` grabado). Sin LLM en el test.
- Las partes con LLM se aíslan tras interfaces y se mockean con respuestas grabadas.
- Cada fase: `pytest` verde + `ruff` limpio. No se cierra una fase con tests rojos.
- **Smoke run en vivo** antes de confiar una pieza (sobre todo los `[SUPUESTO]`).

**Gate de revisión entre fases (obligatorio):** ¿DoD cumplida de verdad? · ¿qué
supuesto contradijo la realidad? → reescribir aquí · ¿qué hueco nuevo apareció? →
backlog (§5) · ¿la fase siguiente sigue siendo la correcta?

**Regla de reescritura:** si una pieza se reescribe **tres veces por el mismo
motivo**, el problema es el diseño → pausar y rediseñar.

**Antídoto contra planear de más:** este documento basta para empezar la Fase 0.
Construir y dejar que lo que rompa reescriba el plan. Volver aquí *después* de
cada fase, no antes.

---

## 5. Evaluación honesta y backlog

**Fortalezas:** el modelo de seguridad (cuarentena + allowlist + confianza por
origen + firma) es lo más fuerte y poco común; habilita el 2b sin temeridad;
identidad desacoplada del modelo (skills, no fine-tuning); "hace y duerme" +
SQLite WAL resuelve ejecución/persistencia/observabilidad/concurrencia con piezas
simples; la arquitectura jerárquica converge al patrón empresarial probado y ya
trae el "governance" (audit plane, gateway, memoria con permisos, HITL) que la
industria cobra caro; el `instance_id` sembrado evita la cirugía multi-tenant.

**Riesgos vivos (por amenaza):**
1. **Tamaño.** Núcleo de meses-años para una persona; modo de fallo = "nunca
   sale". Mitigación: cada fase sirve sola; Fase 1 ya es "valió la pena". La Fase 7
   es proyecto aparte, no bloquea el núcleo.
2. **Calidad de memoria** (no su mecánica): la compresión semántica buena es el
   riesgo técnico central de Fase 2.
3. **Utilidad del 2b:** las compuertas garantizan seguro/correcto; "mejor" medible
   intenta garantizar valor (duro en dominios difusos). Empezar por optimización
   lo mitiga.
4. **Valor back-loaded:** lo útil temprano es casi todo determinista; la magia del
   criterio llega tarde.
5. **Jerarquía (Fase 7) puede degenerar:** cuello de botella en el general,
   contradicción/colusión entre cerebros, costo multiplicado. Mitigado por las
   reglas de oro (coordina-no-ejecuta, mensajes tipados, común de solo-lectura,
   empezar con dos).

**Backlog (gancho listo, decisión pospuesta):**
- Memoria entre agentes: reparto exacto común vs. propio por especialista.
- Esquema de `desired_state` más allá de repos.
- Gestión concreta de secretos en el VPS (mínimo privilegio).
- Cumplimiento de ToS/rate-limits del proveedor del latido para carga automatizada.
- Activación plena de pseudonimización (la resuelve migrar a local).
- Capa 4 de memoria (embeddings): posponer hasta demostrar que hace falta.
- Recuperación ante desastre: reconstruir el cerebro desde la grande (probarlo).
- Métricas de "mejor" por dominio para la compuerta 3.
- **Fase 7:** protocolo concreto de mensajes entre cerebros; matriz RBAC por rol;
  reglas de escalamiento (quién escala a quién); aprobación multi-persona.

**Recomendación:** congelar el diseño aquí. La arquitectura es sólida y está
validada contra el patrón empresarial estándar. El riesgo dominante de ahora en
adelante es el análisis, no la ingeniería. Construir la Fase 0 (con observabilidad
y los tres ganchos organizacionales desde el primer commit) y dejar que lo que
rompa reescriba este documento. La pendiente da fruto en cada escalón; medir el
éxito por la pendiente, no por el destino.

---

## Apéndice A. Esquema SQLite (boceto)

```sql
PRAGMA journal_mode=WAL;   -- lecturas libres, un escritor

-- LA GRANDE (append-only; nunca UPDATE/DELETE)
CREATE TABLE episodes (
  id TEXT PRIMARY KEY, instance_id TEXT, creado_en TEXT,
  despertar_por TEXT, decision TEXT, accion TEXT, resultado TEXT,
  dominio TEXT, costo_usd REAL, memoria_cargada TEXT, raw TEXT
);
CREATE TABLE facts (
  id TEXT PRIMARY KEY, instance_id TEXT, tipo TEXT,   -- dato | directiva
  contenido TEXT, pinned INTEGER, tags TEXT,
  procedencia TEXT, confianza REAL, creado_en TEXT, actualizado_en TEXT
);
CREATE TABLE tasks (
  id TEXT PRIMARY KEY, instance_id TEXT, accion TEXT, objetivo TEXT, params TEXT,
  origen_trust INTEGER, origen_actor TEXT,            -- nivel + QUIÉN
  estado TEXT, firma TEXT, creado_en TEXT
);
CREATE TABLE jobs (
  id TEXT PRIMARY KEY, instance_id TEXT, pid INTEGER, harness TEXT,
  estado TEXT, costo_usd REAL, creado_en TEXT, terminado_en TEXT
);
CREATE TABLE desired_state (
  instance_id TEXT, machine_id TEXT, clave TEXT, valor TEXT, actualizado_en TEXT,
  PRIMARY KEY (instance_id, machine_id, clave)
);
CREATE TABLE tools (
  id TEXT PRIMARY KEY, instance_id TEXT, nombre TEXT, dominio TEXT, repo_url TEXT,
  estado TEXT, metrica_baseline REAL, metrica_actual REAL,
  usos INTEGER, fallos INTEGER, creado_en TEXT
);
CREATE TABLE approvals (
  id TEXT PRIMARY KEY, instance_id TEXT, tipo TEXT, payload TEXT,
  estado TEXT, creado_en TEXT, resuelto_en TEXT
);
-- Artefactos pesados: archivo en disco + puntero (ruta) en `raw`.
-- Toda consulta filtra por instance_id (aislamiento desde el día uno).
```

## Apéndice B. Ejemplos concretos

**Hechos semilla:**
```json
[
 {"tipo":"dato","contenido":"le gusta el chocolate con menta","pinned":false,"tags":["gustos"]},
 {"tipo":"dato","contenido":"prefiere herramientas de código libre","pinned":true,"tags":["tooling"]},
 {"tipo":"directiva","contenido":"responder conciso por defecto","pinned":true,"tags":["estilo"]},
 {"tipo":"directiva","contenido":"optimizar; muy sensible a cualquier delay","pinned":true,"tags":["rendimiento"]}
]
```

**Tarea firmada:**
```json
{"id":"t_001","instance_id":"inst_personal","accion":"sync_repo","objetivo":"proyecto_X",
 "params":{"branch":"main"},"origen_trust":1,"origen_actor":"duenio","estado":"pendiente","firma":"ed25519:…"}
```

**Check-in:**
```
PC -> POST /checkin  {"machine_id":"pc-casa","capabilities":["sync_repo"],"current_state":{"proyecto_X":"abc123"}}
←     {"tasks":[{"id":"t_001","accion":"sync_repo","objetivo":"proyecto_X","params":{"branch":"main"},"firma":"…"}]}
PC -> allowlist.allow(t_001)==True -> git pull -> POST /report {"task_id":"t_001","outcome":"hecha","detail":"ff a def456"}
```
