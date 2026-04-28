# Asistente Smart-IA — Agent-EXE

Versión empaquetable del asistente de ventas para franquiciados. Soporta múltiples franquicias bajo un mismo franquiciado: cuando la consulta es ambigua pregunta al usuario cuál franquicia, y puede comparar franquicias entre sí. Incluye sistema de entrenamiento con feedback de usuarios.

Construido con FastAPI y Claude (Anthropic). Modo híbrido: si existe `db_ventas.db` opera 100% local (sin llamadas a SQL Server); si no, se conecta a Microsoft Fabric Warehouse vía SP. En ambos casos carga los datos en SQLite en memoria y responde preguntas de ventas en lenguaje natural.

## Arquitectura

```
Cliente (UI Web)
    ↓
FastAPI Gateway
    ↓
Orchestrator Agent (Claude Sonnet — decide qué agente responde)
    ↓
Franchise Resolver  (detecta franquicia(s) — sin LLM)
    ├→ Comparative Agent  (Haiku — comparativas entre períodos o entre franquicias)
    ├→ Data Agent         (Haiku — Text-to-SQL sobre datos de ventas)
    ├→ Interaction Agent  (Haiku — conversación básica del negocio)
    ├→ Training Agent     (Haiku — analiza feedback y genera sugerencias)
    ├→ off_topic          (respuesta fija, 0 tokens)
    └→ Memory Agent       (Haiku — resumen y contexto de sesión)
    ↓
SessionContext ──── DateResolver ──── SalesAnalytics
(franquicia,        (extrae fecha,     (load_into_memory,
 fecha, producto)    persiste sesión)   compute_summary)
    ↓
MODO LOCAL: db_ventas.db en RAM       MODO REMOTO: sp_GetSalesForChatbot
    ↓
SQLite en memoria (Text-to-SQL)     SQLite local (memory.db)
```

> Documentación completa de arquitectura y decisiones de diseño: [ARCHITECTURE.md](ARCHITECTURE.md).

## Requisitos

- Python 3.12
- ODBC Driver 18 for SQL Server
- Cuenta Anthropic con acceso a Claude Sonnet y Haiku
- Azure AD con permisos de lectura sobre el Fabric Warehouse

## Instalación

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# Editar .env con tus valores
```

## Variables de entorno (`.env`)

```env
ANTHROPIC_API_KEY=sk-ant-...

DB_SERVER=tu-servidor.datawarehouse.fabric.microsoft.com
DB_DATABASE=nombre_de_tu_warehouse
DB_USER=tu@email.com
DB_AUTH_MODE=activedirectoryinteractive
DB_PASSWORD=

FRANCHISEE_CODE=fd9e42fa...   # código del franquiciado (dueño), obligatorio en modo remoto

MEMORY_DB_PATH=./memory.db
```

`DB_AUTH_MODE=interactive` abre el browser para login Azure AD con MFA. El token se cachea en `~/.azure/` y se reutiliza automáticamente.

## Ejecutar

```bash
uvicorn app.main:app --reload
```

Accesos:
- **UI**: http://localhost:8000/ui/index.html
- **API Docs**: http://localhost:8000/docs
- **Health**: http://localhost:8000/health

## Endpoints

### `POST /chat/`
```json
{
  "message": "¿Cuál fue el producto más vendido ayer?",
  "session_id": "session_abc123",
  "user_id": "opcional",
  "training_mode": false
}
```
Respuesta:
```json
{
  "session_id": "session_abc123",
  "response": "El producto más vendido ayer fue...",
  "agent_type": "data",
  "timestamp": "2026-04-23T11:00:00"
}
```

### `POST /chat/feedback/`
Registra feedback explícito (botones 👍👎 de la UI) para el sistema de entrenamiento.
```json
{
  "session_id": "session_abc123",
  "user_message": "¿Cuánto vendimos ayer?",
  "bot_response": "Ayer las ventas fueron...",
  "feedback": "Los números no coinciden con mi cierre de caja.",
  "feedback_type": "negativo"
}
```
Respuesta:
```json
{ "ok": true, "component": "data_agent", "priority": "alta" }
```

### `GET /chat/sessions/`
Lista todas las sesiones con su resumen.

### `GET /chat/sessions/{session_id}/messages`
Historial completo de mensajes de una sesión.

### `DELETE /chat/sessions/{session_id}`
Elimina una sesión y todos sus mensajes.

### `POST /debug/query/csv` y `POST /debug/query/json`
Ejecutan SQL crudo contra los datos del SP. Útil para validar queries del agente.
```json
{
  "sql": "SELECT * FROM ventas WHERE \"Type\" != '2' LIMIT 100",
  "date_from": "2026-03-25",
  "date_to": "2026-03-25"
}
```

### `GET /debug/token-logs`
Consumo de tokens por consulta. Acepta `?session_id=xxx`.

## Estructura del proyecto

```
Agent-EXE/
├── app/
│   ├── agents/
│   │   ├── orchestrator.py       # Clasifica mensajes (comparative/data/interaction/feedback/off_topic)
│   │   ├── franchise_resolver.py # Detecta franquicia(s) o pide aclaración al usuario
│   │   ├── session_context.py    # Estado de sesión: franquicia, fecha, último producto
│   │   ├── date_resolver.py      # Extrae y persiste rangos de fecha por sesión
│   │   ├── comparative_agent.py  # Comparativas entre períodos o entre franquicias
│   │   ├── data_agent.py         # Text-to-SQL sobre ventas
│   │   ├── interaction.py        # Conversación general del negocio
│   │   ├── memory_agent.py       # Resumen y contexto de sesión
│   │   └── training_agent.py     # Analiza feedback y escribe en training_log.md
│   ├── db/
│   │   ├── connection.py         # Conexión Azure AD a Fabric (pyodbc)
│   │   ├── data_source.py        # Selector híbrido local/remoto
│   │   ├── local_sales_repo.py   # Carga db_ventas.db en RAM (modo local)
│   │   ├── sales_repo.py         # Ejecuta sp_GetSalesForChatbot (modo remoto)
│   │   ├── sales_analytics.py    # load_into_memory() y compute_summary() compartidos
│   │   ├── memory_repo.py        # SQLite local (sesiones + mensajes + token logs)
│   │   └── training_repo.py      # TrainingMemory singleton (RAM + training_log.md)
│   ├── models/
│   │   └── schemas.py            # Pydantic: ChatRequest, FeedbackRequest, ChatResponse...
│   ├── routers/
│   │   ├── chat.py               # Endpoints de chat, feedback y sesiones
│   │   └── debug.py              # Endpoints de debug (query/csv, query/json, token-logs)
│   ├── logger.py                 # Logger por sesión → logs/<session_id>.log
│   ├── config.py                 # Variables de entorno (franchise_code fijo)
│   └── main.py                   # App FastAPI + CORS + rutas
├── context/
│   ├── business_rules.md         # Reglas de negocio (leídas en runtime)
│   ├── franchise_labels.json     # {franchise_code: "Franquicia N"} — editable sin reiniciar
│   └── training_log.md           # Log append-only de sugerencias de entrenamiento
├── sql/
│   └── sp_GetSalesForChatbot.sql
├── csv_to_db.py                  # Convierte CSV exportado de SSMS a db_ventas.db (recomendado)
├── export_db.py                  # Genera db_ventas.db conectándose al SP directamente
├── validate_export.py            # Valida que db_ventas.db coincide con el SP
├── ui_test/
│   ├── index.html                # UI principal (Tailwind CSS)
│   └── Nacho.svg                 # Logo
├── logs/                         # Logs por sesión (auto-generado, en .gitignore)
├── memory.db                     # SQLite local (auto-generado)
├── .env.example
├── requirements.txt
├── ARCHITECTURE.md
└── README.md
```

## Agentes

### Orchestrator (Claude Sonnet)

| Tipo | Descripción | Costo |
|---|---|---|
| `comparative` | Compara dos períodos ("esta semana vs la semana pasada") | Medio |
| `data` | Consultas de un período, productos, precios, reportes | Alto |
| `interaction` | Saludos, preguntas sobre el chatbot | Bajo |
| `feedback` | El usuario comenta la respuesta anterior del bot | Bajo |
| `off_topic` | Sin relación con ventas o el negocio | Cero |

### Franchise Resolver (sin LLM)

Después del Orchestrator, detecta para qué franquicia(s) aplica la consulta:

| Caso | Comportamiento |
|---|---|
| Una sola franquicia configurada | Sin ambigüedad, pasa directo |
| Follow-up a pregunta de aclaración ("la 1", "la primera") | Matching ordinal por posición |
| Mensaje menciona el nombre de una franquicia | Retorna esa franquicia |
| Mensaje menciona "ambas" / "las dos" / "comparalas" | Retorna todas |
| Sesión tiene franquicia previa en SessionContext | La reutiliza en follow-ups |
| Ambiguo | Pregunta al usuario: "¿Para cuál franquicia? (Franquicia 1 o Franquicia 2)" |

### Módulos compartidos (sin LLM)

| Módulo | Responsabilidad |
|---|---|
| `session_context.py` | Singleton en RAM con el estado de sesión: franquicia resuelta, rango de fechas, último artículo consultado |
| `date_resolver.py` | Extrae fechas por keyword Python → SessionContext → LLM fallback. Guarda el rango resuelto en SessionContext para follow-ups |
| `sales_analytics.py` | `load_into_memory()` y `compute_summary()` compartidos entre DataAgent y ComparativeAgent |

### Training Agent (Claude Haiku)

Analiza ciclos de feedback (pregunta → respuesta → evaluación del usuario) e identifica la causa raíz del problema. Genera una entrada estructurada en `context/training_log.md`:

```markdown
## [2026-04-23 14:30] Sesión: session_abc | Tipo: negativo

**Componente afectado:** data_agent
**Causa raíz identificada:** El agente no filtra por Type != '2' al calcular promedios
**Sugerencia de cambio:** Agregar regla explícita en business_rules.md
**Prioridad:** alta
```

Cuando `training_mode: true` en el request, el sistema inyecta las sugerencias de alta/media prioridad como contexto adicional en los prompts de los agentes activos.

## Sistema de entrenamiento

El ciclo de aprendizaje es:

1. Usuario hace una consulta → bot responde
2. Usuario presiona 👍 o 👎 en la UI
3. Para 👎: aparece un campo de texto "¿Qué falló?"
4. El `training_agent` analiza el ciclo y escribe en `context/training_log.md`
5. Con `Entrenamiento` activado en la UI, las sugerencias se inyectan como contexto

Para aplicar manualmente las sugerencias: revisar `context/training_log.md`, filtrar por `Prioridad: alta`, aplicar los cambios en el componente indicado y marcar como `[APLICADO]`.

## Reglas de negocio (`context/business_rules.md`)

Leído en runtime en cada consulta. Para agregar una regla nueva no es necesario reiniciar el servidor.

Reglas clave:
- `Type=2` → cabecera de promoción (excluir de totales con `WHERE "Type" != '2'`)
- Búsqueda de artículos: `LOWER(ArticleDescription) LIKE LOWER('%texto%')`
- Columnas de canal: `CtaChannel`, `VtaOperation`, `Plataforma`, `FormaPago`

## Troubleshooting

**Error de autenticación Azure AD (24803)**
- Verificar que `.env` tiene `DB_AUTH_MODE=interactive`
- `pip install azure-identity`

**SP devuelve 0 filas**
- Verificar que `FRANCHISEE_CODE` en `.env` corresponde a `FranchiseeCode` (el dueño) en Fabric
- Ejecutar `EXEC sp_GetSalesForChatbot @FranchiseeCode = 'tu-id'` directo en Fabric

**Error de ODBC Driver**
```powershell
Get-OdbcDriver | Select-Object Name
```
Descargar ODBC Driver 18 desde Microsoft si no aparece en la lista.

**Resultados inconsistentes**
- Los conteos se calculan con `COUNT(DISTINCT id)` en Python, no por el LLM
- Verificar que `temperature=0` en todos los agentes
