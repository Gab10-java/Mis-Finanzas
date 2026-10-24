"""Fase 2: cuentas, transferencias, metas y dinero disponible. Los cálculos críticos se prueban aquí."""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from app.database import get_db, get_presupuestos, get_transacciones
from app.main import create_app
from app.security import get_current_user
from app.servicios import finanzas as fin
from tests.conftest import SheetsFalso, movimiento

HOY = date.today().isoformat()


@pytest.fixture
def usuario():
    return {"email": "ana@example.com"}


@pytest.fixture
async def c(usuario):
    db = AsyncMongoMockClient()["t"]
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_transacciones] = lambda: db.transacciones
    app.dependency_overrides[get_presupuestos] = lambda: db.presupuestos
    app.dependency_overrides[get_current_user] = lambda: usuario["email"]
    app.state.sheets = SheetsFalso()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as cliente:
        yield cliente


async def cuenta(c, nombre, saldo=0, tipo="efectivo"):
    r = await c.post("/api/cuentas", json={"nombre": nombre, "tipo": tipo, "saldo_inicial": saldo})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def saldo(c, cid):
    return next(x["saldo"] for x in (await c.get("/api/cuentas")).json() if x["id"] == cid)


async def mov(c, cid, **extra):
    r = await c.post("/api/transacciones", json=movimiento(cuenta_id=cid, fecha=HOY, **extra))
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ------------------------------------------------------------ saldos
async def test_ingreso_gasto_edicion_y_eliminacion_actualizan_el_saldo(c):
    cid = await cuenta(c, "Efectivo", 100)
    await mov(c, cid, tipo="ingreso", monto=50, categoria="Sueldo")
    gasto = await mov(c, cid, tipo="gasto", monto=30)
    assert await saldo(c, cid) == 120
    r = await c.put(f"/api/transacciones/{gasto}", json=movimiento(cuenta_id=cid, fecha=HOY, monto=10))
    assert r.status_code == 200 and await saldo(c, cid) == 140       # editar recalcula
    assert (await c.delete(f"/api/transacciones/{gasto}")).status_code == 204
    assert await saldo(c, cid) == 150                                 # eliminar revierte


async def test_diez_ingresos_de_10_centavos_suman_exactamente_un_sol(c):
    cid = await cuenta(c, "Efectivo")
    for _ in range(10):
        await mov(c, cid, tipo="ingreso", monto=0.1, categoria="Propina")
    assert await saldo(c, cid) == 1.0            # con float puro daría 0.9999999999999999


# ------------------------------------------------------------ transferencias
async def test_transferencia_mueve_dinero_sin_crear_ingresos_ni_gastos(c):
    a, b = await cuenta(c, "Efectivo", 100), await cuenta(c, "Yape")
    r = await c.post("/api/transferencias", json={"origen": a, "destino": b, "monto": 40, "fecha": HOY})
    assert r.status_code == 201
    assert (await saldo(c, a), await saldo(c, b)) == (60, 40)
    resumen = (await c.get("/api/transacciones/resumen")).json()
    assert resumen["periodo"]["ingresos"] == 0 and resumen["periodo"]["gastos"] == 0 and resumen["total_movimientos"] == 0


async def test_transferencia_rechaza_casos_invalidos(c):
    a, b = await cuenta(c, "Efectivo", 10), await cuenta(c, "Yape")
    base = {"monto": 5, "fecha": HOY}
    assert (await c.post("/api/transferencias", json={**base, "origen": a, "destino": a})).status_code == 422
    assert (await c.post("/api/transferencias", json={"origen": a, "destino": b, "monto": 999, "fecha": HOY})).status_code == 409
    assert (await c.post("/api/transferencias", json={**base, "origen": a, "destino": "a" * 24})).status_code == 404
    assert (await c.post("/api/transferencias", json={"origen": a, "destino": b, "monto": 0, "fecha": HOY})).status_code == 422


# ------------------------------------------------------------ metas
async def test_aportar_a_una_meta_no_es_ingreso_y_retirar_devuelve_el_dinero(c):
    cid = await cuenta(c, "Efectivo", 500)
    meta = (await c.post("/api/metas", json={"nombre": "Laptop", "objetivo": 1000})).json()["id"]
    r = await c.post(f"/api/metas/{meta}/aportes", json={"tipo": "aporte", "monto": 200, "cuenta_id": cid, "fecha": HOY})
    assert r.status_code == 201 and r.json()["ahorrado"] == 200 and r.json()["porcentaje"] == 20
    assert await saldo(c, cid) == 300
    assert (await c.get("/api/transacciones/resumen")).json()["periodo"]["ingresos"] == 0
    await c.post(f"/api/metas/{meta}/aportes", json={"tipo": "retiro", "monto": 50, "cuenta_id": cid, "fecha": HOY})
    assert await saldo(c, cid) == 350
    assert (await c.post(f"/api/metas/{meta}/aportes", json={"tipo": "retiro", "monto": 999, "cuenta_id": cid, "fecha": HOY})).status_code == 409
    assert (await c.post(f"/api/metas/{meta}/aportes", json={"tipo": "aporte", "monto": 9999, "cuenta_id": cid, "fecha": HOY})).status_code == 409


async def test_no_se_borra_meta_con_dinero_ni_cuenta_con_movimientos(c):
    cid = await cuenta(c, "Efectivo", 100)
    meta = (await c.post("/api/metas", json={"nombre": "Viaje", "objetivo": 500})).json()["id"]
    await c.post(f"/api/metas/{meta}/aportes", json={"tipo": "aporte", "monto": 10, "cuenta_id": cid, "fecha": HOY})
    assert (await c.delete(f"/api/metas/{meta}")).status_code == 409
    assert (await c.delete(f"/api/cuentas/{cid}")).status_code == 409
    await c.post(f"/api/metas/{meta}/aportes", json={"tipo": "retiro", "monto": 10, "cuenta_id": cid, "fecha": HOY})
    assert (await c.delete(f"/api/metas/{meta}")).status_code == 204


def test_ahorro_semanal_y_mensual_necesario():
    hoy = date(2026, 1, 1)
    meta = {"objetivo": 1200, "fecha_limite": (hoy + timedelta(days=364)).isoformat(), "aportes": []}
    p = fin.progreso_meta(meta, hoy)
    assert p["semanal"] == Decimal("23.08") and Decimal(99) < p["mensual"] < Decimal(101)
    cerca = fin.progreso_meta({**meta, "fecha_limite": (hoy + timedelta(days=3)).isoformat()}, hoy)
    assert cerca["semanal"] == Decimal("1200.00")                     # nunca se pide más de lo que falta
    vencida = fin.progreso_meta({**meta, "fecha_limite": "2025-12-31"}, hoy)
    assert vencida["vencida"] and vencida["semanal"] is None
    hecha = fin.progreso_meta({**meta, "aportes": [{"tipo": "aporte", "monto": 1200, "fecha": "2026-01-01"}]}, hoy)
    assert hecha["falta"] == 0 and hecha["porcentaje"] == 100


# ------------------------------------------------------------ dinero disponible
def test_formula_del_dinero_disponible():
    r = fin.dinero_disponible(Decimal(1000), Decimal(100), Decimal(200))
    assert r["disponible"] == 700 and r["deficit"] == 0
    r = fin.dinero_disponible(Decimal(100), Decimal(300), Decimal(0))
    assert r["disponible"] == 0 and r["deficit"] == 200 and "superan tu saldo" in r["mensajes"][0]


async def test_disponible_descuenta_esenciales_y_no_cuenta_ahorros(c):
    efectivo, ahorros = await cuenta(c, "Efectivo", 1000), await cuenta(c, "Ahorros BCP", 500, tipo="ahorros")
    await mov(c, efectivo, tipo="gasto", monto=100, categoria="Comida")
    assert (await c.put("/api/presupuestos/Comida", json={"limite": 300, "esencial": True})).status_code == 204
    r = (await c.get("/api/disponible")).json()
    assert r["saldo_real"] == 900 and r["esenciales_restantes"] == 200 and r["disponible"] == 700    # sin los S/ 500 de ahorros
    no = (await c.get("/api/disponible?compra=800")).json()["compra"]
    assert no["alcanza"] is False and no["faltante"] == 100
    si = (await c.get("/api/disponible?compra=200")).json()["compra"]
    assert si["alcanza"] is True and si["disponible_despues"] == 500


# ------------------------------------------------------------ migración de métodos a cuentas y aislamiento
async def test_desde_metodos_crea_cuentas_y_asigna_una_sola_vez(c):
    await c.post("/api/transacciones", json=movimiento(tipo="ingreso", monto=80, categoria="Sueldo", metodo_pago="Yape", fecha=HOY))
    await c.post("/api/transacciones", json=movimiento(monto=20, metodo_pago="Efectivo", fecha=HOY))
    assert (await c.post("/api/cuentas/desde-metodos")).json() == {"cuentas_creadas": 2, "movimientos_asignados": 2}
    assert (await c.post("/api/cuentas/desde-metodos")).json() == {"cuentas_creadas": 0, "movimientos_asignados": 0}
    cuentas = {x["nombre"]: x for x in (await c.get("/api/cuentas")).json()}
    assert cuentas["Yape"]["saldo"] == 80 and cuentas["Yape"]["tipo"] == "billetera" and cuentas["Efectivo"]["saldo"] == -20


async def test_cada_usuario_ve_solo_lo_suyo(c, usuario):
    await cuenta(c, "Efectivo", 100)
    usuario["email"] = "beto@example.com"
    assert (await c.get("/api/cuentas")).json() == [] and (await c.get("/api/metas")).json() == []
    assert (await c.get("/api/disponible")).json()["saldo_real"] == 0


# ------------------------------------------------------------ Fase 2 (continuación): editar cuentas, avisos, compra con categoría
async def test_editar_cuenta_cambia_el_saldo_inicial_y_valida(c, usuario):
    a, b = await cuenta(c, "Efectivo", 100), await cuenta(c, "Yape", 5)
    r = await c.put(f"/api/cuentas/{a}", json={"nombre": "Billetera", "tipo": "efectivo", "saldo_inicial": 250})
    assert r.status_code == 200 and r.json()["nombre"] == "Billetera" and r.json()["saldo"] == 250
    assert (await c.put(f"/api/cuentas/{a}", json={"nombre": "Yape", "tipo": "otra", "saldo_inicial": 0})).status_code == 409   # nombre repetido
    assert (await c.put(f"/api/cuentas/{a}", json={"nombre": "Billetera", "tipo": "otra", "saldo_inicial": 10})).status_code == 200   # su propio nombre sí
    assert (await c.put("/api/cuentas/" + "a" * 24, json={"nombre": "X1", "tipo": "otra"})).status_code == 404
    usuario["email"] = "beto@example.com"
    assert (await c.put(f"/api/cuentas/{b}", json={"nombre": "Robada", "tipo": "otra"})).status_code == 404   # no es suya


@pytest.mark.parametrize("pct,esperado", [(0, "ok"), (74, "ok"), (75, "aviso1"), (89, "aviso1"), (90, "aviso2"), (99, "aviso2"), (100, "limite"), (140, "limite")])
def test_nivel_de_aviso_con_los_porcentajes_por_defecto(pct, esperado):
    assert fin.nivel_aviso(pct, [75, 90, 100]) == esperado


async def test_avisos_configurables_y_estado_del_presupuesto(c):
    cid = await cuenta(c, "Efectivo", 1000)
    await mov(c, cid, tipo="gasto", monto=80, categoria="Comida")
    assert (await c.put("/api/presupuestos/Comida", json={"limite": 100})).status_code == 204
    assert (await c.get("/api/config/avisos")).json() == {"avisos": [75, 90, 100]}
    hoy = date.today()
    e = (await c.get(f"/api/presupuestos/estado?anio={hoy.year}&mes={hoy.month}")).json()["presupuestos"][0]
    assert (e["gastado"], e["restante"], e["porcentaje"], e["nivel"]) == (80, 20, 80, "aviso1")
    proximo = date(hoy.year + (hoy.month == 12), hoy.month % 12 + 1, 1).isoformat()
    assert e["renueva"] == proximo
    assert (await c.put("/api/config/avisos", json={"avisos": [50, 70, 90]})).json() == {"avisos": [50, 70, 90]}
    e = (await c.get(f"/api/presupuestos/estado?anio={hoy.year}&mes={hoy.month}")).json()["presupuestos"][0]
    assert e["nivel"] == "aviso2"                                # 80 % con avisos 50/70/90
    for malo in ([90, 70, 50], [50, 50, 90], [50, 70], [0, 70, 90]):
        assert (await c.put("/api/config/avisos", json={"avisos": malo})).status_code == 422


async def test_compra_hipotetica_con_categoria_avisa_si_supera_el_presupuesto(c):
    cid = await cuenta(c, "Efectivo", 1000)
    await mov(c, cid, tipo="gasto", monto=80, categoria="Comida")
    await c.put("/api/presupuestos/Comida", json={"limite": 100})
    r = (await c.get("/api/disponible?compra=30&categoria=Comida")).json()
    assert r["compra"]["presupuesto"]["supera"] is True and any("superarías tu presupuesto de Comida" in m for m in r["mensajes"])
    r = (await c.get("/api/disponible?compra=10&categoria=Comida")).json()
    assert r["compra"]["presupuesto"]["supera"] is False and r["compra"]["presupuesto"]["restante_despues"] == 10
    assert "presupuesto" not in (await c.get("/api/disponible?compra=10&categoria=Cine")).json()["compra"]   # sin presupuesto en esa categoría


# ------------------------------------------------------------ categorías personalizadas e historial de aportes
async def test_categorias_personalizadas_crear_listar_repetidas_y_borrar(c, usuario):
    r = await c.post("/api/categorias", json={"nombre": "Gimnasio", "tipo": "gasto", "emoji": "🏋️"})
    assert r.status_code == 201 and r.json()["nombre"] == "Gimnasio"
    assert (await c.post("/api/categorias", json={"nombre": "gimnasio", "tipo": "gasto"})).status_code == 409        # sin distinguir mayúsculas
    assert (await c.post("/api/categorias", json={"nombre": "Gimnasio", "tipo": "ingreso"})).status_code == 201       # otro tipo: permitido
    assert [x["nombre"] for x in (await c.get("/api/categorias")).json()] == ["Gimnasio", "Gimnasio"]
    for malo in ({"nombre": "X", "tipo": "gasto"}, {"nombre": "Ok", "tipo": "otro"}, {"nombre": "Ok", "tipo": "gasto", "emoji": "x" * 9}):
        assert (await c.post("/api/categorias", json=malo)).status_code == 422
    cid = r.json()["id"]
    usuario["email"] = "beto@example.com"
    assert (await c.get("/api/categorias")).json() == [] and (await c.delete(f"/api/categorias/{cid}")).status_code == 404
    usuario["email"] = "ana@example.com"
    assert (await c.delete(f"/api/categorias/{cid}")).status_code == 204 and len((await c.get("/api/categorias")).json()) == 1


async def test_borrar_categoria_no_toca_los_movimientos_ya_registrados(c):
    cid = await cuenta(c, "Efectivo", 100)
    cat = (await c.post("/api/categorias", json={"nombre": "Gimnasio", "tipo": "gasto"})).json()["id"]
    await mov(c, cid, tipo="gasto", monto=20, categoria="Gimnasio")
    await c.delete(f"/api/categorias/{cat}")
    assert (await c.get("/api/transacciones")).json()["items"][0]["categoria"] == "Gimnasio"
    assert await saldo(c, cid) == 80


async def test_meta_devuelve_los_ultimos_aportes_del_mas_reciente_al_mas_antiguo(c):
    cid = await cuenta(c, "Efectivo", 1000)
    meta = (await c.post("/api/metas", json={"nombre": "Laptop", "objetivo": 900})).json()["id"]
    for i in range(1, 8):
        await c.post(f"/api/metas/{meta}/aportes", json={"tipo": "aporte", "monto": i * 10, "cuenta_id": cid, "fecha": HOY})
    await c.post(f"/api/metas/{meta}/aportes", json={"tipo": "retiro", "monto": 5, "cuenta_id": cid, "fecha": HOY})
    h = (await c.get("/api/metas")).json()[0]["historial"]
    assert len(h) == 5 and h[0] == {"tipo": "retiro", "monto": 5, "fecha": HOY} and h[1]["monto"] == 70


# ------------------------------------------------------------ Fase 3: pagos programados, deudas y comparación
from app.servicios.finanzas import interes_mensual, meses_para_pagar, siguiente_fecha  # noqa: E402


def test_siguiente_fecha_respeta_el_fin_de_mes_y_recupera_el_dia_original():
    assert siguiente_fecha(date(2026, 1, 31), "mensual", 31) == date(2026, 2, 28)
    assert siguiente_fecha(date(2026, 2, 28), "mensual", 31) == date(2026, 3, 31)
    assert siguiente_fecha(date(2026, 11, 30), "trimestral", 30) == date(2027, 2, 28)
    assert siguiente_fecha(date(2028, 2, 29), "anual", 29) == date(2029, 2, 28)
    assert siguiente_fecha(date(2026, 12, 15), "mensual") == date(2027, 1, 15)
    assert siguiente_fecha(date(2026, 1, 1), "semanal") == date(2026, 1, 8)


def test_intereses_y_meses_para_pagar():
    assert interes_mensual(1000, 12) == Decimal("10.00")
    assert meses_para_pagar(1000, 0, 100) == 10
    assert meses_para_pagar(1000, 12, 100) == 11
    assert meses_para_pagar(10000, 12, 100) is None        # la cuota no cubre ni los intereses
    assert meses_para_pagar(0, 12, 100) == 0


async def test_un_pago_programado_no_es_gasto_hasta_confirmarlo(c):
    cid = await cuenta(c, "Efectivo", 500)
    p = (await c.post("/api/pagos", json={"nombre": "Netflix", "monto": 50, "frecuencia": "mensual", "proximo": HOY, "categoria": "Suscripciones"})).json()
    assert p["vencimiento"] == "hoy" and await saldo(c, cid) == 500
    assert (await c.get("/api/transacciones/resumen")).json()["periodo"]["gastos"] == 0       # solo está programado
    d = (await c.get("/api/disponible")).json()
    assert d["reservado_pagos"] == 50 and d["disponible"] == 450                                # pero ya está reservado
    r = await c.post(f"/api/pagos/{p['id']}/pagar", json={"cuenta_id": cid, "fecha": HOY})
    assert r.status_code == 200 and r.json()["proximo"] == siguiente_fecha(date.today(), "mensual").isoformat()
    assert await saldo(c, cid) == 450 and (await c.get("/api/transacciones/resumen")).json()["periodo"]["gastos"] == 50
    assert (await c.get("/api/disponible")).json()["reservado_pagos"] == 0                      # ya no vence este periodo


async def test_pago_exige_saldo_y_uno_unico_no_se_paga_dos_veces(c):
    cid = await cuenta(c, "Efectivo", 100)
    caro = (await c.post("/api/pagos", json={"nombre": "Matrícula", "monto": 999, "frecuencia": "unica", "proximo": HOY})).json()["id"]
    assert (await c.post(f"/api/pagos/{caro}/pagar", json={"cuenta_id": cid, "fecha": HOY})).status_code == 409
    barato = (await c.post("/api/pagos", json={"nombre": "Libro", "monto": 30, "frecuencia": "unica", "proximo": HOY})).json()["id"]
    assert (await c.post(f"/api/pagos/{barato}/pagar", json={"fecha": HOY})).status_code == 404            # sin cuenta
    assert (await c.post(f"/api/pagos/{barato}/pagar", json={"cuenta_id": cid, "fecha": HOY})).status_code == 200
    assert (await c.post(f"/api/pagos/{barato}/pagar", json={"cuenta_id": cid, "fecha": HOY})).status_code == 409
    assert next(x for x in (await c.get("/api/pagos")).json() if x["id"] == barato)["vencimiento"] == "pagado"


async def test_lo_reservado_para_una_categoria_esencial_no_se_descuenta_dos_veces(c):
    await cuenta(c, "Efectivo", 1000)
    await c.put("/api/presupuestos/Servicios", json={"limite": 300, "esencial": True})
    await c.post("/api/pagos", json={"nombre": "Luz", "monto": 100, "frecuencia": "mensual", "proximo": HOY, "categoria": "Servicios"})
    d = (await c.get("/api/disponible")).json()
    assert (d["reservado_pagos"], d["esenciales_restantes"], d["disponible"]) == (100, 200, 700)   # no 600


async def test_pago_de_deuda_solo_los_intereses_son_gasto(c):
    cid = await cuenta(c, "Efectivo", 500)
    deuda = (await c.post("/api/deudas", json={"nombre": "Laptop", "saldo": 1000, "tasa_anual": 12, "cuota": 100, "proximo_vencimiento": HOY})).json()
    assert deuda["interes_mensual"] == 10 and deuda["meses_restantes"] == 11
    assert (await c.get("/api/disponible")).json()["reservado_pagos"] == 100                          # la cuota ya está reservada
    r = await c.post(f"/api/deudas/{deuda['id']}/pagos", json={"monto": 100, "cuenta_id": cid, "fecha": HOY})
    assert r.status_code == 201 and r.json()["saldo"] == 910 and r.json()["historial"][0]["interes"] == 10
    assert await saldo(c, cid) == 400                                                                 # salieron los 100
    assert (await c.get("/api/transacciones/resumen")).json()["periodo"]["gastos"] == 10             # pero solo 10 son gasto
    assert (await c.post(f"/api/deudas/{deuda['id']}/pagos", json={"monto": 5000, "cuenta_id": cid, "fecha": HOY})).status_code == 409
    assert (await c.delete(f"/api/deudas/{deuda['id']}")).status_code == 409                          # aún debe


async def test_comparacion_con_el_periodo_anterior(c):
    cid = await cuenta(c, "Efectivo", 1000)
    hoy = date.today()
    previo = date(hoy.year - (hoy.month == 1), 12 if hoy.month == 1 else hoy.month - 1, 1).isoformat()
    for fecha, monto in ((previo, 100), (HOY, 150)):
        await c.post("/api/transacciones", json=movimiento(cuenta_id=cid, fecha=fecha, monto=monto, categoria="Comida"))
    await c.post("/api/transacciones", json=movimiento(cuenta_id=cid, fecha=HOY, monto=40, categoria="Cine"))
    filas = {f["categoria"]: f for f in (await c.get(f"/api/estadisticas/comparacion?anio={hoy.year}&mes={hoy.month}")).json()["filas"]}
    assert (filas["Comida"]["actual"], filas["Comida"]["anterior"], filas["Comida"]["cambio_pct"]) == (150, 100, 50)
    assert filas["Cine"]["cambio_pct"] is None                                                          # categoría nueva


async def test_pagos_y_deudas_son_privados(c, usuario):
    await c.post("/api/pagos", json={"nombre": "Netflix", "monto": 50, "frecuencia": "mensual", "proximo": HOY})
    await c.post("/api/deudas", json={"nombre": "Laptop", "saldo": 100})
    usuario["email"] = "beto@example.com"
    assert (await c.get("/api/pagos")).json() == [] and (await c.get("/api/deudas")).json() == []


# ------------------------------------------------------------ cierre de la Fase 3: PDF, importación, edición
def test_pdf_tolera_emojis_y_caracteres_fuera_de_latin1():
    from app.exportar import generar_pdf
    from app.schemas import Resumen, TransaccionOut, Totales
    t = Totales(ingresos=10, gastos=5, ahorros=0, saldo=5)
    item = TransaccionOut(id="1", tipo="gasto", monto=5, categoria="Comida 🍔 — rápida ñandú", metodo_pago="Yape", fecha=HOY, descripcion=None)
    pdf = generar_pdf([item], Resumen(periodo=t, acumulado=t, gastos_por_categoria={}, total_movimientos=1), "Periodo de prueba", ["tipo gasto"])
    assert pdf.startswith(b"%PDF") and len(pdf) > 1000


async def test_exportar_pdf_y_csv_respetan_los_filtros(c):
    cid = await cuenta(c, "Efectivo", 1000)
    await mov(c, cid, tipo="gasto", monto=20, categoria="Comida")
    await mov(c, cid, tipo="ingreso", monto=300, categoria="Sueldo")
    pdf = await c.get("/api/transacciones/exportar-pdf?tipo=gasto")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and pdf.headers["content-type"] == "application/pdf"
    csv_gasto = (await c.get("/api/transacciones/exportar?tipo=gasto")).content.decode("utf-8-sig")
    assert "Comida" in csv_gasto and "Sueldo" not in csv_gasto
    csv_cat = (await c.get("/api/transacciones/exportar?categoria=Sueldo")).content.decode("utf-8-sig")
    assert "Sueldo" in csv_cat and "Comida" not in csv_cat


async def test_importar_csv_ida_y_vuelta_sin_duplicados(c, usuario):
    cid = await cuenta(c, "Yape", 0)
    await mov(c, cid, tipo="ingreso", monto=100, categoria="Sueldo", metodo_pago="Yape")
    await mov(c, cid, tipo="gasto", monto=12.5, categoria="Comida", metodo_pago="Yape", descripcion="=HYPERLINK(1)")
    await mov(c, cid, tipo="gasto", monto=3, categoria="Cine", metodo_pago="Efectivo")
    texto = (await c.get("/api/transacciones/exportar")).content.decode("utf-8-sig")
    assert "'=HYPERLINK(1)" in texto                                    # la exportación neutraliza la fórmula
    usuario["email"] = "beto@example.com"
    simulado = (await c.post("/api/importar/movimientos", json={"csv": texto, "simular": True})).json()
    assert simulado["por_importar"] == 3 and simulado["importados"] == 0 and (await c.get("/api/transacciones")).json()["total"] == 0
    r = (await c.post("/api/importar/movimientos", json={"csv": texto})).json()
    assert (r["importados"], r["duplicados"], r["con_errores"]) == (3, 0, 0)
    items = (await c.get("/api/transacciones?limite=100")).json()["items"]
    assert len(items) == 3 and any(i["descripcion"] == "=HYPERLINK(1)" for i in items)              # se quitó el apóstrofo
    otra = (await c.post("/api/importar/movimientos", json={"csv": texto})).json()
    assert (otra["importados"], otra["duplicados"]) == (0, 3)                                      # reimportar no duplica


async def test_importar_informa_las_filas_con_error_y_asigna_cuenta_por_metodo(c):
    cid = await cuenta(c, "Yape")
    texto = "Fecha,Tipo,Categoría,Descripción,Monto,Método,Recurrente\n" \
            f"{HOY},Gasto,Comida,,10.50,Yape,No\n" \
            f"{HOY},Regalo,Comida,,5,Yape,No\n" \
            "fecha-mala,Gasto,Comida,,5,Yape,No\n" \
            f"{HOY},Gasto,Comida,,abc,Yape,No\n" \
            f"{HOY},Gasto,Comida,,0,Yape,No\n"
    r = (await c.post("/api/importar/movimientos", json={"csv": texto})).json()
    assert r["importados"] == 1 and r["con_errores"] == 4 and [e["fila"] for e in r["errores"]] == [3, 4, 5, 6]
    assert "monto" in r["errores"][2]["motivo"]
    assert (await c.get("/api/transacciones")).json()["items"][0]["cuenta_id"] == cid and await saldo(c, cid) == -10.5


async def test_editar_pagos_y_deudas(c, usuario):
    p = (await c.post("/api/pagos", json={"nombre": "Luz", "monto": 80, "frecuencia": "unica", "proximo": HOY})).json()["id"]
    cid = await cuenta(c, "Efectivo", 500)
    await c.post(f"/api/pagos/{p}/pagar", json={"cuenta_id": cid, "fecha": HOY})                         # queda "pagado"
    r = await c.put(f"/api/pagos/{p}", json={"nombre": "Luz y agua", "monto": 95, "frecuencia": "mensual", "proximo": HOY})
    assert r.status_code == 200 and r.json()["monto"] == 95 and r.json()["estado"] == "activo" and r.json()["nombre"] == "Luz y agua"
    assert (await c.put("/api/pagos/" + "a" * 24, json={"nombre": "XX", "monto": 1, "frecuencia": "mensual", "proximo": HOY})).status_code == 404
    d = (await c.post("/api/deudas", json={"nombre": "Moto", "saldo": 500})).json()["id"]
    r = await c.put(f"/api/deudas/{d}", json={"nombre": "Moto roja", "saldo": 450, "tasa_anual": 10, "cuota": 50, "proximo_vencimiento": HOY})
    assert r.status_code == 200 and r.json()["saldo"] == 450 and r.json()["meses_restantes"] == 10 and await saldo(c, cid) == 420   # la cuenta no cambia
    usuario["email"] = "beto@example.com"
    assert (await c.put(f"/api/pagos/{p}", json={"nombre": "Robado", "monto": 1, "frecuencia": "mensual", "proximo": HOY})).status_code == 404
    assert (await c.put(f"/api/deudas/{d}", json={"nombre": "Robada", "saldo": 1})).status_code == 404
