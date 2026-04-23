# Arquitectura — Asistente Smart-IA (Agent-EXE)

## Índice
1. [Visión general](#1-visión-general)
2. [Estructura de carpetas](#2-estructura-de-carpetas)
3. [Flujo de una consulta](#3-flujo-de-una-consulta)
4. [Componentes en detalle](#4-componentes-en-detalle)
5. [Sistema de entrenamiento](#5-sistema-de-entrenamiento)
6. [Bases de datos](#6-bases-de-datos)
7. [Variables de entorno](#7-variables-de-entorno)
8. [Decisiones de diseño](#8-decisiones-de-diseño)

---

## 1. Visión general

Chatbot de ventas para franquiciados con franquicia fija (configurada en `.env`). El backend es una API FastAPI con sistema multi-agente donde cada agente tiene una responsabilidad específica. Incluye un ciclo de entrenamiento supervisado basado en feedback de usuarios.

```
Usuario (UI Web) ──POST /chat──► Orchestrator ──► Agente correcto ──► Respuesta
                                                                          │
                               TrainingMemory ◄── Training Agent ◄── Feedback
```

---

## 2. Estructura de carpetas

```
Agent-EXE/
├── app/
│   ├── main.py                    # Punto de entrada FastAPI
│   ├── config.py                  # Variables de entorno + franchise_code fijo
│   ├── logger.py                  # Logger por sesión → logs/<session_id>.log
│   ├── agents/
│   │   ├── orchestrator.py        # Clasifica mensajes en tipos de agente
│   │   ├── comparative_agent.py   # Comparativas entre dos períodos de ventas
│   │   ├── data_agent.py          # Text-to-SQL sobre datos de ventas
│   │   ├── interaction.py         # Conversación general del negocio
│   │   ├── memory_agent.py        # Resúmenes y contexto de sesión
│   │   └── training_agent.py      # Analiza feedback, escribe training_log.md
│   ├── db/
│   │   ├── connection.py          # Conexión Azure AD a Fabric (pyodbc)
│   │   ├── sales_repo.py          # Ejecuta sp_GetSalesForChatbot
│   │   ├── memory_repo.py         # CRUD sesiones/mensajes/token-logs (SQLite)
│   │   └── training_repo.py       # TrainingMemory singleton (RAM + disco)
│   ├── models/
│   │   └── schemas.py             # Pydantic: ChatRequest, FeedbackRequest, etc.
│   └── routers/
│       ├── chat.py                # POST /chat/, POST /chat/feedback/, sesiones
│       └── debug.py               # /debug/query/csv, /debug/query/json, token-logs
├── context/
│   ├── business_rules.md          # Reglas de negocio leídas en runtime
│   └── training_log.md            # Log append-only de sugerencias de mejora
├── sql/
│   └── sp_GetSalesForChatbot.sql
├── ui_test/
│   ├── index.html                 # UI (Tailwind CSS) con feedback 👍👎
│   └── Nacho.svg
├── logs/                          # Logs por sesión (auto-generado)
├── memory.db                      # SQLite local (auto-generado)
├── .env / .env.example
└── requirements.txt
```

---

## 3. Flujo de una consulta

```
1. Usuario escribe en index.html → POST /chat  {message, session_id, training_mode}
        │
        ▼
2. chat.py::chat()
   ├─ Recupera memoria de sesión (memory_agent.retrieve_memory)
   └─ Si training_mode=True: inyecta get_context() de TrainingMemory en el contexto
        │
        ▼
3. OrchestratorAgent.decide_agent(mensaje, contexto)
   ├─ "comparative" → ComparativeAgent.process_comparative_request()
   ├─ "data"        → DataAgent.process_data_request()
   ├─ "interaction" → InteractionAgent.respond()
   ├─ "feedback"    → TrainingAgent.analyze_feedback() + respuesta fija
   ├─ "off_topic"   → Respuesta fija (0 tokens)
   └─ "memory"      → Devuelve resumen guardado
        │
        ▼
4. chat.py guarda mensaje en SQLite (memory_repo) + actualiza resumen (memory_agent)
        │
        ▼
5. Respuesta JSON → index.html → renderiza con markdown + botones 👍👎
        │
        ▼ (si el usuario presiona 👎 o 👍)
6. POST /chat/feedback/ → TrainingAgent.analyze_feedback()
   └─ Escribe entrada estructurada en context/training_log.md
   └─ Agrega parsed entry a TrainingMemory (RAM, máx 20 entradas)
```

### Flujo del Data Agent (detalle)

```
_extract_date_range()       → Python primero (hoy/ayer/esta semana...), LLM fallback
        │
sales_repo.get_sales()      → EXEC sp_GetSalesForChatbot con fecha y franchise_code
        │
_load_into_memory()         → Tabla SQLite en RAM (decodifica DATETIMEOFFSET 20 bytes)
        │
_generate_sql()             → LLM genera SQL SQLite (business_rules + contexto sesión)
        │
_execute_sql()              → Ejecuta contra la tabla en RAM
        │
_compute_summary()          → Métricas en Python: transacciones, totales, vendedores,
                              top productos, horas activas — sin LLM
        │
_format_response()          → LLM presenta los datos pre-calculados en español
```

---

## 4. Componentes en detalle

### `app/agents/orchestrator.py` — El portero

Usa Claude Sonnet para clasificar cada mensaje:

| Tipo | Cuándo | Costo |
|------|--------|-------|
| `comparative` | Compara dos períodos ("esta semana vs la semana pasada") | Medio (2 LLM calls) |
| `data` | Consultas de un período, productos, precios, reportes | Alto (hasta 3 LLM calls) |
| `interaction` | Saludos, preguntas sobre cómo usar el chatbot | Bajo (1 LLM call) |
| `feedback` | El usuario evalúa la respuesta anterior | Bajo (1 LLM call) |
| `off_topic` | Sin relación con ventas o el negocio | Cero (respuesta fija) |

Regla de prioridad: mensajes mixtos (negocio + off-topic) se clasifican siempre por la parte del negocio. `off_topic` es el último recurso.

Fallback por keywords si el LLM no devuelve JSON válido. Default: `off_topic`.

---

### `app/agents/data_agent.py` — El analista (Text-to-SQL)

Pipeline de 7 pasos: extracción de fecha → SP en Fabric → SQLite en RAM → generación SQL → ejecución → cálculo Python → formato LLM.

**Punto crítico — DATETIMEOFFSET:** pyodbc entrega fechas de Fabric como 20 bytes raw. Se decodifican con:
```python
struct.unpack('<hHHHHHIhh', v)
# → year, month, day, hour, minute, second, fraction_ns, tz_h, tz_m
```

**Punto crítico — contexto de sesión:** El resumen de sesión se inyecta en `_extract_date_range` y `_generate_sql`. Permite preguntas de seguimiento ("haz un desglose por items") sin repetir la fecha.

**Punto crítico — métricas pre-calculadas:** `_compute_summary` calcula todo en Python (sin LLM). El LLM de formato recibe los números definitivos y solo los presenta — nunca recalcula.

---

### `app/agents/comparative_agent.py` — El comparador

Maneja consultas con dos períodos. Hace un único llamado al SP con el rango completo (min_from → max_to) y filtra en SQLite para cada período. Reutiliza `data_agent._load_into_memory()` y `data_agent._compute_summary()` directamente.

**Costo:** 2 LLM calls + 1 SP call (vs. 6 LLM calls + 2 SP calls si se usaran dos data queries separadas).

---

### `app/agents/interaction.py` — El recepcionista

Haiku con `max_tokens=200`. Responde saludos y preguntas sobre el chatbot. Si el mensaje mezcla contenido de negocio con off-topic, responde solo la parte del negocio e ignora el resto.

---

### `app/agents/memory_agent.py` — La memoria

- `save_memory()`: Haiku genera un resumen de 2-3 puntos al final de cada intercambio y lo persiste en SQLite.
- `retrieve_memory()`: al inicio de cada request, carga el resumen como contexto para los agentes.

El resumen (compacto, pocos tokens) es distinto al historial completo mensaje-a-mensaje guardado en `chat_messages`.

---

### `app/agents/training_agent.py` — El entrenador

Analiza ciclos de feedback: `(user_message, bot_response, feedback, feedback_type)`. Usa Haiku para identificar la causa raíz y generar una sugerencia estructurada. Escribe en `context/training_log.md` via `TrainingMemory.add_suggestion()`.

Formato de salida del LLM:
```json
{
  "component": "data_agent | orchestrator | interaction | business_rules",
  "root_cause": "causa raíz en una oración",
  "suggestion": "sugerencia concreta en 1-2 oraciones",
  "priority": "alta | media | baja"
}
```

---

### `app/db/training_repo.py` — TrainingMemory

Singleton que gestiona el ciclo de vida del contexto de entrenamiento:

- **Al iniciar la app**: lee `training_log.md`, parsea las últimas 20 entradas y las carga en RAM.
- **En cada feedback**: agrega la nueva sugerencia a RAM (rotando si supera 20) y hace append en disco.
- **En cada request con `training_mode=True`**: `get_context()` devuelve un string con las sugerencias de prioridad alta y media (limitado a ~2000 chars), que se inyecta al comienzo del contexto de los agentes.

---

### `app/db/connection.py` — Conexión a Fabric

Tres modos via `DB_AUTH_MODE`:

| Valor | Comportamiento |
|---|---|
| `interactive` | Abre el browser para login Azure AD con MFA |
| `activedirectoryinteractive` | Alias de `interactive` |
| `activedirectoryintegrated` | Windows Auth integrado (sin popup) |
| `sql` | Usuario + contraseña directos |

El token Azure AD se reutiliza (singleton `_credential`) entre requests para no pedir MFA en cada llamada. `conn.add_output_converter(-155, lambda x: x)` desactiva la conversión automática de DATETIMEOFFSET para recibirlo como bytes raw.

---

### `app/config.py` — Configuración

Incluye `franchise_code` como campo fijo (leído de `.env`, default al código de la franquicia de prueba). En esta versión EXE no hay `franchise_id` dinámico ni en la API ni en la UI.

---

### `context/business_rules.md` — Reglas del negocio

Leído en runtime por DataAgent y ComparativeAgent en cada consulta. No requiere reiniciar el servidor para actualizarse.

Reglas clave:
- `Type=2` → cabecera de promoción, excluir de totales (`WHERE "Type" != '2'`)
- Búsqueda: `LOWER(ArticleDescription) LIKE LOWER('%texto%')`
- Columnas de canal: `CtaChannel`, `VtaOperation`, `Plataforma`, `FormaPago`
- No mostrar nombres técnicos de columnas al usuario final

---

### `sql/sp_GetSalesForChatbot.sql` — El Stored Procedure

Corre en Microsoft Fabric Warehouse. Usa 5 CTEs con `CROSS APPLY OPENJSON` para extraer datos de columnas JSON. Filtra por `h.FranchiseCode` (no `h.FranchiseeCode` — son columnas distintas).

El filtro de fecha usa el header (`h.DateTimeUtc`), no el detalle, para evitar perder tickets donde header y detalle caen en días distintos.

---

## 5. Sistema de entrenamiento

### Ciclo completo

```
Usuario hace consulta → Bot responde → Usuario presiona 👍 o 👎
                                              │
                                   POST /chat/feedback/
                                              │
                                   TrainingAgent.analyze_feedback()
                                              │
                              ┌───────────────┴──────────────────┐
                              │                                   │
                    context/training_log.md              TrainingMemory (RAM)
                    (append en disco)                    (hasta 20 entradas)
                              │                                   │
                    Revisión humana semanal          Inyección en prompts
                    → aplicar en código              cuando training_mode=True
```

### Formato de cada entrada en `training_log.md`

```markdown
## [YYYY-MM-DD HH:MM] Sesión: {session_id} | Tipo: {positivo|negativo}

**Chat analizado:**
- Usuario preguntó: "..."
- Agente respondió: "..."
- Feedback recibido: "..."

**Componente afectado:** {data_agent | orchestrator | interaction | business_rules}

**Causa raíz identificada:**
...

**Sugerencia de cambio:**
...

**Prioridad:** {alta|media|baja}
---
```

### Inyección de contexto

Cuando `training_mode=True` en el request, `chat.py` antepone al contexto de memoria:

```
=== CONTEXTO DE ENTRENAMIENTO ACTIVO ===
Sugerencias de mejora basadas en feedback previo de usuarios:
⚠️ CORRECCIÓN (data_agent): Agregar WHERE Type != '2' al calcular promedios
✅ PATRÓN EXITOSO (business_rules): Formato de hora como "entre las X y X+1 hs"
```

Solo se incluyen entradas de prioridad `alta` y `media`. Límite: ~2000 chars (~500 tokens).

### Cómo aplicar sugerencias manualmente

1. Revisar `context/training_log.md` — filtrar por `Prioridad: alta`
2. Según `Componente afectado`:
   - `business_rules` → editar `context/business_rules.md`
   - `data_agent` → ajustar prompt en `_generate_sql` o `_format_response`
   - `orchestrator` → refinar clasificaciones o agregar keywords
   - `interaction` → ajustar prompt del interaction agent
3. Marcar la entrada como `[APLICADO]` en el log

---

## 6. Bases de datos

| Base de datos | Tecnología | Dónde vive | Para qué |
|---|---|---|---|
| Warehouse | Microsoft Fabric | Cloud | Datos de ventas (fuente de verdad) |
| SQLite en memoria | sqlite3 | RAM del servidor | Tabla temporal por consulta |
| SQLite local | sqlite3 | `memory.db` | Sesiones, historial, token logs |
| training_log.md | Markdown | `context/` | Log append-only de sugerencias |

### Tablas en `memory.db`

**`chatbot_memory`** — un registro por sesión:
```
session_id | user_id | context | summary | created_at | updated_at
```

**`chat_messages`** — historial completo:
```
id | session_id | role | content | agent_type | created_at
```

**`query_logs`** — consumo de tokens por consulta:
```
id | session_id | user_message | agent_type | input_tokens | output_tokens | total_tokens | created_at
```

---

## 7. Variables de entorno

| Variable | Requerida | Descripción |
|---|---|---|
| `ANTHROPIC_API_KEY` | Sí | API key de Anthropic |
| `DB_SERVER` | Sí | Host del Fabric Warehouse |
| `DB_NAME` | Sí | Nombre de la base de datos |
| `DB_USER` | Sí | Email Azure AD |
| `DB_PASSWORD` | Solo si `DB_AUTH_MODE=sql` | Contraseña SQL |
| `DB_AUTH_MODE` | No (default: `sql`) | `interactive` / `activedirectoryintegrated` / `sql` |
| `FRANCHISE_CODE` | No (default en config.py) | Código fijo de franquicia |
| `MEMORY_DB_PATH` | No (default: `./memory.db`) | Ruta del SQLite local |

---

## 8. Decisiones de diseño

### ¿Por qué franchise code fijo en esta versión?

Agent-EXE es una versión de prueba para dos franquiciados específicos. El `franchise_id` dinámico se eliminó de la API y la UI para simplificar la distribución: el franquiciado no necesita saber su código, solo abrir la app.

### ¿Por qué SQLite en memoria para el análisis?

En lugar de generar T-SQL para Fabric directamente, se traen los datos al servidor Python y se cargan en SQLite. Ventajas: SQLite es más simple para el LLM (menos dialecto), el LLM no puede modificar datos reales, y permite métricas calculadas en Python con lógica determinística.

Contrapartida: inviable si la franquicia tiene millones de filas. Para ese escenario habría que pasar a T-SQL directo contra Fabric.

### ¿Por qué leer `business_rules.md` en runtime?

Para agregar o corregir reglas sin reiniciar el servidor. El archivo es editable directamente y el cambio toma efecto en la siguiente consulta. Fundamental para el ciclo de entrenamiento: aplicar sugerencias del `training_log.md` sin deploy.

### ¿Por qué el training_agent no modifica archivos automáticamente?

Las sugerencias son generadas por un LLM y pueden ser incorrectas. Un ciclo de revisión humana garantiza que solo se aplican cambios validados. El training_agent solo escribe en `training_log.md` — nunca toca código ni `business_rules.md`.

### ¿Por qué dos tablas de memoria (resumen + historial)?

`chatbot_memory` (resumen): inyectado como contexto en cada request — compacto, pocos tokens.
`chat_messages` (historial completo): permite reconstruir la conversación en la UI cuando el usuario carga una sesión anterior.

### ¿Por qué el ComparativeAgent reutiliza métodos del DataAgent?

`_load_into_memory` y `_compute_summary` son lógica de datos pura. Cualquier mejora futura al cálculo de métricas beneficia a ambos agentes automáticamente. Un solo SP call cubre el rango completo de ambos períodos — la mitad de round-trips a Fabric vs. dos queries separadas.

### ¿Por qué el filtro de fecha usa `h.DateTimeUtc` y no `d.SaleDateTimeUtc` en el SP?

Header y detalle pueden tener timestamps que caen en días distintos. El filtro por detalle perdía ~20 tickets por día. El filtro por header replica el comportamiento de Spark y coincide con los reportes de negocio.

### ¿Por qué se excluyen tickets cancelados en el SP?

Spark excluye canceladas via `Cloud_StateHistory WHERE Code = 'Cancelled'`. El SP replica esto con `CROSS APPLY OPENJSON(StateHistory)`. Sin este filtro el conteo no coincide con los reportes.
