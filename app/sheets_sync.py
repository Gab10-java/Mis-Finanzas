"""Espejo de las transacciones en una hoja de Google Sheets.

Problemas del original que se corrigen aquí:
  * gspread es BLOQUEANTE y se llamaba dentro de una función async: congelaba TODO el servidor
    durante la sincronización. Ahora la parte bloqueante corre en un hilo (asyncio.to_thread).
  * Cada alta/edición/baja lanzaba una sincronización completa; varias seguidas competían entre
    sí (clear + update intercalados) y agotaban la cuota de Google. Ahora se "agrupan": nunca hay
    más de una en curso y, a lo sumo, una pendiente.
  * Cualquier usuario autenticado podía pisar la hoja del dueño. Ahora solo sincroniza
    SHEETS_SYNC_EMAIL.
  * Las credenciales se leían al importar el módulo (con ruta relativa). Ahora se cargan al
    primer uso y con ruta absoluta.
"""
import asyncio
import logging

import gspread
from fastapi import Request
from google.oauth2.service_account import Credentials
from motor.motor_asyncio import AsyncIOMotorCollection

from .config import Settings
from .exportar import ENCABEZADOS, a_fila
from .schemas import TransaccionOut

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]  # mínimo necesario (sin Drive completo)
ULTIMA_COLUMNA = chr(ord("A") + len(ENCABEZADOS) - 1)  # "G"


class SheetsSync:
    MAX_INTENTOS = 3
    RETRASO_BASE = 2  # segundos; espera 2 s, 4 s... entre reintentos (retroceso exponencial)

    def __init__(self, settings: Settings, coleccion: AsyncIOMotorCollection):
        self._settings = settings
        self._col = coleccion
        self._lock = asyncio.Lock()
        self._pendiente = False
        self._usuario: str | None = None
        self._hoja = None  # worksheet de gspread, se abre una sola vez
        self._cabecera_formateada = False

        self._activo = settings.sheets_habilitado
        if self._activo and not settings.credentials_path.is_file():
            log.warning(
                "Sincronización con Sheets DESACTIVADA: no existe %s", settings.credentials_path
            )
            self._activo = False

    def habilitado_para(self, usuario: str) -> bool:
        return self._activo and usuario.lower() == self._settings.sheets_sync_email.lower()

    async def solicitar(self, usuario: str) -> None:
        """Se llama en segundo plano tras cada cambio. Agrupa solicitudes simultáneas."""
        if not self.habilitado_para(usuario):
            return
        self._usuario = usuario
        self._pendiente = True
        if self._lock.locked():
            return  # ya hay una en curso; al terminar verá _pendiente y repetirá una vez
        async with self._lock:
            while self._pendiente:
                self._pendiente = False
                await self._sincronizar_con_reintentos()

    async def _sincronizar_con_reintentos(self) -> None:
        for intento in range(1, self.MAX_INTENTOS + 1):
            try:
                docs = await (
                    self._col.find({"usuario_id": self._usuario})
                    .sort([("fecha", 1), ("_id", 1)])  # cronológico: antiguo → nuevo
                    .to_list(length=None)
                )
                filas = [ENCABEZADOS] + [
                    a_fila(TransaccionOut.model_validate({**d, "id": str(d["_id"])})) for d in docs
                ]
                await asyncio.to_thread(self._escribir, filas)  # parte bloqueante, en otro hilo
                log.info("Hoja de Sheets sincronizada (%d movimientos)", len(filas) - 1)
                return
            except Exception:
                self._hoja = None  # forzar a reabrir la hoja en el próximo intento
                log.warning(
                    "Fallo al sincronizar con Sheets (intento %d/%d)",
                    intento, self.MAX_INTENTOS, exc_info=True,
                )
                if intento < self.MAX_INTENTOS:
                    await asyncio.sleep(self.RETRASO_BASE * 2 ** (intento - 1))
        log.error("Sincronización con Sheets fallida; se reintentará con el próximo cambio.")

    # ---- Parte bloqueante: se ejecuta SIEMPRE dentro de un hilo ----

    def _obtener_hoja(self):
        if self._hoja is None:
            credenciales = Credentials.from_service_account_file(
                str(self._settings.credentials_path), scopes=SCOPES
            )
            cliente = gspread.authorize(credenciales)
            self._hoja = cliente.open_by_key(self._settings.sheet_id).sheet1
        return self._hoja

    def _escribir(self, filas: list[list]) -> None:
        hoja = self._obtener_hoja()
        # 1º se ESCRIBE y luego se borra lo sobrante: si falla a mitad, la hoja no queda vacía.
        hoja.update(values=filas, range_name="A1", raw=True)  # raw=True: nada se evalúa como fórmula
        hoja.batch_clear([f"A{len(filas) + 1}:{ULTIMA_COLUMNA}"])  # filas viejas que quedaron debajo
        if not self._cabecera_formateada:
            hoja.format(f"A1:{ULTIMA_COLUMNA}1", {"textFormat": {"bold": True}})
            self._cabecera_formateada = True


def get_sheets(request: Request) -> SheetsSync:
    return request.app.state.sheets
