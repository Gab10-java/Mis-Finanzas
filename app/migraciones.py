"""Migraciones de datos: se ejecutan a mano con los scripts de la raíz del proyecto."""
from .servicios.finanzas import a_centavos


def migrar_centavos(col, aplicar: bool = False) -> dict:
    """Añade `monto_centavos` a los movimientos que no lo tienen. `col` es una colección SÍNCRONA de pymongo."""
    pendientes = list(col.find({"monto_centavos": {"$exists": False}}, {"monto": 1}))
    if aplicar:
        for d in pendientes:
            col.update_one({"_id": d["_id"]}, {"$set": {"monto_centavos": a_centavos(d["monto"])}})
    return {"pendientes": len(pendientes), "migrados": len(pendientes) if aplicar else 0}
