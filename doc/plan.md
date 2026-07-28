# Plan vigente — kilometraje (2026-07-21)

Tesis del corte de caja: no falta arquitectura, falta kilometraje. Este plan hace
que los loops existentes VIVAN antes de construir loops nuevos. Orden: Ola 0 → 1
→ 2 → 3 (0.1 es prerequisito de 1.3; la Ola 2 necesita días de tráfico tras la
Ola 1). Nada nuevo (modalidades/departamentos/comandos) hasta acumular millas.

## Ola 0 — encender lo apagado ✅ (0.1 pendiente del owner)
- **0.1 Activar percepción** (owner): `knowledge_roots` + `protected_roots` vía
  `/config`. DoD: `ingest_knowledge` indexa >0 archivos; una lectura protegida
  completa tap→resultado.
- **0.2 Diagnóstico del answer cache**: instrumentar el near-miss (mejor
  similitud en cada miss, visible en el episodio). DoD: en 2–3 días sabemos si
  el problema es umbral, tráfico o el HashingEmbedder.
- **0.3 Telemetría de kilometraje**: sección en el digest — cache hit-rate,
  rutas $0 vs thinks, patrones detectados. DoD: el digest responde "¿está
  aprendiendo?" sin preguntar.

## Ola 1 — memoria que pega ✅
- **1.1 Embeddings reales**: `RemoteEmbedder` → Ollama local
  (`nomic-embed-text`, $0, VPS-portable) + reindex de L4/cache/knowledge. DoD:
  una pregunta parafraseada pega en cache; L4 recupera por significado.
- **1.2 Continuidad de hilo** — ✅ YA EXISTÍA (commit 12c3e99):
  `capabilities.is_followup` + `continue_followup` por capacidad + `_CHAT_TOPIC`
  TTL'd, integrado en el poll con episodios.
- **1.3 Knowledge en el contexto** — ✅ YA EXISTÍA (commit 974d028): capa 6 en
  `memory/load.py`, prioridad mínima, cita el archivo fuente.

## Ola 2 — kilometraje para la consciencia ✅ (2026-07-21)
- **2.1 Sensibilidad a bajo volumen**: umbrales del detector con datos jóvenes;
  `detect_routable` sobre los turnos reales. DoD: primera proposal real en
  Telegram con ahorro estimado.
- **2.2 Feedback → autonomía**: correcciones repetidas bajan streak, silencio
  positivo sube (nunca high-risk/protegido). DoD: el knob se mueve solo.
- **2.3 Simulacro del forjado**: drill end-to-end author → cuarentena → gates →
  approve → producción. DoD: primera fila en `tools` no escrita a mano.

## Ola 3 — jerarquía sin deuda ✅ (2026-07-21; 3.2 fue piloto one-shot, el flag sigue OFF por defecto)
- **3.1 Matar surface-direct sends**: `_run_action_and_report` + CV
  `send_document` pasan por `org/deliver.py`. DoD: cero sends directos.
- **3.2 Piloto `LHASSA_ORG_LIVE`**: un departamento con executor real,
  presupuesto acotado. DoD: delegación viva con costo visible por hop.


## Plan 2 — seis frentes (2026-07-28) ✅ EJECUTADO
Projects (entidad + detector-propone) · standing brains por kind (read-only
auto, escritor con tap) · plataforma (API/dashboard/evidencia
prepared-observed-verified) · knowledge graph LightRAG-lite (kg_sync + retrieval
fusionado, flag OFF) · `lhassa tui` (Textual) · TOON (construido y archivado).
Detalle en doc/decisions.md 2026-07-28. Pendiente natural: encender
LHASSA_MEMORY_GRAPH tras unos kg_sync semanales; pantalla TUI de mensajes/org;
proyectos en el digest.
