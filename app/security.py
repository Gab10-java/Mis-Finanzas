"""Autenticación: valida el ID token de Google y decide quién puede entrar."""
import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Annotated

import cachecontrol
import requests
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.auth import exceptions as google_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from .config import Settings, get_settings

log = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)

# google-auth descarga los certificados públicos de Google en CADA verificación, salvo que
# le des una sesión con caché (lo dice su propia documentación). Una sola sesión reutilizada
# = una descarga cada varias horas en vez de una por petición.
_google_request = google_requests.Request(
    session=cachecontrol.CacheControl(requests.Session())
)


def _no_autorizado(detalle: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detalle, headers={"WWW-Authenticate": "Bearer"}
    )


COOKIE = "sesion"


def _firmar(datos: bytes, settings: Settings) -> str:
    return base64.urlsafe_b64encode(hmac.new(settings.session_key, datos, hashlib.sha256).digest()).decode()


def crear_sesion(email: str, settings: Settings, nombre: str = "") -> str:
    """Token firmado con HMAC-SHA256: "<datos>.<firma>". Nadie puede alterarlo sin la clave del servidor."""
    carga = base64.urlsafe_b64encode(
        json.dumps({"e": email, "x": int(time.time()) + settings.session_days * 86400, "n": nombre}).encode()
    )
    return f"{carga.decode()}.{_firmar(carga, settings)}"


def leer_sesion(token: str, settings: Settings) -> str | None:
    """El correo si la firma es válida y no venció; en cualquier otro caso, None."""
    try:
        carga, firma = token.split(".")
        if not hmac.compare_digest(firma, _firmar(carga.encode(), settings)):
            return None
        datos = json.loads(base64.urlsafe_b64decode(carga))
        return datos["e"] if datos["x"] > time.time() else None
    except (ValueError, KeyError, TypeError):
        return None


def datos_de_sesion(token: str, settings: Settings) -> dict:
    """Datos firmados de la cookie de sesión ({} si es inválida o venció)."""
    try:
        carga, firma = token.split(".")
        if not hmac.compare_digest(firma, _firmar(carga.encode(), settings)):
            return {}
        datos = json.loads(base64.urlsafe_b64decode(carga))
        return datos if datos["x"] > time.time() else {}
    except (ValueError, KeyError, TypeError):
        return {}


def comprobar_acceso(email: str, settings: Settings) -> None:
    permitidos = settings.allowed_emails_list
    if permitidos and email.lower() not in permitidos:
        log.warning("Acceso denegado a %s (no está en ALLOWED_EMAILS)", email)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Tu cuenta no tiene acceso a esta aplicación.")


def google_info(token: str, settings: Settings) -> dict:
    """Valida un ID token de Google y devuelve sus datos (correo, nombre...); lanza 401, 403 o 503 según el caso."""
    try:
        info = id_token.verify_oauth2_token(
            token, _google_request, settings.google_client_id, clock_skew_in_seconds=10
        )
    except ValueError:
        raise _no_autorizado("Token inválido o expirado") from None
    except google_exceptions.TransportError:
        log.error("No se pudo contactar a Google para verificar el token", exc_info=True)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "No se pudo verificar tu sesión con Google. Intenta de nuevo."
        ) from None
    email = info.get("email")
    if not email or not info.get("email_verified"):
        raise _no_autorizado("La cuenta de Google no tiene un correo verificado")
    comprobar_acceso(email, settings)
    return info  # info["email"] va tal cual lo entrega Google: es la misma clave (usuario_id) con la que ya guardaste tus datos


def verificar_google(token: str, settings: Settings) -> str:
    return google_info(token, settings)["email"]


def get_current_user(
    request: Request,
    credenciales: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> str:
    """Acepta la cookie de sesión propia (30 días) o, como alternativa, el token de Google en Authorization.

    Función síncrona a propósito: FastAPI la ejecuta en un hilo y no bloquea el servidor.
    """
    sesion = request.cookies.get(COOKIE)
    email = leer_sesion(sesion, settings) if sesion else None
    if email:
        # Defensa CSRF extra: con cookie, las escrituras deben traer una cabecera que un sitio ajeno no puede enviar.
        if request.method not in ("GET", "HEAD") and request.headers.get("x-requested-with") != "fetch":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Falta la cabecera de seguridad X-Requested-With")
        comprobar_acceso(email, settings)
        return email
    if credenciales is None:
        raise _no_autorizado("No autorizado. Inicia sesión.")
    return verificar_google(credenciales.credentials, settings)


CurrentUser = Annotated[str, Depends(get_current_user)]
