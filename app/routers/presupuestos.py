"""Presupuestos (tope mensual por categoría de gasto) y metas (objetivo por categoría de ahorro)."""
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response
from motor.motor_asyncio import AsyncIOMotorCollection
from pydantic import BaseModel, Field

from ..database import get_presupuestos
from ..security import CurrentUser

router = APIRouter(prefix="/api/presupuestos", tags=["Presupuestos"])
Coleccion = Annotated[AsyncIOMotorCollection, Depends(get_presupuestos)]


class Limite(BaseModel):
    limite: float = Field(ge=0, le=1_000_000_000, allow_inf_nan=False)  # 0 = quitar
    esencial: bool = False  # necesidad esencial: se descuenta del dinero disponible para gastar


@router.get("", response_model=dict[str, float], summary="Límites y metas del usuario, por categoría")
async def listar(usuario: CurrentUser, col: Coleccion):
    docs = await col.find({"usuario_id": usuario}).to_list(length=None)
    return {d["categoria"]: d["limite"] for d in docs}


@router.put("/{categoria}", status_code=204, summary="Fijar (o quitar con 0) el límite de una categoría")
async def fijar(
    categoria: Annotated[str, Path(min_length=2, max_length=50)], datos: Limite, usuario: CurrentUser, col: Coleccion,
):
    filtro = {"usuario_id": usuario, "categoria": categoria}
    if datos.limite == 0:
        await col.delete_one(filtro)
    else:
        await col.update_one(filtro, {"$set": {"limite": round(datos.limite, 2), "esencial": datos.esencial}}, upsert=True)
    return Response(status_code=204)
