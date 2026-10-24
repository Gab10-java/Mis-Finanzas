"""Punto de entrada. Ejecutar con:  uvicorn app.main:app --reload"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pymongo.errors import PyMongoError

from .config import BASE_DIR, get_settings
from .database import NOMBRE_COLECCION, asegurar_indices, crear_cliente
from .routers import auth, planificacion, presupuestos, sistema, transacciones
from .sheets_sync import SheetsSync

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
log = logging.getLogger("app")

FRONTEND_DIR = BASE_DIR / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Código de arranque (antes del yield) y de cierre (después)."""
    settings = get_settings()
    cliente = crear_cliente(settings.mongo_url)
    try:
        await cliente.admin.command("ping")  # falla rápido y claro si la URL/credenciales están mal
    except PyMongoError:
        log.critical("No se pudo conectar a MongoDB. Revisa MONGO_URL en tu .env", exc_info=True)
        cliente.close()
        raise

    coleccion = cliente[settings.mongo_db][NOMBRE_COLECCION]
    await asegurar_indices(coleccion)
    app.state.transacciones = coleccion
    app.state.db = cliente[settings.mongo_db]
    await app.state.db.cuentas.create_index([("usuario_id", 1), ("nombre", 1)], unique=True, name="usuario_nombre")
    await app.state.db.metas.create_index([("usuario_id", 1)], name="usuario")
    await app.state.db.transferencias.create_index([("usuario_id", 1), ("fecha", -1)], name="usuario_fecha")
    app.state.presupuestos = cliente[settings.mongo_db]["presupuestos"]
    await app.state.presupuestos.create_index([("usuario_id", 1), ("categoria", 1)], unique=True, name="usuario_categoria")
    app.state.sheets = SheetsSync(settings, coleccion)
    if "session_secret" not in settings.model_fields_set:
        log.warning("SESSION_SECRET no está definido: las sesiones se cerrarán cada vez que reinicies el servidor.")
    log.info("API lista. BD=%s | Sheets=%s", settings.mongo_db, "ON" if app.state.sheets._activo else "OFF")
    yield
    cliente.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="API Control Financiero", version="2.0.0", lifespan=lifespan)

    # CORS solo si el frontend vive en OTRO origen. Servido desde aquí mismo no hace falta.
    if settings.cors_origins_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins_list,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
            expose_headers=["Content-Disposition"],
            allow_credentials=False,  # la auth va en el header Bearer, no en cookies
        )

    @app.middleware("http")
    async def cabeceras_de_seguridad(request: Request, call_next):
        respuesta = await call_next(request)
        respuesta.headers["X-Content-Type-Options"] = "nosniff"
        respuesta.headers["X-Frame-Options"] = "DENY"
        respuesta.headers["Referrer-Policy"] = "same-origin"
        return respuesta

    @app.exception_handler(PyMongoError)
    async def error_de_base_de_datos(request: Request, exc: PyMongoError):
        log.error("Error de MongoDB en %s %s", request.method, request.url.path, exc_info=exc)
        return JSONResponse(
            status_code=503, content={"detail": "Servicio de base de datos no disponible"}
        )

    app.include_router(sistema.router)
    app.include_router(auth.router)
    app.include_router(presupuestos.router)
    app.include_router(planificacion.router)
    app.include_router(transacciones.router)

    # El frontend se monta AL FINAL: así cualquier ruta /api/... tiene prioridad.
    if FRONTEND_DIR.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    return app


app = create_app()
