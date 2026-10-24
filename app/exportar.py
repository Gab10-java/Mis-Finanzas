"""Formato tabular compartido por el CSV descargable y por la hoja de Google Sheets."""
import csv
import io

from fpdf import FPDF

from .schemas import Resumen, TransaccionOut
from .servicios.finanzas import CERO, d, soles

ENCABEZADOS = ["Fecha", "Tipo", "Categoría", "Descripción", "Monto", "Método", "Recurrente"]
MAX_FILAS_EXPORTACION = 5000

# Un texto que empieza con estos caracteres puede ejecutarse como FÓRMULA en Excel/Sheets.
_PELIGROSOS = ("=", "+", "-", "@", "\t", "\r")


def a_fila(t: TransaccionOut) -> list:
    return [
        t.fecha,
        t.tipo.capitalize(),
        t.categoria,
        t.descripcion or "",
        t.monto,
        t.metodo_pago,
        "Sí" if t.es_recurrente else "No",
    ]


ENCABEZADOS_CSV = ENCABEZADOS + ["Hora", "Subcategoría", "Etiquetas", "Notas"]


def a_fila_csv(t: TransaccionOut) -> list:
    return a_fila(t) + [t.hora or "", t.subcategoria or "", ", ".join(t.etiquetas), t.notas or ""]


def _neutralizar(celda):
    """Antepone ' a los textos que parecen fórmulas (inyección de fórmulas en CSV)."""
    if isinstance(celda, str) and celda.startswith(_PELIGROSOS):
        return "'" + celda
    return celda


def generar_csv(items: list[TransaccionOut]) -> bytes:
    """CSV en UTF-8 con BOM: sin el BOM, Excel muestra 'CategorÃ­a' en vez de 'Categoría'."""
    salida = io.StringIO()
    escritor = csv.writer(salida)  # csv.writer escapa comillas y comas correctamente
    escritor.writerow(ENCABEZADOS_CSV)
    for t in items:
        escritor.writerow([_neutralizar(c) for c in a_fila_csv(t)])
    return b"\xef\xbb\xbf" + salida.getvalue().encode("utf-8")


def _t(texto) -> str:
    """El PDF usa la fuente estándar (Latin-1): tildes y ñ salen bien; emojis y símbolos raros se reemplazan por '?'."""
    return str(texto).encode("latin-1", "replace").decode("latin-1")


def generar_pdf(items: list[TransaccionOut], resumen: Resumen, titulo: str, filtros=()) -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    def linea(texto, alto=6, estilo="", tam=10):
        pdf.set_font("Helvetica", estilo, tam)
        pdf.multi_cell(0, alto, _t(texto), new_x="LMARGIN", new_y="NEXT")

    linea("Mi Billetera - Informe", 9, "B", 16)
    linea(titulo)
    if filtros:
        linea("Filtros aplicados: " + ", ".join(filtros))
    p = resumen.periodo
    linea(f"Resumen del periodo (sin filtros): ingresos {soles(p.ingresos)}, gastos {soles(p.gastos)}, ahorros {soles(p.ahorros)}, resultado {soles(p.saldo)}")
    linea(f"Movimientos listados: {len(items)}")
    pdf.ln(2)
    anchos = (24, 22, 52, 44, 32)

    def fila(celdas, estilo=""):
        pdf.set_font("Helvetica", estilo, 9)
        for ancho, texto, alineacion in zip(anchos, celdas, "LLLLR"):
            pdf.cell(ancho, 6, _t(texto)[: int(ancho / 1.9)], border=1, align=alineacion)
        pdf.ln(6)

    fila(["Fecha", "Tipo", "Categoría", "Método", "Monto"], "B")
    totales = {"ingreso": CERO, "gasto": CERO, "ahorro": CERO}
    for t in items:
        fila([t.fecha, t.tipo.capitalize(), t.categoria, t.metodo_pago, soles(d(t.monto))])
        totales[t.tipo] = totales.get(t.tipo, CERO) + d(t.monto)
    pdf.ln(3)
    linea(f"Total de la lista: ingresos {soles(totales['ingreso'])}, gastos {soles(totales['gasto'])}, ahorros {soles(totales['ahorro'])}", 6, "B")
    return bytes(pdf.output())
