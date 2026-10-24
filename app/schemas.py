from datetime import date
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

MONTO_MAXIMO = 1_000_000_000


def _redondear_monto(valor: float) -> float:
    valor = round(valor, 2)
    if valor <= 0:
        raise ValueError("El monto debe ser de al menos 0.01")
    return valor


# gt/le/allow_inf_nan validan primero; luego se redondea a centavos.
Monto = Annotated[
    float,
    Field(gt=0, le=MONTO_MAXIMO, allow_inf_nan=False),
    AfterValidator(_redondear_monto),
]


class TransaccionIn(BaseModel):
    """Cuerpo de POST y PUT. extra='forbid' rechaza campos desconocidos."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tipo: Literal["ingreso", "gasto", "ahorro"]
    monto: Monto
    categoria: str = Field(min_length=2, max_length=50)
    metodo_pago: str = Field(min_length=2, max_length=40)
    fecha: date = Field(ge=date(2000, 1, 1), le=date(2100, 12, 31))
    descripcion: str | None = Field(default=None, max_length=200)
    es_recurrente: bool = False
    cuenta_id: str | None = Field(default=None, max_length=24)
    hora: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    subcategoria: str | None = Field(default=None, max_length=50)
    notas: str | None = Field(default=None, max_length=500)
    etiquetas: list[Annotated[str, Field(min_length=1, max_length=30)]] = Field(default_factory=list, max_length=10)

    @field_validator("descripcion", "subcategoria", "notas")
    @classmethod
    def _vacio_es_none(cls, valor: str | None) -> str | None:
        return valor or None

    @field_validator("etiquetas", mode="before")
    @classmethod
    def _limpiar_etiquetas(cls, valor):
        """Quita espacios, descarta vacías y repetidas (conserva el orden)."""
        if not isinstance(valor, list):
            return valor
        vistas: list = []
        for x in valor:
            x = x.strip() if isinstance(x, str) else x
            if x != "" and x not in vistas:
                vistas.append(x)
        return vistas


class TransaccionOut(BaseModel):
    """Lo que ve el cliente. Sin usuario_id ni campos internos.

    Es tolerante a propósito: documentos antiguos pueden no tener metodo_pago o
    es_recurrente, y no queremos romper la lista por un dato legado.
    """

    id: str
    tipo: str
    monto: float
    categoria: str
    metodo_pago: str = "General"
    fecha: str
    descripcion: str | None = None
    es_recurrente: bool = False
    cuenta_id: str | None = None
    hora: str | None = None
    subcategoria: str | None = None
    notas: str | None = None
    etiquetas: list[str] = []


class PaginaTransacciones(BaseModel):
    items: list[TransaccionOut]
    total: int
    pagina: int
    limite: int
    hay_mas: bool


class Totales(BaseModel):
    ingresos: float
    gastos: float
    ahorros: float
    saldo: float  # ingresos - gastos - ahorros


class Resumen(BaseModel):
    periodo: Totales  # el mes filtrado (o todo el historial si no hay filtro)
    acumulado: Totales  # desde el inicio de los tiempos hasta el fin del periodo
    gastos_por_categoria: dict[str, float]
    ahorro_por_categoria: dict[str, float] = {}  # acumulado histórico: sirve para medir las metas
    total_movimientos: int


class MesHistorial(BaseModel):
    mes: str  # "2026-10"
    ingresos: float
    gastos: float
    ahorros: float


class Historial(BaseModel):
    meses: list[MesHistorial]


class ConfigPublica(BaseModel):
    google_client_id: str
