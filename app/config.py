"""Configuración centralizada: TODAS las variables salen de .env / entorno.

pydantic-settings valida los tipos al arrancar: si falta MONGO_URL o GOOGLE_CLIENT_ID
la app se detiene con un mensaje claro, en vez de conectarse a "localhost" por error.
"""
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


def _lista(valor: str) -> list[str]:
    return [x.strip() for x in valor.split(",") if x.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Obligatorias
    mongo_url: str
    google_client_id: str

    # Opcionales (con valores por defecto compatibles con tu base de datos actual)
    mongo_db: str = "control_financiero"
    allowed_emails: str = ""
    sheets_sync_email: str = ""
    sheet_id: str = ""
    template_sheet_id: str = ""   # hoja PLANTILLA que se copia para cada usuario nuevo (opcional)
    google_credentials_file: str = "credenciales.json"
    cors_origins: str = ""

    # Sesión propia (cookie de 30 días). Sin SESSION_SECRET se genera una clave al arrancar.
    session_secret: str = Field(default_factory=lambda: secrets.token_urlsafe(48))
    session_days: int = 30
    cookie_secure: bool = False  # ponlo en true cuando publiques con HTTPS

    @field_validator("session_secret")
    @classmethod
    def _clave_no_vacia(cls, v: str) -> str:
        return v or secrets.token_urlsafe(48)  # un "SESSION_SECRET=" vacío no debe dejar la firma sin clave

    @property
    def session_key(self) -> bytes:
        return self.session_secret.encode()

    @property
    def allowed_emails_list(self) -> list[str]:
        return [e.lower() for e in _lista(self.allowed_emails)]

    @property
    def cors_origins_list(self) -> list[str]:
        return _lista(self.cors_origins)

    @property
    def sheets_habilitado(self) -> bool:
        return bool(self.sheet_id and self.sheets_sync_email)

    @property
    def credentials_path(self) -> Path:
        ruta = Path(self.google_credentials_file)
        return ruta if ruta.is_absolute() else BASE_DIR / ruta


@lru_cache
def get_settings() -> Settings:
    return Settings()
