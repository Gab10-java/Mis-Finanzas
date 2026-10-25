"""Sesión propia por cookie: login único con Google, acceso con cookie, protección CSRF y cierre."""
import pytest
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from app import security
from app.config import Settings, get_settings
from app.database import get_db, get_transacciones
from app.main import create_app
from tests.conftest import SheetsFalso, movimiento

CABECERA = {"X-Requested-With": "fetch"}


def hacer_app():
    settings = Settings(_env_file=None, mongo_url="mongodb://x", google_client_id="cid", session_secret="clave-de-prueba")
    db = AsyncMongoMockClient()["t"]
    col = db["transacciones"]
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_transacciones] = lambda: col
    app.dependency_overrides[get_db] = lambda: db
    app.state.sheets = SheetsFalso()
    return app


def google_ok(monkeypatch):
    monkeypatch.setattr(security.id_token, "verify_oauth2_token",
                        lambda *a, **k: {"email": "ana@example.com", "email_verified": True})


def sin_google(monkeypatch):
    def prohibido(*a, **k):
        raise AssertionError("no debería consultar a Google: debe bastar la cookie")
    monkeypatch.setattr(security.id_token, "verify_oauth2_token", prohibido)


async def entrar(c, monkeypatch):
    google_ok(monkeypatch)
    r = await c.post("/api/auth/google", json={"credential": "x" * 30})
    assert r.status_code == 200
    return r


async def test_login_entrega_cookie_segura_y_luego_basta_la_cookie(monkeypatch):
    async with AsyncClient(transport=ASGITransport(app=hacer_app()), base_url="http://test") as c:
        r = await entrar(c, monkeypatch)
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=lax" in cookie and "path=/api" in cookie
        sin_google(monkeypatch)
        assert (await c.get("/api/auth/me")).json() == {"email": "ana@example.com", "nombre": ""}
        assert (await c.get("/api/transacciones")).status_code == 200


async def test_con_cookie_las_escrituras_exigen_la_cabecera(monkeypatch):
    async with AsyncClient(transport=ASGITransport(app=hacer_app()), base_url="http://test") as c:
        await entrar(c, monkeypatch)
        sin_google(monkeypatch)
        assert (await c.post("/api/transacciones", json=movimiento())).status_code == 403
        assert (await c.post("/api/transacciones", json=movimiento(), headers=CABECERA)).status_code == 201


async def test_logout_borra_la_sesion(monkeypatch):
    async with AsyncClient(transport=ASGITransport(app=hacer_app()), base_url="http://test") as c:
        await entrar(c, monkeypatch)
        assert (await c.post("/api/auth/logout", headers=CABECERA)).status_code == 204
        assert (await c.get("/api/auth/me")).status_code == 401


async def test_login_con_token_de_google_invalido_da_401(monkeypatch):
    def invalido(*a, **k):
        raise ValueError("Token expired")
    monkeypatch.setattr(security.id_token, "verify_oauth2_token", invalido)
    async with AsyncClient(transport=ASGITransport(app=hacer_app()), base_url="http://test") as c:
        assert (await c.post("/api/auth/google", json={"credential": "x" * 30})).status_code == 401


def test_firma_vencimiento_y_manipulacion(monkeypatch):
    s = Settings(_env_file=None, mongo_url="x", google_client_id="c", session_secret="k")
    token = security.crear_sesion("ana@example.com", s)
    assert security.leer_sesion(token, s) == "ana@example.com"
    assert security.leer_sesion(token[:-2] + "xx", s) is None                                   # firma alterada
    assert security.leer_sesion(token, s.model_copy(update={"session_secret": "otra"})) is None  # otra clave
    assert security.leer_sesion("basura", s) is None
    monkeypatch.setattr(security.time, "time", lambda: 9_999_999_999)
    assert security.leer_sesion(token, s) is None                                                # vencida
