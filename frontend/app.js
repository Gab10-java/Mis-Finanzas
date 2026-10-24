// Mi Billetera: cliente de la API. Módulo ES: nada se filtra al ámbito global.
const API = "/api";
const PEN = new Intl.NumberFormat("es-PE", { style: "currency", currency: "PEN" });
const $ = (s, r = document) => r.querySelector(s);
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

// Crea nodos con textContent (nunca innerHTML): lo que escribe el usuario no puede inyectar HTML.
function el(tag, props = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === false || v == null) continue;
    n.setAttribute(k, v === true ? "" : v);
  }
  n.append(...kids.flat().filter((x) => x != null));
  return n;
}

// Fechas en hora LOCAL (valueAsDate usa UTC y en Perú daba "mañana" desde las 7 pm).
function fechaLocal(dias = 0) {
  const d = new Date();
  d.setDate(d.getDate() + dias);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function hoy() { return fechaLocal(0); }

const S = { rango: null, nombre: "", mes: hoy().slice(0, 7), q: "", tipo: "", cat: "", pagina: 1, items: [], total: 0, hayMas: false,
  editando: null, borrar: null, res: null, saldo: 0, charts: {}, rq: 0, lq: 0 };

S.dia = Math.min(28, Math.max(1, +localStorage.getItem("dia_corte") || 1));
function mesActual() {   // el periodo que contiene a hoy (antes del día de corte aún rige el periodo anterior)
  const d = new Date(), base = d.getDate() >= S.dia ? d : new Date(d.getFullYear(), d.getMonth() - 1, 1);
  return `${base.getFullYear()}-${String(base.getMonth() + 1).padStart(2, "0")}`;
}
S.mes = mesActual();

// ---------- Comunicación con el backend ----------
async function mensajeError(r) {
  try {
    const { detail } = await r.json();
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((d) => `${d.loc?.at(-1) ?? "dato"}: ${d.msg}`).join("; ");
  } catch { /* la respuesta no era JSON */ }
  return `Error ${r.status}`;
}

async function api(ruta, { json, method = "GET", raw = false } = {}) {
  let r;
  try {
    r = await fetch(API + ruta, {
      method,
      headers: { "X-Requested-With": "fetch", ...(json ? { "Content-Type": "application/json" } : {}) },
      body: json ? JSON.stringify(json) : undefined,
    });
  } catch {
    throw Object.assign(new Error("No hay conexión con el servidor. Revisa tu internet."), { sinRed: true });
  }
  if (r.status === 401) {
    salir("Tu sesión venció. Vuelve a entrar.");
    throw Object.assign(new Error("sesión vencida"), { silencioso: true });
  }
  if (!r.ok) throw new Error(await mensajeError(r));
  return raw ? r : r.status === 204 ? null : r.json();
}

function toast(msg, tipo = "ok", accion) {
  const t = el("div", { class: `toast ${tipo}`, role: tipo === "error" ? "alert" : "status" }, msg);
  if (accion) {
    const b = el("button", { type: "button" }, accion[0]);
    b.addEventListener("click", () => { t.remove(); accion[1](); });
    t.append(b);
  }
  $("#toasts").append(t);
  setTimeout(() => t.remove(), accion ? 7000 : 4200);
}
const avisar = (e) => { if (!e.silencioso) toast(e.message, "error"); };

// ---------- Sesión (Google Sign-In) ----------
let sesion = false;   // ¿hay sesión activa? La guarda el servidor en una cookie httpOnly de 30 días

function entrar(nombre = "") {
  sesion = true;
  S.nombre = nombre;
  $("#login").hidden = true; $("#app").hidden = false; $("#fab").hidden = false;
  $("#login-msg").textContent = "";
  refrescar(); vaciarCola();
}

async function entrarConGoogle(credencial) {   // Google se consulta UNA vez; después basta la cookie
  try {
    const r = await fetch(`${API}/auth/google`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ credential: credencial }) });
    if (!r.ok) throw new Error(await mensajeError(r));
    entrar((await r.json()).nombre);
  } catch (e) { $("#login-msg").textContent = e.message; }
}

function salir(msg) {
  sesion = false; S.rq++; S.lq++;
  $("#app").hidden = true; $("#fab").hidden = true; $("#login").hidden = false;
  $("#login-msg").textContent = msg || "";
  const g = window.google?.accounts?.id;
  if (msg) g?.prompt();   // sesión vencida: intenta volver a entrar sola
  else {
    g?.disableAutoSelect();
    fetch(`${API}/auth/logout`, { method: "POST", headers: { "X-Requested-With": "fetch" } }).catch(() => {});
  }
}

const esperarGoogle = () => new Promise((ok, no) => {
  const t0 = Date.now();
  (function p() {
    if (window.google?.accounts?.id) ok();
    else if (Date.now() - t0 > 8000) no();
    else setTimeout(p, 80);
  })();
});

async function iniciar() {
  try { const r = await fetch(`${API}/auth/me`); if (r.ok) entrar((await r.json()).nombre); } catch { /* sin conexión: se mostrará el acceso */ }
  let cfg;
  try {
    cfg = await (await fetch(`${API}/config`)).json();   // el Client ID vive solo en el backend
    await esperarGoogle();
  } catch {
    if (!sesion) $("#login-msg").textContent = "No se pudo preparar el inicio de sesión. Revisa tu conexión y recarga.";
    return;
  }
  const g = google.accounts.id;
  g.initialize({ client_id: cfg.google_client_id, callback: (r) => entrarConGoogle(r.credential), auto_select: true });
  g.renderButton($("#g-btn"), { theme: "filled_black", size: "large", shape: "pill", text: "continue_with", locale: "es" });
  if (!sesion) g.prompt();
}

// ---------- Datos ----------
function periodoQS() {
  if (S.rango) return `desde=${S.rango.desde}&hasta=${S.rango.hasta}`;   // rango libre (7 días, 3 meses, 6 meses, personalizado)
  const [a, m] = S.mes.split("-");
  return `anio=${a}&mes=${+m}&dia=${S.dia}`;
}
const busquedaQS = () => (S.q ? `&q=${encodeURIComponent(S.q)}` : "") + (S.tipo ? `&tipo=${S.tipo}` : "") + (S.cat ? `&categoria=${encodeURIComponent(S.cat)}` : "");
const refrescar = () => {
  document.dispatchEvent(new Event("datos-actualizados"));   // el módulo de planificación recarga cuentas, metas y disponible
  return Promise.all([cargarResumen(), cargarLista(true), cargarHistorial(), cargarPresupuestos()]);
};

async function cargarResumen() {
  const id = ++S.rq;   // si el usuario cambia de mes rápido, se descartan respuestas viejas
  try {
    const r = await api(`/transacciones/resumen?${periodoQS()}`);
    if (id === S.rq) pintarResumen(r);
  } catch (e) { avisar(e); }
}

async function cargarLista(reset) {
  const id = ++S.lq, lista = $("#lista");
  if (reset) {
    S.pagina = 1; lista.setAttribute("aria-busy", "true");
    if (!S.items.length) lista.replaceChildren(...[1, 2, 3].map(() => el("li", { class: "mov esqueleto", "aria-hidden": "true" })));
  } else S.pagina++;
  try {
    const p = await api(`/transacciones?${periodoQS()}${busquedaQS()}&pagina=${S.pagina}&limite=15`);
    if (id !== S.lq) return;
    S.items = reset ? p.items : S.items.concat(p.items);
    S.total = p.total; S.hayMas = p.hay_mas;
    pintarLista();
  } catch (e) {
    if (!reset) S.pagina--;
    else { S.items = []; pintarLista(); $("#vacio").hidden = true; $("#error-lista").hidden = false; }
    avisar(e);
  }
  finally { if (id === S.lq) lista.removeAttribute("aria-busy"); }
}

async function cargarHistorial() {
  try {
    S.hist = (await api(`/transacciones/historial?${periodoQS()}&meses=6`)).meses;
    pintarTendencia();
  } catch (e) { avisar(e); }
}

function pintarTendencia() {
  S.tendencia?.destroy();
  S.tendencia = null;
  const h = S.hist;
  if (!h || !window.Chart) return;
  const [ant, act] = h.slice(-2);
  $("#tend-txt").textContent = ant.gastos > 0
    ? `Este mes gastaste ${Math.abs(Math.round(((act.gastos - ant.gastos) / ant.gastos) * 100))}% ${act.gastos >= ant.gastos ? "más" : "menos"} que el mes anterior.`
    : "Aún no hay un mes anterior con gastos para comparar.";
  const mes = (m) => { const [y, n] = m.split("-").map(Number); return new Date(y, n - 1, 1).toLocaleDateString("es-PE", { month: "short" }); };
  const tinta = css("--texto-suave");
  const serie = (nombre, color, campo) => ({ label: nombre, data: h.map((x) => x[campo]), borderColor: css(color), backgroundColor: css(color), tension: 0.35, pointRadius: 3 });
  S.tendencia = new Chart($("#g-tend"), {
    type: "line",
    data: { labels: h.map((x) => mes(x.mes)), datasets: [serie("Ingresos", "--ingreso", "ingresos"), serie("Gastos", "--gasto", "gastos")] },
    options: { maintainAspectRatio: false,
      plugins: { legend: { labels: { color: tinta, usePointStyle: true } }, tooltip: { callbacks: { label: (c) => ` ${c.dataset.label}: ${PEN.format(c.parsed.y)}` } } },
      scales: { x: { ticks: { color: tinta }, grid: { display: false } }, y: { ticks: { color: tinta, callback: (v) => `S/ ${v}` }, grid: { color: css("--linea") } } } },
  });
  $("#g-tend").setAttribute("aria-label", "Ingresos y gastos de los últimos 6 meses. " + $("#tend-txt").textContent);
}

// ---------- Pintado ----------
const nombreMes = () => { const [y, m] = S.mes.split("-").map(Number); return new Date(y, m - 1, 1).toLocaleDateString("es-PE", { month: "long", year: "numeric" }); };

function contar(nodo, destino) {
  const desde = S.saldo; S.saldo = destino;
  $("#saldo-sr").textContent = `Disponible ${PEN.format(destino)}`;
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) { nodo.textContent = PEN.format(destino); return; }
  const t0 = performance.now();
  const paso = (t) => {
    const k = Math.min((t - t0) / 700, 1), e = 1 - (1 - k) ** 3;
    nodo.textContent = PEN.format(desde + (destino - desde) * e);
    if (k < 1) requestAnimationFrame(paso);
  };
  requestAnimationFrame(paso);
}

function pintarResumen(r) {
  S.res = r;
  const { ingresos, gastos, ahorros, saldo } = r.periodo;
  $("#saldo-lbl").textContent = S.rango ? "Disponible al cierre del periodo" : `Disponible hasta fin de ${nombreMes()}`;
  contar($("#saldo"), r.acumulado.saldo);
  $("#ahorrado").textContent = `Ahorrado en total: ${PEN.format(r.acumulado.ahorros)}`;
  $("#resultado").textContent = `Resultado del periodo: ${saldo >= 0 ? "+" : "−"} ${PEN.format(Math.abs(saldo))}`;
  $("#s-ing").textContent = PEN.format(ingresos);
  $("#s-gas").textContent = PEN.format(gastos);
  $("#s-aho").textContent = PEN.format(ahorros);
  const pct = ingresos > 0 ? Math.round((gastos / ingresos) * 100) : gastos > 0 ? 100 : 0;
  $("#meter-fill").style.setProperty("--p", `${Math.min(pct, 100)}%`);
  $("#meter").classList.toggle("alerta", pct >= 90);
  $("#meter-txt").textContent = ingresos > 0 ? `Gastaste el ${pct}% de tus ingresos del mes`
    : gastos > 0 ? "Gastaste este mes sin registrar ingresos" : "Aún no hay movimientos este mes";
  pintarGraficos();
  pintarPresupuestos();
}

function pintarGraficos() {
  const r = S.res;
  Object.values(S.charts).forEach((c) => c.destroy());
  S.charts = {};
  if (!r || !window.Chart) return;
  const tinta = css("--texto-suave"), rejilla = css("--linea");
  const todas = Object.entries(r.gastos_por_categoria).sort((a, b) => b[1] - a[1]);
  const cats = todas.length > 6 ? [...todas.slice(0, 5), ["Otros", todas.slice(5).reduce((s, [, v]) => s + v, 0)]] : todas;
  const suma = cats.reduce((s, [, v]) => s + v, 0);
  $("#g-cat-vacio").hidden = cats.length > 0;
  $("#g-cat").hidden = !cats.length;
  if (cats.length) {
    const paleta = [1, 2, 3, 4, 5, 6].map((i) => css(`--c${i}`));
    S.charts.cat = new Chart($("#g-cat"), {
      type: "doughnut",
      data: { labels: cats.map((c) => c[0]), datasets: [{ data: cats.map((c) => c[1]),
        backgroundColor: cats.map((_, i) => paleta[i % paleta.length]), borderColor: css("--tarjeta"), borderWidth: 3 }] },
      options: { cutout: "66%", maintainAspectRatio: false, onClick: (_, els) => { if (els.length && cats[els[0].index][0] !== "Otros") filtrarCategoria(cats[els[0].index][0]); },
        plugins: { legend: { position: "bottom", labels: { color: tinta, boxWidth: 12, usePointStyle: true } },
          tooltip: { callbacks: { label: (c) => ` ${c.label}: ${PEN.format(c.parsed)} (${Math.round((c.parsed / suma) * 100)}%)` } } } },
    });
    $("#g-cat").setAttribute("aria-label", "Gastos por categoría: " + cats.map(([n, v]) => `${n} ${PEN.format(v)}`).join(", "));
  }
  const p = r.periodo;
  S.charts.flujo = new Chart($("#g-flujo"), {
    type: "bar",
    data: { labels: ["Ingresos", "Gastos", "Ahorros"], datasets: [{ data: [p.ingresos, p.gastos, p.ahorros],
      backgroundColor: [css("--ingreso"), css("--gasto"), css("--ahorro")], borderRadius: 8, maxBarThickness: 34 }] },
    options: { indexAxis: "y", maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => ` ${PEN.format(c.parsed.x)}` } } },
      scales: { x: { ticks: { color: tinta, callback: (v) => `S/ ${v}` }, grid: { color: rejilla } },
        y: { ticks: { color: tinta }, grid: { display: false } } } },
  });
  $("#g-flujo").setAttribute("aria-label", `Ingresos ${PEN.format(p.ingresos)}, gastos ${PEN.format(p.gastos)}, ahorros ${PEN.format(p.ahorros)}`);
}

const ICONOS = [[/univers|curso|libro/i, "🎓"], [/rappi|delivery|comida|chifa|poller|kfc|mcdonald/i, "🍔"], [/cine/i, "🎬"],
  [/juego|steam/i, "🎮"], [/mascota|gato|perro/i, "🐾"], [/viaje|vuelo|latam|jet/i, "✈️"], [/super/i, "🛒"],
  [/salud|farmacia/i, "💊"], [/luz|agua|internet|servicio/i, "💡"], [/sueldo|pago|beca/i, "💼"]];
const icono = (t) => Object.values(CATEGORIAS).flat().find(([, n]) => n === t.categoria)?.[0]
  ?? ICONOS.find(([re]) => re.test(t.categoria))?.[1] ?? { ingreso: "💰", ahorro: "🏦", gasto: "🧾" }[t.tipo] ?? "🧾";
const SIGNO = { ingreso: "+", gasto: "−", ahorro: "−" };

function etiquetaDia(f, h, a) {
  if (f === h) return "Hoy";
  if (f === a) return "Ayer";
  const [y, m, d] = f.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("es-PE", { weekday: "long", day: "numeric", month: "long" });
}

function fila(t) {
  const op = (accion, texto) => el("button", { type: "button", "data-accion": accion }, texto);
  return el("li", { class: "mov", "data-id": t.id },
    el("span", { class: `ico ${t.tipo}`, "aria-hidden": "true" }, icono(t)),
    el("div", { class: "cuerpo" }, el("strong", {}, t.categoria),
      el("small", {}, [t.metodo_pago, t.hora, t.subcategoria, t.es_recurrente ? "gasto fijo" : ""].filter(Boolean).join(", ")),
      t.descripcion ? el("small", {}, t.descripcion) : null,
      t.etiquetas?.length ? el("small", { class: "nota" }, t.etiquetas.map((x) => `#${x}`).join(" ")) : null,
      t.notas ? el("small", { class: "nota" }, `📝 ${t.notas}`) : null),
    el("span", { class: `monto ${t.tipo}` }, `${SIGNO[t.tipo]} ${PEN.format(t.monto)}`),
    el("details", { class: "menu" }, el("summary", { "aria-label": `Acciones para ${t.categoria}` }, "⋯"),
      el("div", { class: "menu-op" }, op("editar", "Editar"), op("duplicar", "Duplicar"), op("borrar", "Eliminar"))));
}

function pintarLista() {
  const h = hoy(), a = fechaLocal(-1), nodos = [];
  let dia = "";
  for (const t of S.items) {
    if (t.fecha !== dia) { dia = t.fecha; nodos.push(el("li", { class: "dia" }, etiquetaDia(dia, h, a))); }
    nodos.push(fila(t));
  }
  $("#lista").replaceChildren(...nodos);
  $("#vacio").hidden = S.items.length > 0;
  $("#error-lista").hidden = true;
  $("#mas").hidden = !S.hayMas;
  $("#conteo").textContent = S.total ? `${S.total} ${S.total === 1 ? "movimiento" : "movimientos"}` : "";
}

// ---------- Formulario ----------
const dlg = $("#dlg"), form = $("#form");

// ---------- Selectores desplegables (categoría y método de pago) ----------
const CATEGORIAS = {
  gasto: [["🍽️", "Comida"], ["🚌", "Transporte"], ["🎓", "Universidad"], ["🏠", "Hogar y servicios"], ["🛍️", "Compras"],
    ["🎬", "Ocio y salidas"], ["💊", "Salud"], ["🐾", "Mascotas"], ["📱", "Suscripciones"], ["✈️", "Viajes"]],
  ingreso: [["💼", "Sueldo"], ["🧑‍💻", "Trabajo freelance"], ["🏪", "Negocio propio"], ["🎓", "Beca o apoyo"], ["🎁", "Regalo"], ["↩️", "Reembolso"]],
  ahorro: [["🛟", "Fondo de emergencia"], ["🎯", "Meta personal"], ["✈️", "Para un viaje"], ["📈", "Inversión"], ["🏦", "Cuenta de ahorros"]],
};
const METODOS = [["📱", "Yape"], ["📲", "Plin"], ["💵", "Efectivo"], ["🏦", "Transferencia"], ["💳", "Tarjeta de débito"], ["💳", "Tarjeta de crédito"]];
const SELECTORES = [];
const cerrarPaneles = () => SELECTORES.forEach((s) => s.abrir(false));

// Un desplegable propio: el campo oculto guarda el valor y "Otra" permite escribir uno distinto.
function selector(pref, campo, opciones, textoVacio, nombreCampo) {
  const btn = $(`#${pref}-btn`), panel = $(`#${pref}-panel`), ops = $(`#${pref}-ops`), otra = $(`#${pref}-otra`), oculto = form.elements[campo];
  let esOtra = false;
  const abrir = (si) => { panel.classList.toggle("abierto", si); btn.setAttribute("aria-expanded", String(si)); };
  const mostrar = (emoji, texto) => {
    btn.replaceChildren(el("span", { class: "em", "aria-hidden": "true" }, emoji), texto);
    btn.classList.add("con-valor");
  };
  const pintar = () => {
    const lista = [...opciones(), ["✍️", "Otra"]];
    ops.replaceChildren(...lista.map(([emoji, nombre], i) => {
      const esOpcionOtra = i === lista.length - 1;
      const sel = esOpcionOtra ? esOtra : !esOtra && oculto.value === nombre;
      const b = el("button", { type: "button", class: "op", role: "option", "aria-selected": String(sel) },
        el("span", { class: "em", "aria-hidden": "true" }, emoji), nombre);
      b.style.setProperty("--i", i);
      b.addEventListener("click", () => elegir(esOpcionOtra ? null : nombre, emoji));
      return b;
    }));
  };
  function elegir(nombre, emoji) {
    esOtra = nombre === null;
    otra.hidden = !esOtra;
    if (esOtra) { oculto.value = otra.value.trim(); mostrar("✍️", oculto.value || "Otra"); }
    else { oculto.value = nombre; otra.value = ""; mostrar(emoji, nombre); }
    pintar(); abrir(false);
    if (esOtra) otra.focus();
  }
  const fijar = (valor) => {   // valores antiguos que no están en la lista se muestran como "Otra"
    const hallada = opciones().find(([, n]) => n === valor);
    oculto.value = valor || ""; otra.value = "";
    esOtra = !!valor && !hallada;
    otra.hidden = !esOtra;
    if (hallada) mostrar(hallada[0], valor);
    else if (esOtra) { otra.value = valor; mostrar("✍️", valor); }
    else { btn.replaceChildren(textoVacio); btn.classList.remove("con-valor"); }
    pintar(); abrir(false);
  };
  otra.addEventListener("input", () => { oculto.value = otra.value.trim(); mostrar("✍️", oculto.value || "Otra"); });
  btn.addEventListener("click", () => { const estaba = panel.classList.contains("abierto"); cerrarPaneles(); abrir(!estaba); });
  const ctl = {
    abrir, fijar,
    estaAbierto: () => panel.classList.contains("abierto"),
    repintar: () => (!esOtra && !opciones().some(([, n]) => n === oculto.value) ? fijar("") : pintar()),
    valida: () => {
      if (oculto.value.length >= 2) return "";
      if (esOtra) otra.focus(); else { cerrarPaneles(); abrir(true); }
      return `${esOtra ? "Escribe" : "Elige"} ${nombreCampo}`;
    },
  };
  SELECTORES.push(ctl);
  return ctl;
}

// Las categorías cambian según el tipo: un ingreso no ofrece "Comida".
const cat = selector("cat", "categoria", () => CATEGORIAS[form.elements.tipo.value] ?? CATEGORIAS.gasto, "Elige una categoría", "una categoría");
const met = selector("met", "metodo_pago", () => METODOS, "Elige un método de pago", "un método de pago");
form.addEventListener("change", (e) => { if (e.target.name === "tipo") { cat.repintar(); rapidas(); } });
dlg.addEventListener("cancel", (e) => { if (SELECTORES.some((s) => s.estaAbierto())) { e.preventDefault(); cerrarPaneles(); } });

function abrirForm(t) {
  S.editando = t?.id ?? null;
  $("#dlg-titulo").textContent = S.editando ? "Editar movimiento" : "Nuevo movimiento";
  form.reset();
  form.elements.tipo.value = t?.tipo ?? "gasto";
  form.elements.monto.value = t?.monto ?? "";
  cat.fijar(t?.categoria ?? "");
  met.fijar(t?.metodo_pago ?? localStorage.getItem("ult_met") ?? "");
  rapidas();
  form.elements.fecha.value = t?.fecha ?? hoy();
  form.elements.cuenta.value = t?.cuenta_id ?? localStorage.getItem("ult_cuenta") ?? "";
  const ahora = new Date();
  form.elements.hora.value = t && "hora" in t ? t.hora ?? "" : `${String(ahora.getHours()).padStart(2, "0")}:${String(ahora.getMinutes()).padStart(2, "0")}`;
  form.elements.subcategoria.value = t?.subcategoria ?? "";
  form.elements.etiquetas.value = (t?.etiquetas ?? []).join(", ");
  form.elements.notas.value = t?.notas ?? "";
  form.elements.descripcion.value = t?.descripcion ?? "";
  form.elements.es_recurrente.checked = !!t?.es_recurrente;
  $("#form-err").textContent = "";
  $("#rapido").closest("label").hidden = !!S.editando;
  if (!dlg.open) dlg.showModal();
  form.elements.monto.focus();
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const falta = cat.valida() || met.valida();
  if (falta) { $("#form-err").textContent = falta; return; }
  if (!form.checkValidity()) { form.reportValidity(); return; }
  $("#form-err").textContent = "";
  const datos = { tipo: form.elements.tipo.value, monto: parseFloat(form.elements.monto.value), categoria: form.elements.categoria.value.trim(),
    metodo_pago: form.elements.metodo_pago.value.trim(), fecha: form.elements.fecha.value,
    descripcion: form.elements.descripcion.value.trim() || null, es_recurrente: form.elements.es_recurrente.checked, cuenta_id: form.elements.cuenta.value || null,
    hora: form.elements.hora.value || null, subcategoria: form.elements.subcategoria.value.trim() || null,
    etiquetas: form.elements.etiquetas.value.split(",").map((x) => x.trim()).filter(Boolean), notas: form.elements.notas.value.trim() || null };
  const btn = $("#guardar"), edicion = !!S.editando;
  btn.disabled = true; btn.textContent = "Guardando…";
  try {
    await api(edicion ? `/transacciones/${S.editando}` : "/transacciones", { method: edicion ? "PUT" : "POST", json: datos });
    localStorage.setItem("ult_met", datos.metodo_pago);
    localStorage.setItem("ult_cuenta", datos.cuenta_id || "");
    const fr = frec(); (fr[datos.tipo] ??= {})[datos.categoria] = (fr[datos.tipo][datos.categoria] || 0) + 1;
    localStorage.setItem("frec", JSON.stringify(fr));
    const otro = e.submitter?.id === "otro", destino = datos.fecha.slice(0, 7);
    if (otro) { abrirForm(); form.elements.tipo.value = datos.tipo; form.elements.fecha.value = datos.fecha; cat.repintar(); rapidas(); }
    else dlg.close();
    toast(edicion ? "Movimiento actualizado" : "Movimiento guardado", "ok",
      destino !== S.mes ? ["Ver ese mes", () => { S.mes = destino; mostrarMes(); refrescar(); }] : undefined);
    refrescar();
  } catch (err) {
    if (err.sinRed && !edicion) { encolar(datos); dlg.close(); toast("Sin conexión: se enviará cuando vuelva internet"); }
    else if (!err.silencioso) $("#form-err").textContent = err.message;
  }
  finally { btn.disabled = false; btn.textContent = "Guardar"; }
});

// ---------- Exportar ----------
async function descargarCSV() {
  try {
    const r = await api(`/transacciones/exportar?${periodoQS()}${busquedaQS()}`, { raw: true });
    const a = el("a", { href: URL.createObjectURL(await r.blob()), download: `transacciones_${S.mes}.csv` });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1500);
  } catch (e) { avisar(e); }
}

async function descargarPDF() {   // el PDF lo genera el servidor, con el mismo periodo y los mismos filtros que la lista
  try {
    const r = await api(`/transacciones/exportar-pdf?${periodoQS()}${busquedaQS()}`, { raw: true });
    const a = el("a", { href: URL.createObjectURL(await r.blob()), download: `informe_${S.mes}.pdf` });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1500);
  } catch (e) { avisar(e); }
}

// ---------- Tema y eventos ----------
function tema(t) {
  document.documentElement.dataset.theme = t;
  localStorage.setItem("tema", t);
  $("meta[name=theme-color]").content = t === "dark" ? "#071827" : "#EEF3F8";
  $("#tema").textContent = t === "dark" ? "☀️" : "🌙";
  pintarGraficos();   // Chart.js no lee las variables CSS solo: se redibuja con los colores nuevos
  pintarTendencia();
  document.dispatchEvent(new Event("tema-cambiado"));
}
tema(localStorage.getItem("tema") ?? "dark");   // la identidad de la app es azul marino; el tema claro es opcional

$("#tema").addEventListener("click", () => tema(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
$("#salir").addEventListener("click", () => salir());
$("#vacio-btn").addEventListener("click", () => abrirForm());
$("#cancelar").addEventListener("click", () => dlg.close());
$("#mas").addEventListener("click", () => cargarLista(false));
$("#csv").addEventListener("click", descargarCSV);
$("#pdf").addEventListener("click", descargarPDF);
dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });

function mostrarMes() {
  const corto = (d) => d.toLocaleDateString("es-PE", { day: "numeric", month: "short" });
  const fecha = (iso) => { const [y, m, d] = iso.split("-").map(Number); return new Date(y, m - 1, d); };
  if (S.rango) { $("#mes-txt").textContent = `${corto(fecha(S.rango.desde))} al ${corto(fecha(S.rango.hasta))}`; return; }
  if (S.dia === 1) { $("#mes-txt").textContent = nombreMes(); return; }
  const [y, m] = S.mes.split("-").map(Number);
  $("#mes-txt").textContent = `${corto(new Date(y, m - 1, S.dia))} al ${corto(new Date(y, m, S.dia - 1))}`;
}
function irMes(d) {
  const [a, m] = S.mes.split("-").map(Number), f = new Date(a, m - 1 + d, 1);
  S.mes = `${f.getFullYear()}-${String(f.getMonth() + 1).padStart(2, "0")}`;
  mostrarMes(); refrescar();
}
mostrarMes();
$("#mes-ant").addEventListener("click", () => irMes(-1));
$("#mes-sig").addEventListener("click", () => irMes(1));
$("#mes-txt").addEventListener("click", () => { S.mes = mesActual(); mostrarMes(); refrescar(); });

let espera;
$("#q").addEventListener("input", (e) => {   // espera 300 ms tras la última tecla para no saturar al servidor
  clearTimeout(espera);
  espera = setTimeout(() => { S.q = e.target.value.trim(); cargarLista(true); }, 300);
});

$("#lista").addEventListener("click", (e) => {   // un solo listener para toda la lista
  const fila = e.target.closest(".mov"), b = e.target.closest("[data-accion]");
  const t = S.items.find((x) => x.id === fila?.dataset.id);
  if (!t || e.target.closest("summary")) return;
  b?.closest("details")?.removeAttribute("open");
  const accion = b?.dataset.accion ?? "editar";
  if (accion === "borrar") eliminar(t);
  else abrirForm(accion === "duplicar" ? { ...t, id: undefined, fecha: hoy() } : t);
});

async function eliminar(t) {   // sin confirmar: se borra y se ofrece Deshacer (volver a crearlo)
  try {
    await api(`/transacciones/${t.id}`, { method: "DELETE" });
    refrescar();
    toast("Movimiento eliminado", "ok", ["Deshacer", async () => {
      const { id, ...datos } = t;
      try { await api("/transacciones", { method: "POST", json: datos }); refrescar(); } catch (e) { avisar(e); }
    }]);
  } catch (e) { avisar(e); }
}

// ---------- Registro rápido ----------
function frec() { return JSON.parse(localStorage.getItem("frec") || "{}"); }

function rapidas() {   // las 3 categorías que más usas para el tipo elegido
  const t = form.elements.tipo.value, f = frec()[t] || {};
  const top = [...(CATEGORIAS[t] ?? [])].sort((a, b) => (f[b[1]] || 0) - (f[a[1]] || 0)).slice(0, 3);
  $("#rapidas").replaceChildren(...top.map(([emoji, nombre]) => {
    const b = el("button", { type: "button", class: "chip" }, `${emoji} ${nombre}`);
    b.addEventListener("click", () => cat.fijar(nombre));
    return b;
  }));
}
$("#fechas-rap").addEventListener("click", (e) => {
  const b = e.target.closest("[data-d]");
  if (b) form.elements.fecha.value = fechaLocal(+b.dataset.d);
});

// ---------- Filtros por tipo y categoría ----------
function pintarFiltros() {
  document.querySelectorAll("#chips [data-tipo]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.tipo === S.tipo)));
  $("#quitar-cat").hidden = !S.cat;
  $("#quitar-cat").textContent = `${S.cat} ✕`;
}
function filtrarCategoria(c) { S.cat = c; S.tipo = "gasto"; pintarFiltros(); cargarLista(true); }
$("#chips").addEventListener("click", (e) => {
  const b = e.target.closest("[data-tipo]");
  if (!b) return;
  S.tipo = b.dataset.tipo; S.cat = "";
  pintarFiltros(); cargarLista(true);
});
$("#quitar-cat").addEventListener("click", () => { S.cat = ""; pintarFiltros(); cargarLista(true); });
$("#reintentar").addEventListener("click", refrescar);

// ---------- Gastos fijos: copiar los del mes anterior ----------
async function copiarFijos() {
  const [a, m] = S.mes.split("-").map(Number), ant = new Date(a, m - 2, 1);
  const listar = async (anio, mes) => (await api(`/transacciones?anio=${anio}&mes=${mes}&dia=${S.dia}&limite=100`)).items;
  try {
    const [previos, actuales] = await Promise.all([listar(ant.getFullYear(), ant.getMonth() + 1), listar(a, m)]);
    const ya = new Set(actuales.filter((t) => t.es_recurrente).map((t) => `${t.categoria}|${t.monto}`));
    const nuevos = previos.filter((t) => t.es_recurrente && !ya.has(`${t.categoria}|${t.monto}`));
    if (!nuevos.length) return toast("No hay gastos fijos por copiar este mes");
    const ultimo = new Date(a, m, 0).getDate();
    for (const { id, fecha, ...t } of nuevos) {
      const dia = String(Math.min(+fecha.slice(8), ultimo)).padStart(2, "0");
      await api("/transacciones", { method: "POST", json: { ...t, fecha: S.dia > 1 ? `${S.mes}-${String(S.dia).padStart(2, "0")}` : `${S.mes}-${dia}` } });
    }
    toast(`Se copiaron ${nuevos.length} gastos fijos`);
    refrescar();
  } catch (e) { avisar(e); }
}
$("#fijos").addEventListener("click", copiarFijos);

// ---------- Entrada rápida: "almuerzo 12 yape" rellena el formulario ----------
const PISTAS = [[/almuerzo|cena|desayuno|comida|men[uú]|rappi|pollo|chifa|kfc/, "Comida"], [/taxi|uber|combi|bus|pasaje|gasolina/, "Transporte"],
  [/curso|libro|copias|univers|matr[ií]cula/, "Universidad"], [/luz|agua|internet|alquiler/, "Hogar y servicios"], [/ropa|zapat|compra|tienda|super/, "Compras"],
  [/cine|juego|fiesta|salida|steam/, "Ocio y salidas"], [/farmacia|doctor|medic|salud/, "Salud"], [/gato|perro|mascota|veterin/, "Mascotas"],
  [/netflix|spotify|suscrip|youtube/, "Suscripciones"], [/viaje|vuelo|hotel/, "Viajes"], [/sueldo|me pagaron|cobr/, "Sueldo"],
  [/beca|mesada|apoyo/, "Beca o apoyo"], [/freelance|proyecto/, "Trabajo freelance"], [/regalo|propina/, "Regalo"]];

$("#rapido").addEventListener("input", (e) => {
  const t = e.target.value.toLowerCase(), numero = t.match(/\d+(?:[.,]\d{1,2})?/);
  if (numero) form.elements.monto.value = numero[0].replace(",", ".");
  const tipo = /sueldo|beca|mesada|propina|me pagaron|cobr|regalo|freelance/.test(t) ? "ingreso" : /ahorr/.test(t) ? "ahorro" : "gasto";
  form.elements.tipo.value = tipo; cat.repintar(); rapidas();
  const metodo = METODOS.find(([, n]) => t.includes(n.toLowerCase()));
  if (metodo) met.fijar(metodo[1]);
  const pista = PISTAS.find(([re, nombre]) => re.test(t) && CATEGORIAS[tipo].some(([, n]) => n === nombre));
  if (pista) cat.fijar(pista[1]);
});

// ---------- Presupuestos y metas ----------
const RANGO = { ok: 0, aviso1: 1, aviso2: 2, limite: 3 };
function avisarCruces() {   // aviso emergente SOLO cuando un presupuesto sube de nivel dentro del mismo periodo
  const clave = `${S.mes}|${S.dia}`, ps = S.estado?.presupuestos ?? [];
  if (S.nivelesClave === clave) {
    for (const p of ps) if (RANGO[p.nivel] > (RANGO[S.niveles?.[p.categoria]] ?? 0)) toast(`${p.categoria}: ya usaste el ${p.porcentaje}% de tu presupuesto`, "error");
  }
  S.niveles = Object.fromEntries(ps.map((p) => [p.categoria, p.nivel]));
  S.nivelesClave = clave;
}

async function cargarPresupuestos() {
  try {
    [S.limites, S.estado] = await Promise.all([api("/presupuestos"), api(`/presupuestos/estado?${periodoQS()}`)]);
    avisarCruces();
    pintarPresupuestos();
  } catch (e) { avisar(e); }
}

function pintarPresupuestos() {
  const r = S.res, limites = S.limites;
  if (!r || !limites) return;
  const avisos = S.estado?.avisos ?? [75, 90, 100], est = Object.fromEntries((S.estado?.presupuestos ?? []).map((p) => [p.categoria, p]));
  const esMeta = (c) => CATEGORIAS.ahorro.some(([, n]) => n === c) || c in r.ahorro_por_categoria;
  $("#pres").replaceChildren(...Object.entries(limites).map(([c, limite]) => {
    const meta = esMeta(c), e = est[c];
    const usado = meta ? r.ahorro_por_categoria[c] || 0 : e ? e.gastado : r.gastos_por_categoria[c] || 0;
    const pct = !meta && e ? e.porcentaje : Math.round((usado / limite) * 100);
    const barra = el("div", { class: "meter", role: "img", "aria-label": `${c}: ${pct}%` }, el("span"));
    barra.firstChild.style.setProperty("--p", `${Math.min(pct, 100)}%`);
    if (!meta) barra.classList.add(pct >= avisos[2] ? "alerta" : pct >= avisos[1] ? "aviso2" : pct >= avisos[0] ? "aviso" : "ok");   // avisos configurables
    const nivel = pct >= avisos[2] ? ", te pasaste" : pct >= avisos[1] ? ", muy cerca del límite" : pct >= avisos[0] ? ", cerca del límite" : "";
    const nota = meta ? `Ahorrado ${PEN.format(usado)} de ${PEN.format(limite)} (${pct}%)`
      : `Gastado ${PEN.format(usado)} de ${PEN.format(limite)} (${pct}%)${nivel}${e ? `. Se renueva el ${e.renueva}.` : ""}`;
    return el("li", {}, el("div", { class: "pres-cab" }, el("strong", {}, `${meta ? "🎯" : "💸"} ${c}`),
      el("button", { type: "button", class: "icon-btn", "data-quitar": c, "aria-label": `Quitar ${c}` }, "✕")), barra, el("small", {}, nota));
  }));
}

const dlgP = $("#dlg-pres");
$("#pres-nuevo").addEventListener("click", () => {
  $("#pres-cat").replaceChildren(...[...CATEGORIAS.gasto, ...CATEGORIAS.ahorro].map(([e, n]) => el("option", { value: n }, `${e} ${n}`)));
  $("#pres-limite").value = ""; $("#pres-err").textContent = "";
  dlgP.showModal();
});
$("#pres-cancelar").addEventListener("click", () => dlgP.close());
$("#form-pres").addEventListener("submit", async (e) => {
  e.preventDefault();
  const limite = parseFloat($("#pres-limite").value);
  if (!(limite > 0)) { $("#pres-err").textContent = "Escribe un monto mayor a 0"; return; }
  try {
    await api(`/presupuestos/${encodeURIComponent($("#pres-cat").value)}`, { method: "PUT", json: { limite, esencial: $("#pres-esencial").checked } });
    dlgP.close(); toast("Guardado"); cargarPresupuestos();
  } catch (err) { if (!err.silencioso) $("#pres-err").textContent = err.message; }
});
$("#pres").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-quitar]");
  if (!b) return;
  try { await api(`/presupuestos/${encodeURIComponent(b.dataset.quitar)}`, { method: "PUT", json: { limite: 0 } }); cargarPresupuestos(); }
  catch (err) { avisar(err); }
});

// ---------- Sin conexión: los movimientos nuevos esperan en el teléfono ----------
const cola = () => JSON.parse(localStorage.getItem("cola") || "[]");
function encolar(datos) { localStorage.setItem("cola", JSON.stringify([...cola(), datos])); pintarCola(); }
function pintarCola() {
  const n = cola().length;
  $("#pendientes").hidden = !n;
  $("#pendientes").textContent = `${n} movimiento${n === 1 ? "" : "s"} pendiente${n === 1 ? "" : "s"} de enviar`;
}
async function vaciarCola() {
  let pendientes = cola(), enviados = 0;
  while (pendientes.length && sesion) {
    try { await api("/transacciones", { method: "POST", json: pendientes[0] }); enviados++; }
    catch (e) { if (e.sinRed || e.silencioso) break; /* el servidor lo rechazó: se descarta para no bloquear la cola */ }
    pendientes = pendientes.slice(1);
    localStorage.setItem("cola", JSON.stringify(pendientes));
  }
  pintarCola();
  if (enviados) { toast(`Se enviaron ${enviados} movimientos pendientes`); refrescar(); }
}
window.addEventListener("online", vaciarCola);
pintarCola();
$("#dia").value = S.dia;
$("#dia").addEventListener("change", (e) => {
  S.dia = Math.min(28, Math.max(1, +e.target.value || 1)); e.target.value = S.dia;
  localStorage.setItem("dia_corte", S.dia); S.mes = mesActual(); mostrarMes(); refrescar();
});

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js");
iniciar();

export { $, abrirForm, api, avisar, CATEGORIAS, cargarLista, cargarPresupuestos, el, hoy, mostrarMes, PEN, periodoQS, refrescar, S, toast };
