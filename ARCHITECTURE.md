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
9. [Modo híbrido: db_ventas.db local vs SP remoto](#9-modo-híbrido-db_ventasdb-local-vs-sp-remoto)

---

## 1. Visión general

Chatbot de ventas para franquiciados. Soporta N franquicias bajo un mismo franquiciado: resuelve automáticamente la franquicia de cada consulta, pide aclaración cuando es ambiguo y compara franquicias entre sí. El backend es una API FastAPI con sistema multi-agente donde cada agente tiene una responsabilidad específica. Incluye un ciclo de entrenamiento supervisado basado en feedback de usuarios.

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
│   ├── config.py                  # Variables de entorno + franchise_map desde JSON
│   ├── logger.py                  # Logger por sesión → logs/<session_id>.log
│   ├── agents/
│   │   ├── orchestrator.py        # Clasifica mensajes en tipos de agente
│   │   ├── franchise_resolver.py  # Detecta franquicia(s) o pide aclaración
│   │   ├── session_context.py     # ★ Estado de sesión: franquicia, fecha, último producto
│   │   ├── date_resolver.py       # ★ Extrae y persiste rangos de fecha por sesión
│   │   ├── comparative_agent.py   # Comparativas entre períodos o entre franquicias
│   │   ├── data_agent.py          # Text-to-SQL sobre datos de ventas
│   │   ├── interaction.py         # Conversación general del negocio
│   │   ├── memory_agent.py        # Resúmenes y contexto de sesión
│   │   └── training_agent.py      # Analiza feedback, escribe training_log.md
│   ├── db/
│   │   ├── connection.py          # Conexión Azure AD a Fabric (pyodbc)
│   │   ├── data_source.py         # ★ Selector híbrido local/remoto — punto de entrada único
│   │   ├── local_sales_repo.py    # ★ Repositorio local: carga db_ventas.db en RAM
│   │   ├── sales_repo.py          # Ejecuta sp_GetSalesForChatbot (modo remoto)
│   │   ├── sales_analytics.py     # ★ load_into_memory() y compute_summary() compartidos
│   │   ├── memory_repo.py         # CRUD sesiones/mensajes/token-logs (SQLite)
│   │   └── training_repo.py       # TrainingMemory singleton (RAM + disco)
│   ├── models/
│   │   └── schemas.py             # Pydantic: ChatRequest, FeedbackRequest, etc.
│   └── routers/
│       ├── chat.py                # POST /chat/, POST /chat/feedback/, sesiones
│       └── debug.py               # /debug/query/csv, /debug/query/json, token-logs
├── context/
│   ├── business_rules.md          # Reglas de negocio leídas en runtime
│   ├── franchise_labels.json      # {franchise_code: label} — hot-reload por mtime
│   └── training_log.md            # Log append-only de sugerencias de mejora
├── sql/
│   └── sp_GetSalesForChatbot.sql
├── ui_test/
│   ├── index.html                 # UI (Tailwind CSS) con feedback 👍👎
│   └── Nacho.svg
├── csv_to_db.py                   # Convierte CSV exportado de SSMS a db_ventas.db
├── export_db.py                   # Genera db_ventas.db conectándose al SP
├── validate_export.py             # Valida que db_ventas.db coincide con el SP
├── logs/                          # Logs por sesión (auto-generado)
├── memory.db                      # SQLite local (auto-generado)
├── db_ventas.db                   # ★ Opcional: si existe activa el modo LOCAL
├── data/                          # Carpeta junto al .exe (auto-generada)
│   ├── memory.db
│   ├── db_ventas.db               # ★ Opcional: modo LOCAL en producción compilada
│   └── logs/
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
        │
        ▼
3b. FranchiseResolver.resolve(mensaje, contexto, franchise_map, agent_type)
   ├─ clarification → devuelve pregunta al usuario (sin llamar agente)
   ├─ is_franchise_compare → ComparativeAgent.process_franchise_comparison()
   └─ resolved_codes → pasa al agente correspondiente
        │
        ▼
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
DateResolver.resolve()      → Python directo (hoy/ayer/esta semana…)
                              → SessionContext.get_date() si es follow-up
                              → LLM fallback para fechas específicas
                              → Guarda en SessionContext para el próximo turno
        │
data_source.get_sales()     → LOCAL: RAM  /  REMOTO: sp_GetSalesForChatbot @ Fabric
        │
sales_analytics.load_into_memory()   → SQLite en RAM (decodifica DATETIMEOFFSET si aplica)
        │
_generate_sql()             → LLM genera SQL SQLite
                              (business_rules + SessionContext.last_product inyectado)
        │
_execute_sql()              → Ejecuta contra la tabla en RAM
                              → Extrae producto mencionado del SQL → SessionContext
        │
sales_analytics.compute_summary()   → Métricas en Python: transacciones, totales,
                                       vendedores, top productos, horas activas — sin LLM
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

Pipeline de 6 pasos: resolución de fecha → obtención de datos → SQLite en RAM → generación SQL → ejecución → formato LLM. La extracción de fechas y el cálculo de métricas viven en módulos compartidos (`date_resolver`, `sales_analytics`).

**Punto crítico — contexto de sesión:** El último artículo consultado se extrae del SQL generado (regex sobre `ArticleDescription`) y se guarda en `SessionContext`. En el siguiente turno, se inyecta como `ÚLTIMO ARTÍCULO/PRODUCTO CONSULTADO` en el contexto de `_generate_sql`, permitiendo preguntas como "y en kilos?" que heredan el producto correcto.

**Punto crítico — métricas pre-calculadas:** `compute_summary()` calcula todo en Python (sin LLM). El LLM de formato recibe los números definitivos y solo los presenta — nunca recalcula.

---

### `app/agents/comparative_agent.py` — El comparador

Maneja consultas con dos períodos. Hace un único llamado a la fuente de datos con el rango completo (min_from → max_to) y filtra en SQLite para cada período. Usa `load_into_memory()` y `compute_summary()` de `sales_analytics.py`, y `date_resolver.resolve()` para extraer fechas. No depende de ningún método privado de `DataAgent`.

**Costo:** 2 LLM calls + 1 llamado a datos (vs. 6 LLM calls + 2 llamados si se usaran dos queries separadas).

---

### `app/agents/session_context.py` — El estado de sesión (sin LLM)

Singleton en RAM que centraliza el estado conversacional por sesión. Evita que cada módulo mantenga su propio dict de sesión y que se busquen datos en texto del historial.

| Campo | Tipo | Qué guarda |
|---|---|---|
| `franchise` | `list[str]` | Códigos de franquicia resueltos en el último turno |
| `date` | `(datetime, datetime, str)` | Rango de fechas y filtro SQL del último período explícito |
| `last_product` | `str` | Último artículo consultado (extraído del SQL generado) |

`chat.py` lee/escribe `franchise`. `DateResolver` lee/escribe `date`. `DataAgent` lee/escribe `last_product`.

---

### `app/agents/date_resolver.py` — El extractor de fechas (0-1 LLM calls)

Extrae el rango de fechas de un mensaje. Retorna `(date_from, date_to, date_filter, tok_in, tok_out, clarification)`.

Lógica en orden de prioridad (sin LLM hasta el paso 3):
1. Keywords directos (`hoy`, `ayer`, `esta semana`, `semana pasada`, `este mes`) → 0 tokens
2. Follow-up detectado + `SessionContext.get_date()` disponible → reutiliza fecha guardada, 0 tokens
3. LLM fallback (Haiku) → extrae fecha del mensaje o contexto

**Detección de follow-up:** usa regex `\b\d{2}[/\-]\d{2}` para detectar fechas reales — evita dispararse con fracciones como "1/4 Kilo" o "1/2 Kilo" (que solo tienen 1 dígito por lado).

Después de resolver, guarda en `SessionContext.set_date()` para que el próximo turno no necesite buscar fechas en texto del historial.

---

### `app/db/sales_analytics.py` — Analítica de ventas compartida (sin LLM)

Módulo con dos funciones puras usadas por `DataAgent` y `ComparativeAgent`:

- **`load_into_memory(sales)`**: carga una lista de dicts en SQLite en RAM. Calcula `DiaSemana`. Decodifica `DATETIMEOFFSET` de 20 bytes (modo remoto).
- **`compute_summary(conn, date_filter, period_label, franchise_map)`**: calcula en Python (sin LLM) transacciones, totales, ticket promedio, desglose por vendedor, top productos y horas activas. El LLM de formato recibe estos números y solo los presenta.

Antes de este módulo, `ComparativeAgent` llamaba directamente a `data_agent._load_into_memory()` y `data_agent._compute_summary()` — acoplamiento frágil que se eliminó.

---

### `app/agents/franchise_resolver.py` — El selector de franquicia (sin LLM)

Corre después del Orchestrator, sin costo de tokens. Recibe `(mensaje, contexto, franchise_map, agent_type, session_franchise)` y retorna `(franchise_codes, clarification, is_franchise_compare)`.

Lógica en orden de prioridad:
1. Una sola franquicia → sin ambigüedad
2. Contexto tiene pregunta de aclaración previa → matching ordinal ("la 1", "primera", "2", "segunda")
3. Mensaje menciona todas las franquicias o keyword de comparación → todas
4. Mensaje menciona un label específico → esa franquicia
5. Contexto previo usó una sola franquicia → hereda esa
6. `session_franchise` (desde `SessionContext`) → reutiliza franquicia del turno anterior
7. Ambiguo + agent_type data/comparative → devuelve `clarification` al usuario

---

### `app/agents/interaction.py` — El recepcionista

Haiku con `max_tokens=500`. Responde saludos y preguntas sobre el chatbot. Si el mensaje mezcla contenido de negocio con off-topic, responde solo la parte del negocio e ignora el resto.

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

`franchisee_code` leído de `FRANCHISEE_CODE` (o `FRANCHISE_CODE` como fallback). `franchise_map` es una property que lee `context/franchise_labels.json` con cache por mtime — se recarga automáticamente si el archivo cambia, sin reiniciar la app.

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

Corre en Microsoft Fabric Warehouse. Usa 5 CTEs con `CROSS APPLY OPENJSON` para extraer datos de columnas JSON.

Parámetros:
- `@FranchiseeCode` (obligatorio) — filtra `WHERE h.FranchiseeCode = @FranchiseeCode` (el dueño)
- `@FranchiseCodes` (opcional, CSV) — filtra adicionalmente por `h.FranchiseCode IN (STRING_SPLIT(...))`; NULL = todos los locales del dueño

Retorna ambas columnas: `h.FranchiseeCode` (el dueño) y `h.FranchiseCode` (el local). El bot agrupa por `FranchiseCode` para distinguir franquicias.

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
| Warehouse | Microsoft Fabric | Cloud | Datos de ventas (fuente de verdad — modo remoto) |
| db_ventas.db | SQLite (archivo) | Raíz del proyecto **o** `data/` junto al .exe | Datos de ventas locales — activa el **modo LOCAL** |
| SQLite en memoria | sqlite3 | RAM del servidor | Tabla temporal por consulta (ambos modos) |
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
| `DB_SERVER` | Solo modo remoto | Host del Fabric Warehouse |
| `DB_DATABASE` | Solo modo remoto | Nombre de la base de datos (acepta también `DB_NAME`) |
| `DB_USER` | Solo modo remoto | Email Azure AD |
| `DB_PASSWORD` | Solo si `DB_AUTH_MODE=sql` | Contraseña SQL |
| `DB_AUTH_MODE` | No (default: `sql`) | `activedirectoryinteractive` / `activedirectoryintegrated` / `sql` |
| `FRANCHISEE_CODE` | Solo modo remoto | Código del franquiciado (dueño); acepta también `FRANCHISE_CODE` |
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

### ¿Por qué `SalesAnalytics`, `DateResolver` y `SessionContext` son módulos separados?

**SalesAnalytics**: `load_into_memory` y `compute_summary` son lógica de datos pura sin dependencias de agentes. Extraerlos elimina el acoplamiento donde `ComparativeAgent` llamaba a métodos privados de `DataAgent`. Cualquier mejora al cálculo de métricas beneficia a ambos agentes automáticamente.

**DateResolver**: `_extract_date_range` era un método de 130 líneas dentro de `DataAgent`. Al extraerlo, tanto `DataAgent` como `ComparativeAgent` pueden resolver fechas de forma independiente y consistente, usando la misma lógica de follow-up y el mismo `SessionContext`.

**SessionContext**: antes, `_session_franchise` vivía en `chat.py` y `_session_dates` vivía en `data_agent.py`. Centralizar el estado de sesión en un único singleton elimina la duplicación, hace el estado auditable en un solo lugar, y permite que `last_product` sea accesible sin acoplar los agentes entre sí.

### ¿Por qué persistir la fecha resuelta en SessionContext en vez de buscar en el contexto de memoria?

Buscar fechas en texto del historial (el enfoque anterior) propagaba errores: si el bot respondía "período 25/03 al 31/03" cuando el usuario pedía "marzo", esa fecha incorrecta se guardaba en memoria y contaminaba todos los follow-ups. `DateResolver` guarda la fecha que él mismo resolvió — nunca la que el LLM de formato escribió en su respuesta.

### ¿Por qué el filtro de fecha usa `h.DateTimeUtc` y no `d.SaleDateTimeUtc` en el SP?

Header y detalle pueden tener timestamps que caen en días distintos. El filtro por detalle perdía ~20 tickets por día. El filtro por header replica el comportamiento de Spark y coincide con los reportes de negocio.

### ¿Por qué se excluyen tickets cancelados en el SP?

Spark excluye canceladas via `Cloud_StateHistory WHERE Code = 'Cancelled'`. El SP replica esto con `CROSS APPLY OPENJSON(StateHistory)`. Sin este filtro el conteo no coincide con los reportes.

---

## 9. Modo híbrido: db_ventas.db local vs SP remoto

### Concepto

El sistema puede operar en dos modos excluyentes, elegidos automáticamente **al arrancar** la aplicación:

| Modo | Activación | Fuente de datos | Latencia |
|---|---|---|---|
| **LOCAL** | `db_ventas.db` presente en disco | SQLite cargado en RAM | ~0 ms (RAM pura) |
| **REMOTO** | `db_ventas.db` ausente | `sp_GetSalesForChatbot` en Azure/Fabric | 2-10 seg |

Una vez elegido el modo, **no cambia** durante la vida del proceso. Para cambiar de modo hay que detener y reiniciar la app.

---

### Flujo de decisión al inicio

```
app.main módulo cargando
        │
        ▼
init_data_source()   ← app/db/data_source.py
        │
        ├─ [LOG] "Buscando archivo db_ventas.db…"
        │
        ├─ ¿existe db_ventas.db?
        │     │
        │     ├─ SÍ → [LOG] "✔ Encontrado: <ruta>"
        │     │       [LOG] "MODO LOCAL activado — NO se ejecutará el Store Procedure"
        │     │       LocalSalesRepository.load() → carga TODAS las filas en RAM
        │     │       _mode = "local"
        │     │
        │     └─ NO → [LOG] "db_ventas.db no encontrado — MODO REMOTO activado"
        │               _mode = "remote"
        │
        ▼
Toda consulta posterior → data_source.get_sales()
        ├─ mode=local  → filtra en RAM (LocalSalesRepository)
        └─ mode=remote → llama sales_repo.get_sales() → SP Azure
```

---

### Rutas de búsqueda de db_ventas.db

El sistema busca en este orden de prioridad:

1. `<exe_dir>/data/db_ventas.db` — carpeta `data/` junto al `.exe` (producción)
2. `<exe_dir>/db_ventas.db` — raíz junto al `.exe` (alternativa producción)
3. `<project_root>/db_ventas.db` — raíz del proyecto (desarrollo local)

Donde `exe_dir` es:
- En `.exe` compilado: carpeta donde vive el `.exe`
- En desarrollo: carpeta donde vive `launcher.py` / `sys.argv[0]`

---

### Componentes involucrados

| Archivo | Responsabilidad |
|---|---|
| `app/db/data_source.py` | Selector. Decide el modo, inicializa repo y expone `get_sales()` unificado |
| `app/db/local_sales_repo.py` | Carga db_ventas.db en RAM. Filtra en Python. Mismo contrato que `SalesRepository` |
| `app/db/sales_repo.py` | Repositorio remoto. Sin cambios — solo se usa si modo=remoto |
| `app/main.py` | Llama `init_data_source()` antes de validar ODBC/credenciales |
| `app/agents/data_agent.py` | Usa `data_source.get_sales()` en vez de `sales_repo` directamente |
| `app/agents/comparative_agent.py` | Ídem |

---

### Estructura de db_ventas.db

`db_ventas.db` debe tener una tabla llamada `ventas` con exactamente las mismas columnas que devuelve `sp_GetSalesForChatbot`. Los valores de `SaleDateTimeUtc` deben ser strings ISO (`YYYY-MM-DD HH:MM:SS.ffffff`) — ya no vienen como DATETIMEOFFSET de 20 bytes porque son SQLite nativos.

Esquema mínimo esperado:
```sql
CREATE TABLE ventas (
    id                 TEXT,
    FranchiseeCode     TEXT,   -- código del dueño (mismo para todas las filas)
    FranchiseCode      TEXT,   -- código del local (distingue Franquicia 1 / Franquicia 2)
    Franquicia         TEXT,   -- "1" o "2" — posición en franchise_labels.json (generado por export_db.py)
    ShiftCode          TEXT,
    PosCode            TEXT,
    UserName           TEXT,   -- anonimizado como "Colaborador N" por export_db.py
    SaleDateTimeUtc    TEXT,   -- ISO string: '2025-03-15 14:23:00.000000' (UTC-3)
    Quantity           TEXT,
    ArticleId          TEXT,
    ArticleDescription TEXT,
    TypeDetail         TEXT,
    UnitPriceFix       TEXT,
    Type               TEXT,
    CtaChannel         TEXT,
    VtaOperation       TEXT,
    Plataforma         TEXT,
    FormaPago          TEXT
);
```

`LocalSalesRepository` filtra y agrupa por `FranchiseCode` (el local). `FranchiseeCode` queda disponible pero no se usa para grouping.

---

### Logs de referencia

Al arrancar con `db_ventas.db` presente:
```
[DATA-SOURCE] Buscando archivo db_ventas.db…
[DATA-SOURCE] ✔ Encontrado: C:\SmartIA\data\db_ventas.db
[DATA-SOURCE] MODO LOCAL activado — NO se ejecutará el Store Procedure. Trabajando con db_ventas.db en RAM.
[LOCAL-DB]    Cargando db_ventas.db en RAM…
[LOCAL-DB]    db_ventas.db cargada en RAM: 48321 filas, 312 ms
```

Al arrancar sin `db_ventas.db`:
```
[DATA-SOURCE] Buscando archivo db_ventas.db…
[DATA-SOURCE] db_ventas.db no encontrado — MODO REMOTO activado. Se usará sp_GetSalesForChatbot en Azure/Fabric.
```

En cada consulta (modo local):
```
DATA SRC : LOCAL db_ventas.db → 1842 filas devueltas
```

En cada consulta (modo remoto):
```
DATA SRC : SP sp_GetSalesForChatbot → 1842 filas devueltas
```

---

### Decisión de diseño: carga total en RAM al inicio

El SP ya opera por RAM (los datos llegan, se cargan en SQLite `:memory:` y se consultan ahí). Para mantener la misma velocidad en modo local, `LocalSalesRepository` carga **todo** `db_ventas.db` en RAM al inicializar — sin TTL ni caducidad. El filtrado es puro Python sobre una lista de dicts en memoria.

Ventajas:
- Latencia de consulta: < 5 ms (sin I/O)
- Sin dependencia de pyodbc, driver ODBC, ni credenciales Azure
- Comportamiento idéntico al modo remoto desde la perspectiva de los agentes

Contrapartida:
- La RAM usada es proporcional al tamaño de `db_ventas.db` (≈ 25 MB comprimido → ~80-150 MB en RAM según las columnas)
- Los datos no se refrescan en caliente; si `db_ventas.db` se actualiza hay que reiniciar la app

