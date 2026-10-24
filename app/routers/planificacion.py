"""Fase 2: cuentas, transferencias, metas de ahorro y "dinero disponible para gastar"."""
import csv
import io
import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated, Literal

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .. import repository as repo
from ..database import get_db
from ..schemas import TransaccionIn
from ..security import CurrentUser
from ..servicios import asistente as asis
from ..servicios import finanzas as fin
from .transacciones import PeriodoMes as Periodo   # estas pantallas siempre usan un mes

router = APIRouter(prefix="/api", tags=["Planificación"])
Db = Annotated[AsyncIOMotorDatabase, Depends(get_db)]
Dinero = Annotated[float, Field(gt=0, le=1_000_000_000, allow_inf_nan=False)]


class CuentaIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    nombre: str = Field(min_length=2, max_length=40)
    tipo: Literal["efectivo", "banco", "billetera", "ahorros", "otra"] = "otra"
    saldo_inicial: float = Field(default=0, ge=-1_000_000_000, le=1_000_000_000, allow_inf_nan=False)


class CuentaOut(BaseModel):
    id: str
    nombre: str
    tipo: str
    saldo_inicial: float
    saldo: float


class TransferenciaIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    origen: str = Field(max_length=24)
    destino: str = Field(max_length=24)
    monto: Dinero
    fecha: date
    nota: str | None = Field(default=None, max_length=120)


class MetaIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    nombre: str = Field(min_length=2, max_length=60)
    descripcion: str | None = Field(default=None, max_length=200)
    objetivo: Dinero
    fecha_limite: date | None = None
    prioridad: Literal["alta", "media", "baja"] = "media"


class AporteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tipo: Literal["aporte", "retiro"]
    monto: Dinero
    cuenta_id: str = Field(max_length=24)
    fecha: date


def oid(valor: str) -> ObjectId:
    if not ObjectId.is_valid(valor):
        raise HTTPException(422, "ID inválido")
    return ObjectId(valor)


async def _saldos(db, usuario: str) -> tuple[list[dict], dict[str, Decimal]]:
    cuentas = await db.cuentas.find({"usuario_id": usuario}).to_list(None)
    for c in cuentas:
        c["id"] = str(c["_id"])
    txs = await db.transacciones.find({"usuario_id": usuario, "cuenta_id": {"$ne": None}}).to_list(None)
    trs = await db.transferencias.find({"usuario_id": usuario}).to_list(None)
    metas = await db.metas.find({"usuario_id": usuario}).to_list(None)
    movs = [{"cuenta_id": a["cuenta_id"], "monto": a["monto"] if a["tipo"] == "aporte" else -a["monto"]} for m in metas for a in m.get("aportes", [])]
    deudas = await db.deudas.find({"usuario_id": usuario}).to_list(None)
    movs += [{"cuenta_id": p["cuenta_id"], "monto": p["capital"]} for dd in deudas for p in dd.get("pagos", [])]   # el capital pagado sale de la cuenta
    return cuentas, fin.saldos_cuentas(cuentas, txs, trs, movs)


def _cuenta_out(c: dict, saldo: Decimal) -> CuentaOut:
    return CuentaOut(id=c["id"], nombre=c["nombre"], tipo=c["tipo"], saldo_inicial=c["saldo_inicial"], saldo=saldo)


# ---------------------------------------------------------------- Cuentas
@router.get("/cuentas", response_model=list[CuentaOut])
async def listar_cuentas(usuario: CurrentUser, db: Db):
    cuentas, s = await _saldos(db, usuario)
    return [_cuenta_out(c, s[c["id"]]) for c in cuentas]


@router.post("/cuentas", response_model=CuentaOut, status_code=201)
async def crear_cuenta(datos: CuentaIn, usuario: CurrentUser, db: Db):
    if await db.cuentas.find_one({"usuario_id": usuario, "nombre": datos.nombre}):
        raise HTTPException(409, "Ya existe una cuenta con ese nombre")
    doc = {**datos.model_dump(), "saldo_inicial": round(datos.saldo_inicial, 2), "usuario_id": usuario, "creada": datetime.now(timezone.utc)}
    await db.cuentas.insert_one(doc)
    doc["id"] = str(doc["_id"])
    return _cuenta_out(doc, fin.d(doc["saldo_inicial"]))


def _tipo_de(metodo: str) -> str:
    m = metodo.lower()
    return "efectivo" if "efectivo" in m else "billetera" if m in ("yape", "plin") else "banco" if ("transfer" in m or "tarjeta" in m) else "otra"


@router.post("/cuentas/desde-metodos", summary="Crear cuentas a partir de tus métodos de pago y asignarles los movimientos antiguos")
async def cuentas_desde_metodos(usuario: CurrentUser, db: Db):
    """Idempotente: solo asigna movimientos que aún no tienen cuenta. El saldo inicial queda en 0 (puedes corregirlo después)."""
    metodos = [m for m in await db.transacciones.distinct("metodo_pago", {"usuario_id": usuario}) if m]
    existentes = {c["nombre"]: str(c["_id"]) for c in await db.cuentas.find({"usuario_id": usuario}).to_list(None)}
    creadas = asignados = 0
    for m in metodos:
        if m not in existentes:
            r = await db.cuentas.insert_one({"usuario_id": usuario, "nombre": m[:40], "tipo": _tipo_de(m), "saldo_inicial": 0.0, "creada": datetime.now(timezone.utc)})
            existentes[m] = str(r.inserted_id)
            creadas += 1
        r = await db.transacciones.update_many({"usuario_id": usuario, "metodo_pago": m, "cuenta_id": None}, {"$set": {"cuenta_id": existentes[m]}})
        asignados += r.modified_count
    return {"cuentas_creadas": creadas, "movimientos_asignados": asignados}


@router.put("/cuentas/{cuenta_id}", response_model=CuentaOut, summary="Editar nombre, tipo o saldo inicial")
async def editar_cuenta(cuenta_id: str, datos: CuentaIn, usuario: CurrentUser, db: Db):
    _id = oid(cuenta_id)
    if await db.cuentas.find_one({"usuario_id": usuario, "nombre": datos.nombre, "_id": {"$ne": _id}}):
        raise HTTPException(409, "Ya existe una cuenta con ese nombre")
    r = await db.cuentas.update_one({"_id": _id, "usuario_id": usuario}, {"$set": {**datos.model_dump(), "saldo_inicial": round(datos.saldo_inicial, 2)}})
    if r.matched_count != 1:
        raise HTTPException(404, "Cuenta no encontrada")
    cuentas, s = await _saldos(db, usuario)
    return _cuenta_out(next(c for c in cuentas if c["id"] == cuenta_id), s[cuenta_id])


@router.delete("/cuentas/{cuenta_id}", status_code=204)
async def borrar_cuenta(cuenta_id: str, usuario: CurrentUser, db: Db):
    _id = oid(cuenta_id)
    en_uso = (await db.transacciones.find_one({"usuario_id": usuario, "cuenta_id": cuenta_id})
              or await db.transferencias.find_one({"usuario_id": usuario, "$or": [{"origen": cuenta_id}, {"destino": cuenta_id}]})
              or await db.metas.find_one({"usuario_id": usuario, "aportes.cuenta_id": cuenta_id})
              or await db.deudas.find_one({"usuario_id": usuario, "pagos.cuenta_id": cuenta_id}))
    if en_uso:
        raise HTTPException(409, "La cuenta tiene movimientos; no se puede eliminar")
    if (await db.cuentas.delete_one({"_id": _id, "usuario_id": usuario})).deleted_count != 1:
        raise HTTPException(404, "Cuenta no encontrada")
    return Response(status_code=204)


# ---------------------------------------------------------------- Transferencias (no son ingreso ni gasto)
@router.post("/transferencias", status_code=201)
async def transferir(datos: TransferenciaIn, usuario: CurrentUser, db: Db):
    if datos.origen == datos.destino:
        raise HTTPException(422, "El origen y el destino deben ser distintos")
    cuentas, s = await _saldos(db, usuario)
    if {datos.origen, datos.destino} - {c["id"] for c in cuentas}:
        raise HTTPException(404, "Cuenta no encontrada")
    if s[datos.origen] < fin.d(datos.monto):
        raise HTTPException(409, "Saldo insuficiente en la cuenta de origen")
    doc = {**datos.model_dump(mode="json"), "monto": round(datos.monto, 2), "usuario_id": usuario, "creada": datetime.now(timezone.utc)}
    await db.transferencias.insert_one(doc)
    return {"id": str(doc["_id"])}


@router.get("/transferencias")
async def listar_transferencias(usuario: CurrentUser, db: Db):
    docs = await db.transferencias.find({"usuario_id": usuario}).sort([("fecha", -1), ("_id", -1)]).to_list(50)
    return [{"id": str(t["_id"]), "origen": t["origen"], "destino": t["destino"], "monto": t["monto"], "fecha": t["fecha"], "nota": t.get("nota")} for t in docs]


@router.delete("/transferencias/{transferencia_id}", status_code=204)
async def borrar_transferencia(transferencia_id: str, usuario: CurrentUser, db: Db):
    if (await db.transferencias.delete_one({"_id": oid(transferencia_id), "usuario_id": usuario})).deleted_count != 1:
        raise HTTPException(404, "Transferencia no encontrada")
    return Response(status_code=204)


# ---------------------------------------------------------------- Metas de ahorro
def _meta_out(m: dict, hoy: date) -> dict:
    p = fin.progreso_meta(m, hoy)
    estado = "cumplida" if p["falta"] == 0 else "vencida" if p["vencida"] else "en curso"
    return {"id": str(m["_id"]), "nombre": m["nombre"], "descripcion": m.get("descripcion"), "objetivo": m["objetivo"],
            "fecha_limite": m.get("fecha_limite"), "prioridad": m.get("prioridad", "media"), "estado": estado,
            "historial": [{"tipo": a["tipo"], "monto": a["monto"], "fecha": a["fecha"]} for a in reversed(m.get("aportes", [])[-5:])],
            **{k: (float(v) if isinstance(v, Decimal) else v) for k, v in p.items()}}


@router.get("/metas")
async def listar_metas(usuario: CurrentUser, db: Db):
    return [_meta_out(m, date.today()) for m in await db.metas.find({"usuario_id": usuario}).sort("_id", 1).to_list(None)]


@router.post("/metas", status_code=201)
async def crear_meta(datos: MetaIn, usuario: CurrentUser, db: Db):
    doc = {**datos.model_dump(mode="json"), "objetivo": round(datos.objetivo, 2), "aportes": [], "usuario_id": usuario, "creada": datetime.now(timezone.utc)}
    await db.metas.insert_one(doc)
    return _meta_out(doc, date.today())


@router.post("/metas/{meta_id}/aportes", status_code=201, summary="Aportar a una meta o retirar de ella (no es ingreso ni gasto)")
async def aportar(meta_id: str, datos: AporteIn, usuario: CurrentUser, db: Db):
    meta = await db.metas.find_one({"_id": oid(meta_id), "usuario_id": usuario})
    if not meta:
        raise HTTPException(404, "Meta no encontrada")
    _, s = await _saldos(db, usuario)
    if datos.cuenta_id not in s:
        raise HTTPException(404, "Cuenta no encontrada")
    monto = fin.d(datos.monto)
    if datos.tipo == "aporte" and s[datos.cuenta_id] < monto:
        raise HTTPException(409, "Saldo insuficiente en la cuenta elegida")
    if datos.tipo == "retiro" and monto > fin.progreso_meta(meta, date.today())["ahorrado"]:
        raise HTTPException(409, "No puedes retirar más de lo ahorrado en la meta")
    aporte = {"id": str(ObjectId()), "tipo": datos.tipo, "monto": float(monto), "cuenta_id": datos.cuenta_id, "fecha": datos.fecha.isoformat()}
    await db.metas.update_one({"_id": meta["_id"]}, {"$push": {"aportes": aporte}})
    meta.setdefault("aportes", []).append(aporte)
    return _meta_out(meta, date.today())


@router.delete("/metas/{meta_id}", status_code=204)
async def borrar_meta(meta_id: str, usuario: CurrentUser, db: Db):
    meta = await db.metas.find_one({"_id": oid(meta_id), "usuario_id": usuario})
    if not meta:
        raise HTTPException(404, "Meta no encontrada")
    if fin.progreso_meta(meta, date.today())["ahorrado"] > 0:
        raise HTTPException(409, "La meta tiene dinero ahorrado: retíralo a una cuenta antes de eliminarla")
    await db.metas.delete_one({"_id": meta["_id"]})
    return Response(status_code=204)


# ---------------------------------------------------------------- Dinero disponible para gastar
@router.get("/disponible", summary="¿Cuánto puedo gastar sin comprometer mis necesidades y mis metas?")
async def disponible(usuario: CurrentUser, db: Db, periodo: Periodo, compra: Annotated[float | None, Query(gt=0, le=1_000_000_000)] = None,
                     categoria: Annotated[str | None, Query(max_length=50, description="Categoría de la compra hipotética")] = None):
    hoy = date.today()
    cuentas, s = await _saldos(db, usuario)
    liquido = sum((s[c["id"]] for c in cuentas if c["tipo"] != "ahorros"), fin.CERO)  # una cuenta de ahorros no es dinero para gastar
    metas = await db.metas.find({"usuario_id": usuario}).to_list(None)
    per = periodo or (hoy.year, hoy.month, 1)
    gastado = (await repo.resumen(db.transacciones, usuario, per)).gastos_por_categoria
    hasta = date.fromisoformat(repo.rango_mes(*per)[1]) - timedelta(days=1)
    reservado, por_cat = fin.reservado(await db.pagos_programados.find({"usuario_id": usuario}).to_list(None),
                                       await db.deudas.find({"usuario_id": usuario}).to_list(None), hasta)
    # lo ya reservado para pagos de una categoría esencial no se descuenta otra vez de su presupuesto esencial
    esenciales = sum((max(fin.d(p["limite"]) - fin.d(gastado.get(p["categoria"], 0)) - por_cat.get(p["categoria"], fin.CERO), fin.CERO)
                      for p in await db.presupuestos.find({"usuario_id": usuario, "esencial": True}).to_list(None)), fin.CERO)
    r = fin.dinero_disponible(liquido, fin.apartado_ahorro(metas, hoy), esenciales, reservado, fin.d(compra) if compra else None)
    if compra and categoria and (p := await db.presupuestos.find_one({"usuario_id": usuario, "categoria": categoria})):
        despues = fin.d(p["limite"]) - fin.d(gastado.get(categoria, 0)) - fin.d(compra)
        r["compra"]["presupuesto"] = {"categoria": categoria, "limite": float(fin.d(p["limite"])), "restante_despues": float(max(despues, fin.CERO)), "supera": despues < 0}
        r["mensajes"].append(f"Con esta compra superarías tu presupuesto de {categoria} por {fin.soles(-despues)}." if despues < 0
                             else f"En {categoria} te quedarían {fin.soles(despues)} de presupuesto.")
    return r


# ---------------------------------------------------------------- Avisos de presupuesto configurables
AVISOS_POR_DEFECTO = [75, 90, 100]


class AvisosIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    avisos: list[Annotated[int, Field(ge=1, le=200)]] = Field(min_length=3, max_length=3)

    @field_validator("avisos")
    @classmethod
    def _ordenados(cls, v: list[int]) -> list[int]:
        if v != sorted(set(v)):
            raise ValueError("Deben ser tres porcentajes distintos y de menor a mayor")
        return v


async def _avisos(db, usuario: str) -> list[int]:
    return ((await db.config.find_one({"usuario_id": usuario})) or {}).get("avisos", AVISOS_POR_DEFECTO)


@router.get("/config/avisos", summary="Porcentajes de aviso de los presupuestos")
async def ver_avisos(usuario: CurrentUser, db: Db):
    return {"avisos": await _avisos(db, usuario)}


@router.put("/config/avisos")
async def fijar_avisos(datos: AvisosIn, usuario: CurrentUser, db: Db):
    await db.config.update_one({"usuario_id": usuario}, {"$set": {"avisos": datos.avisos}}, upsert=True)
    return {"avisos": datos.avisos}


@router.get("/presupuestos/estado", summary="Cada presupuesto con lo gastado, lo que queda, su nivel de aviso y su renovación")
async def estado_presupuestos(usuario: CurrentUser, db: Db, periodo: Periodo):
    hoy = date.today()
    per = periodo or (hoy.year, hoy.month, 1)
    gastado = (await repo.resumen(db.transacciones, usuario, per)).gastos_por_categoria
    avisos, renueva, salida = await _avisos(db, usuario), repo.rango_mes(*per)[1], []
    for p in await db.presupuestos.find({"usuario_id": usuario}).to_list(None):
        limite, usado = fin.d(p["limite"]), fin.d(gastado.get(p["categoria"], 0))
        pct = int(usado / limite * 100) if limite > 0 else 0
        salida.append({"categoria": p["categoria"], "limite": float(limite), "esencial": p.get("esencial", False), "gastado": float(usado),
                       "restante": float(max(limite - usado, fin.CERO)), "porcentaje": pct, "nivel": fin.nivel_aviso(pct, avisos), "renueva": renueva})
    return {"avisos": avisos, "presupuestos": salida}


# ---------------------------------------------------------------- Categorías personalizadas
class CategoriaIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    nombre: str = Field(min_length=2, max_length=50)
    tipo: Literal["gasto", "ingreso", "ahorro"]
    emoji: str = Field(default="🏷️", min_length=1, max_length=8)


def _categoria_out(d: dict) -> dict:
    return {"id": str(d["_id"]), "nombre": d["nombre"], "tipo": d["tipo"], "emoji": d.get("emoji", "🏷️")}


@router.get("/categorias", summary="Categorías creadas por el usuario")
async def listar_categorias(usuario: CurrentUser, db: Db):
    return [_categoria_out(d) for d in await db.categorias.find({"usuario_id": usuario}).sort([("tipo", 1), ("nombre", 1)]).to_list(None)]


@router.post("/categorias", status_code=201)
async def crear_categoria(datos: CategoriaIn, usuario: CurrentUser, db: Db):
    repetida = {"usuario_id": usuario, "tipo": datos.tipo, "nombre": {"$regex": f"^{re.escape(datos.nombre)}$", "$options": "i"}}
    if await db.categorias.find_one(repetida):
        raise HTTPException(409, "Ya tienes una categoría con ese nombre")
    doc = {**datos.model_dump(), "usuario_id": usuario}
    await db.categorias.insert_one(doc)
    return _categoria_out(doc)


@router.delete("/categorias/{categoria_id}", status_code=204, summary="Quita la categoría de las listas; los movimientos conservan su nombre")
async def borrar_categoria(categoria_id: str, usuario: CurrentUser, db: Db):
    if (await db.categorias.delete_one({"_id": oid(categoria_id), "usuario_id": usuario})).deleted_count != 1:
        raise HTTPException(404, "Categoría no encontrada")
    return Response(status_code=204)


# ---------------------------------------------------------------- Pagos programados (pendiente hasta confirmar el pago)
class PagoIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    nombre: str = Field(min_length=2, max_length=60)
    monto: Dinero
    frecuencia: Literal["semanal", "mensual", "trimestral", "anual", "unica"]
    proximo: date
    cuenta_id: str | None = Field(default=None, max_length=24)
    categoria: str = Field(default="Servicios", min_length=2, max_length=50)
    recordatorio_dias: int = Field(default=3, ge=0, le=30)


class PagarIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cuenta_id: str | None = Field(default=None, max_length=24)
    fecha: date


def _pago_out(p: dict, hoy: date) -> dict:
    dias = (date.fromisoformat(p["proximo"]) - hoy).days
    pagado = p.get("estado") == "pagado"
    vencimiento = "pagado" if pagado else "vencido" if dias < 0 else "hoy" if dias == 0 else "pronto" if dias <= p.get("recordatorio_dias", 3) else "futuro"
    return {"id": str(p["_id"]), "nombre": p["nombre"], "monto": p["monto"], "frecuencia": p["frecuencia"], "proximo": p["proximo"], "categoria": p["categoria"],
            "cuenta_id": p.get("cuenta_id"), "estado": "pagado" if pagado else "activo", "dias": dias, "vencimiento": vencimiento, "recordatorio_dias": p.get("recordatorio_dias", 3)}


@router.get("/pagos", summary="Calendario de pagos, del más próximo al más lejano")
async def listar_pagos(usuario: CurrentUser, db: Db):
    return [_pago_out(p, date.today()) for p in await db.pagos_programados.find({"usuario_id": usuario}).sort([("proximo", 1), ("_id", 1)]).to_list(None)]


@router.post("/pagos", status_code=201)
async def crear_pago(datos: PagoIn, usuario: CurrentUser, db: Db):
    doc = {**datos.model_dump(mode="json"), "monto": round(datos.monto, 2), "dia": datos.proximo.day, "estado": "activo", "historial": [], "usuario_id": usuario}
    await db.pagos_programados.insert_one(doc)
    return _pago_out(doc, date.today())


@router.put("/pagos/{pago_id}", summary="Editar un pago programado")
async def editar_pago(pago_id: str, datos: PagoIn, usuario: CurrentUser, db: Db):
    cambios = {**datos.model_dump(mode="json"), "monto": round(datos.monto, 2), "dia": datos.proximo.day}
    if datos.frecuencia != "unica":
        cambios["estado"] = "activo"          # un pago único ya pagado que pasa a ser periódico vuelve a estar activo
    if (await db.pagos_programados.update_one({"_id": oid(pago_id), "usuario_id": usuario}, {"$set": cambios})).matched_count != 1:
        raise HTTPException(404, "Pago no encontrado")
    return _pago_out(await db.pagos_programados.find_one({"_id": oid(pago_id)}), date.today())


@router.post("/pagos/{pago_id}/pagar", summary="Confirmar el pago: crea el gasto y avanza el vencimiento")
async def pagar(pago_id: str, datos: PagarIn, usuario: CurrentUser, db: Db):
    p = await db.pagos_programados.find_one({"_id": oid(pago_id), "usuario_id": usuario})
    if not p:
        raise HTTPException(404, "Pago no encontrado")
    if p.get("estado") == "pagado":
        raise HTTPException(409, "Este pago ya fue pagado")
    cuentas, s = await _saldos(db, usuario)
    cid = datos.cuenta_id or p.get("cuenta_id")
    cuenta = next((c for c in cuentas if c["id"] == cid), None)
    if not cuenta:
        raise HTTPException(404, "Elige una cuenta para pagar")
    monto = fin.d(p["monto"])
    if s[cid] < monto:
        raise HTTPException(409, "Saldo insuficiente en la cuenta elegida")
    tx = await repo.crear(db.transacciones, usuario, TransaccionIn(
        tipo="gasto", monto=float(monto), categoria=p["categoria"], metodo_pago=cuenta["nombre"], fecha=datos.fecha,
        descripcion=p["nombre"], es_recurrente=p["frecuencia"] != "unica", cuenta_id=cid))
    cambios = {"estado": "pagado"} if p["frecuencia"] == "unica" else {"proximo": fin.siguiente_fecha(date.fromisoformat(p["proximo"]), p["frecuencia"], p.get("dia")).isoformat()}
    await db.pagos_programados.update_one({"_id": p["_id"]}, {"$set": cambios, "$push": {"historial": {"fecha": datos.fecha.isoformat(), "monto": float(monto), "transaccion_id": tx.id}}})
    return {"transaccion_id": tx.id, **cambios}


@router.delete("/pagos/{pago_id}", status_code=204)
async def borrar_pago(pago_id: str, usuario: CurrentUser, db: Db):
    if (await db.pagos_programados.delete_one({"_id": oid(pago_id), "usuario_id": usuario})).deleted_count != 1:
        raise HTTPException(404, "Pago no encontrado")
    return Response(status_code=204)


# ---------------------------------------------------------------- Deudas y cuotas
class DeudaIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    nombre: str = Field(min_length=2, max_length=60)
    acreedor: str | None = Field(default=None, max_length=60)
    saldo: Dinero
    tasa_anual: float = Field(default=0, ge=0, le=200, allow_inf_nan=False)
    cuota: float | None = Field(default=None, gt=0, le=1_000_000_000, allow_inf_nan=False)
    proximo_vencimiento: date | None = None


class PagoDeudaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    monto: Dinero
    cuenta_id: str = Field(max_length=24)
    fecha: date


def _deuda_out(dd: dict) -> dict:
    return {"id": str(dd["_id"]), "nombre": dd["nombre"], "acreedor": dd.get("acreedor"), "saldo": dd["saldo"], "tasa_anual": dd.get("tasa_anual", 0),
            "cuota": dd.get("cuota"), "proximo_vencimiento": dd.get("proximo_vencimiento"), "interes_mensual": float(fin.interes_mensual(dd["saldo"], dd.get("tasa_anual", 0))),
            "meses_restantes": fin.meses_para_pagar(dd["saldo"], dd.get("tasa_anual", 0), dd["cuota"]) if dd.get("cuota") else None,
            "historial": [{"fecha": p["fecha"], "monto": p["monto"], "interes": p["interes"], "capital": p["capital"]} for p in reversed(dd.get("pagos", [])[-5:])]}


@router.get("/deudas")
async def listar_deudas(usuario: CurrentUser, db: Db):
    return [_deuda_out(dd) for dd in await db.deudas.find({"usuario_id": usuario}).sort("_id", 1).to_list(None)]


@router.post("/deudas", status_code=201)
async def crear_deuda(datos: DeudaIn, usuario: CurrentUser, db: Db):
    doc = {**datos.model_dump(mode="json"), "saldo": round(datos.saldo, 2), "dia": datos.proximo_vencimiento.day if datos.proximo_vencimiento else None, "pagos": [], "usuario_id": usuario}
    await db.deudas.insert_one(doc)
    return _deuda_out(doc)


@router.put("/deudas/{deuda_id}", summary="Editar una deuda (si cambias el saldo, no se tocan tus cuentas)")
async def editar_deuda(deuda_id: str, datos: DeudaIn, usuario: CurrentUser, db: Db):
    cambios = {**datos.model_dump(mode="json"), "saldo": round(datos.saldo, 2), "dia": datos.proximo_vencimiento.day if datos.proximo_vencimiento else None}
    if (await db.deudas.update_one({"_id": oid(deuda_id), "usuario_id": usuario}, {"$set": cambios})).matched_count != 1:
        raise HTTPException(404, "Deuda no encontrada")
    return _deuda_out(await db.deudas.find_one({"_id": oid(deuda_id)}))


@router.post("/deudas/{deuda_id}/pagos", status_code=201, summary="Registrar un pago: los intereses son gasto; el capital baja la deuda")
async def pagar_deuda(deuda_id: str, datos: PagoDeudaIn, usuario: CurrentUser, db: Db):
    dd = await db.deudas.find_one({"_id": oid(deuda_id), "usuario_id": usuario})
    if not dd:
        raise HTTPException(404, "Deuda no encontrada")
    cuentas, s = await _saldos(db, usuario)
    cuenta = next((c for c in cuentas if c["id"] == datos.cuenta_id), None)
    if not cuenta:
        raise HTTPException(404, "Cuenta no encontrada")
    monto, saldo = fin.d(datos.monto), fin.d(dd["saldo"])
    interes = min(fin.interes_mensual(saldo, dd.get("tasa_anual", 0)), monto)
    capital = monto - interes
    if capital > saldo:
        raise HTTPException(409, "El pago supera lo que debes (incluidos los intereses)")
    if s[datos.cuenta_id] < monto:
        raise HTTPException(409, "Saldo insuficiente en la cuenta elegida")
    if interes > 0:   # solo los intereses son un gasto real; el capital es dinero que sale para reducir la deuda
        await repo.crear(db.transacciones, usuario, TransaccionIn(tipo="gasto", monto=float(interes), categoria="Intereses", metodo_pago=cuenta["nombre"],
                                                                  fecha=datos.fecha, descripcion=f"Intereses de {dd['nombre']}", cuenta_id=datos.cuenta_id))
    pago = {"id": str(ObjectId()), "fecha": datos.fecha.isoformat(), "monto": float(monto), "interes": float(interes), "capital": float(capital), "cuenta_id": datos.cuenta_id}
    cambios = {"saldo": float(saldo - capital)}
    if dd.get("cuota") and dd.get("proximo_vencimiento"):
        cambios["proximo_vencimiento"] = fin.siguiente_fecha(date.fromisoformat(dd["proximo_vencimiento"]), "mensual", dd.get("dia")).isoformat()
    await db.deudas.update_one({"_id": dd["_id"]}, {"$set": cambios, "$push": {"pagos": pago}})
    return _deuda_out({**dd, **cambios, "pagos": [*dd.get("pagos", []), pago]})


@router.delete("/deudas/{deuda_id}", status_code=204)
async def borrar_deuda(deuda_id: str, usuario: CurrentUser, db: Db):
    dd = await db.deudas.find_one({"_id": oid(deuda_id), "usuario_id": usuario})
    if not dd:
        raise HTTPException(404, "Deuda no encontrada")
    if fin.d(dd["saldo"]) > 0:
        raise HTTPException(409, "La deuda aún tiene saldo pendiente")
    await db.deudas.delete_one({"_id": dd["_id"]})
    return Response(status_code=204)


# ---------------------------------------------------------------- Comparación con el periodo anterior
@router.get("/estadisticas/comparacion", summary="Gasto por categoría frente al periodo anterior")
async def comparacion(usuario: CurrentUser, db: Db, periodo: Periodo):
    hoy = date.today()
    a, m, dia = periodo or (hoy.year, hoy.month, 1)
    pa, pm = (a - 1, 12) if m == 1 else (a, m - 1)
    act = (await repo.resumen(db.transacciones, usuario, (a, m, dia))).gastos_por_categoria
    ant = (await repo.resumen(db.transacciones, usuario, (pa, pm, dia))).gastos_por_categoria
    filas = []
    for cat in sorted(set(act) | set(ant)):
        x, y = fin.d(act.get(cat, 0)), fin.d(ant.get(cat, 0))
        filas.append({"categoria": cat, "actual": float(x), "anterior": float(y), "diferencia": float(x - y), "cambio_pct": int((x - y) / y * 100) if y > 0 else None})
    return {"filas": sorted(filas, key=lambda f: -abs(f["diferencia"]))}


# ---------------------------------------------------------------- Importar movimientos desde CSV
MAX_FILAS_IMPORTACION = 2000


class ImportarIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    csv: str = Field(max_length=1_500_000)
    simular: bool = False  # true: valida y cuenta, pero no guarda nada


def _limpiar(valor) -> str:
    """Quita espacios y el apóstrofo que añade la exportación para neutralizar fórmulas (=, +, -, @)."""
    v = (valor or "").strip()
    return v[1:] if len(v) > 1 and v[0] == "'" and v[1] in "=+-@" else v


def _fila_a_transaccion(f: dict, cuentas: dict[str, str]) -> TransaccionIn:
    def g(*nombres):
        return next((_limpiar(f[n]) for n in nombres if n in f), "")
    monto = float(re.sub(r"[S/\s,]", "", g("Monto", "monto")) or "x")       # ValueError si no es un número
    metodo = g("Método", "Metodo", "metodo_pago")
    return TransaccionIn(
        tipo=g("Tipo", "tipo").lower(), monto=monto, categoria=g("Categoría", "Categoria", "categoria"), metodo_pago=metodo, fecha=g("Fecha", "fecha"),
        descripcion=g("Descripción", "Descripcion", "descripcion") or None, es_recurrente=g("Recurrente", "recurrente").lower() in ("sí", "si", "true", "1"),
        cuenta_id=cuentas.get(metodo), hora=g("Hora", "hora") or None, subcategoria=g("Subcategoría", "Subcategoria", "subcategoria") or None,
        etiquetas=g("Etiquetas", "etiquetas").split(","), notas=g("Notas", "notas") or None)


def _motivo(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:2])
    return "monto: no es un número" if isinstance(e, ValueError) and "float" in str(e) else str(e)


@router.post("/importar/movimientos", summary="Importar movimientos desde un CSV (el mismo formato que se exporta)")
async def importar_movimientos(datos: ImportarIn, usuario: CurrentUser, db: Db):
    filas = list(csv.DictReader(io.StringIO(datos.csv.lstrip("\ufeff"))))
    if len(filas) > MAX_FILAS_IMPORTACION:
        raise HTTPException(413, f"Máximo {MAX_FILAS_IMPORTACION} filas por importación")
    cuentas = {c["nombre"]: str(c["_id"]) for c in await db.cuentas.find({"usuario_id": usuario}).to_list(None)}
    clave = lambda fecha, tipo, cat, monto, metodo, desc: (fecha, tipo, cat, fin.d(monto), metodo, desc or "")
    existentes = {clave(t["fecha"], t["tipo"], t["categoria"], t["monto"], t.get("metodo_pago"), t.get("descripcion"))
                  for t in await db.transacciones.find({"usuario_id": usuario}).to_list(None)}
    nuevos, errores, duplicados = [], [], 0
    for n, f in enumerate(filas, start=2):          # la fila 1 es el encabezado
        try:
            tx = _fila_a_transaccion(f, cuentas)
        except (ValueError, ValidationError) as e:
            errores.append({"fila": n, "motivo": _motivo(e)})
            continue
        k = clave(tx.fecha.isoformat(), tx.tipo, tx.categoria, tx.monto, tx.metodo_pago, tx.descripcion)
        if k in existentes:
            duplicados += 1
            continue
        existentes.add(k)
        nuevos.append(tx)
    if not datos.simular:
        for tx in nuevos:
            await repo.crear(db.transacciones, usuario, tx)
    return {"importados": 0 if datos.simular else len(nuevos), "por_importar": len(nuevos), "duplicados": duplicados,
            "con_errores": len(errores), "errores": errores[:20], "simulacion": datos.simular}


# ---------------------------------------------------------------- Alertas configurables
class AlertasIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    saldo_minimo: float = Field(default=0, ge=0, le=1_000_000_000, allow_inf_nan=False)   # 0 = desactivada
    umbral_aumento_pct: int = Field(default=20, ge=5, le=500)


async def _config(db, usuario: str) -> dict:
    c = await db.config.find_one({"usuario_id": usuario}) or {}
    return {"avisos": c.get("avisos", AVISOS_POR_DEFECTO), "saldo_minimo": c.get("saldo_minimo", 0), "umbral_aumento_pct": c.get("umbral_aumento_pct", 20)}


@router.get("/config/alertas")
async def ver_alertas(usuario: CurrentUser, db: Db):
    c = await _config(db, usuario)
    return {"saldo_minimo": c["saldo_minimo"], "umbral_aumento_pct": c["umbral_aumento_pct"]}


@router.put("/config/alertas")
async def fijar_alertas(datos: AlertasIn, usuario: CurrentUser, db: Db):
    await db.config.update_one({"usuario_id": usuario}, {"$set": datos.model_dump()}, upsert=True)
    return datos.model_dump()


# ---------------------------------------------------------------- Asistente financiero (reglas deterministas)
@router.get("/asistente", summary="Observaciones automáticas sobre tus datos, con la explicación de cómo se calcularon")
async def asistente(usuario: CurrentUser, db: Db, periodo: Periodo):
    hoy = date.today()
    per = periodo or (hoy.year, hoy.month, 1)
    a, m, dia = per
    pa, pm = (a - 1, 12) if m == 1 else (a, m - 1)
    actual = (await repo.resumen(db.transacciones, usuario, per)).gastos_por_categoria
    anterior = (await repo.resumen(db.transacciones, usuario, (pa, pm, dia))).gastos_por_categoria
    inicio, fin_excl = (date.fromisoformat(x) for x in repo.rango_mes(*per))
    cfg = await _config(db, usuario)
    presupuestos = [{"categoria": p["categoria"], "limite": p["limite"], "gastado": actual.get(p["categoria"], 0)}
                    for p in await db.presupuestos.find({"usuario_id": usuario}).to_list(None)]
    obs = asis.observaciones(
        hoy=hoy, inicio=inicio, fin_excl=fin_excl, actual=actual, anterior=anterior, presupuestos=presupuestos, avisos=cfg["avisos"],
        pagos=await db.pagos_programados.find({"usuario_id": usuario}).to_list(None), metas=await db.metas.find({"usuario_id": usuario}).to_list(None),
        umbral_aumento=cfg["umbral_aumento_pct"], disponible=await disponible(usuario, db, per, None, None), saldo_minimo=cfg["saldo_minimo"])
    return {"observaciones": obs, "aviso": "Son reglas sencillas sobre lo que registraste, no predicciones garantizadas. No se envía nada a servicios externos."}


# ---------------------------------------------------------------- Proyección del saldo
@router.get("/proyeccion", summary="Proyección del saldo líquido con tus pagos programados, tus cuotas y tu gasto habitual")
async def proyeccion(usuario: CurrentUser, db: Db, dias: Annotated[int, Query(ge=7, le=180)] = 30):
    hoy = date.today()
    cuentas, s = await _saldos(db, usuario)
    saldo = sum((s[c["id"]] for c in cuentas if c["tipo"] != "ahorros"), fin.CERO)
    limite, manana, eventos = hoy + timedelta(days=dias), hoy + timedelta(days=1), []
    for p in await db.pagos_programados.find({"usuario_id": usuario, "estado": {"$ne": "pagado"}}).to_list(None):
        f = date.fromisoformat(p["proximo"])
        while f <= limite:
            eventos.append((max(f, manana), fin.d(p["monto"])))      # lo ya vencido se asume pagadero mañana
            if p["frecuencia"] == "unica":
                break
            f = fin.siguiente_fecha(f, p["frecuencia"], p.get("dia"))
    for dd in await db.deudas.find({"usuario_id": usuario}).to_list(None):
        if dd.get("cuota") and dd.get("proximo_vencimiento"):
            f, resto = date.fromisoformat(dd["proximo_vencimiento"]), fin.d(dd["saldo"])
            while f <= limite and resto > 0:
                monto = min(fin.d(dd["cuota"]), resto)
                eventos.append((max(f, manana), monto))
                resto, f = resto - monto, fin.siguiente_fecha(f, "mensual", dd.get("dia"))
    recientes = await db.transacciones.find({"usuario_id": usuario, "tipo": "gasto", "es_recurrente": {"$ne": True}, "fecha": {"$gte": (hoy - timedelta(days=30)).isoformat()}}).to_list(None)
    habitual = (sum((fin.d(t["monto"]) for t in recientes), fin.CERO) / 30).quantize(fin.CENT)
    puntos = fin.proyectar(saldo, eventos, habitual, hoy, dias)
    umbral = float((await _config(db, usuario))["saldo_minimo"])
    corte = umbral if umbral > 0 else 0
    que = f"bajaría de {fin.soles(umbral)}" if umbral > 0 else "llegaría a ser negativo"
    alertas = []
    if (b := next((p for p in puntos if p["solo_obligaciones"] < corte), None)):
        alertas.append(f"Con tus pagos y cuotas programados, tu saldo líquido {que} el {b['fecha']}.")
    elif (b := next((p for p in puntos if p["con_ritmo"] < corte), None)):
        alertas.append(f"Si además mantienes tu gasto habitual, tu saldo líquido podría {que.replace('bajaría', 'bajar').replace('llegaría a ser', 'llegar a ser')} hacia el {b['fecha']}.")
    return {"saldo_actual": float(saldo), "habitual_diario": float(habitual), "puntos": puntos, "alertas": alertas,
            "minimo_obligaciones": min(p["solo_obligaciones"] for p in puntos), "minimo_con_ritmo": min(p["con_ritmo"] for p in puntos),
            "aviso": "Estimación: no incluye ingresos futuros ni gastos imprevistos."}


# ---------------------------------------------------------------- Copias de seguridad
COLECCIONES = ["transacciones", "cuentas", "transferencias", "metas", "categorias", "presupuestos", "pagos_programados", "deudas", "config"]
MODELOS = {"transacciones": TransaccionIn, "cuentas": CuentaIn, "transferencias": TransferenciaIn, "metas": MetaIn,
           "categorias": CategoriaIn, "pagos_programados": PagoIn, "deudas": DeudaIn}


def _ser(v):
    if isinstance(v, ObjectId):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _ser(x) for k, x in v.items()}
    return [_ser(x) for x in v] if isinstance(v, list) else v


class RestaurarIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    datos: dict
    confirmar: bool


@router.get("/respaldo", summary="Descargar todos tus datos en un archivo JSON")
async def respaldo(usuario: CurrentUser, db: Db):
    datos = {n: [{k: _ser(v) for k, v in doc.items() if k != "usuario_id"} for doc in await db[n].find({"usuario_id": usuario}).to_list(None)] for n in COLECCIONES}
    cuerpo = {"formato": "mi-billetera-respaldo", "version": 1, "generado": datetime.now(timezone.utc).isoformat(), "datos": datos}
    return Response(content=json.dumps(cuerpo, ensure_ascii=False, indent=1), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="respaldo_billetera_{date.today().isoformat()}.json"'})


@router.post("/respaldo/restaurar", summary="Reemplazar TODOS tus datos por los de un respaldo (exige confirmar)")
async def restaurar(cuerpo: RestaurarIn, usuario: CurrentUser, db: Db):
    if not cuerpo.confirmar:
        raise HTTPException(422, "Falta confirmar la restauración")
    origen = cuerpo.datos.get("datos")
    if cuerpo.datos.get("formato") != "mi-billetera-respaldo" or cuerpo.datos.get("version") != 1 or not isinstance(origen, dict):
        raise HTTPException(422, "El archivo no es un respaldo válido de Mi Billetera")
    nuevos: dict[str, list[dict]] = {}
    for nombre in COLECCIONES:                       # 1) se valida TODO antes de tocar tus datos
        docs = origen.get(nombre, [])
        if not isinstance(docs, list) or len(docs) > 50_000:
            raise HTTPException(422, f"{nombre}: lista inválida o demasiado grande")
        validos = []
        for i, doc in enumerate(docs, start=1):
            try:
                if not isinstance(doc, dict) or not ObjectId.is_valid(doc.get("_id", "")):
                    raise ValueError("falta un _id válido")
                if nombre in MODELOS:
                    MODELOS[nombre](**{k: v for k, v in doc.items() if k in MODELOS[nombre].model_fields})
            except (ValueError, TypeError, ValidationError) as e:
                raise HTTPException(422, f"{nombre}, elemento {i}: {_motivo(e)}") from None
            validos.append({**doc, "_id": ObjectId(doc["_id"]), "usuario_id": usuario})   # nunca se confía en el usuario_id del archivo
        nuevos[nombre] = validos
    for nombre in COLECCIONES:                       # 2) los identificadores no pueden pertenecer a otra cuenta
        ids = [d["_id"] for d in nuevos[nombre]]
        if ids and await db[nombre].find_one({"_id": {"$in": ids}, "usuario_id": {"$ne": usuario}}):
            raise HTTPException(409, "Este respaldo pertenece a otra cuenta y no se puede restaurar aquí")
    for nombre in COLECCIONES:                       # 3) reemplazo
        await db[nombre].delete_many({"usuario_id": usuario})
        if nuevos[nombre]:
            await db[nombre].insert_many(nuevos[nombre])
    return {"restaurado": {n: len(v) for n, v in nuevos.items()}}
