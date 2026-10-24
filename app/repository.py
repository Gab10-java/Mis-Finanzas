"""Capa de datos: TODAS las consultas a MongoDB viven aquí.

Cada función recibe la colección como primer argumento (inyección de dependencias), y
SIEMPRE filtra por usuario_id: es lo que impide que un usuario toque datos de otro.
"""
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import NamedTuple

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorCollection
from pymongo import ReturnDocument

from .schemas import TransaccionIn, TransaccionOut, Totales, Resumen
from .servicios.finanzas import a_centavos

class Rango(NamedTuple):
    """Rango libre de fechas: [inicio, fin) en texto ISO (fin ya es EXCLUSIVO: el día siguiente a "hasta")."""
    inicio: str
    fin: str


Periodo = tuple[int, int, int] | Rango | None  # (año, mes, día de corte), un rango libre, o None = todo el historial


def limites(periodo) -> tuple[str, str]:
    return (periodo.inicio, periodo.fin) if isinstance(periodo, Rango) else rango_mes(*periodo)

ORDEN = [("fecha", -1), ("_id", -1)]  # más reciente primero; _id desempata de forma estable


def rango_mes(anio: int, mes: int, dia: int = 1) -> tuple[str, str]:
    """[inicio, fin) del "mes financiero" que empieza el día `dia` (1 = mes calendario).

    Las fechas ISO ordenan igual como texto que como fecha, así que un rango $gte/$lt es exacto y usa el índice.
    """
    inicio = date(anio, mes, dia)
    fin = date(anio + 1, 1, dia) if mes == 12 else date(anio, mes + 1, dia)
    return inicio.isoformat(), fin.isoformat()


def construir_filtro(usuario: str, periodo: Periodo = None, q: str | None = None,
                     tipo: str | None = None, categoria: str | None = None) -> dict:
    filtro: dict = {"usuario_id": usuario}
    if tipo:
        filtro["tipo"] = tipo
    if categoria:
        filtro["categoria"] = categoria
    if periodo:
        inicio, fin = limites(periodo)
        filtro["fecha"] = {"$gte": inicio, "$lt": fin}
    if q:
        # re.escape: el texto del usuario se busca literalmente (".*" no es un comodín).
        patron = {"$regex": re.escape(q), "$options": "i"}
        filtro["$or"] = [
            {"categoria": patron},
            {"metodo_pago": patron},
            {"descripcion": patron},
            {"fecha": patron},
            {"subcategoria": patron},
            {"notas": patron},
            {"etiquetas": patron},
        ]
    return filtro


def _a_salida(doc: dict) -> TransaccionOut:
    # Los campos que TransaccionOut no declara (usuario_id, _id...) se descartan solos.
    return TransaccionOut.model_validate({**doc, "id": str(doc["_id"])})


async def listar(
    col: AsyncIOMotorCollection, usuario: str, periodo: Periodo, q: str | None,
    pagina: int, limite: int, tipo: str | None = None, categoria: str | None = None,
) -> tuple[list[TransaccionOut], int]:
    filtro = construir_filtro(usuario, periodo, q, tipo, categoria)
    total = await col.count_documents(filtro)
    cursor = col.find(filtro).sort(ORDEN).skip((pagina - 1) * limite).limit(limite)
    docs = await cursor.to_list(length=limite)
    return [_a_salida(d) for d in docs], total


async def listar_todas(
    col: AsyncIOMotorCollection, usuario: str, periodo: Periodo, q: str | None, maximo: int,
    tipo: str | None = None, categoria: str | None = None,
) -> list[TransaccionOut]:
    cursor = col.find(construir_filtro(usuario, periodo, q, tipo, categoria)).sort(ORDEN).limit(maximo)
    return [_a_salida(d) for d in await cursor.to_list(length=maximo)]


# Suma en CENTAVOS enteros. Los movimientos antiguos (sin monto_centavos) se convierten al vuelo: no hace falta migrar para que los totales sigan bien.
CENTAVOS = {"$ifNull": ["$monto_centavos", {"$multiply": ["$monto", 100]}]}


def _de_centavos(filas: list[dict]) -> list[dict]:
    for f in filas:
        f["total"] = float(Decimal(int(round(f["total"]))) / 100)
    return filas


def _totales(por_tipo: dict[str, float]) -> Totales:
    ingresos = por_tipo.get("ingreso", 0.0)
    gastos = por_tipo.get("gasto", 0.0)
    ahorros = por_tipo.get("ahorro", 0.0)
    return Totales(
        ingresos=round(ingresos, 2),
        gastos=round(gastos, 2),
        ahorros=round(ahorros, 2),
        saldo=round(ingresos - gastos - ahorros, 2),
    )


async def resumen(col: AsyncIOMotorCollection, usuario: str, periodo: Periodo) -> Resumen:
    """Totales calculados EN LA BASE DE DATOS sobre TODOS los registros del periodo.

    Antes el cliente sumaba solo los primeros 50 registros que recibía, y el saldo salía mal
    en cuanto había más de 50 movimientos.
    """
    pipeline = [
        {"$match": construir_filtro(usuario, periodo)},
        {"$group": {
            "_id": {"tipo": "$tipo", "categoria": "$categoria"},
            "total": {"$sum": CENTAVOS},
            "n": {"$sum": 1},
        }},
    ]
    grupos = _de_centavos(await col.aggregate(pipeline).to_list(length=None))

    por_tipo: dict[str, float] = {}
    gastos_por_categoria: dict[str, float] = {}
    movimientos = 0
    for g in grupos:
        tipo, categoria = g["_id"]["tipo"], g["_id"]["categoria"]
        por_tipo[tipo] = por_tipo.get(tipo, 0.0) + g["total"]
        movimientos += g["n"]
        if tipo == "gasto":
            gastos_por_categoria[categoria] = round(g["total"], 2)

    del_periodo = _totales(por_tipo)

    ahorro_cat: dict[str, float] = {}
    if periodo:
        # "Disponible" real = todo lo ocurrido hasta el fin del mes consultado (arrastra meses previos).
        _, fin = limites(periodo)
        acum = _de_centavos(await col.aggregate([
            {"$match": {"usuario_id": usuario, "fecha": {"$lt": fin}}},
            {"$group": {"_id": {"tipo": "$tipo", "categoria": "$categoria"}, "total": {"$sum": CENTAVOS}}},
        ]).to_list(length=None))
    else:
        acum = [{"_id": g["_id"], "total": g["total"]} for g in grupos]
    acum_tipo: dict[str, float] = {}
    for g in acum:
        t = g["_id"]["tipo"]
        acum_tipo[t] = acum_tipo.get(t, 0.0) + g["total"]
        if t == "ahorro":
            ahorro_cat[g["_id"]["categoria"]] = round(g["total"], 2)
    acumulado = _totales(acum_tipo)

    return Resumen(
        periodo=del_periodo,
        acumulado=acumulado,
        ahorro_por_categoria=ahorro_cat,
        gastos_por_categoria=gastos_por_categoria,
        total_movimientos=movimientos,
    )


async def crear(col: AsyncIOMotorCollection, usuario: str, datos: TransaccionIn) -> TransaccionOut:
    doc = datos.model_dump(mode="json")  # mode="json": la fecha se guarda como "YYYY-MM-DD"
    doc["usuario_id"] = usuario
    doc["monto_centavos"] = a_centavos(datos.monto)
    doc["fecha_registro_sistema"] = datetime.now(timezone.utc)
    await col.insert_one(doc)  # insert_one añade "_id" al diccionario
    return _a_salida(doc)


async def actualizar(
    col: AsyncIOMotorCollection, usuario: str, oid: ObjectId, datos: TransaccionIn
) -> TransaccionOut | None:
    """None = no existe (o no es tuyo). Una sola operación atómica: busca, actualiza y devuelve."""
    cambios = datos.model_dump(mode="json")
    cambios["fecha_actualizacion"] = datetime.now(timezone.utc)
    cambios["monto_centavos"] = a_centavos(datos.monto)
    doc = await col.find_one_and_update(
        {"_id": oid, "usuario_id": usuario},
        {"$set": cambios},
        return_document=ReturnDocument.AFTER,
    )
    return _a_salida(doc) if doc else None


async def eliminar(col: AsyncIOMotorCollection, usuario: str, oid: ObjectId) -> bool:
    resultado = await col.delete_one({"_id": oid, "usuario_id": usuario})
    return resultado.deleted_count == 1


async def historial(col: AsyncIOMotorCollection, usuario: str, fin: tuple[int, int, int], meses: int) -> list[dict]:
    """Totales de los últimos `meses` periodos (calendario o financiero) que terminan en `fin`; los vacíos salen en 0."""
    anio, mes, dia = fin
    periodos = []
    for _ in range(meses):
        periodos.append((f"{anio}-{mes:02d}", *rango_mes(anio, mes, dia)))
        anio, mes = (anio - 1, 12) if mes == 1 else (anio, mes - 1)
    periodos.reverse()
    ramas = [{"case": {"$and": [{"$gte": ["$fecha", ini]}, {"$lt": ["$fecha", hasta]}]}, "then": clave} for clave, ini, hasta in periodos]
    grupos = _de_centavos(await col.aggregate([
        {"$match": {"usuario_id": usuario, "fecha": {"$gte": periodos[0][1], "$lt": periodos[-1][2]}}},
        {"$group": {"_id": {"mes": {"$switch": {"branches": ramas, "default": "fuera"}}, "tipo": "$tipo"}, "total": {"$sum": CENTAVOS}}},
    ]).to_list(length=None))
    tabla = {c: {"ingreso": 0.0, "gasto": 0.0, "ahorro": 0.0} for c, _, _ in periodos}
    for g in grupos:
        clave, tipo = g["_id"]["mes"], g["_id"]["tipo"]
        if clave in tabla and tipo in tabla[clave]:
            tabla[clave][tipo] = round(g["total"], 2)
    return [{"mes": c, "ingresos": v["ingreso"], "gastos": v["gasto"], "ahorros": v["ahorro"]} for c, v in tabla.items()]
