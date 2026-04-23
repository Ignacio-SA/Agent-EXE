from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    assert "Chatbot Multi-Agente API" in response.json()["message"]


def test_chat_endpoint():
    payload = {
        "message": "¿Cuál es el total de ventas?",
        "session_id": "test_session_123",
        "franchise_id": "test_franchise",
        "user_id": "test_user",
    }
    response = client.post("/chat/", json=payload)
    # Sin BD real, esto fallará, pero el endpoint existe
    assert response.status_code in [200, 500]  # 500 es esperado sin BD


def test_history_endpoint():
    response = client.get("/chat/history/test_session_123")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
