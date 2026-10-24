"""Unifica las categorías antiguas de tus GASTOS con las del desplegable (ej. "Chifa / Pollería" -> "Comida").

Uso:  python migrar_categorias.py            (simulación: solo muestra qué cambiaría)
      python migrar_categorias.py --aplicar  (guarda los cambios)
"""
import re
import sys

from pymongo import MongoClient

from app.config import get_settings

REGLAS = [  # (patrón sin distinguir mayúsculas, categoría nueva); gana la primera que coincida
    (r"chifa|poller|comida|rappi|delivery|kfc|mcdonald|bembos|almuerzo|cena|desayuno", "Comida"),
    (r"taxi|uber|combi|bus\b|pasaje|transporte|metro|gasolina", "Transporte"),
    (r"univers|curso|libro|matr[ií]cula|pensi[oó]n|fotocopia", "Universidad"),
    (r"luz|agua|internet|alquiler|renta|servicio|cable", "Hogar y servicios"),
    (r"super|ropa|compra|zapat|tienda", "Compras"),
    (r"cine|juego|steam|salida|fiesta|discoteca|ocio", "Ocio y salidas"),
    (r"salud|farmacia|doctor|m[eé]dic|cl[ií]nica|dentista", "Salud"),
    (r"mascota|gato|perro|veterin", "Mascotas"),
    (r"netflix|spotify|suscrip|youtube|prime|disney|hbo|icloud", "Suscripciones"),
    (r"viaje|vuelo|latam|jet|hotel", "Viajes"),
]
CANONICAS = {destino for _, destino in REGLAS}


def nueva(categoria: str) -> str | None:
    if categoria in CANONICAS:
        return None
    return next((d for patron, d in REGLAS if re.search(patron, categoria, re.I)), None)


def main() -> None:
    s = get_settings()
    col = MongoClient(s.mongo_url)[s.mongo_db]["transacciones"]
    cambios = {c: nueva(c) for c in col.distinct("categoria", {"tipo": "gasto"}) if nueva(c)}
    if not cambios:
        return print("Nada que migrar.")
    for viejo, nuevo in cambios.items():
        print(f"{viejo!r:45} -> {nuevo}")
    if "--aplicar" not in sys.argv:
        return print("\nSimulación: ejecuta con --aplicar para guardar.")
    for viejo, nuevo in cambios.items():
        r = col.update_many({"tipo": "gasto", "categoria": viejo}, {"$set": {"categoria": nuevo}})
        print(f"{r.modified_count} movimientos: {viejo} -> {nuevo}")


if __name__ == "__main__":
    main()
