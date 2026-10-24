import os

# Variables mínimas ANTES de importar la app (Settings las exige). El entorno manda sobre .env.
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client.apps.googleusercontent.com")
os.environ["ALLOWED_EMAILS"] = ""
os.environ["SHEETS_SYNC_EMAIL"] = ""
os.environ["SHEET_ID"] = ""
os.environ["CORS_ORIGINS"] = ""

import pytest
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from app.database import get_transacciones
from app.main import create_app
from app.security import get_current_user


class SheetsFalso:
    """Registra las solicitudes de sincronización sin tocar Google."""

    def __init__(self):
        self.llamadas: list[str] = []

    async def solicitar(self, usuario: str) -> None:
        self.llamadas.append(usuario)


@pytest.fixture
def col():
    return AsyncMongoMockClient()["test_db"]["transacciones"]


@pytest.fixture
def sesion():
    """Usuario "logueado" mutable: los tests pueden cambiarlo para probar aislamiento."""
    return {"email": "ana@example.com"}


@pytest.fixture
def app(col, sesion):
    app = create_app()
    app.dependency_overrides[get_transacciones] = lambda: col
    app.dependency_overrides[get_current_user] = lambda: sesion["email"]
    app.state.sheets = SheetsFalso()
    return app


@pytest.fixture
async def cliente(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def movimiento(**extra) -> dict:
    base = {
        "tipo": "gasto", "monto": 10.5, "categoria": "Supermercado",
        "metodo_pago": "Yape", "fecha": "2026-10-15",
    }
    return {**base, **extra}
