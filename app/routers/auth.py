"""Sesión propia: se verifica a Google UNA vez y se entrega una cookie httpOnly de 30 días."""
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..security import COOKIE, CurrentUser, crear_sesion, datos_de_sesion, google_info

router = APIRouter(prefix="/api/auth", tags=["Sesión"])


class LoginGoogle(BaseModel):
    credential: str = Field(min_length=20, max_length=4096)


@router.post("/google", summary="Cambiar el token de Google por una sesión de 30 días")
def iniciar_sesion(datos: LoginGoogle, respuesta: Response, settings: Annotated[Settings, Depends(get_settings)]):
    info = google_info(datos.credential, settings)
    email, nombre = info["email"], (info.get("given_name") or info.get("name") or "")[:60]
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
