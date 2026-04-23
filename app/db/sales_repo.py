from datetime import datetime

from .connection import db


class SalesRepository:
    @staticmethod
    def get_sales(
        franchise_code: str,
        year: int = None,
        date_from: datetime = None,
        date_to: datetime = None,
    ) -> list[dict]:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "EXEC sp_GetSalesForChatbot @FranchiseCode=?, @Year=?, @DateFrom=?, @DateTo=?",
                (franchise_code, year, date_from, date_to),
            )
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            return [dict(zip(columns, row)) for row in rows]

    @staticmethod
    def get_sales_summary(
        franchise_code: str,
        year: int = None,
        date_from: datetime = None,
        date_to: datetime = None,
    ) -> dict:
        sales = SalesRepository.get_sales(franchise_code, year, date_from, date_to)
        if not sales:
            return {"total": 0, "items": []}
        return {
            "total": len(sales),
            "items": sales[:20],
        }


sales_repo = SalesRepository()
