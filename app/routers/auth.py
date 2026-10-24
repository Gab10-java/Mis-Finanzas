import os
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, BackgroundTasks
from pydantic import BaseModel, Field

# Librerías necesarias para el clonado de Google Sheets/Drive
import gspread
from google.oauth2.service_account import Credentials

from ..config import Settings, get_settings
from ..security import COOKIE, CurrentUser, crear_sesion, datos_de_sesion, google_info

router = APIRouter(prefix="/api/auth", tags=["Sesión"])
log = logging.getLogger(__name__)

class LoginGoogle(BaseModel):
    credential: str = Field(min_length=20, max_length=4096)


def clonar_y_compartir_hoja(email_usuario: str, db):
    """Tarea en segundo plano para clonar la plantilla en Google Drive."""
    template_id = os.getenv("TEMPLATE_SHEET_ID")
    cred_file = os.getenv("GOOGLE_CREDENTIALS_FILE", "credenciales.json")
    
    if not template_id or not os.path.exists(cred_file):
        log.error("Faltan variables TEMPLATE_SHEET_ID o credenciales.json para clonar.")
        return

    # IMPORTANTE: Se necesita el scope de Drive para poder hacer copias
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    
    try:
        creds = Credentials.from_service_account_file(cred_file, scopes=scopes)
        gc = gspread.authorize(creds)
        
        # 1. Copiar la plantilla
        log.info(f"Clonando plantilla para {email_usuario}...")
        nueva_hoja = gc.copy(template_id, title=f"Control Financiero - {email_usuario}")
        
        # 2. Compartir el nuevo archivo con el usuario que acaba de entrar
        log.info(f"Compartiendo archivo con {email_usuario}...")
        nueva_hoja.share(email_usuario, perm_type='user', role='writer')
        
        # 3. Guardar el ID de la nueva hoja en la colección de usuarios
        db.usuarios.update_one(
            {"email": email_usuario},
            {"$set": {"sheet_id": nueva_hoja.id, "email": email_usuario}},
            upsert=True # Si el documento del usuario no existe, lo crea
        )
        log.info(f"¡Éxito! Hoja clonada para {email_usuario} (ID: {nueva_hoja.id})")
        
    except Exception as e:
        log.error(f"Error al clonar hoja para {email_usuario}: {e}")


@router.post("/google", summary="Cambiar el token de Google por una sesión de 30 días")
def iniciar_sesion(
    datos: LoginGoogle, 
    respuesta: Response, 
    request: Request,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)]
):
    info = google_info(datos.credential, settings)
    email, nombre = info["email"], (info.get("given_name") or info.get("name") or "")[:60]
    
    # === VERIFICACIÓN Y CLONACIÓN MULTIUSUARIO ===
    db = request.app.state.db
    usuario_db = db.usuarios.find_one({"email": email})
    
    # Si el usuario no existe en la BD o aún no tiene su propia hoja asignada:
    if not usuario_db or not usuario_db.get("sheet_id"):
        background_tasks.add_task(clonar_y_compartir_hoja, email, db)
    # =============================================

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