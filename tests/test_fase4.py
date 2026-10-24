"""Fase 4: asistente (reglas), proyección del saldo, alertas configurables y copias de seguridad."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.servicios import asistente as asis
from app.servicios import finanzas as fin
from tests.test_planificacion import HOY, c, cuenta, mov, saldo, usuario  # noqa: F401  (fixtures y ayudas)
from tests.conftest import movimiento

INI, FIN, DIA = date(2026, 10, 1), date(2026, 11, 1), date(2026, 10, 11)       # el día 11 de 31


def obs(**extra):
    base = dict(hoy=DIA, inicio=INI, fin_excl=FIN, actual={}, anterior={}, presupuestos=[], avisos=[75, 90, 100], pagos=[], metas=[])
    return asis.observaciones(**{**base, **extra})


def ids(lista): return {o["id"] for o in lista}


# ------------------------------------------------------------ reglas del asistente
def test_aumento_por_categoria_respeta_umbral_y_minimo():
    r = obs(actual={"Comida": 150}, anterior={"Comida": 100})
    assert "+50%" in r[0]["mensaje"] and "150.00" in r[0]["como_se_calculo"] and "100.00" in r[0]["como_se_calculo"]
    assert obs(actual={"Comida": 110}, anterior={"Comida": 100}) == []                       # +10 %: bajo el umbral
    assert obs(actual={"Comida": 260}, anterior={"Comida": 200}, umbral_aumento=40) == []    # +30 %: bajo un umbral de 40 %
    assert obs(actual={"Comida": 260}, anterior={"Comida": 200}, umbral_aumento=25)          # con un umbral de 25 % sí avisa
    assert obs(actual={"Cine": 50}, anterior={}) == []                                       # sin periodo anterior no hay comparación
    assert obs(actual={"Cafe": 15}, anterior={"Cafe": 5}) == []                              # +200 % pero solo S/ 10


@pytest.mark.parametrize("gastado,nivel,texto", [(80, "aviso", "cerca del límite"), (95, "aviso", "muy cerca"), (120, "alerta", "Superaste")])
def test_presupuesto_cerca_o_superado(gastado, nivel, texto):
    r = obs(presupuestos=[{"categoria": "Comida", "limite": 100, "gastado": gastado}])
    assert r[0]["nivel"] == nivel and texto in r[0]["mensaje"]


def test_ritmo_actual_podria_superar_el_presupuesto():
    r = obs(presupuestos=[{"categoria": "Comida", "limite": 100, "gastado": 40}])           # 40/11 días * 31 = 112.73
    assert r[0]["id"] == "ritmo-Comida" and "podrías" in r[0]["mensaje"] and "112.73" in r[0]["como_se_calculo"] and "no un hecho" in r[0]["como_se_calculo"]
    assert obs(presupuestos=[{"categoria": "Comida", "limite": 100, "gastado": 30}]) == []   # 84.5: dentro del límite
    assert obs(hoy=date(2026, 10, 3), presupuestos=[{"categoria": "Comida", "limite": 100, "gastado": 40}]) == []   # muy pocos días para estimar


def test_suscripciones_pesan_entre_los_recurrentes():
    pagos = [{"nombre": "Netflix", "monto": 40, "frecuencia": "mensual", "categoria": "Suscripciones", "proximo": "2026-11-01"},
             {"nombre": "Spotify", "monto": 20, "frecuencia": "mensual", "categoria": "Suscripciones", "proximo": "2026-11-01"},
             {"nombre": "Luz", "monto": 40, "frecuencia": "mensual", "categoria": "Servicios", "proximo": "2026-11-01"}]
    r = obs(pagos=pagos)
    assert r[0]["id"] == "suscripciones" and "60%" in r[0]["mensaje"]
    assert obs(pagos=pagos[1:]) == []                                                          # con 2 pagos no se opina


def test_metas_ritmo_de_ahorro_semanal():
    meta = {"nombre": "Laptop", "objetivo": 1200, "fecha_limite": (DIA + timedelta(days=364)).isoformat(), "aportes": []}
    r = obs(metas=[meta])
    assert "23.08 más cada semana" in r[0]["mensaje"]
    bueno = {**meta, "aportes": [{"tipo": "aporte", "monto": 300, "fecha": (DIA - timedelta(days=10)).isoformat()}]}
    r = obs(metas=[bueno])                                                                     # 300 en 90 días = 23.33 por semana
    assert r[0]["nivel"] == "info" and "buen ritmo" in r[0]["mensaje"]


def test_pagos_vencidos_deficit_y_saldo_minimo():
    vencido = {"nombre": "Luz", "monto": 80, "frecuencia": "mensual", "categoria": "Servicios", "proximo": "2026-10-01"}
    assert obs(pagos=[vencido])[0]["id"] == "vencidos"
    r = obs(disponible={"deficit": 50, "saldo_real": 100})
    assert ids(r) == {"deficit"}
    r = obs(disponible={"deficit": 0, "saldo_real": 100}, saldo_minimo=200)
    assert ids(r) == {"saldo-minimo"}
    assert obs(disponible={"deficit": 0, "saldo_real": 500}, saldo_minimo=200) == []
    orden = obs(actual={"Comida": 150}, anterior={"Comida": 100}, pagos=[vencido])             # las alertas van primero
    assert [o["nivel"] for o in orden] == ["alerta", "aviso"]


# ------------------------------------------------------------ proyección
def test_proyeccion_pura():
    hoy = date(2026, 1, 1)
    ev = [(hoy + timedelta(days=5), Decimal(300)), (hoy + timedelta(days=5), Decimal(100)), (hoy + timedelta(days=20), Decimal(700))]
    p = fin.proyectar(Decimal(1000), ev, Decimal(10), hoy, 30)
    assert len(p) == 30 and p[0]["fecha"] == "2026-01-02"
    assert (p[4]["solo_obligaciones"], p[4]["con_ritmo"]) == (600, 550)                          # día 5
    assert (p[19]["solo_obligaciones"], p[19]["con_ritmo"]) == (-100, -300)                      # día 20: 1000 - 400 - 700 - 20 días × 10


async def test_proyeccion_avisa_cuando_el_saldo_llegaria_a_negativo(c):
    cid = await cuenta(c, "Efectivo", 1000)
    await cuenta(c, "Ahorros", 5000, tipo="ahorros")                                              # no es dinero para gastar
    en = lambda n: (date.today() + timedelta(days=n)).isoformat()
    await c.post("/api/pagos", json={"nombre": "Matrícula", "monto": 300, "frecuencia": "unica", "proximo": en(3)})
    r = (await c.get("/api/proyeccion?dias=30")).json()
    assert len(r["puntos"]) == 30 and r["saldo_actual"] == 1000 and r["minimo_obligaciones"] == 700 and r["alertas"] == []
    await c.post("/api/pagos", json={"nombre": "Equipo", "monto": 2000, "frecuencia": "unica", "proximo": en(5)})
    r = (await c.get("/api/proyeccion?dias=30")).json()
    assert r["minimo_obligaciones"] == -1300 and f"el {en(5)}" in r["alertas"][0] and "negativo" in r["alertas"][0]
    assert (await c.get("/api/proyeccion?dias=3")).status_code == 422


# ------------------------------------------------------------ alertas configurables y asistente por API
async def test_alertas_configurables(c):
    assert (await c.get("/api/config/alertas")).json() == {"saldo_minimo": 0, "umbral_aumento_pct": 20}
    assert (await c.put("/api/config/alertas", json={"saldo_minimo": 150, "umbral_aumento_pct": 35})).status_code == 200
    assert (await c.get("/api/config/alertas")).json() == {"saldo_minimo": 150, "umbral_aumento_pct": 35}
    for malo in ({"saldo_minimo": -1, "umbral_aumento_pct": 20}, {"saldo_minimo": 0, "umbral_aumento_pct": 2}, {"saldo_minimo": 0, "umbral_aumento_pct": 20, "x": 1}):
        assert (await c.put("/api/config/alertas", json=malo)).status_code == 422


async def test_el_asistente_usa_tus_datos_y_tu_configuracion(c):
    cid = await cuenta(c, "Efectivo", 100)
    await mov(c, cid, tipo="gasto", monto=95, categoria="Comida")
    await c.put("/api/presupuestos/Comida", json={"limite": 100})
    r = (await c.get("/api/asistente")).json()
    assert "No se envía nada" in r["aviso"] and any("muy cerca del límite de Comida" in o["mensaje"] for o in r["observaciones"])
    await c.put("/api/config/alertas", json={"saldo_minimo": 500, "umbral_aumento_pct": 20})
    assert any(o["id"] == "saldo-minimo" for o in (await c.get("/api/asistente")).json()["observaciones"])


# ------------------------------------------------------------ copias de seguridad
async def armar(c):
    a, y = await cuenta(c, "Efectivo", 500), await cuenta(c, "Yape", 0)
    await mov(c, a, tipo="ingreso", monto=200, categoria="Sueldo")
    await mov(c, a, tipo="gasto", monto=35.5, categoria="Comida")
    await c.post("/api/transferencias", json={"origen": a, "destino": y, "monto": 100, "fecha": HOY})
    meta = (await c.post("/api/metas", json={"nombre": "Laptop", "objetivo": 900})).json()["id"]
    await c.post(f"/api/metas/{meta}/aportes", json={"tipo": "aporte", "monto": 50, "cuenta_id": a, "fecha": HOY})
    await c.post("/api/categorias", json={"nombre": "Gimnasio", "tipo": "gasto"})
    await c.put("/api/presupuestos/Comida", json={"limite": 300, "esencial": True})
    await c.post("/api/pagos", json={"nombre": "Netflix", "monto": 40, "frecuencia": "mensual", "proximo": HOY})
    await c.post("/api/deudas", json={"nombre": "Moto", "saldo": 800, "tasa_anual": 12, "cuota": 100, "proximo_vencimiento": HOY})
    return a


async def foto(c):
    cuentas = {x["nombre"]: x["saldo"] for x in (await c.get("/api/cuentas")).json()}
    return (cuentas, (await c.get("/api/transacciones")).json()["total"], [m["ahorrado"] for m in (await c.get("/api/metas")).json()],
            len((await c.get("/api/pagos")).json()), [d["saldo"] for d in (await c.get("/api/deudas")).json()], len((await c.get("/api/categorias")).json()),
            (await c.get("/api/disponible")).json()["disponible"])


async def test_restaurar_devuelve_exactamente_el_estado_del_respaldo(c):
    await armar(c)
    antes = await foto(c)
    r = await c.get("/api/respaldo")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    archivo = r.json()
    assert archivo["formato"] == "mi-billetera-respaldo" and archivo["version"] == 1 and "usuario_id" not in r.text
    await cuenta(c, "Extra", 10)                                                                 # cambios posteriores al respaldo
    await c.post("/api/transacciones", json=movimiento(fecha=HOY, monto=77))
    assert await foto(c) != antes
    ok = await c.post("/api/respaldo/restaurar", json={"datos": archivo, "confirmar": True})
    assert ok.status_code == 200 and ok.json()["restaurado"]["cuentas"] == 2
    assert await foto(c) == antes                                                                # idéntico: saldos, metas, pagos, deudas, disponible


async def test_restaurar_exige_confirmacion_y_valida_antes_de_borrar(c):
    await armar(c)
    antes = await foto(c)
    archivo = (await c.get("/api/respaldo")).json()
    assert (await c.post("/api/respaldo/restaurar", json={"datos": archivo, "confirmar": False})).status_code == 422
    assert (await c.post("/api/respaldo/restaurar", json={"datos": {"formato": "otro"}, "confirmar": True})).status_code == 422
    roto = {**archivo, "datos": {**archivo["datos"], "transacciones": [{**archivo["datos"]["transacciones"][0], "monto": -5}]}}
    r = await c.post("/api/respaldo/restaurar", json={"datos": roto, "confirmar": True})
    assert r.status_code == 422 and "transacciones, elemento 1" in r.json()["detail"]
    sin_id = {**archivo, "datos": {**archivo["datos"], "cuentas": [{"nombre": "X1", "tipo": "otra"}]}}
    assert (await c.post("/api/respaldo/restaurar", json={"datos": sin_id, "confirmar": True})).status_code == 422
    assert await foto(c) == antes                                                                # nada se tocó


async def test_un_respaldo_ajeno_no_se_puede_restaurar_en_otra_cuenta(c, usuario):
    await armar(c)
    archivo = (await c.get("/api/respaldo")).json()
    usuario["email"] = "beto@example.com"
    assert (await c.post("/api/respaldo/restaurar", json={"datos": archivo, "confirmar": True})).status_code == 409
    assert (await c.get("/api/cuentas")).json() == []
    usuario["email"] = "ana@example.com"
    assert len((await c.get("/api/cuentas")).json()) == 2                                       # y los datos de Ana siguen intactos


async def test_el_usuario_id_del_archivo_no_se_respeta(c, usuario):
    cid = await cuenta(c, "Efectivo", 100)
    archivo = (await c.get("/api/respaldo")).json()
    archivo["datos"]["cuentas"][0]["usuario_id"] = "otra@persona.com"                            # intento de colar datos en otra cuenta
    assert (await c.post("/api/respaldo/restaurar", json={"datos": archivo, "confirmar": True})).status_code == 200
    assert [x["nombre"] for x in (await c.get("/api/cuentas")).json()] == ["Efectivo"]
    usuario["email"] = "otra@persona.com"
    assert (await c.get("/api/cuentas")).json() == []
