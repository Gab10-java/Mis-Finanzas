"""Añade `monto_centavos` (entero) a tus movimientos antiguos. Antes de modificar nada guarda un respaldo JSON.

Uso:  python migrar_centavos.py            (simulación: solo cuenta cuántos faltan)
      python migrar_centavos.py --aplicar  (guarda un respaldo y luego migra)
Es opcional: los totales ya funcionan con movimientos antiguos; esto solo deja todos los datos con el mismo formato.
"""
import sys
from datetime import date

from bson import json_util
from pymongo import MongoClient

from app.config import get_settings
from app.migraciones import migrar_centavos


def main() -> None:
    s = get_settings()
    col = MongoClient(s.mongo_url)[s.mongo_db]["transacciones"]
    print(f"Movimientos sin monto_centavos: {migrar_centavos(col)['pendientes']}")
    if "--aplicar" not in sys.argv:
        return print("Simulación: ejecuta con --aplicar para guardar los cambios.")
    ruta = f"respaldo_antes_de_centavos_{date.today().isoformat()}.json"
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(json_util.dumps(list(col.find({})), ensure_ascii=False))
    print("Respaldo guardado en", ruta)
    print(f"Migrados: {migrar_centavos(col, aplicar=True)['migrados']}")


if __name__ == "__main__":
    main()
