<p align="center">
  <img src="assets/lhassa-mark.svg" alt="Lhassa; la estupa dorada" width="88">
</p>

<h1 align="center">Lhassa</h1>

<p align="center"><b>Construye el cerebro, alquila el cuerpo.</b></p>

<p align="center">Un proyecto de <a href="https://witara.site">Witara</a>; software calmado, consejo no control.</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="doc/architecture.md">Arquitectura</a> ·
  <a href="doc/cli.md">Referencia CLI</a> ·
  <a href="LICENSE">AGPL-3.0</a>
</p>

---

Lhassa es un **cerebro** personal y persistente; una identidad que vive
despierta todo el día a $0, orquesta modelos y herramientas alquiladas, se gana
su autonomía con confianza verificada, y crece de un cerebro a una organización.

Lleva el nombre de Lhasa, la ciudad en el techo del mundo: un lugar al que los
peregrinos llegan despacio, a propósito. Lo durable aquí es *quién es Lhassa*;
su memoria, su juicio, su historial de confianza. Los modelos, harnesses y
herramientas son músculo intercambiable, alquilado por horas y reemplazable sin
cirugía. Esa es toda la tesis: **construye el cerebro, alquila el cuerpo.**

> Esto no es un chatbot con cron jobs. Es un organismo **hace-y-duerme**: el
> estado vive en disco, un heartbeat lo avanza un tick determinista cada dos
> minutos, y pensar (una llamada real a un modelo) es la excepción, no la
> regla.

## Qué es esto

Lhassa es el **cerebro soberano** propio del autor: corre en su máquina, lo
recuerda entre cada sesión, no cuesta nada mantener despierto, y responde a
un registro auditable que el autor controla. Una apuesta radical guía todo el
diseño: **pensar es la excepción.** Un plano determinista de $0 resuelve casi
todo; solo se toca un modelo cuando el juicio de verdad no se puede evitar.
Todo lo demás; la soberanía, la memoria, las herramientas que se forja solo,
el org de cerebros; sale de ahí.

El código es público (este repo, AGPL-3.0) por dos razones: es la
implementación de referencia del diseño de arriba, y es la fuente de
evidencia de un proyecto hermano de [Witara](https://witara.site) que
necesita un sistema real con historial de uso real para poder medirse contra
algo. No se ofrece aquí como producto hospedado ni como servicio para otras
personas; si corres tu propia instancia, obtienes exactamente lo mismo que
tiene el autor, nada alquilado, nada gestionado.

A propósito, **no** es varias cosas:

- **no es un chatbot hospedado**: vive en disco, no en la nube de otro;
- **no es magia lista-para-usar**: necesita su propio cerebro (una llave de
  API, o un modelo 100% local; nada se alquila desde este proyecto, porque no
  hay un servicio detrás);
- **no es un framework para construir encima**: es un cerebro completo y
  opinionado, no un kit de piezas.

Si "soberano, ambiente y $0 por defecto" es un trato que te interesa, el
código está aquí para leerlo, correrlo o bifurcarlo. Si quieres un asistente
hospedado que solo abres y usas, esto no es eso, y está bien.

## Por qué Lhassa

| | |
|---|---|
| **Un día rutinario cuesta $0** | El heartbeat late ~720 veces al día; un plano determinista (watchlist, reflejos, acciones, monitor, digest, routing del org) responde casi todo sin tocar un modelo. Del ledger real del autor. 4 de julio de 2026: ~720 despertares, **5** thinks medidos, **$0.0031**. |
| **Suscripciones antes que APIs** | Cuando sí piensa, recorre tus suscripciones CLI ya pagadas (`claude` / `codex` / `opencode`) antes de gastar un centavo de API medida. Un router de presupuesto, un freno suave al 80% y un kill-switch cuidan lo que quede. |
| **Se forja sus propias manos** | La repetición se detecta con SQL a $0 sobre su log de episodios; el cerebro escribe la herramienta (y su test) que reemplaza el gasto repetido. El código auto-escrito corre **solo en cuarentena** hasta pasar las cuatro puertas y que tú apruebes su graduación. Las herramientas que dejan de ganarse su lugar se retiran. |
| **Soberano por construcción** | Allowlist de acciones deny-by-default, tareas firmadas con Ed25519, confianza que solo degrada, aprobaciones human-in-the-loop con una perilla de autonomía que se *gana*, y un log de episodios append-only: cada decisión que tomó, auditable. |
| **Sin lock-in, nunca** | Dos ejes de intercambio; un `ModelProvider` para pensar, un `HarnessAdapter` para actuar; detrás de registries; cada capacidad entra como plugin sin editar el core. Cambia el modelo, conserva el cerebro. |
| **De un cerebro a una org** | El mismo core escala a una jerarquía de cerebros: departamentos, cadenas de mando, un rol monitor que escucha siempre a $0, routing determinista y rastro de auditoría completo en cada delegación. |
| **Recuerda, por capas** | Hechos tipados con manejo de contradicciones, un caché de trabajo, recuperación semántica y recall FTS5 sobre sus propios episodios; pregúntale algo y su respuesta llega ya informada por su experiencia. |

## Un día en su vida

Cómo se ve "ambiente a $0" en la práctica, tick a tick:

```
07:58  heartbeat; watchlist en silencio, nada pendiente. $0.
08:00  el check-in matutino llega a Telegram, en la voz de Lhassa,
       construido determinísticamente del estado ya en disco. $0.
10:14  le preguntas algo; el recall FTS5 trae dos episodios pasados al
       contexto antes del think; una llamada al CLI de suscripción, $0 medido.
11:03  un repo vigilado recibe un commit → el reflejo repo_changed lo
       sincroniza. Sin juicio. $0.
13:40  el monitor (su oído siempre encendido) ve un episodio de fallo
       nuevo y lo nombra en una alerta de Telegram; qué falló, no
       cuántos llevas. $0.
14:03  mantenimiento diario: la memoria se destila, se escanea la
       repetición, las skills sin uso se curan (nunca se borran;
       se archivan, con respaldo). $0.
19:04  el detector de repetición propone: "Hacer 'responder' automático;
       repetido ×41, $0.26 gastados en eso." Tu decisión, un tap.
21:00  el check-in de la noche. La rueda giró todo el día; el ledger
       apenas se movió.
```

## Se forja sus propias manos

El código auto-escrito se gana la confianza como un peregrino gana altitud;
una puerta a la vez, sin atajos:

1. **detectado**: SQL a $0 encuentra un patrón que sigues pagando;
2. **escrito**: el modelo rutinario redacta una herramienta candidata *y su test*;
3. **en cuarentena**: corre solo dentro del sandbox más fuerte disponible
   (`unshare`/`firejail`/`bwrap`), sin red, con el entorno limpio;
4. **con puertas**: segura → correcta → mejor: verificadores objetivos, nunca
   un LLM juzgando a un LLM;
5. **graduado**: un humano aprueba; la API rechaza (409) lo que saltó las puertas;
6. **retirado**: las herramientas que dejan de rendir se van por el mismo
   camino auditado.

La misma disciplina alcanza a la orquestación: con PTC (Programmatic Tool
Calling), un solo think puede escribir un script Python que dirige muchas
acciones deterministas por un socket RPC; dentro de la misma cuarentena, cada
llamada re-verificada contra el mismo allowlist.

---

## Inicio rápido

Hablando con Lhassa en unos dos minutos, sin systemd, sin Docker:

```bash
git clone https://github.com/Sadriica/Lhassa.git
cd Lhassa
make quickstart
```

`make quickstart` resuelve el venv (`uv sync`, sin compilar el dashboard todavía),
se asegura de que exista un `.env`, y revisa si puede encontrar un cerebro con
el que pensar. **Trae el tuyo**: ya sea una API key gratis o barata (**[el
free tier de Groq](https://console.groq.com)** es el arranque más fácil;
Anthropic/OpenAI/OpenRouter/Cerebras también funcionan, con una sola key
alcanza) puesta en `.env`, o vas **totalmente local y a $0** con
**[Ollama](https://ollama.com)**: sin ninguna key. Vuelve a correr
`make quickstart` en cuanto agregues una; te dirá qué sigue:
`uv run lhassa onboard` (un cuestionario corto que siembra sus primeros datos
sobre ti) y luego `uv run lhassa`: ya estás hablando con él.

Primeras cosas para probar:

- simplemente háblale, en lenguaje llano, sin sintaxis especial
- `lhassa recall <texto>`: busca en sus propios episodios pasados (FTS5)
- `lhassa memory <consulta>`: busca en sus datos durables
- `lhassa capabilities`: qué está conectado, qué está encendido/apagado

¿Lo quieres **siempre encendido**: un heartbeat que sigue latiendo mientras
duermes, Telegram, systemd + Docker? Esa es la siguiente sección
(`bash scripts/install.sh`), o el recorrido completo en
[`doc/install.md`](doc/install.md).

---

## Instalación completa (siempre encendido)

### Requisitos

Dependencias de sistema (instálalas primero):

- **[`uv`](https://docs.astral.sh/uv/)**: el toolchain de Python (`uv sync` resuelve el venv). Requerido.
- **`node` + `npm`**: para compilar el dashboard React/Vite (`npm ci && npm run build`). Requerido para el dashboard.
- **`docker`**: solo para el camino containerizado / el smoke test del instalador (omite con `--no-docker`).
- **`tectonic`** *(opcional)*; el motor LaTeX que compila el CV `.tex` a PDF. Sin él, el puente de CV envía el `.tex` crudo.
- **`ccusage`** *(opcional)*; CLI de gasto LLM multi-harness para los paneles de costo. `npm install -g --prefix ~/.local ccusage`.

Los CLIs de suscripción (`claude` / `codex` / `opencode`) son opcionales y **no necesitan API key**: instala el que uses.

### Instalación de un comando (Linux siempre encendido)

```bash
cd ~/Code/lhassa
bash scripts/install.sh
```

Verifica `python3`/`uv`/`docker`, corre `uv sync`, compila el dashboard,
construye y prueba la imagen Docker, instala las units **user** de systemd (app +
heartbeat + timer de salud + el monitor `$0` siempre encendido, más el poller de
Telegram si hay token) y habilita el lingering. El dashboard queda en
**http://127.0.0.1:7766**. Detalles y flags: [`doc/install.md`](doc/install.md).

### Bootstrap del pipeline de CV

En una máquina fresca, siembra el work-flow del "CV imposible de rechazar":

```bash
lhassa seed list       # lista los seeds registrados
lhassa seed apply cv       # recrea el departamento interns + el work-flow del CV
lhassa seed apply personal # crea el departamento personal + flows mañana/noche
```

Después deja tu CV real en `~/.lhassa/cv/cv_largo.tex` (o define `LHASSA_CV_DIR`).

### Camino de desarrollo

```bash
make install   # uv sync + compilar el dashboard (primera vez)
make run       # voz + API + scheduler (uv run python main.py)
make dev       # igual, con LHASSA_DEBUG=1 LHASSA_LOG_LEVEL=DEBUG
make test      # uv run --extra dev python -m pytest
make lint      # ruff
make check     # valida el entorno, sin llamadas a API
```

### Docker

```bash
cp .env.example .env          # llena las keys que uses
docker compose up -d          # roles api + heartbeat + monitor
docker compose logs -f lhassa
```

Una imagen, `LHASSA_RUN` elige el rol (`api` / `heartbeat` / `telegram` /
`monitor`). Todo el estado vive bajo `/data` (`HOME=/data`). Lhassa corre hoy en
una laptop y está construida para correr sin cambios en un VPS pequeño: los
detalles y la auditoría de portabilidad viven en
[`doc/deployment.md`](doc/deployment.md) y [`doc/portability.md`](doc/portability.md).

---

## Superficies

**CLI (`lhassa`)**: chat con el loop de herramientas (tema Lhasa: dorado / rojo /
blanco / verde), `lhassa code "<tarea>"` para tareas agénticas vía CLI local en
suscripción, y subcomandos offline contra el cerebro local (`status`, `watch`,
`report`, `memory`, `tools`, `approvals`, `org`, `skills`, `config`…). Referencia
completa: [`doc/cli.md`](doc/cli.md).

**Bot de Telegram**: comandos LLM-free (`/status`, `/approvals` con cada decisión
descrita en palabras (qué es, cuánto lleva, qué hace aprobarla), `/recall
<texto>` para buscar en sus episodios pasados, `/watch`, `/goal`, `/today`,
`/tomorrow`, `/checkin <texto>`); los mensajes libres los responde un think sin
herramientas; la puerta de chat puede fijar su propio proveedor rápido
(`telegram_provider`) mientras el trabajo pesado sigue en el asiento del heartbeat,
sobre la cadena de fallback (`opencode → claude → codex → cloud`). El digest de
las 8:00/21:00 llega en primera persona (plantilla determinista a $0; re-voz
opcional con `digest_style=persona`); con `LHASSA_DIGEST_PERSONAL_CHECKIN=1`
también pregunta por tu día y guarda tu siguiente respuesta como check-in diario.

**Dashboard**: React/Vite en **:7766** con la identidad Lhasa: Overview, Brains
(editor de jerarquía + overlay de relaciones), Skills, Run (tareas agénticas en
vivo), Flows (editor estilo n8n), Settings, Reports, Fleet y Activity (con
búsqueda de texto completo sobre el log de episodios). La misma app FastAPI
expone la API REST (`/api/org/*`, `/api/flows`, `/api/episodes/search`,
`/api/code`, …).

**Voz (opt-in)**: wake-word → Deepgram → cerebro → Cartesia (pipecat). Apagada
por defecto; los servicios siempre-encendidos corren solo-API (sin audio).

---

## Configuración

Todo se maneja por entorno; un **`.env`** gitignoreado (copia de `.env.example`)
guarda los secretos. Un segundo archivo, **`~/.lhassa/settings.env`**, guarda los
overrides de runtime escritos por `lhassa config set` / el panel Settings (se
carga *después* de `.env`, nunca guarda secretos). La tabla completa de knobs
`LHASSA_*` está en el [README en inglés](README.md#configuration) y en
[`.env.example`](.env.example); los más importantes: el proveedor de chat
(`LHASSA_LLM_PROVIDER`), el asiento del heartbeat (`LHASSA_HEARTBEAT_PROVIDER`),
el asiento del chat de Telegram (`LHASSA_TELEGRAM_PROVIDER`), presupuesto y
kill-switch (`LHASSA_LLM_DAILY_TOKEN_BUDGET`, `LHASSA_KILL_SWITCH`), el digest
(`LHASSA_DIGEST_STYLE`, `LHASSA_DIGEST_LANGUAGE`,
`LHASSA_DIGEST_PERSONAL_CHECKIN`) y el curator de skills
(`LHASSA_SKILL_STALE_DAYS`, `LHASSA_SKILL_ARCHIVE_DAYS`). **Con una sola API key
basta** (se auto-detecta), y los CLIs `claude` / `codex` / `opencode` no
necesitan ninguna; corren con tu suscripción.

---

## Arquitectura

El estado del cerebro vive en `~/.lhassa/`. El código está en capas; infra →
espina → interfaces → adaptadores → shells; y un test guardián de arquitectura
hace fallar cualquier import que cruce capas. El mapa completo:
[`doc/architecture.md`](doc/architecture.md); la especificación:
[`doc/lhassa-brief.md`](doc/lhassa-brief.md); el log de decisiones:
[`doc/decisions.md`](doc/decisions.md); el libro de voz narrativo:
[`doc/narrative.md`](doc/narrative.md).

---

## Estado y honestidad

Lhassa es un proyecto joven con un primer usuario exigente: su autor. Todo lo
descrito arriba corre hoy y está cubierto por ~1,400 tests; lo que aún no corre
no está en esta página. Vive en una laptop y está construida para mudarse a un
VPS pequeño sin cirugía.

## Licencia

**AGPL-3.0-or-later**, con un término adicional bajo la sección 7(b); ver
[LICENSE](LICENSE).

**Qué significa si te lo auto-hospedás.** Si corrés Lhassa en tu propia máquina,
para vos o dentro de tu empresa, no debés nada: modificalo, guardate tus cambios
y usalo como quieras. La obligación extra de la AGPL es estrecha; aplica **solo
si le ofrecés Lhassa a otras personas por red, como servicio**. En ese caso, esos
usuarios deben poder obtener el código de la versión que estás corriendo,
incluidas tus modificaciones.

El término adicional 7(b) te pide conservar visible el aviso de atribución
("Lhassa; a Witara project", la licencia y un enlace al código) allí donde el
software ya muestra información legal o de versión: la CLI, la API y el
dashboard. Nada más; no otorga derechos sobre los nombres ni te quita ninguna
libertad que la AGPL te da.

**Licenciamiento comercial.** Si la AGPL no encaja en tu caso, es posible una
licencia comercial aparte; las contribuciones se recogen bajo un CLA (ver
[CONTRIBUTING.md](CONTRIBUTING.md)) precisamente para mantener esa puerta
abierta.

<p align="center"><i>La rueda gira todo el día; el ledger apenas se mueve.</i></p>
