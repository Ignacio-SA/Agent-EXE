# Registro de Cambios: Trazabilidad, Debugging Seguro y Correcciones

Este resumen documenta los cambios estructurales aplicados localmente frente a la rama `main`. El objetivo de estas implementaciones fue diagnosticar con precisión fallas del flujo Text-to-SQL, proveer completa visibilidad en consola de cada paso del agente y proteger excepciones internas.

## 1. Ruteo Global y Sistema de Memoria (`app/routers/chat.py`)
Se implementaron cabeceras visuales para demarcar el inicio o fin del ciclo de vida de un request.

* **Nuevos Logs agregados:**
  * `[Router Global]` Nueva petición recibida (informando el número de ID de sesión).
  * `[MemoryAgent]` Rescatando y exponiendo en consola si se adjuntó historial previo contextual (para saber exactamente de qué "se acuerda" la IA entre turnos).

## 2. Visión del Orquestador (`app/agents/orchestrator.py`)
Se añadió trazabilidad para interceptar la respuesta de *Claude Sonnet* (Orquestador principal) en el instante que decide la derivación:

* **Nuevos Logs agregados:**
  * `[Orchestrator]` Mensaje recibido a la espera de derivación.
  * `[Orchestrator]` Exposición pública del "Agente Final" seleccionado y, fundamental para auditar, **la Razón Pensada por el Modelo** (el razonamiento psicológico por el que el LLM optó por *data* vs *interaction*).
  * Logs secundarios detallando si el ruteo se dio por *Default/Keyword Fallback* en caso de que falle la API principal.

## 3. Radiografía Text-to-SQL y Extracción de Datos (`app/agents/data_agent.py`)
Dado que `DataAgent` es el motor central lógico y el factor de riesgo, se instrumentó un verbose a nivel de "Pasos", junto a una protección masiva.

* **Añadidos Principales y Trazabilidad:**
  * **(Paso 1)** `[DataAgent]` Extracción y parseo de las de Fechas/Horarios extraídas por la Inteligencia Artificial.
  * **(Paso 2)** `[DataAgent]` Llamada de consulta al Store Procedure (Fabric) para la Franquicia identificada.
  * **(Paso 3)** `[DataAgent]` Volcado de tablas hacia la SQLite RAM temporal, acompañada de una **vista previa tabular dinámica** en consola que imprime visualmente los primeros 5 renglones exactos que recibe de Azure.
  * **(Paso 4)** `[DataAgent]` Impresión inmediata del Código de Inteligencia Artificial (SQL) puro generado por el LLM.
  * **(Paso 5)** `[DataAgent]` Ejecución estricta del SQL recién creado y cantidad de filas devueltas generadas de regreso del motor SQLite local.
  * **(Paso 6)** `[DataAgent]` Llamado final para traducción humano-natural y respuesta al chat web.




## 5. Trazabilidad del Sistema de Entrenamiento (`TrainingAgent` y Feedback Global)
Para interceptar adecuadamente la compresión del LLM secundario (`Claude Haiku`) encargado de reaccionar proactivamente al humano, se agregaron cabeceras visuales para demarcar el ciclo de "Feedback":

* **Nuevos Logs agregados:**
  * `[Feedback Global]` Informa la intercepción del texto exacto que el humano ha dictado señalándolo como feedback.
  * `[Feedback Global]` Reporta cuántos mensajes de contexto reconstruido se envían a empaquetar para dárselos al AI.
  * `[TrainingAgent]` Notifica el comienzo del "(Paso 1) Extrayendo contexto y analizando retroalimentación humana profunda".
  * `[TrainingAgent]` Informa la disección psíquica del caso *(Paso 2)* clasificándolo en: `POSITIVO/NEGATIVO` y cuál determinó que es el `COMPONENTE` fallido (ej: `data_agent`).
  * `[TrainingAgent]` Publica *(Paso 3)* su conclusión detallando la causa raíz del fallo / o del acierto.
  * `[TrainingAgent]` Publica *(Paso 4)* la sugerencia que inyectará.
  * `[TrainingAgent]` Confirma en pantalla que la sugerencia impactó en RAM y disco y será usada de acá de por vida (hasta ser manualmente aprobada).

