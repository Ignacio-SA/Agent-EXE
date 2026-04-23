-- Crear tabla de memoria del chatbot (compatible con Microsoft Fabric Warehouse)
CREATE TABLE chatbot_memory (
    id         INT            NOT NULL,
    session_id VARCHAR(255)   NOT NULL,
    user_id    VARCHAR(255)   NOT NULL,
    context    VARCHAR(8000),
    summary    VARCHAR(1000),
    created_at DATETIME2(6)   NOT NULL,
    updated_at DATETIME2(6)   NOT NULL
);
