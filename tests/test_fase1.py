"""Fase 1: movimientos completos, montos en centavos, rangos de fechas, saludo y migración."""
from datetime import date, timedelta

import mongomock
import pytest
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from app import security
from app.config import Settings, get_settings
from app.database import get_db, get_presupuestos, get_transacciones
from app.main import create_app
from app.migraciones import migrar_centavos
from app.security import get_current_user
from app.servicios.finanzas import a_centavos
from tests.conftest import SheetsFalso, movimiento
from tests.test_sesion import hacer_app


@pytest.fixture
def db():
    return AsyncMongoMockClient()["t"]


@pytest.fixture
async def c(db):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_transacciones] = lambda: db.transacciones
    app.dependency_overrides[get_presupuestos] = lambda: db.presupuestos
    app.dependency_overrides[get_current_user] = lambda: "ana@example.com"
    app.state.sheets = SheetsFalso()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as cliente:
        yield cliente


HOY = date.today()
def dia(n): return (HOY - timedelta(days=n)).isoformat()


# ------------------------------------------------------------ movimientos completos
async def test_movimiento_con_hora_subcategoria_etiquetas_y_notas(c):
    r = await c.post("/api/transacciones", json=movimiento(hora="13:45", subcategoria="Almuerzo", etiquetas=["  viaje ", "trabajo", "viaje", ""], notas="Con el equipo"))
    assert r.status_code == 201
    t = r.json()
    assert (t["hora"], t["subcategoria"], t["etiquetas"], t["notas"]) == ("13:45", "Almuerzo", ["viaje", "trabajo"], "Con el equipo")
    sin = (await c.post("/api/transacciones", json=movimiento())).json()
    assert (sin["hora"], sin["subcategoria"], sin["etiquetas"], sin["notas"]) == (None, None, [], None)


@pytest.mark.parametrize("cambio", [{"hora": "25:00"}, {"hora": "7:5"}, {"hora": "12:60"}, {"etiquetas": [f"t{i}" for i in range(11)]}, {"etiquetas": ["y" * 31]}, {"notas": "n" * 501}, {"subcategoria": "s" * 51}])
async def test_campos_nuevos_se_validan(c, cambio):
    assert (await c.post("/api/transacciones", json=movimiento(**cambio))).status_code == 422


async def test_se_busca_por_etiqueta_nota_y_subcategoria(c):
    await c.post("/api/transacciones", json=movimiento(etiquetas=["viaje"], notas="boletos de bus", subcategoria="Pasajes", categoria="Transporte"))
    await c.post("/api/transacciones", json=movimiento(categoria="Cine"))
    for q in ("viaje", "boletos", "Pasajes"):
        assert (await c.get(f"/api/transacciones?q={q}")).json()["total"] == 1


async def test_csv_exporta_e_importa_los_campos_nuevos(c):
    await c.post("/api/transacciones", json=movimiento(hora="09:30", subcategoria="Desayuno", etiquetas=["a", "b"], notas="nota, con coma", monto=7.25))
    csv_ = (await c.get("/api/transacciones/exportar")).content.decode("utf-8-sig")
    assert "Hora,Subcategoría,Etiquetas,Notas" in csv_ and "09:30" in csv_ and '"a, b"' in csv_
    await c.delete("/api/transacciones/" + (await c.get("/api/transacciones")).json()["items"][0]["id"])
    r = (await c.post("/api/importar/movimientos", json={"csv": csv_})).json()
    assert r["importados"] == 1 and r["con_errores"] == 0
    t = (await c.get("/api/transacciones")).json()["items"][0]
    assert (t["hora"], t["subcategoria"], t["etiquetas"], t["notas"], t["monto"]) == ("09:30", "Desayuno", ["a", "b"], "nota, con coma", 7.25)


# ------------------------------------------------------------ centavos
def test_a_centavos_es_exacto():
    assert [a_centavos(x) for x in (0.1, 0.29, 1.005, 19.99, 1234.5, 0.01)] == [10, 29, 101, 1999, 123450, 1]


async def test_se_guarda_monto_centavos_y_los_antiguos_tambien_suman(c, db):
    await c.post("/api/transacciones", json=movimiento(monto=0.66, tipo="gasto", categoria="Comida"))
    nuevo = await db.transacciones.find_one({})
    assert nuevo["monto_centavos"] == 66 and isinstance(nuevo["monto_centavos"], int)
    await db.transacciones.insert_one({"usuario_id": "ana@example.com", "tipo": "gasto", "monto": 12.34, "categoria": "Comida", "metodo_pago": "Yape", "fecha": dia(0)})   # antiguo
    r = (await c.get("/api/transacciones/resumen")).json()
    assert r["periodo"]["gastos"] == 13.0 and r["gastos_por_categoria"] == {"Comida": 13.0} and r["total_movimientos"] == 2


async def test_editar_actualiza_los_centavos(c, db):
    i = (await c.post("/api/transacciones", json=movimiento(monto=10))).json()["id"]
    await c.put(f"/api/transacciones/{i}", json=movimiento(monto=10.07))
    assert (await db.transacciones.find_one({}))["monto_centavos"] == 1007
    assert (await c.get("/api/transacciones/resumen")).json()["periodo"]["gastos"] == 10.07


async def test_muchos_montos_pequenos_suman_exacto(c):
    for _ in range(200):
        await c.post("/api/transacciones", json=movimiento(monto=0.07, fecha=dia(0)))
    assert (await c.get("/api/transacciones/resumen")).json()["periodo"]["gastos"] == 14.0


def test_la_migracion_agrega_centavos_y_es_idempotente():
    col = mongomock.MongoClient()["t"]["transacciones"]
    col.insert_many([{"monto": 12.34}, {"monto": 0.1}, {"monto": 5, "monto_centavos": 500}])
    assert migrar_centavos(col) == {"pendientes": 2, "migrados": 0}                    # simulación: no toca nada
    assert col.count_documents({"monto_centavos": {"$exists": False}}) == 2
    assert migrar_centavos(col, aplicar=True) == {"pendientes": 2, "migrados": 2}
    assert sorted(d["monto_centavos"] for d in col.find()) == [10, 500, 1234]
    assert migrar_centavos(col, aplicar=True) == {"pendientes": 0, "migrados": 0}


# ------------------------------------------------------------ rangos de fechas
async def sembrar(c):
    for n, m in ((0, 10), (3, 20), (6, 30), (7, 40), (40, 50), (100, 60), (200, 70)):
        await c.post("/api/transacciones", json=movimiento(fecha=dia(n), monto=m))


async def test_rango_libre_en_lista_y_resumen(c):
    await sembrar(c)
    ult7 = f"desde={dia(6)}&hasta={dia(0)}"                                              # últimos 7 días: hoy-6 ... hoy
    assert (await c.get(f"/api/transacciones?{ult7}")).json()["total"] == 3
    assert (await c.get(f"/api/transacciones/resumen?{ult7}")).json()["periodo"]["gastos"] == 60
    seis_meses = f"desde={(HOY - timedelta(days=182)).isoformat()}&hasta={dia(0)}"
    assert (await c.get(f"/api/transacciones?{seis_meses}")).json()["total"] == 6
    r = (await c.get(f"/api/transacciones/resumen?desde={dia(6)}&hasta={dia(5)}")).json()       # solo el movimiento de hace 6 días
    assert r["periodo"]["gastos"] == 30 and r["total_movimientos"] == 1
    assert r["acumulado"]["gastos"] == 30 + 40 + 50 + 60 + 70                                   # el acumulado llega hasta el fin del rango


async def test_el_rango_incluye_el_dia_final(c):
    await c.post("/api/transacciones", json=movimiento(fecha="2026-03-31", monto=5))
    assert (await c.get("/api/transacciones?desde=2026-03-01&hasta=2026-03-31")).json()["total"] == 1
    assert (await c.get("/api/transacciones?desde=2026-04-01&hasta=2026-04-30")).json()["total"] == 0


@pytest.mark.parametrize("qs", ["desde=2026-03-01", "hasta=2026-03-01", "desde=2026-04-01&hasta=2026-03-01", "desde=2000-01-01&hasta=2026-01-01"])
async def test_rangos_invalidos(c, qs):
    assert (await c.get(f"/api/transacciones?{qs}")).status_code == 422


async def test_exportar_con_rango_y_las_pantallas_de_mes_no_se_rompen(c):
    await sembrar(c)
    r = await c.get(f"/api/transacciones/exportar?desde={dia(6)}&hasta={dia(0)}")
    assert r.status_code == 200 and dia(6) in r.headers["content-disposition"] and r.content.decode("utf-8-sig").count("\n") == 4   # encabezado + 3 filas
    pdf = await c.get(f"/api/transacciones/exportar-pdf?desde={dia(6)}&hasta={dia(0)}")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and dia(6) in pdf.headers["content-disposition"]
    rango = f"desde={dia(6)}&hasta={dia(0)}"
    for ruta in ("/api/transacciones/historial", "/api/disponible", "/api/asistente", "/api/estadisticas/comparacion", "/api/presupuestos/estado"):
        assert (await c.get(f"{ruta}?{rango}")).status_code == 200, ruta                  # ignoran el rango y usan el mes


# ------------------------------------------------------------ saludo con el nombre de Google
async def test_la_sesion_guarda_el_nombre_para_el_saludo(monkeypatch):
    monkeypatch.setattr(security.id_token, "verify_oauth2_token", lambda *a, **k: {"email": "ana@example.com", "email_verified": True, "given_name": "Ana"})
    async with AsyncClient(transport=ASGITransport(app=hacer_app()), base_url="http://test") as cliente:
        r = await cliente.post("/api/auth/google", json={"credential": "x" * 30})
        assert r.json() == {"email": "ana@example.com", "nombre": "Ana"}
        assert (await cliente.get("/api/auth/me")).json() == {"email": "ana@example.com", "nombre": "Ana"}
