"""Inicio de sesión + clonado de la hoja plantilla por usuario (regresión del error 500 en el login)."""
import asyncio
import time
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient
from pymongo.errors import PyMongoError

from app import security
from app.config import Settings, get_settings
from app.database import get_db, get_transacciones
from app.main import create_app
from app.routers import auth
from tests.conftest import SheetsFalso


@pytest.fixture
async def e(tmp_path, monkeypatch):
    cred = tmp_path / "credenciales.json"
    cred.write_text("{}")
    settings = Settings(_env_file=None, mongo_url="mongodb://x", google_client_id="cid", session_secret="clave",
                        template_sheet_id="PLANTILLA", google_credentials_file=str(cred))
    db = AsyncMongoMockClient()["t"]
    await db.usuarios.create_index("email", unique=True)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_transacciones] = lambda: db.transacciones
    app.state.sheets = SheetsFalso()
    monkeypatch.setattr(security.id_token, "verify_oauth2_token",
                        lambda *a, **k: {"email": "ana@example.com", "email_verified": True, "given_name": "Ana"})
    llamadas = []

    def clonar(email, s):
        llamadas.append(email)
        return f"HOJA-{len(llamadas)}"

    monkeypatch.setattr(auth, "_clonar_sync", clonar)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield SimpleNamespace(c=c, db=db, llamadas=llamadas, mp=monkeypatch, settings=settings, app=app)


async def entrar(c):
    return await c.post("/api/auth/google", json={"credential": "x" * 30})


async def test_el_login_no_da_500_y_clona_la_hoja_una_sola_vez(e):
    r = await entrar(e.c)
    assert r.status_code == 200 and r.json() == {"email": "ana@example.com", "nombre": "Ana"}
    assert e.llamadas == ["ana@example.com"]
    u = await e.db.usuarios.find_one({"email": "ana@example.com"})
    assert u["sheet_id"] == "HOJA-1" and "clonando_desde" not in u
    assert (await entrar(e.c)).status_code == 200 and len(e.llamadas) == 1                 # al volver a entrar NO se clona otra vez
    assert await e.db.usuarios.count_documents({}) == 1


async def test_si_falla_la_clonacion_se_entra_igual_y_se_reintenta_despues(e):
    def falla(email, s):
        e.llamadas.append("intento")
        raise RuntimeError("Drive no disponible")
    e.mp.setattr(auth, "_clonar_sync", falla)
    assert (await entrar(e.c)).status_code == 200
    u = await e.db.usuarios.find_one({"email": "ana@example.com"})
    assert "sheet_id" not in u and "clonando_desde" not in u                                # la reserva se liberó
    e.mp.setattr(auth, "_clonar_sync", lambda email, s: "HOJA-OK")
    await entrar(e.c)
    assert (await e.db.usuarios.find_one({}))["sheet_id"] == "HOJA-OK"


async def test_tres_inicios_simultaneos_clonan_una_sola_hoja(e):
    def lenta(email, s):
        e.llamadas.append(email)
        time.sleep(0.2)
        return "HOJA-1"
    e.mp.setattr(auth, "_clonar_sync", lenta)
    respuestas = await asyncio.gather(entrar(e.c), entrar(e.c), entrar(e.c))
    assert [r.status_code for r in respuestas] == [200, 200, 200] and len(e.llamadas) == 1
    assert await e.db.usuarios.count_documents({}) == 1


async def test_sin_plantilla_configurada_se_entra_sin_clonar(e):
    e.settings.template_sheet_id = ""
    assert (await entrar(e.c)).status_code == 200 and e.llamadas == []


async def test_si_mongo_falla_al_buscar_al_usuario_igual_se_entra(e):
    class DbRota:
        class usuarios:
            @staticmethod
            async def find_one(*a, **k):
                raise PyMongoError("sin conexión")
    e.app.dependency_overrides[get_db] = lambda: DbRota()
    r = await entrar(e.c)
    assert r.status_code == 200 and "sesion" in r.headers["set-cookie"] and e.llamadas == []
