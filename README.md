# Chatbot Multi-Agente — Asistente de Ventas para Franquiciados

Chatbot multi-agente para análisis de ventas de franquiciados. Construido con FastAPI y Claude (Anthropic), se conecta a Microsoft Fabric Warehouse para consultar datos de ventas mediante stored procedures, los carga en SQLite en memoria y usa Text-to-SQL para responder preguntas en lenguaje natural.

## Arquitectura

```
Cliente (UI Web / API)
    ↓
FastAPI Gateway
    ↓
Orchestrator Agent (Claude Sonnet — decide qué agente responde)
    ├→ Comparative Agent (Haiku — comparativas entre dos períodos)
    ├→ Data Agent        (Haiku — Text-to-SQL sobre datos de ventas)
    ├→ Interaction Agent (Haiku — conversación básica del negocio)
    ├→ off_topic         (respuesta fija sin LLM — 0 tokens)
    └→ Memory Agent      (Haiku — resumen y contexto de sesión)
    ↓
Microsoft Fabric Warehouse  →  sp_GetSalesForChatbot
    ↓
SQLite en memoria (Text-to-SQL)     SQLite local (memoria de sesiones)
```

> Para la documentación completa de arquitectura, componentes y decisiones de diseño ver [ARCHITECTURE.md](ARCHITECTURE.md).

### Flujo del Data Agent (Text-to-SQL)
1. Extrae el rango de fechas del mensaje (Python primero, LLM como fallback) — usa el contexto de conversación previa si el mensaje no tiene fecha explícita
2. Ejecuta `sp_GetSalesForChatbot` en Fabric Warehouse con el `FranchiseCode` y rango de fechas
3. Carga los resultados en una tabla `ventas` SQLite en memoria
4. El LLM genera una consulta SQLite a partir del lenguaje natural (`temperature=0`) — con contexto de conversación previa para seguimiento de preguntas
5. Ejecuta el SQL
6. Calcula métricas clave en Python (transacciones únicas, totales, por vendedor, top productos, horas activas) — sin LLM, filtradas por el mismo período que pidió el usuario
7. Formatea la respuesta en español usando los datos pre-calculados

## Requisitos

- Python 3.12
- Microsoft Fabric Warehouse (con ODBC Driver 18 for SQL Server)
- Cuenta Anthropic con acceso a Claude Sonnet y Haiku
- Azure AD con permisos de lectura sobre el Warehouse

## Instalación

```bash
# 1. Crear entorno virtual
python -m venv venv
venv\Scripts\activate       # Windows
# source venv/bin/activate  # Linux/Mac

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Configurar variables de entorno
copy .env.example .env
# Editar .env con tus valores
```

## Variables de entorno (`.env`)

```env
# Anthropic API
ANTHROPIC_API_KEY=sk-ant-...

# Microsoft Fabric Warehouse
DB_SERVER=tu-servidor.datawarehouse.fabric.microsoft.com
DB_NAME=nombre_de_tu_warehouse
DB_USER=tu@email.com
DB_AUTH_MODE=interactive    # Abre el navegador para login con MFA

# Solo si DB_AUTH_MODE=sql
DB_PASSWORD=

# Memoria local (SQLite)
MEMORY_DB_PATH=./memory.db
```

## Configurar el Warehouse

Ejecutar en el **SQL Query Editor de Microsoft Fabric**:

```sql
-- 1. Stored procedure de ventas
-- (contenido en sql/sp_GetSalesForChatbot.sql)

-- 2. Verificar ejecución
EXEC sp_GetSalesForChatbot @FranchiseCode = 'tu-franchise-code'
```

> La memoria de sesiones se guarda localmente en SQLite (`memory.db`). No requiere permisos DDL en Fabric.

## Ejecutar

```bash
uvicorn app.main:app --reload
```

Al iniciar por primera vez con `DB_AUTH_MODE=interactive`, se abrirá el navegador para autenticarse con Azure AD (MFA). El token se cachea en `~/.azure/`.

Accesos:
- **UI Web**: http://localhost:8000/ui/index.html
- **API Docs**: http://localhost:8000/docs
- **Health**: http://localhost:8000/health

## Endpoints

### `POST /chat/`
```json
{
  "message": "¿Cuál fue el producto más vendido ayer?",
  "session_id": "session_abc123",
  "franchise_id": "4066b2def050495a8fc9ff8c0cb3f8f4",
  "user_id": "opcional"
}
```
Respuesta:
```json
{
  "session_id": "session_abc123",
  "response": "El producto más vendido ayer fue...",
  "agent_type": "data",
  "timestamp": "2026-04-08T11:00:00"
}
```

### `GET /chat/sessions/`
Lista todas las sesiones guardadas con su resumen.

### `GET /chat/sessions/{session_id}/messages`
Historial completo de mensajes de una sesión.

### `DELETE /chat/sessions/{session_id}`
Elimina una sesión y todos sus mensajes.

### `GET /chat/history/{session_id}`
Resumen de contexto de una sesión.

### `POST /debug/query/csv`
Ejecuta un SQL crudo contra los datos del SP y devuelve un CSV descargable (UTF-8 con BOM para Excel). Útil para validar las consultas generadas por el agente.
```json
{
  "franchise_id": "4066b2def050495a8fc9ff8c0cb3f8f4",
  "sql": "SELECT * FROM ventas WHERE \"Type\" != '2' LIMIT 100",
  "date_from": "2026-03-25",
  "date_to": "2026-03-25"
}
```

### `POST /debug/query/json`
Igual que `/debug/query/csv` pero devuelve JSON con `{total_rows, columns, rows}`.

### `GET /debug/token-logs`
Log de consumo de tokens por consulta. Acepta `?session_id=xxx` para filtrar por sesión. Sin parámetro devuelve el historial completo de todas las sesiones.
```json
{
  "total_queries": 12,
  "total_input_tokens": 18400,
  "total_output_tokens": 3200,
  "total_tokens": 21600,
  "rows": [...]
}
```

## Estructura del proyecto

```
Agent/
├── app/
│   ├── agents/
│   │   ├── orchestrator.py      # Decide qué agente responde
│   │   ├── comparative_agent.py # Comparativas entre dos períodos
│   │   ├── data_agent.py        # Text-to-SQL sobre ventas
│   │   ├── interaction.py       # Conversación general
│   │   └── memory_agent.py      # Resumen y contexto
│   ├── db/
│   │   ├── connection.py        # Conexión Azure AD a Fabric
│   │   ├── sales_repo.py        # Llama al SP de ventas
│   │   └── memory_repo.py       # SQLite local (sesiones + mensajes)
│   ├── models/
│   │   └── schemas.py           # Modelos Pydantic
│   ├── routers/
│   │   ├── chat.py              # Endpoints de chat y sesiones
│   │   └── debug.py             # Endpoints de debug (query/csv, query/json)
│   ├── logger.py                # Logger por sesión (logs/<session_id>.log)
│   ├── config.py                # Variables de entorno
│   └── main.py                  # App FastAPI
├── context/
│   └── business_rules.md        # Reglas de negocio (leídas en runtime)
├── logs/                        # Logs por sesión (auto-generado, en .gitignore)
├── sql/
│   └── sp_GetSalesForChatbot.sql
├── ui_test/
│   └── index.html               # UI web de prueba
├── validate_setup.py            # Valida conexión y configuración
├── ARCHITECTURE.md              # Documentación completa de arquitectura
├── .env.example
├── requirements.txt
└── README.md
```

## Reglas de negocio (`context/business_rules.md`)

El agente lee este archivo en cada consulta. Contiene:
- Descripción de columnas de la tabla `ventas`, incluyendo los campos de canal y pago:
  - `CtaChannel` — canal de venta (Tienda, Delivery, Take Away)
  - `VtaOperation` — tipo de operación (Socios / No Socios)
  - `Plataforma` — plataforma delivery (PediGrido, PedidosYa, Rappi) o NULL
  - `FormaPago` — medio de pago del ticket
- Regla del campo `Type`: `0` = venta regular, `1` = ítem dentro de promoción, `2` = cabecera de promoción (excluir de totales con `Type != '2'`)
- Reglas de presentación: no mostrar información técnica al usuario
- Reglas de búsqueda: siempre usar `LOWER(...) LIKE LOWER('%texto%')` para nombres de artículos

Para agregar una nueva regla de negocio, editar `context/business_rules.md` — no se requiere reiniciar el servidor.

## Agentes

### Orchestrator (Claude Sonnet)
Clasifica cada mensaje en uno de cinco tipos:

| Tipo | Descripción | Costo |
|---|---|---|
| `comparative` | Comparativas entre dos períodos ("esta semana vs la semana pasada") | Medio (2 LLM calls) |
| `data` | Consultas de ventas de un solo período, productos, precios, reportes | Alto (hasta 3 LLM calls) |
| `interaction` | Saludos, preguntas sobre el chatbot | Bajo (1 LLM call, 200 tokens) |
| `off_topic` | Programación, clima, traducciones, etc. | Cero (respuesta fija, 0 tokens) |
| `memory` | "¿Qué hablamos antes?" | Mínimo (solo lectura DB) |

Cada consulta registra el consumo real de tokens (input + output, por agente) en la tabla `query_logs` del SQLite local. Consultable via `GET /debug/token-logs`.

Usa palabras clave como fallback si el LLM no responde en formato JSON válido. El default de fallback es `off_topic` (no `interaction`) para evitar gastar tokens en mensajes irrelevantes.

### Comparative Agent (Claude Haiku)
Maneja consultas que comparan dos períodos ("ayer vs antes de ayer", "enero vs febrero"). Pipeline:
1. LLM extrae los dos períodos del mensaje (labels + rangos de fecha)
2. Un solo llamado al SP cubriendo el rango completo de ambos períodos
3. Carga los datos en SQLite en memoria
4. Calcula métricas en Python para cada período por separado (reutiliza `_compute_summary` del Data Agent)
5. LLM formatea la comparativa con tabla de deltas, desglose por vendedor, top productos y conclusión

Reutiliza `_load_into_memory` y `_compute_summary` del Data Agent — sin duplicar lógica.

### Data Agent (Claude Haiku)
1. Extrae el rango de fechas con detección Python primero (hoy/ayer/esta semana/semana pasada/este mes) y LLM como fallback para fechas específicas — el contexto de conversación previa se inyecta para mantener el período en preguntas de seguimiento ("haz un desglose por items" después de "ventas del 01/12")
2. Llama al SP con el `franchise_id` y rango de fechas extraído
3. Carga los datos en SQLite en memoria (decodificando `DATETIMEOFFSET` binario de pyodbc)
4. Genera SQL SQLite con el LLM (`temperature=0`, usando las reglas de negocio y contexto de conversación como contexto)
5. Ejecuta el SQL
6. Calcula métricas de resumen en Python (sin LLM): transacciones únicas por `COUNT(DISTINCT id)`, totales, desglose por vendedor, top productos y franjas horarias — filtradas por el mismo período que el usuario pidió
7. Formatea la respuesta usando los números pre-calculados — el LLM solo presenta, no recalcula

### Interaction Agent (Claude Haiku)
Responde únicamente saludos y preguntas sobre el uso del chatbot (`max_tokens=200`). Si el mensaje no está relacionado con ventas o el negocio, devuelve una respuesta corta fija sin gastar tokens adicionales. El filtrado real de mensajes off-topic ocurre en el Orchestrator antes de llegar a este agente.

### Memory Agent (Claude Haiku)
Genera resúmenes de la conversación y los persiste en SQLite local. El contexto se recupera al inicio de cada sesión.

## Troubleshooting

**Error de autenticación Azure AD (22007 / 24803)**
- Usar `DB_AUTH_MODE=interactive` en lugar de `sql`
- Asegurarse de tener instalado `azure-identity` (`pip install azure-identity`)

**El SP devuelve 0 filas**
- Verificar que el WHERE usa `h.FranchiseCode` (no `h.FranchiseeCode`)
- Ejecutar `EXEC sp_GetSalesForChatbot @FranchiseCode = 'tu-id'` directo en Fabric

**Fechas incorrectas**
- La columna `SaleDateTimeUtc` en Fabric es `DATETIMEOFFSET` en UTC+00:00
- El SP aplica `SWITCHOFFSET(..., '-03:00')` para convertir a Argentina
- pyodbc devuelve el valor como bytes binarios de 20 bytes — el agente los decodifica con `struct.unpack`

**No aparece información de ventas**
- Validar setup: `python validate_setup.py`
- Verificar que `franchise_id` en el UI corresponde a `FranchiseCode` en Fabric (no `FranchiseeCode`)

**Error de ODBC Driver**
- Verificar que el ODBC Driver 18 for SQL Server está instalado: `Get-OdbcDriver | Select-Object Name` en PowerShell
- Descargar desde: https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server

**Resultados inconsistentes entre consultas idénticas**
- Asegurarse de que los tres llamados LLM del Data Agent usan `temperature=0`
- Los conteos de transacciones se calculan con `COUNT(DISTINCT id)` en Python, no por el LLM
