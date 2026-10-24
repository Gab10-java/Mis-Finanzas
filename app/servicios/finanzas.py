"""Cálculos financieros puros (sin base de datos): fáciles de probar. Todo el dinero se calcula con Decimal."""
import calendar
import math
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
CERO = Decimal(0)
AVISO = "Es una estimación basada en lo que registraste; no garantiza tu disponibilidad futura."


def d(valor) -> Decimal:
    """Convierte a Decimal con 2 decimales (via texto, para que 0.1 sea exactamente 0.10)."""
    return Decimal(str(valor or 0)).quantize(CENT, ROUND_HALF_UP)


def soles(x) -> str:
    return f"S/ {x:,.2f}"


def saldos_cuentas(cuentas: list[dict], transacciones: list[dict], transferencias: list[dict], movs_meta: list[dict]) -> dict[str, Decimal]:
    """Saldo de cada cuenta.

    Ingreso suma; gasto y ahorro salen de la cuenta. Una transferencia resta del origen y suma al destino (el total no cambia
    y no es ingreso ni gasto). Un aporte a una meta sale de la cuenta; un retiro (monto negativo) vuelve a ella.
    """
    s = {c["id"]: d(c.get("saldo_inicial")) for c in cuentas}
    for t in transacciones:
        if t.get("cuenta_id") in s:
            s[t["cuenta_id"]] += d(t["monto"]) if t["tipo"] == "ingreso" else -d(t["monto"])
    for tr in transferencias:
        if tr["origen"] in s:
            s[tr["origen"]] -= d(tr["monto"])
        if tr["destino"] in s:
            s[tr["destino"]] += d(tr["monto"])
    for m in movs_meta:
        if m["cuenta_id"] in s:
            s[m["cuenta_id"]] -= d(m["monto"])
    return s


def progreso_meta(meta: dict, hoy: date) -> dict:
    objetivo = d(meta["objetivo"])
    ahorrado = sum((d(a["monto"]) if a["tipo"] == "aporte" else -d(a["monto"]) for a in meta.get("aportes", [])), CERO)
    falta = max(objetivo - ahorrado, CERO)
    r = {"ahorrado": ahorrado, "falta": falta, "porcentaje": int(min(ahorrado / objetivo * 100, 100)) if objetivo > 0 else 0,
         "semanal": None, "mensual": None, "vencida": False}
    if meta.get("fecha_limite") and falta > 0:
        dias = (date.fromisoformat(meta["fecha_limite"]) - hoy).days
        if dias <= 0:
            r["vencida"] = True
        else:  # al menos 1 semana / 1 mes: no se pide ahorrar "más de lo que falta" en un solo periodo
            r["semanal"] = (falta / max(Decimal(dias) / 7, Decimal(1))).quantize(CENT, ROUND_HALF_UP)
            r["mensual"] = (falta / max(Decimal(dias) / Decimal("30.4375"), Decimal(1))).quantize(CENT, ROUND_HALF_UP)
    return r


def apartado_ahorro(metas: list[dict], hoy: date) -> Decimal:
    """Lo que aún falta apartar ESTE mes para las metas con fecha límite (descuenta lo ya aportado este mes)."""
    total = CERO
    for m in metas:
        mensual = progreso_meta(m, hoy)["mensual"]
        if mensual is None:
            continue
        este_mes = sum((d(a["monto"]) for a in m.get("aportes", []) if a["tipo"] == "aporte" and a["fecha"][:7] == hoy.isoformat()[:7]), CERO)
        total += max(mensual - este_mes, CERO)
    return total


def dinero_disponible(saldo_real: Decimal, apartado: Decimal, esenciales: Decimal, reservado_pagos: Decimal = CERO, compra: Decimal | None = None) -> dict:
    """saldo real - pagos reservados - ahorro apartado - necesidades esenciales por cubrir = dinero para gastos flexibles."""
    base = saldo_real - reservado_pagos - apartado - esenciales
    f = float
    r = {"saldo_real": f(saldo_real), "reservado_pagos": f(reservado_pagos), "apartado_ahorro": f(apartado), "esenciales_restantes": f(esenciales),
         "disponible": f(max(base, CERO)), "deficit": f(max(-base, CERO)), "mensajes": [], "aviso": AVISO}
    if base < 0:
        r["mensajes"].append(f"Tus reservas y necesidades superan tu saldo en {soles(-base)}: hoy no tienes dinero libre para gastos flexibles.")
    if compra is not None:
        despues = base - compra
        r["compra"] = {"precio": f(compra), "alcanza": despues >= 0, "disponible_despues": f(max(despues, CERO)), "faltante": f(max(-despues, CERO))}
        r["mensajes"].append(
            f"Después de esta compra te quedarían {soles(despues)} para gastos flexibles." if despues >= 0 else
            f"Esta compra supera tu dinero disponible por {soles(-despues)}: tendrías que usar dinero reservado para ahorro, necesidades o pagos.")
    return r


def nivel_aviso(porcentaje: int, avisos: list[int]) -> str:
    """ok / aviso1 / aviso2 / limite según los 3 porcentajes de aviso configurados (por defecto 75, 90 y 100)."""
    return "limite" if porcentaje >= avisos[2] else "aviso2" if porcentaje >= avisos[1] else "aviso1" if porcentaje >= avisos[0] else "ok"


def siguiente_fecha(fecha: date, frecuencia: str, dia: int | None = None) -> date:
    """Próximo vencimiento. `dia` es el día original (31, por ejemplo): en febrero cae el 28 y en marzo vuelve al 31."""
    if frecuencia == "semanal":
        return fecha + timedelta(days=7)
    total = fecha.month - 1 + {"mensual": 1, "trimestral": 3, "anual": 12}[frecuencia]
    anio, mes = fecha.year + total // 12, total % 12 + 1
    return date(anio, mes, min(dia or fecha.day, calendar.monthrange(anio, mes)[1]))


def interes_mensual(saldo, tasa_anual) -> Decimal:
    return (d(saldo) * Decimal(str(tasa_anual)) / 1200).quantize(CENT, ROUND_HALF_UP)


def meses_para_pagar(saldo, tasa_anual, cuota) -> int | None:
    """Meses que faltan pagando siempre la misma cuota; None si la cuota no alcanza ni para los intereses."""
    saldo, cuota = d(saldo), d(cuota)
    if saldo <= 0:
        return 0
    if cuota <= 0:
        return None
    r = float(tasa_anual) / 1200
    if r == 0:
        return math.ceil(saldo / cuota)
    if float(cuota) <= float(saldo) * r:
        return None
    return math.ceil(-math.log(1 - r * float(saldo) / float(cuota)) / math.log(1 + r))


def reservado(pagos: list[dict], deudas: list[dict], hasta: date) -> tuple[Decimal, dict[str, Decimal]]:
    """Pagos pendientes y cuotas de deudas que vencen hasta `hasta` (incluidos los vencidos): total y desglose por categoría."""
    total, por_cat = CERO, {}
    for p in pagos:
        if p.get("estado", "activo") == "activo" and date.fromisoformat(p["proximo"]) <= hasta:
            m = d(p["monto"])
            total += m
            por_cat[p["categoria"]] = por_cat.get(p["categoria"], CERO) + m
    for dd in deudas:
        if dd.get("cuota") and dd.get("proximo_vencimiento") and d(dd["saldo"]) > 0 and date.fromisoformat(dd["proximo_vencimiento"]) <= hasta:
            m = min(d(dd["cuota"]), d(dd["saldo"]))
            total += m
            por_cat["Deudas"] = por_cat.get("Deudas", CERO) + m
    return total, por_cat


def proyectar(saldo: Decimal, eventos: list[tuple[date, Decimal]], habitual_diario: Decimal, hoy: date, dias: int) -> list[dict]:
    """Un punto por día: saldo con solo tus obligaciones conocidas, y saldo además con tu ritmo de gasto habitual."""
    salidas: dict[date, Decimal] = {}
    for f, m in eventos:
        salidas[f] = salidas.get(f, CERO) + m
    solo = con_ritmo = saldo
    puntos = []
    for i in range(1, dias + 1):
        f = hoy + timedelta(days=i)
        solo -= salidas.get(f, CERO)
        con_ritmo = con_ritmo - salidas.get(f, CERO) - habitual_diario
        puntos.append({"fecha": f.isoformat(), "solo_obligaciones": float(solo), "con_ritmo": float(con_ritmo)})
    return puntos


def a_centavos(monto) -> int:
    """Monto en soles -> centavos ENTEROS (0.1 -> 10). Las sumas de enteros no acumulan errores de redondeo."""
    return int((Decimal(str(monto)) * 100).to_integral_value(ROUND_HALF_UP))
