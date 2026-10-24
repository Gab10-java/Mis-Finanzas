"""Pruebas de los endpoints. Cada test documenta un fallo del código original."""
import pytest

from tests.conftest import movimiento


# ------------------------------------------------------------------ crear
async def test_crear_devuelve_201_y_no_filtra_datos_internos(cliente, app):
    r = await cliente.post("/api/transacciones", json=movimiento())
    assert r.status_code == 201
    cuerpo = r.json()
    assert cuerpo["id"] and cuerpo["monto"] == 10.5
    assert "usuario_id" not in cuerpo and "fecha_registro_sistema" not in cuerpo
    assert app.state.sheets.llamadas == ["ana@example.com"]  # se pidió sincronizar


@pytest.mark.parametrize("cambio", [
    {"monto": 0},                       # debe ser > 0
    {"monto": -5},
    {"monto": 0.004},                   # se redondea a 0.00
    {"monto": 10_000_000_000},          # pasa el máximo
    {"tipo": "regalo"},                 # no está en el Literal
    {"fecha": "hola"},                  # el original aceptaba CUALQUIER texto
    {"fecha": "2026-13-45"},
    {"categoria": "   "},               # solo espacios
    {"categoria": "x" * 51},
    {"metodo_pago": ""},
    {"descripcion": "d" * 201},
    {"campo_inventado": 1},             # extra="forbid"
])
async def test_crear_rechaza_datos_invalidos(cliente, cambio):
    r = await cliente.post("/api/transacciones", json=movimiento(**cambio))
    assert r.status_code == 422, r.text


async def test_crear_normaliza_texto_y_monto(cliente):
    r = await cliente.post("/api/transacciones", json=movimiento(
        categoria="  Cine  ", monto=12.3456, descripcion="   "))
    cuerpo = r.json()
    assert cuerpo["categoria"] == "Cine"
    assert cuerpo["monto"] == 12.35
    assert cuerpo["descripcion"] is None


# ------------------------------------------------------------------ listar / paginar
async def test_paginacion_no_trunca_en_silencio(cliente):
    for i in range(120):
        await cliente.post("/api/transacciones", json=movimiento(monto=1, fecha=f"2026-10-{(i % 28) + 1:02d}"))
    p1 = (await cliente.get("/api/transacciones?anio=2026&mes=10&limite=50")).json()
    assert (p1["total"], len(p1["items"]), p1["hay_mas"]) == (120, 50, True)
    p3 = (await cliente.get("/api/transacciones?anio=2026&mes=10&limite=50&pagina=3")).json()
    assert (len(p3["items"]), p3["hay_mas"]) == (20, False)
    ids = {t["id"] for t in p1["items"]} | {t["id"] for t in p3["items"]}
    assert len(ids) == 70  # sin repetidos entre páginas


async def test_orden_mas_reciente_primero(cliente):
    for f in ("2026-10-01", "2026-10-20", "2026-10-10"):
        await cliente.post("/api/transacciones", json=movimiento(fecha=f))
    fechas = [t["fecha"] for t in (await cliente.get("/api/transacciones")).json()["items"]]
    assert fechas == ["2026-10-20", "2026-10-10", "2026-10-01"]


async def test_filtro_por_mes_respeta_limites_incluido_diciembre(cliente):
    for f in ("2026-09-30", "2026-10-01", "2026-10-31", "2026-11-01", "2026-12-31", "2027-01-01"):
        await cliente.post("/api/transacciones", json=movimiento(fecha=f))
    async def fechas(anio, mes):
        r = await cliente.get(f"/api/transacciones?anio={anio}&mes={mes}")
        return sorted(t["fecha"] for t in r.json()["items"])
    assert await fechas(2026, 10) == ["2026-10-01", "2026-10-31"]
    assert await fechas(2026, 12) == ["2026-12-31"]        # diciembre -> enero del año siguiente


@pytest.mark.parametrize("qs", ["mes=10", "anio=2026", "anio=2026&mes=13", "anio=1999&mes=1", "pagina=0", "limite=101"])
async def test_parametros_de_consulta_invalidos(cliente, qs):
    assert (await cliente.get(f"/api/transacciones?{qs}")).status_code == 422


async def test_busqueda_es_literal_no_regex(cliente):
    await cliente.post("/api/transacciones", json=movimiento(categoria="Rappi / Delivery"))
    await cliente.post("/api/transacciones", json=movimiento(categoria="Cineplanet"))
    assert (await cliente.get("/api/transacciones?q=rappi")).json()["total"] == 1   # sin distinguir mayúsculas
    assert (await cliente.get("/api/transacciones?q=.*")).json()["total"] == 0      # NO es comodín


# ------------------------------------------------------------------ resumen
async def test_resumen_suma_todos_los_registros_no_solo_los_primeros_50(cliente):
    """Bug original: el saldo se calculaba en el cliente con solo 50 registros."""
    for _ in range(60):
        await cliente.post("/api/transacciones", json=movimiento(tipo="gasto", monto=10, categoria="Comida"))
    await cliente.post("/api/transacciones", json=movimiento(tipo="ingreso", monto=1000, categoria="Sueldo"))
    await cliente.post("/api/transacciones", json=movimiento(tipo="ahorro", monto=100, categoria="Meta"))
    r = (await cliente.get("/api/transacciones/resumen?anio=2026&mes=10")).json()
    assert r["periodo"] == {"ingresos": 1000, "gastos": 600, "ahorros": 100, "saldo": 300}
    assert r["gastos_por_categoria"] == {"Comida": 600}
    assert r["total_movimientos"] == 62


async def test_resumen_acumulado_arrastra_meses_anteriores(cliente):
    await cliente.post("/api/transacciones", json=movimiento(tipo="ingreso", monto=500, fecha="2026-09-10", categoria="Sueldo"))
    await cliente.post("/api/transacciones", json=movimiento(tipo="gasto", monto=100, fecha="2026-10-05"))
    await cliente.post("/api/transacciones", json=movimiento(tipo="gasto", monto=999, fecha="2026-11-05"))  # futuro: no cuenta
    r = (await cliente.get("/api/transacciones/resumen?anio=2026&mes=10")).json()
    assert r["periodo"]["saldo"] == -100
    assert r["acumulado"]["saldo"] == 400


async def test_resumen_sin_datos(cliente):
    r = (await cliente.get("/api/transacciones/resumen?anio=2026&mes=1")).json()
    assert r["periodo"]["saldo"] == 0 and r["gastos_por_categoria"] == {} and r["total_movimientos"] == 0


# ------------------------------------------------------------------ actualizar / eliminar
async def test_actualizar_ok_y_404_si_no_existe(cliente):
    id_ = (await cliente.post("/api/transacciones", json=movimiento())).json()["id"]
    r = await cliente.put(f"/api/transacciones/{id_}", json=movimiento(monto=99, categoria="Cine"))
    assert r.status_code == 200 and r.json()["monto"] == 99 and r.json()["categoria"] == "Cine"
    # El original devolvía 200 con "No hubo cambios o no se encontró" para un ID inexistente.
    r = await cliente.put("/api/transacciones/" + "a" * 24, json=movimiento())
    assert r.status_code == 404


async def test_eliminar_204_y_404(cliente):
    """Bug original: el `except:` desnudo convertía este 404 en un 400 'ID inválido'."""
    id_ = (await cliente.post("/api/transacciones", json=movimiento())).json()["id"]
    assert (await cliente.delete(f"/api/transacciones/{id_}")).status_code == 204
    assert (await cliente.delete(f"/api/transacciones/{id_}")).status_code == 404


async def test_id_malformado_da_422(cliente):
    assert (await cliente.delete("/api/transacciones/no-es-un-id")).status_code == 422
    assert (await cliente.put("/api/transacciones/123", json=movimiento())).status_code == 422


async def test_un_usuario_no_toca_datos_de_otro(cliente, sesion):
    id_ana = (await cliente.post("/api/transacciones", json=movimiento())).json()["id"]
    sesion["email"] = "beto@example.com"
    assert (await cliente.get("/api/transacciones")).json()["total"] == 0
    assert (await cliente.get("/api/transacciones/resumen")).json()["total_movimientos"] == 0
    assert (await cliente.put(f"/api/transacciones/{id_ana}", json=movimiento())).status_code == 404
    assert (await cliente.delete(f"/api/transacciones/{id_ana}")).status_code == 404
    sesion["email"] = "ana@example.com"
    assert (await cliente.get("/api/transacciones")).json()["total"] == 1  # sigue intacto


# ------------------------------------------------------------------ exportar CSV
async def test_exportar_csv_escapa_y_neutraliza_formulas(cliente):
    await cliente.post("/api/transacciones", json=movimiento(categoria='Dijo "hola", ok', descripcion="=HYPERLINK(1)"))
    r = await cliente.get("/api/transacciones/exportar?anio=2026&mes=10")
    assert r.status_code == 200
    assert r.headers["content-disposition"] == 'attachment; filename="transacciones_2026-10.csv"'
    assert r.content.startswith(b"\xef\xbb\xbf")                 # BOM para Excel
    texto = r.content.decode("utf-8-sig")
    assert "Categoría" in texto                                   # tilde correcta
    assert '"Dijo ""hola"", ok"' in texto                        # comillas escapadas
    assert "'=HYPERLINK(1)" in texto                              # fórmula neutralizada


# ------------------------------------------------------------------ sistema
async def test_config_publica_y_cabeceras_de_seguridad(cliente):
    r = await cliente.get("/api/config")
    assert r.json() == {"google_client_id": "test-client.apps.googleusercontent.com"}
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
