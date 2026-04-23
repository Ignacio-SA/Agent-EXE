import csv
import io

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..db.sales_repo import sales_repo
from ..agents.data_agent import data_agent

router = APIRouter(prefix="/debug", tags=["debug"])


class QueryRequest(BaseModel):
    franchise_id: str
    sql: str
    date_from: str | None = None  # "YYYY-MM-DD"
    date_to: str | None = None    # "YYYY-MM-DD"


@router.post("/query/csv")
async def run_query_csv(request: QueryRequest):
    """
    Ejecuta un SQL crudo contra los datos del SP y devuelve un CSV descargable.
    Útil para validar las consultas generadas por el agente.

    Ejemplo de body:
    {
      "franchise_id": "4066b2def050495a8fc9ff8c0cb3f8f4",
      "sql": "SELECT * FROM ventas WHERE Type != '2' LIMIT 100",
      "date_from": "2026-03-25",
      "date_to": "2026-03-25"
    }
    """
    try:
        from datetime import datetime

        date_from = datetime.strptime(request.date_from, "%Y-%m-%d") if request.date_from else None
        date_to   = datetime.strptime(request.date_to + " 23:59:59", "%Y-%m-%d %H:%M:%S") if request.date_to else None

        sales = sales_repo.get_sales(request.franchise_id, date_from=date_from, date_to=date_to)
        mem_conn = data_agent._load_into_memory(sales)

        columns, rows = data_agent._execute_sql(mem_conn, request.sql)
        mem_conn.close()

        if not columns:
            raise HTTPException(status_code=400, detail=str(rows[0]) if rows else "Sin resultados")

        # Generar CSV en memoria con BOM UTF-8 para que Excel lo abra correctamente
        output = io.StringIO()
        output.write("\ufeff")  # BOM UTF-8
        writer = csv.writer(output)
        writer.writerow(columns)
        writer.writerows(rows)
        output.seek(0)

        filename = f"query_{request.date_from or 'all'}.csv"
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/token-logs")
async def get_token_logs(session_id: str = None):
    """
    GET /debug/token-logs?session_id=xxx  — Log de tokens por consulta.
    Sin session_id devuelve el historial completo de todas las sesiones.
    """
    from ..db.memory_repo import memory_repo as repo
    rows = repo.get_query_logs(session_id)
    total_in  = sum(r["input_tokens"]  for r in rows)
    total_out = sum(r["output_tokens"] for r in rows)
    return {
        "total_queries": len(rows),
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "total_tokens": total_in + total_out,
        "rows": rows,
    }


@router.post("/query/json")
async def run_query_json(request: QueryRequest):
    """Igual que /csv pero devuelve JSON — útil para ver resultados en el browser."""
    try:
        from datetime import datetime

        date_from = datetime.strptime(request.date_from, "%Y-%m-%d") if request.date_from else None
        date_to   = datetime.strptime(request.date_to + " 23:59:59", "%Y-%m-%d %H:%M:%S") if request.date_to else None

        sales = sales_repo.get_sales(request.franchise_id, date_from=date_from, date_to=date_to)
        mem_conn = data_agent._load_into_memory(sales)

        columns, rows = data_agent._execute_sql(mem_conn, request.sql)
        mem_conn.close()

        if not columns:
            raise HTTPException(status_code=400, detail=str(rows[0]) if rows else "Sin resultados")

        return {
            "total_rows": len(rows),
            "columns": columns,
            "rows": [dict(zip(columns, row)) for row in rows],
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
