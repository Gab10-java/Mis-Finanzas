"""Sincronización con Sheets: agrupación de solicitudes, reintentos y control de acceso."""
import asyncio
import threading
import time

import pytest
from mongomock_motor import AsyncMongoMockClient

from app.config import Settings
from app.sheets_sync import SheetsSync


@pytest.fixture
def col():
    return AsyncMongoMockClient()["test_db"]["transacciones"]


@pytest.fixture
def credenciales(tmp_path):
    ruta = tmp_path / "credenciales.json"
    ruta.write_text("{}")
    return ruta


def hacer_sync(col, credenciales, **extra) -> SheetsSync:
    settings = Settings(
        _env_file=None, mongo_url="mongodb://x", google_client_id="cid",
        sheet_id="HOJA", sheets_sync_email="ana@example.com",
        google_credentials_file=str(credenciales), **extra,
    )
    sync = SheetsSync(settings, col)
    sync.RETRASO_BASE = 0  # los tests no deben esperar
    return sync


async def sembrar(col, n, usuario="ana@example.com"):
    await col.insert_many([
        {"usuario_id": usuario, "tipo": "gasto", "monto": 1.0, "categoria": "X",
         "metodo_pago": "Yape", "fecha": f"2026-10-{i % 28 + 1:02d}"} for i in range(n)
    ])


async def test_solicitudes_simultaneas_se_agrupan_y_nunca_se_solapan(col, credenciales, monkeypatch):
    await sembrar(col, 5)
    sync = hacer_sync(col, credenciales)
    estado = {"escrituras": 0, "activas": 0, "max_activas": 0, "filas": []}
    cerrojo = threading.Lock()

    def escribir_falso(filas):
        with cerrojo:
            estado["activas"] += 1
            estado["max_activas"] = max(estado["max_activas"], estado["activas"])
        time.sleep(0.05)  # simula la latencia de Google
        with cerrojo:
            estado["activas"] -= 1
            estado["escrituras"] += 1
            estado["filas"].append(len(filas))

    monkeypatch.setattr(sync, "_escribir", escribir_falso)
    await asyncio.gather(*(sync.solicitar("ana@example.com") for _ in range(20)))

    assert estado["max_activas"] == 1          # jamás dos escrituras a la vez (antes: carrera)
    assert 1 <= estado["escrituras"] <= 2      # 20 cambios -> 1 o 2 sincronizaciones, no 20
    assert estado["filas"][-1] == 6            # encabezado + 5 movimientos


async def test_el_servidor_no_se_congela_mientras_se_sincroniza(col, credenciales, monkeypatch):
    """gspread bloquea; si corriera en el bucle de eventos, nada más avanzaría."""
    await sembrar(col, 1)
    sync = hacer_sync(col, credenciales)
    monkeypatch.setattr(sync, "_escribir", lambda filas: time.sleep(0.3))

    ticks = 0
    async def latido():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    tarea = asyncio.create_task(latido())
    await sync.solicitar("ana@example.com")
    tarea.cancel()
    assert ticks >= 10  # el bucle siguió vivo durante los 0.3 s de escritura


async def test_reintenta_y_termina_bien(col, credenciales, monkeypatch):
    await sembrar(col, 2)
    sync = hacer_sync(col, credenciales)
    intentos = []

    def inestable(filas):
        intentos.append(1)
        if len(intentos) < 3:
            raise ConnectionError("red caída")

    monkeypatch.setattr(sync, "_escribir", inestable)
    await sync.solicitar("ana@example.com")
    assert len(intentos) == 3


async def test_si_agota_reintentos_no_propaga_el_error(col, credenciales, monkeypatch):
    await sembrar(col, 1)
    sync = hacer_sync(col, credenciales)
    def siempre_falla(filas): raise ConnectionError("sin red")
    monkeypatch.setattr(sync, "_escribir", siempre_falla)
    await sync.solicitar("ana@example.com")  # no debe lanzar excepción hacia la petición HTTP


async def test_solo_sincroniza_al_dueno_de_la_hoja(col, credenciales, monkeypatch):
    """Bug original: cualquier cuenta de Google pisaba la hoja del dueño."""
    await sembrar(col, 1, usuario="intruso@gmail.com")
    sync = hacer_sync(col, credenciales)
    llamadas = []
    monkeypatch.setattr(sync, "_escribir", lambda filas: llamadas.append(filas))
    await sync.solicitar("intruso@gmail.com")
    assert llamadas == []
    await sync.solicitar("ANA@example.com")   # mayúsculas no importan
    assert len(llamadas) == 1


async def test_sin_configuracion_o_sin_credenciales_queda_desactivado(col, tmp_path):
    sin_config = SheetsSync(Settings(_env_file=None, mongo_url="x", google_client_id="c"), col)
    assert not sin_config.habilitado_para("ana@example.com")
    sin_archivo = SheetsSync(Settings(
        _env_file=None, mongo_url="x", google_client_id="c", sheet_id="H",
        sheets_sync_email="ana@example.com", google_credentials_file=str(tmp_path / "no_existe.json"),
    ), col)
    assert not sin_archivo.habilitado_para("ana@example.com")


def test_escribir_orden_de_llamadas_a_gspread(col, credenciales):
    """Primero update, luego borrar sobrantes, y el formato de cabecera solo una vez."""
    sync = hacer_sync(col, credenciales)
    llamadas = []

    class HojaFalsa:
        def update(self, **kw): llamadas.append(("update", kw["range_name"], kw["raw"], len(kw["values"])))
        def batch_clear(self, rangos): llamadas.append(("batch_clear", rangos))
        def format(self, rango, fmt): llamadas.append(("format", rango))

    sync._hoja = HojaFalsa()
    sync._escribir([["h"], ["a"], ["b"]])
    sync._escribir([["h"], ["a"]])
    assert llamadas == [
        ("update", "A1", True, 3), ("batch_clear", ["A4:G"]), ("format", "A1:G1"),
        ("update", "A1", True, 2), ("batch_clear", ["A3:G"]),   # sin format la 2ª vez
    ]
