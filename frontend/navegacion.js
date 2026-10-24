// Navegación entre secciones (menú lateral), encabezado, acciones rápidas (+) y selector de periodo.
import { $, abrirForm, mostrarMes, refrescar, S } from "./app.js";

// ---------- Secciones: cada tarjeta declara en data-vista a qué sección pertenece ----------
const VISTAS = ["inicio", "movimientos", "presupuestos", "metas", "pagos", "cuentas", "deudas", "informes", "config"];

function mostrarVista() {
  const pedida = location.hash.replace("#/", "");
  const v = VISTAS.includes(pedida) ? pedida : "inicio";
  document.querySelectorAll("[data-vista]").forEach((n) => { n.hidden = !n.dataset.vista.split(" ").includes(v); });
  document.querySelectorAll("#menu [data-ir]").forEach((a) => a.setAttribute("aria-current", String(a.dataset.ir === v)));
  scrollTo(0, 0);
}
window.addEventListener("hashchange", mostrarVista);
mostrarVista();

// ---------- Encabezado: saludo y fecha ----------
function saludo() {
  const h = new Date().getHours(), momento = h < 12 ? "Buenos días" : h < 19 ? "Buenas tardes" : "Buenas noches";
  $("#saludo").textContent = `${momento}${S.nombre ? `, ${S.nombre}` : ""}`;
  $("#fecha-hoy").textContent = new Date().toLocaleDateString("es-PE", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
}
document.addEventListener("datos-actualizados", saludo);
saludo();

// La búsqueda del encabezado lleva a Movimientos y reutiliza el buscador (con su espera de 300 ms)
$("#buscar-global").addEventListener("input", (e) => {
  location.hash = "#/movimientos";
  const q = $("#q");
  q.value = e.target.value;
  q.dispatchEvent(new Event("input"));
});

// ---------- Botón flotante (+): gasto, ingreso, transferencia o ahorro ----------
const fab = $("#fab"), menu = $("#fab-menu");
const cerrarMenu = () => { menu.hidden = true; fab.setAttribute("aria-expanded", "false"); };
fab.addEventListener("click", () => { menu.hidden = !menu.hidden; fab.setAttribute("aria-expanded", String(!menu.hidden)); });
menu.addEventListener("click", (e) => {
  const b = e.target.closest("[data-rapida]");
  if (!b) return;
  cerrarMenu();
  if (b.dataset.rapida === "transferir") $("#transferir").click();   // el diálogo de transferencias vive en planificacion.js
  else abrirForm({ tipo: b.dataset.rapida });
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") cerrarMenu(); });
document.addEventListener("click", (e) => { if (!menu.hidden && !e.target.closest("#fab, #fab-menu")) cerrarMenu(); });

// ---------- Periodo de análisis: mes, 7 días, 3 meses, 6 meses o rango personalizado ----------
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

function aplicarRango() {
  const tipo = $("#rango").value, hoy = new Date();
  $("#rango-custom").hidden = tipo !== "custom";
  for (const id of ["mes-ant", "mes-sig"]) $(`#${id}`).disabled = tipo !== "mes";
  if (tipo === "mes") S.rango = null;
  else if (tipo === "7d") { const d = new Date(); d.setDate(d.getDate() - 6); S.rango = { desde: iso(d), hasta: iso(hoy) }; }
  else if (tipo === "3m" || tipo === "6m") {
    const d = new Date(); d.setMonth(d.getMonth() - (tipo === "3m" ? 3 : 6)); d.setDate(d.getDate() + 1);
    S.rango = { desde: iso(d), hasta: iso(hoy) };
  } else {
    if (!$("#rango-desde").value) { $("#rango-desde").value = iso(new Date(hoy.getFullYear(), hoy.getMonth(), 1)); $("#rango-hasta").value = iso(hoy); }
    const desde = $("#rango-desde").value, hasta = $("#rango-hasta").value;
    if (!desde || !hasta || desde > hasta) return;   // espera a tener un rango válido
    S.rango = { desde, hasta };
  }
  mostrarMes();
  refrescar();
}
$("#rango").addEventListener("change", aplicarRango);
$("#rango-desde").addEventListener("change", aplicarRango);
$("#rango-hasta").addEventListener("change", aplicarRango);
