from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pymongo.errors import PyMongoError

from ..config import Settings, get_settings
from ..schemas import ConfigPublica
from .transacciones import Coleccion

router = APIRouter(prefix="/api", tags=["Sistema"])


@router.get("/health", summary="¿El servidor y la base de datos responden?")
async def health(col: Coleccion):
    try:
        await col.database.command("ping")
    except PyMongoError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Base de datos no disponible"
        ) from None
    return {"estado": "ok"}


@router.get("/config", response_model=ConfigPublica, summary="Configuración pública del cliente")
async def config(settings: Annotated[Settings, Depends(get_settings)]):
    """El Client ID de Google vive SOLO en el backend; el frontend lo pide aquí.
    (Antes estaba duplicado en main.py e index.html.)"""
    return ConfigPublica(google_client_id=settings.google_client_id)
