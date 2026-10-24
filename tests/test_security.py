"""Autenticación: se simula la respuesta de Google, sin red."""
import pytest
from google.auth import exceptions as google_exceptions
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from app import security
from app.config import Settings, get_settings
from app.database import get_transacciones
from app.main import create_app

from tests.conftest import SheetsFalso


def hacer_app(**ajustes):
    settings = Settings(_env_file=None, mongo_url="mongodb://x", google_client_id="cid", **ajustes)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_transacciones] = lambda: AsyncMongoMockClient()["t"]["transacciones"]
    app.state.sheets = SheetsFalso()
    return app


async def llamar(app, token="token-de-prueba"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.get("/api/transacciones", headers=headers)


def google_responde(monkeypatch, resultado):
    def falso(token, request, audience, clock_skew_in_seconds=0):
        assert audience == "cid"  # se valida contra NUESTRO client id
        if isinstance(resultado, Exception):
            raise resultado
        return resultado
    monkeypatch.setattr(security.id_token, "verify_oauth2_token", falso)


async def test_sin_cabecera_authorization_401():
    r = await llamar(hacer_app(), token=None)
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"


async def test_token_valido_200(monkeypatch):
    google_responde(monkeypatch, {"email": "ana@example.com", "email_verified": True})
    assert (await llamar(hacer_app())).status_code == 200


async def test_token_invalido_o_expirado_401(monkeypatch):
    google_responde(monkeypatch, ValueError("Token expired"))
    assert (await llamar(hacer_app())).status_code == 401


async def test_google_caido_da_503_y_no_401(monkeypatch):
    """Antes cualquier fallo de red salía como error 500 sin explicación."""
    google_responde(monkeypatch, google_exceptions.TransportError("sin red"))
    assert (await llamar(hacer_app())).status_code == 503


async def test_correo_no_verificado_401(monkeypatch):
    google_responde(monkeypatch, {"email": "ana@example.com", "email_verified": False})
    assert (await llamar(hacer_app())).status_code == 401


async def test_lista_blanca_permite_y_bloquea(monkeypatch):
    app = hacer_app(allowed_emails="Ana@Example.com, otra@x.com")  # sin distinguir mayúsculas
    google_responde(monkeypatch, {"email": "ana@example.com", "email_verified": True})
    assert (await llamar(app)).status_code == 200
    google_responde(monkeypatch, {"email": "intruso@gmail.com", "email_verified": True})
    assert (await llamar(app)).status_code == 403


async def test_sin_lista_blanca_cualquier_cuenta_entra(monkeypatch):
    google_responde(monkeypatch, {"email": "cualquiera@gmail.com", "email_verified": True})
    assert (await llamar(hacer_app())).status_code == 200
