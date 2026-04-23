# Reglas de Negocio — Chatbot de Ventas

## Tabla `ventas` — Descripción de columnas

| Columna            | Tipo    | Descripción                                      |
|--------------------|---------|--------------------------------------------------|
| id                 | TEXT    | Identificador único de la transacción (puede repetirse si hay múltiples ítems en una venta) |
| FranchiseeCode     | TEXT    | Código interno del franquiciado                  |
| ShiftCode          | TEXT    | Código de turno                                  |
| PosCode            | TEXT    | Código del punto de venta (POS)                  |
| UserName           | TEXT    | Nombre del vendedor                              |
| SaleDateTimeUtc    | TEXT    | Fecha y hora de la venta en horario Argentina (UTC-3), formato: YYYY-MM-DD HH:MM:SS.ffffff |
| Quantity           | REAL    | Cantidad de unidades vendidas                    |
| ArticleId          | TEXT    | Código del artículo                              |
| ArticleDescription | TEXT    | Descripción del artículo                         |
| TypeDetail         | TEXT    | Tipo de detalle del ítem                         |
| UnitPriceFix       | REAL    | Precio unitario del ítem                         |
| Type               | TEXT    | Tipo de registro (ver regla crítica abajo)       |
| CtaChannel         | TEXT    | Canal de venta: 'Tienda', 'Delivery', 'Take Away', 'Tienda *' (sin datos de canal) |
| VtaOperation       | TEXT    | Tipo de operación: 'Socios' (ClubGrido) o 'No Socios' |
| Plataforma         | TEXT    | Plataforma delivery: 'PediGrido', 'PedidosYa', 'Rappi'. NULL si es venta directa |
| FormaPago          | TEXT    | Medio de pago del ticket. 'Múltiples medios de pago' si usó más de uno. NULL si sin datos |

---

## REGLA CRÍTICA: Campo `Type`

El campo `Type` indica el rol de cada fila dentro de una transacción:

| Valor | Significado                  | Uso en cálculos                                       |
|-------|------------------------------|-------------------------------------------------------|
| `0`   | Venta regular (sin promo)    | **INCLUIR** en conteos y sumas de ventas              |
| `1`   | Ítem dentro de una promoción | **INCLUIR** en conteos y sumas de ventas              |
| `2`   | Cabecera de promoción        | **EXCLUIR** — es el precio total de la promo, ya está representado por los ítems Type=1 que la componen |

### Por qué importa
Una misma transacción (`id`) puede tener:
- Varios ítems con `Type=1` (los productos reales vendidos, con su precio individual)
- Una fila con `Type=2` que representa el total de la promoción

Si se suma `UnitPriceFix` sin filtrar, el monto de la promo se cuenta **dos veces**.

### Regla de SQL
**SIEMPRE** agregar `WHERE Type != '2'` (o `AND Type != '2'`) cuando se calculen:
- Total de ventas (`SUM(UnitPriceFix * Quantity)`)
- Cantidad de transacciones (`COUNT(DISTINCT id)`)
- Productos más vendidos (`SUM(Quantity)`)
- Cualquier agregación de montos o cantidades

**Ejemplo correcto:**
```sql
SELECT ArticleDescription, SUM(CAST(Quantity AS REAL)) AS total_vendido
FROM ventas
WHERE Type != '2'
GROUP BY ArticleDescription
ORDER BY total_vendido DESC
LIMIT 10
```

**Ejemplo incorrecto (doble conteo):**
```sql
SELECT ArticleDescription, SUM(CAST(Quantity AS REAL)) AS total_vendido
FROM ventas  -- Sin filtro por Type → cuenta la cabecera de promo dos veces
GROUP BY ArticleDescription
```

---

## Otras reglas

- Las fechas en `SaleDateTimeUtc` ya están en **horario Argentina (UTC-3)**.
- Para filtrar por fecha usar `DATE(SaleDateTimeUtc)`.
- Para filtrar por mes usar `strftime('%Y-%m', SaleDateTimeUtc)`.
- Para filtrar por año usar `strftime('%Y', SaleDateTimeUtc)`.
- **Nunca** usar `YEAR()`, `MONTH()`, `DATEPART()` — no existen en SQLite.
- El precio total de una venta es `SUM(UnitPriceFix * Quantity)` filtrando `Type != '2'`.
- Para buscar artículos por nombre **siempre** usar `LOWER(ArticleDescription) LIKE LOWER('%texto%')` — nunca comparación exacta, el usuario puede escribir en minúsculas y el dato tener mayúsculas.
- Para franjas horarias usar `strftime('%H', SaleDateTimeUtc)` que devuelve la hora en formato '00'-'23'.
- Para agrupar por franja horaria: `GROUP BY strftime('%H', SaleDateTimeUtc) ORDER BY COUNT(*) DESC`.

---

## REGLA DE PRESENTACIÓN: Cómo mostrar resultados al usuario

El usuario es un **franquiciado o vendedor**, no un analista ni desarrollador. Las respuestas deben ser claras y comerciales.

### Nunca mostrar al usuario:
- Valores técnicos del campo `Type` (no escribir "Type=1", "Type=2", "tipo 1", "tipo 2")
- Nombres de columnas internas (`FranchiseeCode`, `ShiftCode`, `PosCode`, `TypeDetail`, `ArticleId`)
- Códigos UUID o hexadecimales de IDs internos
- Menciones a la estructura de la base de datos
- Valor `'Tienda *'` — mostrarlo simplemente como "Tienda"

### Cómo presentar la información:
| Concepto técnico         | Cómo mostrarlo al usuario                          |
|--------------------------|----------------------------------------------------|
| Ítems con Type=1         | "Productos vendidos" o simplemente listarlos       |
| Fila con Type=2          | "Promoción aplicada: [descripción de la promo]"    |
| FranchiseeCode           | Omitir o mostrar como "Franquicia"                 |
| ShiftCode                | "Turno"                                            |
| PosCode                  | "Caja" o "Punto de venta"                          |
| UserName                 | "Vendedor/a"                                       |
| UnitPriceFix             | "Precio"                                           |
| SaleDateTimeUtc          | Fecha y hora formateada en español                 |
| CtaChannel               | "Canal" o "tipo de venta"                          |
| VtaOperation             | "Operación Socios" / "No Socios"                   |
| Plataforma               | Mostrar tal cual (PedidosYa, PediGrido, Rappi)     |
| FormaPago                | "Medio de pago"                                    |

### Cómo presentar franjas horarias:
No mostrar el número de hora crudo. Convertirlo a rango legible:
- `'14'` → "entre las 14:00 y 15:00 hs"
- `'09'` → "entre las 9:00 y 10:00 hs"

Ejemplo correcto:
> Las ventas de "1 Kilo" se concentran entre las **21:00 y 22:00 hs**, con 47 unidades vendidas.

Ejemplo incorrecto:
> La franja horaria con más ventas es la hora 21.

---

### Ejemplo de respuesta correcta para una venta con promo:
> **Venta del 7 de abril a las 21:51 — Vendedora: Luz Chirino**
>
> Productos:
> - 1 Kilo — $13.500
> - 1/2 Kilo — $7.800
> - 1/2 Kilo — gratis (incluido en la promoción)
>
> 🎁 Promoción aplicada: "Comprando 1 kilo y medio, llevás medio gratis"
>
> **Total: $21.300**

---

*Este archivo se actualiza a medida que se descubren nuevas reglas de negocio.*
