// Fase 2: dinero disponible, cuentas, transferencias y metas de ahorro.
import { $, api, avisar, CATEGORIAS, cargarPresupuestos, el, hoy, PEN, periodoQS, refrescar, toast } from "./app.js";

const f2 = { cuentas: [] };
const ICONO = { efectivo: "💵", banco: "🏦", billetera: "📱", ahorros: "🐷", otra: "💼" };
const OPC_CUENTAS = () => f2.cuentas.map((c) => [c.id, `${c.nombre} (${PEN.format(c.saldo)})`]);
const recargar = async () => {
  await Promise.all([cargarCuentas(), cargarCategorias()]);   // las transferencias necesitan los nombres de las cuentas
  await Promise.all([cargarMetas(), cargarDisponible(), cargarTransferencias(), cargarPagos(), cargarDeudas(), cargarComparacion(), cargarAsistente(), cargarProyeccion()]);
};
document.addEventListener("datos-actualizados", recargar);

// ---------- Diálogo genérico: un solo formulario para cuentas, transferencias, metas y aportes ----------
const dlg = $("#dlg-f2"), form = $("#form-f2");
let enviar = null;

function dialogo(titulo, campos, alEnviar) {
  $("#f2-t").textContent = titulo; $("#f2-err").textContent = ""; enviar = alEnviar;
  $("#f2-campos").replaceChildren(...campos.map((c) => el("label", {}, c.etiqueta,
    c.opciones
      ? el("select", { id: `f2-${c.id}` }, ...c.opciones.map(([v, t]) => el("option", { value: v }, t)))
      : el("input", { id: `f2-${c.id}`, type: c.tipo ?? "text", step: c.tipo === "number" ? (c.paso ?? "0.01") : null, min: c.min ?? null,
          maxlength: c.max ?? null, required: c.req ?? true, value: c.valor ?? null }))));
  campos.forEach((c) => { if (c.opciones && c.valor != null) $(`#f2-${c.id}`).value = c.valor; });
  dlg.showModal();
  $("#f2-campos").querySelector("input,select")?.focus();
}
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!form.checkValidity()) { form.reportValidity(); return; }
  const v = Object.fromEntries([...$("#f2-campos").querySelectorAll("input,select")].map((n) => [n.id.slice(3), n.value]));
  try { await enviar(v); dlg.close(); await recargar(); } catch (err) { if (!err.silencioso) $("#f2-err").textContent = err.message; }
});
$("#f2-cancelar").addEventListener("click", () => dlg.close());

// ---------- Dinero disponible ----------
let espera;
async function cargarDisponible() {
  const compra = parseFloat($("#compra").value);
  try {
    const cat = $("#compra-cat").value;
    const r = await api(`/disponible?${periodoQS()}${compra > 0 ? `&compra=${compra}` : ""}${compra > 0 && cat ? `&categoria=${encodeURIComponent(cat)}` : ""}`);
    $("#disp-monto").textContent = PEN.format(r.disponible);
    const fila = (t, v, signo) => el("li", {}, el("span", {}, t), el("span", {}, signo ? `${signo} ${PEN.format(v)}` : PEN.format(v)));
    $("#disp-desglose").replaceChildren(fila("Saldo en cuentas (sin cuentas de ahorro)", r.saldo_real),
      fila("Pagos y cuotas por vencer en el periodo", r.reservado_pagos, "−"),
      fila("Apartado para metas este mes", r.apartado_ahorro, "−"),
      fila("Necesidades esenciales por cubrir", r.esenciales_restantes, "−"));
    $("#disp-msg").textContent = r.mensajes.join(" ");
  } catch (e) { avisar(e); }
}
$("#compra").addEventListener("input", () => { clearTimeout(espera); espera = setTimeout(cargarDisponible, 350); });

// ---------- Cuentas y transferencias ----------
async function cargarCuentas() {
  try { f2.cuentas = await api("/cuentas"); pintarCuentas(); poblarSelectCuenta(); } catch (e) { avisar(e); }
}
function pintarCuentas() {
  const total = f2.cuentas.reduce((s, c) => s + c.saldo, 0);
  $("#cuentas-total").textContent = f2.cuentas.length ? `Total en cuentas: ${PEN.format(total)}` : "Aún no tienes cuentas. Crea una, o genera las tuyas desde tus métodos de pago.";
  $("#cuentas").replaceChildren(...f2.cuentas.map((c) => el("li", { class: "fila-simple cuenta", "data-id": c.id, tabindex: "0", title: "Toca para editar" },
    el("span", {}, `${ICONO[c.tipo] ?? "💼"} ${c.nombre}`), el("strong", { class: c.saldo < 0 ? "neg" : null }, PEN.format(c.saldo)))));
}
function poblarSelectCuenta() {   // el selector "Cuenta" del formulario de movimientos
  const sel = $("#cuenta"), previo = sel.value;
  sel.replaceChildren(el("option", { value: "" }, "Sin cuenta"), ...f2.cuentas.map((c) => el("option", { value: c.id }, c.nombre)));
  sel.value = previo || localStorage.getItem("ult_cuenta") || "";
  sel.closest("label").hidden = !f2.cuentas.length;
}

$("#cuenta-nueva").addEventListener("click", () => dialogo("Nueva cuenta", [
  { id: "nombre", etiqueta: "Nombre", max: 40 },
  { id: "tipo", etiqueta: "Tipo", opciones: [["efectivo", "Efectivo"], ["banco", "Cuenta bancaria"], ["billetera", "Billetera digital (Yape, Plin…)"],
    ["ahorros", "Cuenta de ahorros (no cuenta como dinero para gastar)"], ["otra", "Otra"]] },
  { id: "saldo", etiqueta: "Saldo inicial (S/)", tipo: "number", valor: "0" },
], (v) => api("/cuentas", { method: "POST", json: { nombre: v.nombre, tipo: v.tipo, saldo_inicial: parseFloat(v.saldo) } })));

$("#transferir").addEventListener("click", () => {
  if (f2.cuentas.length < 2) { toast("Necesitas al menos 2 cuentas para transferir", "error"); return; }
  dialogo("Transferir entre tus cuentas", [
    { id: "origen", etiqueta: "Desde", opciones: OPC_CUENTAS() }, { id: "destino", etiqueta: "Hacia", opciones: OPC_CUENTAS() },
    { id: "monto", etiqueta: "Monto (S/)", tipo: "number", min: "0.01" }, { id: "fecha", etiqueta: "Fecha", tipo: "date", valor: hoy() },
  ], (v) => api("/transferencias", { method: "POST", json: { origen: v.origen, destino: v.destino, monto: parseFloat(v.monto), fecha: v.fecha } }));
});

$("#cuentas-auto").addEventListener("click", async () => {
  try {
    const r = await api("/cuentas/desde-metodos", { method: "POST" });
    toast(`${r.cuentas_creadas} cuentas creadas, ${r.movimientos_asignados} movimientos asignados. Ajusta el saldo inicial si hace falta.`);
    recargar();
  } catch (e) { avisar(e); }
});

// ---------- Metas de ahorro ----------
async function cargarMetas() {
  try { pintarMetas(await api("/metas")); } catch (e) { avisar(e); }
}
function pintarMetas(metas) {
  $("#metas").replaceChildren(...metas.map((m) => {
    const barra = el("div", { class: "meter", role: "img", "aria-label": `${m.nombre}: ${m.porcentaje}%` }, el("span"));
    barra.firstChild.style.setProperty("--p", `${m.porcentaje}%`);
    const plan = m.semanal != null ? `Para llegar a tiempo: ${PEN.format(m.semanal)} por semana o ${PEN.format(m.mensual)} por mes.` : m.vencida ? "La fecha límite ya pasó." : "";
    const boton = (op, texto) => el("button", { type: "button", class: "chip", "data-meta": m.id, "data-op": op }, texto);
    return el("li", {}, el("div", { class: "pres-cab" }, el("strong", {}, `🎯 ${m.nombre}`), el("span", { class: "nota" }, `prioridad ${m.prioridad}`)), barra,
      el("small", {}, `${PEN.format(m.ahorrado)} de ${PEN.format(m.objetivo)} (${m.porcentaje}%)${m.fecha_limite ? `, hasta ${m.fecha_limite}` : ""}`),
      plan ? el("small", {}, plan) : null,
      m.historial.length ? el("small", { class: "nota" }, "Últimos movimientos: " + m.historial.map((h) => `${h.tipo === "aporte" ? "+" : "−"}${PEN.format(h.monto)} (${h.fecha})`).join(", ")) : null, el("div", { class: "acciones-meta" }, boton("aporte", "Aportar"), boton("retiro", "Retirar"), boton("borrar", "Eliminar")));
  }));
}

$("#metas").addEventListener("click", (e) => {
  const b = e.target.closest("[data-op]");
  if (!b) return;
  const id = b.dataset.meta, op = b.dataset.op;
  if (op === "borrar") { api(`/metas/${id}`, { method: "DELETE" }).then(() => { toast("Meta eliminada"); recargar(); }).catch(avisar); return; }
  if (!f2.cuentas.length) { toast("Primero crea una cuenta", "error"); return; }
  dialogo(op === "aporte" ? "Aportar a la meta" : "Retirar de la meta", [
    { id: "cuenta", etiqueta: op === "aporte" ? "Desde la cuenta" : "Hacia la cuenta", opciones: OPC_CUENTAS() },
    { id: "monto", etiqueta: "Monto (S/)", tipo: "number", min: "0.01" }, { id: "fecha", etiqueta: "Fecha", tipo: "date", valor: hoy() },
  ], (v) => api(`/metas/${id}/aportes`, { method: "POST", json: { tipo: op, monto: parseFloat(v.monto), cuenta_id: v.cuenta, fecha: v.fecha } }));
});

$("#meta-nueva").addEventListener("click", () => dialogo("Nueva meta de ahorro", [
  { id: "nombre", etiqueta: "Nombre (ej. Laptop)", max: 60 },
  { id: "objetivo", etiqueta: "Cantidad objetivo (S/)", tipo: "number", min: "0.01" },
  { id: "fecha", etiqueta: "Fecha límite (opcional)", tipo: "date", req: false },
  { id: "prioridad", etiqueta: "Prioridad", opciones: [["media", "Media"], ["alta", "Alta"], ["baja", "Baja"]] },
], (v) => api("/metas", { method: "POST", json: { nombre: v.nombre, objetivo: parseFloat(v.objetivo), fecha_limite: v.fecha || null, prioridad: v.prioridad } })));

// ---------- Editar cuentas ----------
const TIPOS = [["efectivo", "Efectivo"], ["banco", "Cuenta bancaria"], ["billetera", "Billetera digital (Yape, Plin…)"],
  ["ahorros", "Cuenta de ahorros (no cuenta como dinero para gastar)"], ["otra", "Otra"]];

function editarCuenta(id) {
  const c = f2.cuentas.find((x) => x.id === id);
  if (!c) return;
  dialogo("Editar cuenta", [
    { id: "nombre", etiqueta: "Nombre", max: 40, valor: c.nombre },
    { id: "tipo", etiqueta: "Tipo", opciones: TIPOS, valor: c.tipo },
    { id: "saldo", etiqueta: "Saldo inicial (S/)", tipo: "number", valor: String(c.saldo_inicial) },
  ], (v) => api(`/cuentas/${id}`, { method: "PUT", json: { nombre: v.nombre, tipo: v.tipo, saldo_inicial: parseFloat(v.saldo) } }));
}
$("#cuentas").addEventListener("click", (e) => { const li = e.target.closest("[data-id]"); if (li) editarCuenta(li.dataset.id); });
$("#cuentas").addEventListener("keydown", (e) => { if (e.key === "Enter") { const li = e.target.closest("[data-id]"); if (li) editarCuenta(li.dataset.id); } });

// ---------- Historial de transferencias ----------
async function cargarTransferencias() {
  try {
    const t = await api("/transferencias"), nombre = (id) => f2.cuentas.find((c) => c.id === id)?.nombre ?? "cuenta eliminada";
    $("#transf").replaceChildren(...t.slice(0, 5).map((x) => el("li", {}, el("span", {}, `${x.fecha}: ${nombre(x.origen)} → ${nombre(x.destino)}`), el("span", {}, PEN.format(x.monto)))));
    $("#transf").previousElementSibling.hidden = !t.length;
  } catch (e) { avisar(e); }
}

// ---------- Avisos de presupuesto configurables ----------
$("#pres-avisos").addEventListener("click", async () => {
  let a;
  try { a = (await api("/config/avisos")).avisos; } catch (e) { avisar(e); return; }
  const campo = (id, etiqueta, valor) => ({ id, etiqueta, tipo: "number", min: "1", paso: "1", valor: String(valor) });
  dialogo("Avisos de presupuesto (% del límite)", [campo("a1", "Primer aviso", a[0]), campo("a2", "Segundo aviso", a[1]), campo("a3", "Límite", a[2])],
    async (v) => { await api("/config/avisos", { method: "PUT", json: { avisos: [+v.a1, +v.a2, +v.a3] } }); cargarPresupuestos(); });
});

// ---------- Categoría de la compra hipotética ----------
const poblarCompraCat = () => $("#compra-cat").replaceChildren(el("option", { value: "" }, "Sin categoría"), ...CATEGORIAS.gasto.map(([e, n]) => el("option", { value: n }, `${e} ${n}`)));
poblarCompraCat();
$("#compra-cat").addEventListener("change", cargarDisponible);

// ---------- Categorías personalizadas: se mezclan con las incorporadas en TODOS los selectores ----------
let propias = [];

async function cargarCategorias() {
  try {
    for (const t of Object.keys(CATEGORIAS)) CATEGORIAS[t] = CATEGORIAS[t].filter(([, n]) => !propias.some((p) => p.tipo === t && p.nombre === n));
    propias = await api("/categorias");
    for (const p of propias) if (!CATEGORIAS[p.tipo].some(([, n]) => n === p.nombre)) CATEGORIAS[p.tipo].push([p.emoji, p.nombre]);
    $("#cats-propias").replaceChildren(...propias.map((p) => el("li", {}, el("span", {}, `${p.emoji} ${p.nombre} (${p.tipo})`),
      el("button", { type: "button", class: "icon-btn", "data-cat": p.id, "aria-label": `Eliminar la categoría ${p.nombre}` }, "✕"))));
    poblarCompraCat();
  } catch (e) { avisar(e); }
}

$("#cat-nueva").addEventListener("click", () => dialogo("Nueva categoría", [
  { id: "nombre", etiqueta: "Nombre", max: 50 },
  { id: "tipo", etiqueta: "Tipo", opciones: [["gasto", "Gasto"], ["ingreso", "Ingreso"], ["ahorro", "Ahorro"]] },
  { id: "emoji", etiqueta: "Emoji (opcional)", max: 8, req: false, valor: "🏷️" },
], async (v) => {
  if (CATEGORIAS[v.tipo].some(([, n]) => n.toLowerCase() === v.nombre.trim().toLowerCase())) throw new Error("Ya existe una categoría con ese nombre");
  await api("/categorias", { method: "POST", json: { nombre: v.nombre, tipo: v.tipo, emoji: v.emoji || "🏷️" } });
  await cargarCategorias();
}));

$("#cats-propias").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-cat]");
  if (!b) return;
  try { await api(`/categorias/${b.dataset.cat}`, { method: "DELETE" }); toast("Categoría eliminada. Tus movimientos conservan su nombre."); cargarCategorias(); }
  catch (err) { avisar(err); }
});

// ---------- Pagos programados ----------
const FREC = [["mensual", "Mensual"], ["semanal", "Semanal"], ["trimestral", "Trimestral"], ["anual", "Anual"], ["unica", "Una sola vez"]];
let pagosCache = [];

async function cargarPagos() {
  try {
    const pagos = pagosCache = await api("/pagos");
    $("#pagos-vacio").hidden = pagos.length > 0;
    const cuando = (p) => p.estado === "pagado" ? "pagado" : `vence ${p.proximo} (${p.dias < 0 ? `venció hace ${-p.dias} d` : p.dias === 0 ? "hoy" : `en ${p.dias} d`})`;
    $("#pagos").replaceChildren(...pagos.map((p) => el("li", { class: `pago ${p.vencimiento}` },
      el("div", { class: "pres-cab" }, el("strong", {}, p.nombre), el("strong", {}, PEN.format(p.monto))),
      el("small", {}, `${p.frecuencia}, ${cuando(p)}`),
      el("div", { class: "acciones-meta" },
        p.estado === "pagado" ? null : el("button", { type: "button", class: "chip", "data-pago": p.id, "data-op": "pagar" }, "Marcar como pagado"),
        el("button", { type: "button", class: "chip", "data-pago": p.id, "data-op": "editar" }, "Editar"),
        el("button", { type: "button", class: "chip", "data-pago": p.id, "data-op": "quitar" }, "Quitar")))));
    avisarVencimientos(pagos);
  } catch (e) { avisar(e); }
}

$("#pagos").addEventListener("click", (e) => {
  const b = e.target.closest("[data-op]");
  if (!b) return;
  const id = b.dataset.pago;
  if (b.dataset.op === "editar") { dialogo("Editar pago programado", camposPago(pagosCache.find((x) => x.id === id)), (v) => api(`/pagos/${id}`, { method: "PUT", json: cuerpoPago(v) })); return; }
  if (b.dataset.op === "quitar") { api(`/pagos/${id}`, { method: "DELETE" }).then(() => { toast("Pago quitado"); recargar(); }).catch(avisar); return; }
  if (!f2.cuentas.length) { toast("Primero crea una cuenta", "error"); return; }
  dialogo("Confirmar pago", [{ id: "cuenta", etiqueta: "Pagar desde", opciones: OPC_CUENTAS() }, { id: "fecha", etiqueta: "Fecha del pago", tipo: "date", valor: hoy() }],
    async (v) => { await api(`/pagos/${id}/pagar`, { method: "POST", json: { cuenta_id: v.cuenta, fecha: v.fecha } }); toast("Pago registrado como gasto"); await refrescar(); });
});

const SERVICIOS_PRIMERO = ["Suscripciones", "Hogar y servicios"];
const camposPago = (p = {}) => [
  { id: "nombre", etiqueta: "Nombre (ej. Netflix)", max: 60, valor: p.nombre ?? null },
  { id: "monto", etiqueta: "Monto (S/)", tipo: "number", min: "0.01", valor: p.monto != null ? String(p.monto) : null },
  { id: "frecuencia", etiqueta: "Frecuencia", opciones: FREC, valor: p.frecuencia },
  { id: "proximo", etiqueta: "Próximo vencimiento", tipo: "date", valor: p.proximo ?? hoy() },
  { id: "categoria", etiqueta: "Categoría", valor: p.categoria,
    opciones: [...CATEGORIAS.gasto].sort((a, b) => SERVICIOS_PRIMERO.includes(b[1]) - SERVICIOS_PRIMERO.includes(a[1])).map(([, n]) => [n, n]) },
];
const cuerpoPago = (v) => ({ nombre: v.nombre, monto: parseFloat(v.monto), frecuencia: v.frecuencia, proximo: v.proximo, categoria: v.categoria });
$("#pago-nuevo").addEventListener("click", () => dialogo("Nuevo pago programado", camposPago(), (v) => api("/pagos", { method: "POST", json: cuerpoPago(v) })));

// ---------- Avisos de vencimiento del navegador (solo mientras la app está abierta o instalada) ----------
const hayNotif = "Notification" in window;
function pintarBotonAvisos() { $("#avisos-pagos").hidden = !hayNotif || Notification.permission !== "default"; }
$("#avisos-pagos").addEventListener("click", async () => { await Notification.requestPermission(); pintarBotonAvisos(); cargarPagos(); });
pintarBotonAvisos();

async function avisarVencimientos(pagos) {   // un aviso por pago y por día
  if (!hayNotif || Notification.permission !== "granted") return;
  const dia = hoy(), vistos = JSON.parse(localStorage.getItem("pagos_avisados") || "{}");
  for (const p of pagos.filter((x) => ["vencido", "hoy", "pronto"].includes(x.vencimiento) && vistos[x.id] !== dia)) {
    const cuando = p.vencimiento === "vencido" ? `venció hace ${-p.dias} días` : p.vencimiento === "hoy" ? "vence hoy" : `vence en ${p.dias} días`;
    const opciones = { body: `${p.nombre}: ${PEN.format(p.monto)} ${cuando}`, tag: `pago-${p.id}` };
    try {
      const reg = await navigator.serviceWorker?.ready;
      if (reg) await reg.showNotification("Mi Billetera", opciones); else new Notification("Mi Billetera", opciones);
      vistos[p.id] = dia;
    } catch { /* el navegador no permite notificar */ }
  }
  localStorage.setItem("pagos_avisados", JSON.stringify(vistos));
}

// ---------- Deudas y cuotas ----------
let deudas = [];
async function cargarDeudas() {
  try {
    deudas = await api("/deudas");
    $("#deudas").replaceChildren(...deudas.map((d) => {
      const datos = [d.acreedor ? `Acreedor: ${d.acreedor}.` : "", d.tasa_anual ? `Interés ${d.tasa_anual}% anual (unos ${PEN.format(d.interes_mensual)} al mes).` : "",
        d.cuota ? `Cuota ${PEN.format(d.cuota)}${d.proximo_vencimiento ? `, vence ${d.proximo_vencimiento}` : ""}.` : "",
        d.saldo > 0 && d.cuota ? (d.meses_restantes != null ? `Terminarías de pagar en unos ${d.meses_restantes} meses.` : "Con esa cuota no alcanzas a cubrir los intereses.") : "",
        d.saldo === 0 ? "Deuda pagada." : ""].filter(Boolean).join(" ");
      const boton = (op, texto) => el("button", { type: "button", class: "chip", "data-deuda": d.id, "data-op": op }, texto);
      return el("li", {}, el("div", { class: "pres-cab" }, el("strong", {}, d.nombre), el("strong", {}, PEN.format(d.saldo))),
        el("small", {}, datos), el("div", { class: "acciones-meta" }, d.saldo > 0 ? boton("pagar", "Registrar pago") : null, boton("editar", "Editar"), boton("borrar", "Eliminar")));
    }));
  } catch (e) { avisar(e); }
}

$("#deudas").addEventListener("click", (e) => {
  const b = e.target.closest("[data-op]");
  if (!b) return;
  const id = b.dataset.deuda, d = deudas.find((x) => x.id === id);
  if (b.dataset.op === "editar") { dialogo(`Editar ${d.nombre}`, camposDeuda(d), (v) => api(`/deudas/${id}`, { method: "PUT", json: cuerpoDeuda(v) })); return; }
  if (b.dataset.op === "borrar") { api(`/deudas/${id}`, { method: "DELETE" }).then(() => { toast("Deuda eliminada"); recargar(); }).catch(avisar); return; }
  if (!f2.cuentas.length) { toast("Primero crea una cuenta", "error"); return; }
  dialogo(`Registrar pago de ${d.nombre}`, [
    { id: "cuenta", etiqueta: "Pagar desde", opciones: OPC_CUENTAS() },
    { id: "monto", etiqueta: "Monto pagado (S/)", tipo: "number", min: "0.01", valor: d.cuota ? String(d.cuota) : null },
    { id: "fecha", etiqueta: "Fecha", tipo: "date", valor: hoy() },
  ], async (v) => { await api(`/deudas/${id}/pagos`, { method: "POST", json: { monto: parseFloat(v.monto), cuenta_id: v.cuenta, fecha: v.fecha } }); await refrescar(); });
});

const camposDeuda = (d = {}) => [
  { id: "nombre", etiqueta: "Nombre (ej. Préstamo de la laptop)", max: 60, valor: d.nombre ?? null },
  { id: "acreedor", etiqueta: "Acreedor (opcional)", max: 60, req: false, valor: d.acreedor ?? null },
  { id: "saldo", etiqueta: "Saldo pendiente (S/)", tipo: "number", min: "0.01", valor: d.saldo != null ? String(d.saldo) : null },
  { id: "tasa", etiqueta: "Interés anual % (0 si no hay)", tipo: "number", min: "0", valor: String(d.tasa_anual ?? 0) },
  { id: "cuota", etiqueta: "Cuota mensual (opcional)", tipo: "number", min: "0.01", req: false, valor: d.cuota != null ? String(d.cuota) : null },
  { id: "vence", etiqueta: "Próximo vencimiento (opcional)", tipo: "date", req: false, valor: d.proximo_vencimiento ?? null },
];
const cuerpoDeuda = (v) => ({ nombre: v.nombre, acreedor: v.acreedor || null, saldo: parseFloat(v.saldo), tasa_anual: parseFloat(v.tasa || 0),
  cuota: v.cuota ? parseFloat(v.cuota) : null, proximo_vencimiento: v.vence || null });
$("#deuda-nueva").addEventListener("click", () => dialogo("Nueva deuda", camposDeuda(), (v) => api("/deudas", { method: "POST", json: cuerpoDeuda(v) })));

// ---------- Comparación con el periodo anterior ----------
async function cargarComparacion() {
  try {
    const { filas } = await api(`/estadisticas/comparacion?${periodoQS()}`);
    $("#comparacion-vacio").hidden = filas.length > 0;
    $("#comparacion").replaceChildren(...filas.slice(0, 6).map((f) => el("li", {}, el("span", {}, f.categoria),
      el("span", { class: f.diferencia > 0 ? "neg" : "pos" }, `${PEN.format(f.actual)} ${f.cambio_pct == null ? "(nuevo)" : `(${f.cambio_pct > 0 ? "+" : ""}${f.cambio_pct}%)`}`))));
  } catch (e) { avisar(e); }
}

// ---------- Importar movimientos desde un CSV (el mismo formato que exporta la app) ----------
$("#importar").addEventListener("click", () => $("#archivo-csv").click());
$("#archivo-csv").addEventListener("change", async (e) => {
  const archivo = e.target.files[0];
  e.target.value = "";
  if (!archivo) return;
  if (archivo.size > 1_400_000) { toast("El archivo es demasiado grande (máximo 1.4 MB)", "error"); return; }
  try {
    const r = await api("/importar/movimientos", { method: "POST", json: { csv: await archivo.text() } });
    toast(`Se importaron ${r.importados} movimientos. Omitidos: ${r.duplicados} repetidos y ${r.con_errores} con errores.`, r.con_errores ? "error" : "ok");
    if (r.errores.length) toast(`Fila ${r.errores[0].fila}: ${r.errores[0].motivo}`, "error");
    await refrescar();
  } catch (err) { avisar(err); }
});

// ---------- Asistente financiero ----------
const ICONO_NIVEL = { alerta: "🔴", aviso: "🟠", info: "🔵" };
async function cargarAsistente() {
  try {
    const { observaciones } = await api(`/asistente?${periodoQS()}`);
    $("#asistente-vacio").hidden = observaciones.length > 0;
    $("#asistente").replaceChildren(...observaciones.map((o) => el("li", { class: `obs ${o.nivel}` },
      el("details", {}, el("summary", {}, `${ICONO_NIVEL[o.nivel]} ${o.mensaje}`), el("p", { class: "nota" }, `Cómo se calculó: ${o.como_se_calculo}`)))));
  } catch (e) { avisar(e); }
}

// ---------- Proyección del saldo ----------
let graficoProy = null;
async function cargarProyeccion() {
  try {
    const r = await api(`/proyeccion?dias=${$("#proy-dias").value}`);
    $("#proy-txt").textContent = r.alertas.length ? r.alertas.join(" ") : `Con tus obligaciones conocidas, tu saldo líquido no bajaría de ${PEN.format(r.minimo_obligaciones)} en este periodo.`;
    graficoProy?.destroy(); graficoProy = null;
    if (!window.Chart) return;
    const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim(), tinta = css("--texto-suave");
    const serie = (nombre, color, campo) => ({ label: nombre, data: r.puntos.map((p) => p[campo]), borderColor: css(color), backgroundColor: css(color), pointRadius: 0, tension: 0.25 });
    graficoProy = new Chart($("#g-proy"), { type: "line",
      data: { labels: r.puntos.map((p) => p.fecha.slice(5)), datasets: [serie("Solo obligaciones", "--c1", "solo_obligaciones"), serie("Con tu ritmo de gasto", "--c2", "con_ritmo")] },
      options: { maintainAspectRatio: false, plugins: { legend: { labels: { color: tinta, usePointStyle: true } }, tooltip: { callbacks: { label: (c) => ` ${c.dataset.label}: ${PEN.format(c.parsed.y)}` } } },
        scales: { x: { ticks: { color: tinta, maxTicksLimit: 8 }, grid: { display: false } }, y: { ticks: { color: tinta, callback: (v) => `S/ ${v}` }, grid: { color: css("--linea") } } } } });
    $("#g-proy").setAttribute("aria-label", `Proyección a ${r.puntos.length} días. ${$("#proy-txt").textContent}`);
  } catch (e) { avisar(e); }
}
$("#proy-dias").addEventListener("change", cargarProyeccion);
document.addEventListener("tema-cambiado", cargarProyeccion);

// ---------- Preferencias de la interfaz (se guardan en este dispositivo) ----------
const PREFS = ["privado", "compacto", "grande"];
function aplicarPrefs() {
  const p = JSON.parse(localStorage.getItem("prefs") || "{}"), raiz = document.documentElement;
  raiz.classList.toggle("privado", !!p.privado);
  raiz.dataset.densidad = p.compacto ? "compacta" : "normal";
  raiz.dataset.texto = p.grande ? "grande" : "normal";
  for (const k of PREFS) $(`#pref-${k}`).checked = !!p[k];
}
for (const k of PREFS) $(`#pref-${k}`).addEventListener("change", (e) => {
  const p = JSON.parse(localStorage.getItem("prefs") || "{}");
  p[k] = e.target.checked;
  localStorage.setItem("prefs", JSON.stringify(p));
  aplicarPrefs();
});
aplicarPrefs();

// ---------- Alertas configurables ----------
$("#alertas-conf").addEventListener("click", async () => {
  let a;
  try { a = await api("/config/alertas"); } catch (e) { avisar(e); return; }
  dialogo("Alertas del asistente", [
    { id: "minimo", etiqueta: "Avisarme si mi saldo líquido baja de (S/; 0 = desactivado)", tipo: "number", min: "0", valor: String(a.saldo_minimo) },
    { id: "umbral", etiqueta: "Avisarme si una categoría sube más de (%) frente al periodo anterior", tipo: "number", min: "5", paso: "1", valor: String(a.umbral_aumento_pct) },
  ], (v) => api("/config/alertas", { method: "PUT", json: { saldo_minimo: parseFloat(v.minimo), umbral_aumento_pct: parseInt(v.umbral, 10) } }));
});

// ---------- Copias de seguridad ----------
async function descargarRespaldo(nombre) {
  const r = await api("/respaldo", { raw: true });
  const a = el("a", { href: URL.createObjectURL(await r.blob()), download: nombre ?? `respaldo_billetera_${hoy()}.json` });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1500);
}
$("#respaldo-bajar").addEventListener("click", () => descargarRespaldo().catch(avisar));
$("#respaldo-subir").addEventListener("click", () => $("#archivo-respaldo").click());
$("#archivo-respaldo").addEventListener("change", async (e) => {
  const archivo = e.target.files[0];
  e.target.value = "";
  if (!archivo) return;
  let datos;
  try { datos = JSON.parse(await archivo.text()); } catch { toast("El archivo no es un JSON válido", "error"); return; }
  const n = (k) => datos?.datos?.[k]?.length ?? 0;
  dialogo(`Restaurar copia: ${n("transacciones")} movimientos y ${n("cuentas")} cuentas`, [
    { id: "conf", etiqueta: "Se REEMPLAZARÁN todos tus datos actuales. Antes se descargará una copia de lo que tienes ahora. Escribe RESTAURAR para continuar." },
  ], async (v) => {
    if (v.conf.trim() !== "RESTAURAR") throw new Error("Escribe RESTAURAR para confirmar");
    await descargarRespaldo(`antes_de_restaurar_${hoy()}.json`);
    await api("/respaldo/restaurar", { method: "POST", json: { datos, confirmar: true } });
    toast("Copia restaurada"); await refrescar();
  });
});
