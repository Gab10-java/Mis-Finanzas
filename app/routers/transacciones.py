"""Endpoints HTTP de transacciones. Solo traduce HTTP <-> repositorio: sin consultas aquí."""
from datetime import date, timedelta
from typing import Annotated, Literal

from bson import ObjectId
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from motor.motor_asyncio import AsyncIOMotorCollection

from .. import repository as repo
from ..database import get_transacciones
from ..exportar import MAX_FILAS_EXPORTACION, generar_csv, generar_pdf
from ..schemas import Historial, PaginaTransacciones, Resumen, TransaccionIn, TransaccionOut
from ..security import CurrentUser
from ..sheets_sync import SheetsSync, get_sheets

router = APIRouter(prefix="/api/transacciones", tags=["Transacciones"])

Coleccion = Annotated[AsyncIOMotorCollection, Depends(get_transacciones)]
Sheets = Annotated[SheetsSync, Depends(get_sheets)]


def periodo_mes(
    anio: Annotated[int | None, Query(ge=2000, le=2100)] = None,
    mes: Annotated[int | None, Query(ge=1, le=12)] = None,
    dia: Annotated[int, Query(ge=1, le=28, description="Día en que empieza tu mes financiero")] = 1,
) -> tuple[int, int, int] | None:
    if (anio is None) != (mes is None):
        raise HTTPException(422, "Envía 'anio' y 'mes' juntos")
    return None if anio is None else (anio, mes, dia)


def periodo_query(
    desde: Annotated[date | None, Query(description="Inicio de un rango libre (YYYY-MM-DD)")] = None,
    hasta: Annotated[date | None, Query(description="Fin del rango, incluido")] = None,
    anio: Annotated[int | None, Query(ge=2000, le=2100)] = None,
    mes: Annotated[int | None, Query(ge=1, le=12)] = None,
    dia: Annotated[int, Query(ge=1, le=28, description="Día en que empieza tu mes financiero")] = 1,
):
    """Un rango libre (desde/hasta) o, si no viene, el mes (anio/mes/dia)."""
    if desde is not None or hasta is not None:
        if desde is None or hasta is None or desde > hasta or (hasta - desde).days > 3660:
            raise HTTPException(422, "Envía 'desde' y 'hasta' juntos (desde no puede ser posterior a hasta; máximo 10 años)")
        return repo.Rango(desde.isoformat(), (hasta + timedelta(days=1)).isoformat())
    return periodo_mes(anio, mes, dia)


def _etiqueta(periodo) -> str:
    if isinstance(periodo, repo.Rango):
        return f"{periodo.inicio}_{(date.fromisoformat(periodo.fin) - timedelta(days=1)).isoformat()}"
    return f"{periodo[0]}-{periodo[1]:02d}"


def object_id(transaccion_id: str) -> ObjectId:
    if not ObjectId.is_valid(transaccion_id):
        raise HTTPException(422, "ID de transacción inválido")
    return ObjectId(transaccion_id)


PeriodoMes = Annotated[tuple[int, int, int] | None, Depends(periodo_mes)]   # solo meses: lo usan las pantallas que dependen del mes
Periodo = Annotated[tuple[int, int, int] | repo.Rango | None, Depends(periodo_query)]
Busqueda = Annotated[str | None, Query(min_length=1, max_length=50, description="Texto a buscar")]
NoEncontrada = HTTPException(status.HTTP_404_NOT_FOUND, "Transacción no encontrada")


@router.get("", response_model=PaginaTransacciones, summary="Listar con paginación y búsqueda")
async def listar(
    usuario: CurrentUser, col: Coleccion, periodo: Periodo, q: Busqueda = None,
    pagina: Annotated[int, Query(ge=1)] = 1,
    limite: Annotated[int, Query(ge=1, le=100)] = 20,
    tipo: Annotated[Literal["ingreso", "gasto", "ahorro"] | None, Query()] = None,
    categoria: Annotated[str | None, Query(max_length=50)] = None,
):
    items, total = await repo.listar(col, usuario, periodo, q, pagina, limite, tipo, categoria)
    return PaginaTransacciones(
        items=items, total=total, pagina=pagina, limite=limite, hay_mas=pagina * limite < total
    )


@router.get("/resumen", response_model=Resumen, summary="Totales calculados en el servidor")
async def resumen(usuario: CurrentUser, col: Coleccion, periodo: Periodo):
    return await repo.resumen(col, usuario, periodo)


@router.get("/exportar", summary="Descargar CSV (UTF-8 con BOM, apto para Excel)")
async def exportar(
    usuario: CurrentUser, col: Coleccion, periodo: Periodo, q: Busqueda = None,
    tipo: Annotated[Literal["ingreso", "gasto", "ahorro"] | None, Query()] = None,
    categoria: Annotated[str | None, Query(max_length=50)] = None,
):
    items = await repo.listar_todas(col, usuario, periodo, q, MAX_FILAS_EXPORTACION, tipo, categoria)
    nombre = f"transacciones_{_etiqueta(periodo)}.csv" if periodo else "transacciones.csv"
    return Response(
        content=generar_csv(items),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


@router.get("/historial", response_model=Historial, summary="Totales de los últimos meses")
async def historial(
    usuario: CurrentUser, col: Coleccion, periodo: PeriodoMes,
    meses: Annotated[int, Query(ge=2, le=24)] = 6,
):
    hoy = date.today()
    return Historial(meses=await repo.historial(col, usuario, periodo or (hoy.year, hoy.month, 1), meses))


@router.get("/exportar-pdf", summary="Informe en PDF: el resumen del periodo y la lista con los mismos filtros")
async def exportar_pdf(
    usuario: CurrentUser, col: Coleccion, periodo: Periodo, q: Busqueda = None,
    tipo: Annotated[Literal["ingreso", "gasto", "ahorro"] | None, Query()] = None,
    categoria: Annotated[str | None, Query(max_length=50)] = None,
):
    items = await repo.listar_todas(col, usuario, periodo, q, MAX_FILAS_EXPORTACION, tipo, categoria)
    resumen = await repo.resumen(col, usuario, periodo)
    titulo = "Todo el historial" if not periodo else (f"Del {periodo.inicio} al {(date.fromisoformat(periodo.fin) - timedelta(days=1)).isoformat()}" if isinstance(periodo, repo.Rango)
                                                   else f"Periodo {periodo[0]}-{periodo[1]:02d}" + (f" (desde el día {periodo[2]})" if periodo[2] > 1 else ""))
    filtros = [f for f in (f"tipo {tipo}" if tipo else "", f"categoría {categoria}" if categoria else "", f"búsqueda «{q}»" if q else "") if f]
    nombre = f"informe_{_etiqueta(periodo)}.pdf" if periodo else "informe.pdf"
    return Response(content=generar_pdf(items, resumen, titulo, filtros), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@router.post("", response_model=TransaccionOut, status_code=status.HTTP_201_CREATED)
async def crear(
    datos: TransaccionIn, usuario: CurrentUser, col: Coleccion, sheets: Sheets,
    tareas: BackgroundTasks,
):
    nueva = await repo.crear(col, usuario, datos)
    tareas.add_task(sheets.solicitar, usuario)
    return nueva


@router.put("/{transaccion_id}", response_model=TransaccionOut)
async def actualizar(
    oid: Annotated[ObjectId, Depends(object_id)], datos: TransaccionIn,
    usuario: CurrentUser, col: Coleccion, sheets: Sheets, tareas: BackgroundTasks,
):
    actualizada = await repo.actualizar(col, usuario, oid, datos)
    if actualizada is None:
        raise NoEncontrada
    tareas.add_task(sheets.solicitar, usuario)
    return actualizada


@router.delete("/{transaccion_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar(
    oid: Annotated[ObjectId, Depends(object_id)],
    usuario: CurrentUser, col: Coleccion, sheets: Sheets, tareas: BackgroundTasks,
):
    if not await repo.eliminar(col, usuario, oid):
        raise NoEncontrada
    tareas.add_task(sheets.solicitar, usuario)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
