# 💰 Mi Billetera - Sistema de Control Financiero

Un sistema completo de finanzas personales, diseñado para ser rápido, seguro y fácil de desplegar. Construido con un backend moderno en **FastAPI + MongoDB** y una PWA ligera en **JavaScript puro**.

✨ **Características principales:**
- **Autenticación Segura:** Login integrado con Google.
- **Multiusuario & Aislado:** Cada usuario tiene su propia base de datos de movimientos.
- **Sincronización en la Nube:** Espejo opcional automatizado hacia Google Sheets (cada usuario recibe su propia hoja clonada).
- **Despliegue Gratuito:** Optimizado para la capa gratuita de Render.com y MongoDB Atlas.

---

## 🚀 Puesta en marcha (Desarrollo Local en Windows)

Sigue estos pasos para probar el proyecto en tu propia computadora:

```bash
# 1. Crear y activar el entorno virtual
py -m venv venv
venv\Scripts\activate

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Configurar variables de entorno (ver sección abajo)
copy .env.example .env

# 4. Iniciar el servidor
uvicorn app.main:app --reload
```
Abre `http://localhost:8000` en tu navegador. El backend sirve directamente la carpeta `frontend/` en el mismo origen.

---

## ⚙️ Configuración (`.env`)

Debes crear un archivo `.env` en la raíz del proyecto. Las variables más importantes son:

| Variable | Descripción |
|---|---|
| `MONGO_URL`, `MONGO_DB` | Conexión a tu clúster de MongoDB Atlas. (Por defecto: `control_financiero`). |
| `GOOGLE_CLIENT_ID` | ID de cliente OAuth de Google (Credencial tipo "Aplicación web"). |
| `SESSION_SECRET` | Llave secreta para mantener la sesión de 30 días activa. Puedes generar una ejecutando: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `ALLOWED_EMAILS` | (Opcional) Correos permitidos separados por comas. Si se deja vacío, cualquier cuenta de Google puede registrarse. |
| `TEMPLATE_SHEET_ID` | **Nuevo:** El ID de tu archivo plantilla de Google Sheets. El robot usará este archivo como molde para clonarlo a los nuevos usuarios. |
| `GOOGLE_CREDENTIALS_FILE` | Ruta de tu archivo `credenciales.json` (cuenta de servicio de Google) para que el bot pueda clonar y escribir en los Sheets. |
| `COOKIE_SECURE` | Cambiar a `true` cuando se publique el proyecto en internet (HTTPS). |

⚠️ **Seguridad:** Los archivos `.env` y `credenciales.json` son estrictamente secretos. 

---

## 🛠️ ¿Qué incluye la plataforma?

El sistema cuenta con las siguientes secciones:

- 📊 **Inicio:** Resumen de saldo, dinero disponible y proyecciones.
- 💸 **Movimientos:** Registro detallado con etiquetas, notas y exportación a CSV/PDF.
- 🎯 **Presupuestos & Metas:** Establece límites de gasto por categoría y metas de ahorro con cálculo de progreso.
- 📅 **Pagos Programados:** Avisos de vencimiento para que nunca olvides una fecha de pago.
- 📈 **Informes:** Gráficos de tendencias y comparaciones de meses anteriores.
- ⚙️ **Ajustes:** Personalización, alertas y sistema de copias de seguridad.

> *Nota: Todos los cálculos y asistentes en pantalla utilizan reglas fijas basadas estrictamente en tus datos; no se envía información a servicios de IA externos (proximo).*

---

## 🧪 Pruebas (Testing)

El proyecto incluye un entorno robusto de pruebas tanto para el backend como para el frontend.

```bash
# Instalar dependencias de desarrollo
pip install -r requirements-dev.txt

# Pruebas del Backend (129 pruebas automáticas)
pytest

# Pruebas del Frontend (Extremo a Extremo)
cd e2e
npm install
npm run humo          # Interfaz en DOM simulado
npx playwright install chromium
npx playwright test   # Pruebas completas en un navegador real
```

---

## 🔧 Scripts de Mantenimiento (Opcionales)

Se ejecutan en la raíz del proyecto para realizar migraciones de datos antiguos:
- `python migrar_categorias.py`: Unifica categorías antiguas de gastos.
- `python migrar_centavos.py`: Añade formato de centavos a registros antiguos.
*(Agrega la bandera `--aplicar` al final del comando para guardar los cambios en la base de datos).*

---

## 📌 Límites Conocidos actuales
- Moneda única (Soles).
- Los presupuestos, el asistente y la proyección están anclados al mes en curso.
- Las alertas de vencimiento solo funcionan mientras la aplicación está abierta (sin notificaciones push en el teléfono).
- Registro manual o por importación de CSV (sin conexión directa con bancos).

Para ver el historial de desarrollo, consulta `docs/CAMBIOS.md`.
