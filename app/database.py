"""Acceso a MongoDB: cliente, índices y la dependencia que inyecta la colección."""
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from fastapi import Request

NOMBRE_COLECCION = "transacciones"


def crear_cliente(mongo_url: str) -> AsyncIOMotorClient:
    # serverSelectionTimeoutMS: si Atlas no responde, falla en 5 s (no se cuelga 30 s).
    return AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=5000)


async def asegurar_indices(coleccion: AsyncIOMotorCollection) -> None:
    """Índice compuesto que cubre el filtro (usuario_id) y el orden (fecha, _id).

    Sin índice, cada consulta recorre TODA la colección. create_index es idempotente:
    si ya existe, no hace nada.
    """
    await coleccion.create_index(
        [("usuario_id", 1), ("fecha", -1), ("_id", -1)], name="usuario_fecha"
    )


def get_transacciones(request: Request) -> AsyncIOMotorCollection:
    """Dependencia de FastAPI: los routers piden la colección, no la importan.

    Así los tests pueden reemplazarla por una base de datos falsa sin tocar el código.
    """
    return request.app.state.transacciones


def get_presupuestos(request: Request) -> AsyncIOMotorCollection:
    return request.app.state.presupuestos


def get_db(request: Request):
    return request.app.state.db
