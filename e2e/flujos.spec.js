// Pruebas de extremo a extremo del frontend. La API se simula: no necesitan Google, Mongo ni el backend.
const { test, expect } = require("@playwright/test");

const resumen = { periodo: { ingresos: 2000, gastos: 750, ahorros: 100, saldo: 1150 }, acumulado: { ingresos: 3000, gastos: 1000, ahorros: 300, saldo: 1700 },
  gastos_por_categoria: { Comida: 500, Transporte: 250 }, ahorro_por_categoria: { "Meta personal": 300 }, total_movimientos: 3 };
const hoy = new Date().toISOString().slice(0, 10);
const mov = (id, extra = {}) => ({ id, tipo: "gasto", monto: 25, categoria: "Comida", metodo_pago: "Yape", fecha: hoy, descripcion: null, es_recurrente: false, ...extra });

test.beforeEach(async ({ page }) => {
  page.peticiones = [];
  await page.route("**/accounts.google.com/**", (r) => r.abort());
  await page.route("**/api/**", async (route) => {
    const req = route.request(), url = new URL(req.url()), ruta = url.pathname, json = (d, status = 200) => route.fulfill({ status, json: d });
    page.peticiones.push({ metodo: req.method(), ruta, query: url.search, cuerpo: req.postData() });
    if (ruta === "/api/auth/me") return json({ email: "ana@example.com" });
    if (ruta === "/api/config") return json({ google_client_id: "cid" });
    if (ruta === "/api/presupuestos") return json({ Comida: 400 });
    if (ruta === "/api/transacciones/resumen") return json(resumen);
    if (ruta === "/api/transacciones/historial") return json({ meses: [{ mes: "2026-09", ingresos: 1000, gastos: 500, ahorros: 0 }, { mes: "2026-10", ingresos: 2000, gastos: 750, ahorros: 100 }] });
    if (req.method() === "POST") return json({ id: "9", ...JSON.parse(req.postData()) }, 201);
    if (req.method() === "DELETE") return route.fulfill({ status: 204 });
    return json({ items: [mov("1"), mov("2", { categoria: "Transporte", monto: 4 })], total: 2, pagina: 1, limite: 15, hay_mas: false });
  });
  await page.goto("/");
});

test("con sesión activa muestra saldo, movimientos, gráficos y presupuestos", async ({ page }) => {
  await expect(page.locator("#saldo")).toContainText("1,700.00");
  await expect(page.locator(".mov")).toHaveCount(2);
  await expect(page.locator("#g-cat")).toBeAttached();   // el gráfico está en la sección Informes
  await expect(page.locator("#pres")).toContainText("Gastado S/ 500.00 de S/ 400.00");   // 125%: se pasó
});

test("registrar un gasto con categoría frecuente y método", async ({ page }) => {
  await page.click("#fab");
  await page.click('[data-rapida="gasto"]');
  await page.fill("#monto", "12.5");
  await page.locator("#rapidas .chip", { hasText: "Comida" }).click();
  await page.click("#met-btn");
  await page.locator("#met-ops .op", { hasText: "Yape" }).click();
  await page.click("#guardar");
  await expect.poll(() => page.peticiones.some((p) => p.metodo === "POST" && JSON.parse(p.cuerpo).categoria === "Comida")).toBe(true);
});

test("escribir 'almuerzo 12 yape' rellena el formulario", async ({ page }) => {
  await page.click("#fab");
  await page.click('[data-rapida="gasto"]');
  await page.fill("#rapido", "almuerzo 12 yape");
  await expect(page.locator("#monto")).toHaveValue("12");
  await expect(page.locator("#cat-btn")).toContainText("Comida");
  await expect(page.locator("#met-btn")).toContainText("Yape");
});

test("eliminar ofrece deshacer", async ({ page }) => {
  await page.evaluate(() => { location.hash = "#/movimientos"; });
  await page.locator(".mov").first().locator("summary").click();
  await page.locator(".mov").first().getByText("Eliminar").click();
  await expect(page.locator(".toast", { hasText: "eliminado" })).toBeVisible();
  await page.getByRole("button", { name: "Deshacer" }).click();
  await expect.poll(() => page.peticiones.filter((p) => p.metodo === "POST").length).toBe(1);
});

test("cambiar de mes, filtrar por tipo y cambiar el día de corte", async ({ page }) => {
  await page.evaluate(() => { location.hash = "#/movimientos"; });
  await page.click("#mes-ant");
  await page.getByRole("button", { name: "Gastos" }).click();
  await expect.poll(() => page.peticiones.some((p) => p.ruta === "/api/transacciones" && p.query.includes("tipo=gasto"))).toBe(true);
  await page.fill("#dia", "15");
  await page.locator("#dia").dispatchEvent("change");
  await expect.poll(() => page.peticiones.some((p) => p.query.includes("dia=15"))).toBe(true);
});

test("sin conexión el movimiento queda pendiente", async ({ page }) => {
  await page.click("#fab");
  await page.click('[data-rapida="gasto"]');
  await page.fill("#monto", "9");
  await page.locator("#rapidas .chip").first().click();
  await page.click("#met-btn");
  await page.locator("#met-ops .op").first().click();
  await page.route("**/api/transacciones", (r) => (r.request().method() === "POST" ? r.abort() : r.fallback()));
  await page.click("#guardar");
  await expect(page.locator("#pendientes")).toContainText("1 movimiento pendiente");
});
