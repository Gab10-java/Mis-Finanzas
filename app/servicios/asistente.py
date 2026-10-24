"""Asistente financiero: reglas deterministas sobre TUS datos. Cada observación explica cómo se calculó.

No usa inteligencia artificial ni envía información a servicios externos. Las proyecciones se redactan como
posibilidades ("podrías"), no como hechos.
"""
from datetime import date, timedelta
from decimal import Decimal

from . import finanzas as fin

ORDEN = {"alerta": 0, "aviso": 1, "info": 2}


def _obs(id_: str, nivel: str, mensaje: str, como: str) -> dict:
    return {"id": id_, "nivel": nivel, "mensaje": mensaje, "como_se_calculo": como}


def equivalente_mensual(monto, frecuencia: str) -> Decimal:
    m = fin.d(monto)
    return {"semanal": m * Decimal("4.345"), "mensual": m, "trimestral": m / 3, "anual": m / 12, "unica": fin.CERO}[frecuencia].quantize(fin.CENT)


def observaciones(*, hoy: date, inicio: date, fin_excl: date, actual: dict, anterior: dict, presupuestos: list[dict], avisos: list[int],
                  pagos: list[dict], metas: list[dict], umbral_aumento: int = 20, disponible: dict | None = None, saldo_minimo: float = 0) -> list[dict]:
    soles, out = fin.soles, []

    # 1) Categorías que subieron frente al periodo anterior (con un mínimo de S/ 20 para no avisar por centavos)
    for cat, valor in actual.items():
        a, b = fin.d(valor), fin.d(anterior.get(cat, 0))
        if b > 0 and a - b >= 20 and (a - b) / b * 100 >= umbral_aumento:
            pct = int((a - b) / b * 100)
            out.append(_obs(f"aumento-{cat}", "aviso", f"Has gastado más en {cat} que el periodo pasado (+{pct}%).",
                            f"Comparé {cat}: {soles(a)} en este periodo frente a {soles(b)} en el anterior (+{pct}%; tu umbral es {umbral_aumento}%)."))

    # 2) Presupuestos: cerca del límite, superados, o con un ritmo que podría superarlos
    dias_pasados, dias_total = (hoy - inicio).days + 1, (fin_excl - inicio).days
    for p in presupuestos:
        limite, gastado = fin.d(p["limite"]), fin.d(p["gastado"])
        if limite <= 0:
            continue
        pct = int(gastado / limite * 100)
        como = f"Llevas {soles(gastado)} de {soles(limite)} ({pct}%) en {p['categoria']}."
        if pct >= avisos[2]:
            out.append(_obs(f"limite-{p['categoria']}", "alerta", f"Superaste tu presupuesto de {p['categoria']}.", como))
        elif pct >= avisos[1]:
            out.append(_obs(f"cerca-{p['categoria']}", "aviso", f"Estás muy cerca del límite de {p['categoria']} ({pct}%).", como))
        elif pct >= avisos[0]:
            out.append(_obs(f"cerca-{p['categoria']}", "aviso", f"Estás cerca del límite de {p['categoria']} ({pct}%).", como))
        elif dias_pasados >= 5 and hoy < fin_excl and gastado > 0:
            proyectado = (gastado / dias_pasados * dias_total).quantize(fin.CENT)
            if proyectado > limite:
                out.append(_obs(f"ritmo-{p['categoria']}", "aviso", f"Si mantienes el ritmo actual, podrías superar tu presupuesto de {p['categoria']}.",
                                f"{como} Han pasado {dias_pasados} de {dias_total} días; a ese ritmo llegarías a unos {soles(proyectado)}. Es una estimación, no un hecho."))

    # 3) Peso de las suscripciones entre los gastos recurrentes (solo si hay al menos 3 pagos recurrentes)
    activos = [x for x in pagos if x.get("estado", "activo") == "activo" and x["frecuencia"] != "unica"]
    if len(activos) >= 3:
        total = sum((equivalente_mensual(x["monto"], x["frecuencia"]) for x in activos), fin.CERO)
        subs = sum((equivalente_mensual(x["monto"], x["frecuencia"]) for x in activos if x["categoria"] == "Suscripciones"), fin.CERO)
        if total > 0 and subs / total >= Decimal("0.3"):
            pct = int(subs / total * 100)
            out.append(_obs("suscripciones", "info", f"Tus suscripciones representan el {pct}% de tus gastos recurrentes.",
                            f"Pasé tus {len(activos)} pagos recurrentes a su equivalente mensual: {soles(total)} en total, de los cuales {soles(subs)} son suscripciones."))

    # 4) Metas: ¿tu ritmo de ahorro de los últimos 90 días alcanza para llegar a tiempo?
    desde = (hoy - timedelta(days=90)).isoformat()
    for m in metas:
        p = fin.progreso_meta(m, hoy)
        if p["semanal"] is None:
            continue
        neto = sum((fin.d(a["monto"]) if a["tipo"] == "aporte" else -fin.d(a["monto"]) for a in m.get("aportes", []) if a["fecha"] >= desde), fin.CERO)
        ritmo = (neto / 90 * 7).quantize(fin.CENT)
        como = f"Te faltan {soles(p['falta'])} y la fecha límite es {m['fecha_limite']}: necesitas {soles(p['semanal'])} por semana. En 90 días ahorraste {soles(ritmo)} por semana en promedio."
        if p["semanal"] > ritmo * Decimal("1.1"):
            extra = p["semanal"] - max(ritmo, fin.CERO)
            out.append(_obs(f"meta-{m['nombre']}", "aviso", f"Para alcanzar «{m['nombre']}» a tiempo necesitas ahorrar {soles(extra)} más cada semana.", como))
        else:
            out.append(_obs(f"meta-{m['nombre']}", "info", f"Vas a buen ritmo para alcanzar «{m['nombre']}».", como))

    # 5) Pagos vencidos
    vencidos = [x for x in pagos if x.get("estado", "activo") == "activo" and date.fromisoformat(x["proximo"]) < hoy]
    if vencidos:
        out.append(_obs("vencidos", "alerta", f"Tienes {len(vencidos)} pago(s) vencido(s): {', '.join(x['nombre'] for x in vencidos[:3])}.",
                        "Revisé tus pagos programados activos con fecha de vencimiento anterior a hoy."))

    # 6) Dinero disponible y saldo mínimo
    if disponible and disponible["deficit"] > 0:
        out.append(_obs("deficit", "alerta", f"Tus reservas y necesidades superan tu saldo en {soles(fin.d(disponible['deficit']))}.",
                        "Saldo líquido − pagos reservados − ahorro apartado − necesidades esenciales dio un resultado negativo."))
    if disponible and saldo_minimo > 0 and disponible["saldo_real"] < saldo_minimo:
        out.append(_obs("saldo-minimo", "alerta", f"Tu saldo líquido ({soles(fin.d(disponible['saldo_real']))}) está por debajo del mínimo que configuraste ({soles(fin.d(saldo_minimo))}).",
                        "Comparé la suma de tus cuentas (sin cuentas de ahorro) con tu alerta de saldo mínimo."))
    return sorted(out, key=lambda o: ORDEN[o["nivel"]])
