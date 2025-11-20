# Gestor de Bodega

Aplicación Flask para administrar inventario, ingresos, despachos, órdenes de compra y evidencias fotográficas, con exportes a PDF y trazabilidad vía logs.

## Requisitos
- Python 3.10+ (probado con SQLite por defecto).
- `python -m venv` disponible para aislar dependencias.

## Puesta en marcha
1) Crear y activar entorno:
```bash
python -m venv .venv
source .venv/bin/activate
```
2) Instalar dependencias:
```bash
pip install -r requirements.txt
```
3) Ejecutar la app (crea tablas en `instance/mydb.db` si faltan):
```bash
python app.py
```
   Alternativa: `FLASK_APP=app.py FLASK_ENV=development flask run`

## Configuración
- Base de datos: SQLite en `instance/mydb.db` (carpeta ignorada por Git). Para reset local elimina ese archivo y vuelve a ejecutar la app.
- Secret key: usa `SECRET_KEY` hardcodeado para local; en despliegues define `SECRET_KEY` vía entorno.
- Subidas: imágenes de despacho se guardan en `static/uploads/` (extensiones `png,jpg,jpeg,webp`).

## Estructura principal
- `app.py`: rutas, lógica de negocio, generación de PDFs y protección de vistas.
- `database/db.py`: modelos SQLAlchemy (Usuarios, Productos, Clientes, Ingresos, Despachos, Fotos, Órdenes, Logs) y helper de inicialización.
- `templates/`: vistas HTML (dashboard, inventario, ingresos, despachos, órdenes, logs, perfil, etc.).
- `static/`: JS/CSS (`main.js`, `edit_user.js`) y carpeta `uploads/` para fotos de despachos.
- `instance/`: base SQLite y secretos locales (no versionada por Flask).

## Flujos y funcionalidades
- **Onboarding y sesión**: `/setup` crea el primer admin si no hay usuarios. Login vía `/api/login`; logout simple en `/api/logout`. Perfil permite cambiar nombre/email, contraseña y tema (`dark`/`light`).
- **Dashboard**: KPIs de stock, ingresos, despachos, clientes, órdenes; serie de despachos últimos 14 días; top 3 clientes por unidades despachadas; conteo de órdenes completas/parciales/pendientes.
- **Inventario (ingresos)**: `/ingresos/nuevo` crea ingresos de stock; valida productos/qty, actualiza stock y registra `InventoryEntry`. Edición de ingresos ajusta stock según delta. Histórico en `/ingresos/historicos` y API `/api/ingresos/historico`.
- **Productos**: API de sugerencias `/api/productos/suggest`. Detalle de referencias (`ingresos`, `despachos`, `ordenes`) en `/api/productos/<id>/refs_detail` con borrado selectivo `/refs_delete`. Merge de productos `/api/productos/merge` conserva referencias; borrado `/api/productos/<id>` bloqueado si tiene uso.
- **Despachos**: `/despachos/nuevo` crea lote asociado a cliente (crea cliente si no existe) y opcional número de orden. Descuenta stock, advierte si se despacha menos por falta. Histórico `/despachos/historico` + API `/api/despachos/historico`. Edición permite ajustar items y stock. Fotos por etapa (`salida`, `entrega`) en `/api/despachos/<batch_id>/fotos`. Exportación PDF en `/despachos/<id>/export/pdf`.
- **Órdenes de compra**: listar en `/ordenes`, crear en `/ordenes/nuevo`, ver detalle `/ordenes/<id>` (incluye despachos vinculados por `order_number`), editar `/ordenes/editar/<id>`, eliminar `/api/ordenes/<id>`. PDF en `/ordenes/<id>/export/pdf`. Estado de orden se calcula por unidades despachadas vs solicitadas.
- **Logs**: `/api/logs` ofrece filtrado por usuario/acción/tabla/fechas para auditar operaciones (ingresos, despachos, merges, etc.). Vista en `/logs`.
- **Reportes PDF**: usa `fpdf2`. Encabezado + tabla de metadatos (cliente, operador, fecha, orden) y tabla de productos. Despachos incluyen fotos agrupadas por etapa. Órdenes muestran sus ítems y, si existen, los despachos asociados con sus tablas y fotos.

## Operativa y mantenimiento
- Reset local: eliminar `instance/mydb.db` y volver a correr la app para recrear esquema.
- Limpieza de fotos huérfanas: las rutas se guardan en DB; si faltan archivos, el PDF marca `[Archivo faltante: ...]`.
- Sanitización: entradas de texto pasan por `bleach.clean`. PDFs usan `_pdf_sanitize` para caracteres latin-1.

## Pruebas manuales sugeridas
- Crear admin en `/setup`, luego login y cambio de tema en perfil.
- Alta/edición de ingreso y verificación de stock en inventario.
- Creación de despacho con y sin stock suficiente; carga de fotos en ambas etapas; descarga de PDF.
- Creación, edición y eliminación de productos (incluye merge y bloqueo por referencias).
- Creación/edición/eliminación de órdenes; ver detalle y PDF con despachos asociados.
- Revisar filtros de logs con acciones recientes.
