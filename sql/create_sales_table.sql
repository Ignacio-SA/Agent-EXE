-- Tabla de ventas para el chatbot de franquicias (compatible con Microsoft Fabric Warehouse)
CREATE TABLE Sales (
    id           INT            NOT NULL,
    [date]       DATETIME2(6)   NOT NULL,
    product_id   INT            NOT NULL,
    amount       DECIMAL(18, 2) NOT NULL,
    customer     VARCHAR(255)   NOT NULL,
    status       VARCHAR(50)    NOT NULL,
    franchise_id VARCHAR(255)   NOT NULL
);
