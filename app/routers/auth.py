"""Sesión propia: se verifica a Google UNA vez y se entrega una cookie httpOnly de 30 días.

Además, la primera vez que entra un usuario, en segundo plano se copia tu hoja plantilla de Google Sheets y se comparte con él
(solo si TEMPLATE_SHEET_ID y el archivo de credenciales están configurados).
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

import gspread
from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from google.oauth2.service_account import Credentials
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError, PyMongoError
from starlette.concurrency import run_in_threadpool

from ..config import Settings, get_settings
from ..database import get_db
from ..security import COOKIE, CurrentUser, crear_sesion, datos_de_sesion, google_info

router = APIRouter(prefix="/api/auth", tags=["Sesión"])
log = logging.getLogger(__name__)

SCOPES_COPIA = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]   # copiar una hoja exige el permiso de Drive
ESPERA_CLONADO = timedelta(minutes=10)   # si una clonación quedó a medias, se permite reintentar pasado este tiempo


def _correo_servicio(settings: Settings) -> str:
    try:
        return json.loads(settings.credentials_path.read_text(encoding="utf-8")).get("client_email", "")
    except (OSError, ValueError):
        return ""


def _explicar_error(e: Exception, settings: Settings) -> str:
    """Traduce los errores típicos de Google a algo que puedas corregir (el detalle completo queda en el log del servidor)."""
    t, cuenta = str(e).lower(), _correo_servicio(settings) or "la cuenta de servicio"
    if "storage quota" in t or "storagequota" in t:
        return ("Google no deja que una cuenta de servicio sea dueña de archivos nuevos (no tiene cuota de almacenamiento). "
                "Define CLONE_FOLDER_ID con una carpeta de una unidad compartida, o usa 'Hacer una copia manualmente'.")
    if "has not been used" in t or "accessnotconfigured" in t:
        return "La API de Google Drive no está activada en tu proyecto de Google Cloud: actívala en 'APIs y servicios'."
    if "[404]" in t or "not found" in t:
        return f"No se encontró la plantilla o {cuenta} no tiene acceso: comparte la plantilla con ese correo (lector) y revisa TEMPLATE_SHEET_ID."
    if "[403]" in t or "permission" in t or "forbidden" in t:
        return f"Google rechazó el permiso: {cuenta} necesita acceso a la plantilla (y a la carpeta CLONE_FOLDER_ID, si la usas)."
    return f"Error inesperado al clonar ({type(e).__name__}); revisa el log del servidor."


class LoginGoogle(BaseModel):
    credential: str = Field(min_length=20, max_length=4096)


def _clonar_sync(email: str, settings: Settings) -> str:
    """Parte BLOQUEANTE (gspread usa la red): se ejecuta siempre en un hilo, nunca en el bucle del servidor."""
    credenciales = Credentials.from_service_account_file(str(settings.credentials_path), scopes=SCOPES_COPIA)
    nueva = gspread.authorize(credenciales).copy(settings.template_sheet_id, title=f"Control Financiero - {email}", folder_id=settings.clone_folder_id or None)
    nueva.share(email, perm_type="user", role="writer")
    return nueva.id


async def clonar_y_compartir_hoja(email: str, db: AsyncIOMotorDatabase, settings: Settings) -> None:
    """Tarea en segundo plano: copia la plantilla, la comparte con el usuario y guarda su sheet_id."""
    if not settings.template_sheet_id or not settings.credentials_path.is_file():
        log.warning("No se clona la hoja de %s: falta TEMPLATE_SHEET_ID o el archivo de credenciales", email)
        return
    ahora = datetime.now(timezone.utc).replace(tzinfo=None)      # Mongo guarda las fechas en UTC sin zona
    try:   # 1) Reservar: aunque el usuario entre varias veces seguidas, solo UNA tarea clona (índice único en usuarios.email)
        await db.usuarios.update_one(
            {"email": email, "sheet_id": {"$exists": False},
             "$or": [{"clonando_desde": {"$exists": False}}, {"clonando_desde": {"$lt": ahora - ESPERA_CLONADO}}]},
            {"$set": {"email": email, "clonando_desde": ahora}}, upsert=True)
    except DuplicateKeyError:
        return                        # ya tiene hoja, o hay otra tarea clonando
    except PyMongoError:
        log.exception("No se pudo reservar la clonación de la hoja de %s", email)
        return
    try:   # 2) Clonar y guardar el id
        sheet_id = await asyncio.to_thread(_clonar_sync, email, settings)
        await db.usuarios.update_one({"email": email}, {"$set": {"sheet_id": sheet_id}, "$unset": {"clonando_desde": "", "clonado_error": ""}})
        log.info("Hoja clonada para %s (ID: %s)", email, sheet_id)
    except Exception as e:
        log.exception("Error al clonar la hoja de %s", email)
        try:   # se libera la reserva (para reintentar) y se guarda el motivo para mostrárselo al usuario
            await db.usuarios.update_one({"email": email}, {"$unset": {"clonando_desde": ""}, "$set": {"clonado_error": _explicar_error(e, settings)}})
        except PyMongoError:
            pass


@router.post("/google", summary="Cambiar el token de Google por una sesión de 30 días")
async def iniciar_sesion(
    datos: LoginGoogle, respuesta: Response, tareas: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)], db: Annotated[AsyncIOMotorDatabase, Depends(get_db)],
):
    info = await run_in_threadpool(google_info, datos.credential, settings)   # validar el token usa la red: va en un hilo
    email, nombre = info["email"], (info.get("given_name") or info.get("name") or "")[:60]

    # Un fallo al consultar o clonar la hoja NUNCA debe impedir el inicio de sesión
    try:
        usuario = await db.usuarios.find_one({"email": email}, {"sheet_id": 1})
        if settings.template_sheet_id and not (usuario and usuario.get("sheet_id")):
            tareas.add_task(clonar_y_compartir_hoja, email, db, settings)
    except PyMongoError:
        log.warning("No se pudo consultar la hoja de %s; el inicio de sesión continúa", email, exc_info=True)

    respuesta.set_cookie(
        COOKIE, crear_sesion(email, settings, nombre), max_age=settings.session_days * 86400,
        httponly=True,                   # JavaScript no puede leerla: un XSS no se la lleva
        samesite="lax",                  # otros sitios no pueden mandarla en escrituras
        secure=settings.cookie_secure, path="/api",
    )
    return {"email": email, "nombre": nombre}


@router.get("/me", summary="¿Hay una sesión activa?")
def quien_soy(usuario: CurrentUser, request: Request, settings: Annotated[Settings, Depends(get_settings)]):
    return {"email": usuario, "nombre": datos_de_sesion(request.cookies.get(COOKIE, ""), settings).get("n", "")}


@router.post("/logout", status_code=204, summary="Cerrar sesión")
def cerrar_sesion(respuesta: Response):
    respuesta.delete_cookie(COOKIE, path="/api")


def _estado_hoja(usuario: dict | None, settings: Settings) -> dict:
    copia = f"https://docs.google.com/spreadsheets/d/{settings.template_sheet_id}/copy" if settings.template_sheet_id else None
    if not settings.template_sheet_id:
        return {"estado": "sin_configurar", "mensaje": "El servidor no tiene TEMPLATE_SHEET_ID: no se crean hojas automáticas.", "url": None, "url_copia": None}
    if not settings.credentials_path.is_file():
        return {"estado": "sin_configurar", "url": None, "url_copia": copia,
                "mensaje": "Falta el archivo de credenciales de Google en el servidor (credenciales.json). En Render súbelo como 'Secret File'."}
    usuario = usuario or {}
    if usuario.get("sheet_id"):
        return {"estado": "lista", "mensaje": "Tu hoja de Google Sheets está lista y compartida con tu correo.", "url_copia": None,
                "url": f"https://docs.google.com/spreadsheets/d/{usuario['sheet_id']}"}
    desde = usuario.get("clonando_desde")
    if desde and desde.replace(tzinfo=None) > datetime.now(timezone.utc).replace(tzinfo=None) - ESPERA_CLONADO:
        return {"estado": "pendiente", "mensaje": "Estamos creando tu hoja; vuelve a mirar en unos segundos.", "url": None, "url_copia": copia}
    if usuario.get("clonado_error"):
        return {"estado": "error", "mensaje": usuario["clonado_error"], "url": None, "url_copia": copia}
    return {"estado": "sin_hoja", "mensaje": "Todavía no tienes tu hoja. Puedes crearla ahora.", "url": None, "url_copia": copia}


@router.get("/hoja", summary="Estado de tu hoja de Google Sheets (y el motivo si falló)")
async def estado_de_mi_hoja(usuario: CurrentUser, db: Annotated[AsyncIOMotorDatabase, Depends(get_db)], settings: Annotated[Settings, Depends(get_settings)]):
    return _estado_hoja(await db.usuarios.find_one({"email": usuario}), settings)


@router.post("/hoja/reintentar", summary="Crear tu hoja ahora (o reintentar si falló)")
async def crear_mi_hoja(usuario: CurrentUser, tareas: BackgroundTasks, db: Annotated[AsyncIOMotorDatabase, Depends(get_db)], settings: Annotated[Settings, Depends(get_settings)]):
    actual = _estado_hoja(await db.usuarios.find_one({"email": usuario}), settings)
    if actual["estado"] in ("sin_hoja", "error"):
        tareas.add_task(clonar_y_compartir_hoja, usuario, db, settings)
        return {**actual, "estado": "pendiente", "mensaje": "Creando tu hoja…"}
    return actual
