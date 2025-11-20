import io
import json
import os
import time
from datetime import datetime, timedelta
from functools import wraps

import bleach
from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    send_file,
    url_for,
)
from fpdf import FPDF
from werkzeug.utils import secure_filename
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import joinedload

from database.db import (
    Client,
    DispatchBatch,
    DispatchEntry,
    DispatchPhoto,
    InventoryEntry,
    IngresoBatch,
    Log,
    Product,
    PurchaseOrder,
    PurchaseOrderItem,
    Users,
    db,
    init_db,
)

app = Flask(__name__)

# Configuration
app.config.from_mapping(
    SECRET_KEY='your-secret-key',
    DEBUG=True,
)

ALLOWED_TAGS = []
ALLOWED_ATTRS = {}
UPLOAD_DIR = os.path.join(app.root_path, 'static', 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)
ALLOWED_IMAGE_EXT = {'png', 'jpg', 'jpeg', 'webp'}


def clean_text(value: str) -> str:
    """Sanitize incoming text fields with shared bleach rules."""
    return bleach.clean(
        (value or '').strip(),
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        strip=True
    )


def get_json_body(default=None):
    """Safe JSON loader with predictable default."""
    return request.get_json(silent=True) or (default if default is not None else {})


def render_view(template_name: str, **context):
    """Render templates injecting session identity helpers."""
    base_context = {
        'name': session.get('name'),
        'is_Admin': session.get('is_Admin', False)
    }
    base_context.update(context)
    return render_template(template_name, **base_context)

init_db(app)


def _parse_dmy(s: str):
    """Convierte 'dd/mm/aaaa' a datetime a las 00:00; devuelve None si no aplica."""
    try:
        d = datetime.strptime(s.strip(), "%d/%m/%Y")
        return d
    except Exception:
        return None


def _to_iso(dt):
    if isinstance(dt, datetime):
        try:
            return dt.isoformat(sep=' ', timespec='seconds')
        except Exception:
            return dt.isoformat()
    return dt  # ya es str o None


# --------------------------------------------------------------------------- #
# Session context & decorators
# --------------------------------------------------------------------------- #
@app.context_processor
def inject_globals():
    return {
        'name': session.get('name'),
        'is_Admin': session.get('is_Admin', False)
    }


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Si no está logueado o no es admin, lo redirigimos o devolvemos 403
        if not session.get('user_id'):
            return redirect(url_for('index'))
        if not session.get('is_Admin', False):
            # Puedes redirigir al dashboard normal, o lanzar 403:
            return redirect(url_for('logout'))
        # Si llegó aquí, es admin: ejecuta la vista
        return f(*args, **kwargs)
    return decorated_function


def _allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXT


def _pdf_sanitize(text):
    s = "-" if text in (None, "") else str(text)
    s = s.replace("—", "-")
    return s.encode('latin-1', 'replace').decode('latin-1')


def _wrap_pdf_text(text, width=50):
    import textwrap
    s = _pdf_sanitize(text)
    return "\n".join(textwrap.fill(s, width=width, break_long_words=True, break_on_hyphens=False).splitlines())


def _pdf_header(pdf: FPDF, title: str):
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, title, ln=1)
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 11)


def _pdf_add_keyvals(pdf: FPDF, pairs):
    max_width = max(20, pdf.w - pdf.l_margin - pdf.r_margin - 2)
    for label, value in pairs:
        line = _pdf_sanitize(f"{label}: {value if value not in (None, '') else '-'}")
        wrapped = _wrap_pdf_text(line, width=60)
        pdf.multi_cell(max_width, 8, wrapped, border=0)
    pdf.ln(1)


def _pdf_add_table(pdf: FPDF, headers, rows):
    # Ajustar ancho total disponible
    avail = pdf.w - pdf.l_margin - pdf.r_margin
    total_w = sum(w for _, w in headers)
    scale = avail / total_w if total_w > avail else 1
    scaled = [(h, w * scale) for h, w in headers]

    pdf.set_font("Helvetica", "B", 9)
    for h, w in scaled:
        pdf.cell(w, 8, h, border=1)
    pdf.ln()
    pdf.set_font("Helvetica", "", 9)

    line_h = 6
    for row in rows:
        texts = [_wrap_pdf_text(val, width=35) for val in row]
        line_counts = [max(1, len(t.split("\n"))) for t in texts]
        height = line_h * max(line_counts)
        x0 = pdf.get_x()
        y0 = pdf.get_y()
        offset = 0
        for idx, (_, w) in enumerate(scaled):
            pdf.set_xy(x0 + offset, y0)
            pdf.multi_cell(w, line_h, texts[idx], border=1)
            offset += w
        pdf.set_xy(x0, y0 + height)
    pdf.ln(3)


def _pdf_add_photos(pdf: FPDF, photos):
    if not photos:
        pdf.cell(0, 8, "Sin fotos adjuntas.", ln=1)
        pdf.ln(2)
        return
    stage_order = ['salida', 'entrega']
    grouped = {s: [] for s in stage_order}
    for p in photos:
        grouped.setdefault(p.stage, []).append(p)
    for stage in stage_order:
        plist = grouped.get(stage, [])
        if not plist:
            continue
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, f"Fotos {stage}", ln=1)
        pdf.set_font("Helvetica", "", 11)
        for p in plist:
            path = os.path.join(app.root_path, 'static', p.path)
            if os.path.exists(path):
                pdf.cell(0, 6, _to_iso(p.created_at) or "", ln=1)
                try:
                    avail = pdf.w - pdf.l_margin - pdf.r_margin
                    pdf.image(path, w=min(150, avail))
                except Exception:
                    pdf.cell(0, 6, f"[No se pudo cargar la imagen {p.path}]", ln=1)
            else:
                pdf.cell(0, 6, f"[Archivo faltante: {p.path}]", ln=1)
        pdf.ln(4)


# --------------------------------------------------------------------------- #
# Auth bootstrap & onboarding
# --------------------------------------------------------------------------- #
@app.before_request
def ensure_first_user():
    # Solo si no hay ningún usuario registrado
    if Users.query.count() == 0:
        # Permitir el acceso a /setup, a los assets (/static/*) y al login
        if request.endpoint not in ('setup', 'static', 'login'):
            return redirect(url_for('setup'))


@app.route('/setup', methods=['GET', 'POST'])
def setup():
    # Si ya hay usuarios, redirige al login
    if Users.query.count() > 0:
        return redirect(url_for('login'))

    if request.method == 'GET':
        return render_view('setup.html')

    # POST: recibe JSON con name, username, email, password, password2
    data = get_json_body()
    name = clean_text(data.get('name'))
    username = clean_text(data.get('username'))
    email = clean_text(data.get('email'))
    password = data.get('password') or ''
    password2 = data.get('password2') or ''

    # Validaciones mínimas
    if not all([name, username, email, password, password2]):
        return jsonify(error="Todos los campos son obligatorios"), 400

    if password != password2:
        return jsonify(error="Las contraseñas no coinciden"), 400

    if Users.query.filter_by(username=username).first():
        return jsonify(error="Usuario ya existe"), 400

    try:
        # Crear primer administrador
        user = Users(
            name=name,
            username=username,
            email=email,
            is_Admin=True
        )
        user.password = password
        db.session.add(user)
        db.session.commit()
        return jsonify(message="Usuario administrador creado"), 201

    except SQLAlchemyError:
        db.session.rollback()
        return jsonify(error="Error creando usuario"), 500


@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))  # asume que tienes esta ruta
    return render_view('index.html')


@app.route('/logs')
@login_required
def logs_page():
    return render_view('logs.html')


# --------------------------------------------------------------------------- #
# Perfil y configuración de cuenta
# --------------------------------------------------------------------------- #
@app.route('/perfil', methods=['GET'])
@login_required
def perfil():
    # obtenemos el usuario logueado
    user = Users.query.get_or_404(session['user_id'])
    return render_view('perfil.html',
                       username=user.username,
                       name=user.name,
                       email=user.email)


@app.route('/api/perfil', methods=['POST'])
@login_required
def api_actualizar_perfil():
    user = Users.query.get_or_404(session['user_id'])
    data = get_json_body()
    # Validar campos
    name = clean_text(data.get('name'))
    email = clean_text(data.get('email'))
    if not name or not email:
        return jsonify(error="Nombre y email son obligatorios"), 400

    try:
        user.name = name
        user.email = email
        db.session.commit()
        return jsonify(message="Perfil actualizado"), 200
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify(error="Error al actualizar perfil"), 500


@app.route('/api/perfil/password', methods=['POST'])
@login_required
def api_cambiar_password():
    user = Users.query.get_or_404(session['user_id'])
    data = get_json_body()
    old = data.get('old_pass', '')
    new = data.get('new_pass', '')
    rep = data.get('repet_new_pass', '')

    if not old or not new or not rep:
        return jsonify(error="Todos los campos de contraseña son obligatorios"), 400
    if new != rep:
        return jsonify(error="Las contraseñas nuevas no coinciden"), 400
    if not user.check_password(old):
        return jsonify(error="Contraseña actual incorrecta"), 400

    try:
        user.password = new
        db.session.commit()
        return jsonify(message="Contraseña cambiada"), 200
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify(error="Error al cambiar contraseña"), 500


@app.route('/api/logs')
@login_required
def api_logs():
    # Parámetros de filtro opcionales
    user_q = request.args.get('user', '').strip().lower()
    action_q = request.args.get('action', '').strip().lower()
    table_q = request.args.get('table', '').strip().lower()
    start = request.args.get('start')  # YYYY-MM-DD
    end = request.args.get('end')    # YYYY-MM-DD

    q = Log.query.join(Log.user)  # para poder filtrar por username

    # Filtros
    if user_q:
        q = q.filter(Users.username.ilike(f"%{user_q}%"))
    if action_q:
        q = q.filter(Log.action.ilike(f"%{action_q}%"))
    if table_q:
        q = q.filter(Log.target_table.ilike(f"%{table_q}%"))
    if start:
        q = q.filter(Log.created_at >= f"{start} 00:00:00")
    if end:
        q = q.filter(Log.created_at <= f"{end} 23:59:59")

    entries = q.order_by(Log.created_at.desc()).all()

    # Serializar
    out = []
    for e in entries:
        out.append({
            'id':           e.id,
            'user':         e.user.username,
            'action':       e.action,
            'table':        e.target_table,
            'target_id':    e.target_id,
            'details':      e.details,
            'created_at':   e.created_at.strftime('%d/%m/%Y %H:%M:%S')
        })
    return jsonify(out)


@app.route('/perfil/theme', methods=['POST'])
@login_required
def perfil_theme():
    data = get_json_body()
    theme = data.get('theme')
    if theme not in ('dark', 'light'):
        return jsonify(error="Tema inválido"), 400

    user = Users.query.get(session['user_id'])
    user.theme = theme
    db.session.commit()
    session['theme'] = theme
    return jsonify(message="Tema actualizado"), 200


@app.route('/api/login', methods=["POST"])
def login():
    data = get_json_body()
    if not data:
        return jsonify({"error": "JSON inválido"}), 400

    username = data.get('username')
    password = data.get('password')
    if not username or not password:
        return jsonify({"error": "Faltan credenciales"}), 422

    user = Users.query.filter_by(username=username).first()
    if not user or not user.check_password(password):
        return jsonify({"error": "Usuario o contraseña incorrectos"}), 401
    session.clear()
    session['user_id'] = user.id
    session['is_Admin'] = user.is_Admin
    session['name'] = user.name
    session['theme'] = user.theme
    db.session.add(Log(
        user_id=user.id,
        action='login',
        target_table='users',
        target_id=user.id,
        details=None
    ))
    db.session.commit()  # si no habías hecho commit ya
    return jsonify({
        "user_id": user.id,
        "username": user.username
    })


@app.route('/api/logout')
def logout():
    # Elimina únicamente las claves de sesión que usas
    # O bien, para limpiar todo:
    session.clear()

    return redirect(url_for('index'))


# --------------------------------------------------------------------------- #
# Ingresos y visualización de inventario
# --------------------------------------------------------------------------- #
@app.route('/api/ingresos/historico', methods=['GET'])
@login_required
def api_ingresos_historico():
    """
    Devuelve cabeceras de ingresos (IngresoBatch) con sus items.
    Parámetros opcionales:
      - start=dd/mm/aaaa
      - end=dd/mm/aaaa   (se incluye hasta las 23:59:59 del día)
    """
    start_s = request.args.get('start', '')
    end_s = request.args.get('end', '')

    q = IngresoBatch.query.options(
        joinedload(IngresoBatch.user),  # b.user puede ser None
        joinedload(IngresoBatch.entries).joinedload(InventoryEntry.product)
    ).order_by(IngresoBatch.id.desc())

    start_dt = _parse_dmy(start_s)
    end_dt = _parse_dmy(end_s)
    if start_dt:
        q = q.filter(IngresoBatch.created_at >= start_dt)
    if end_dt:
        q = q.filter(IngresoBatch.created_at < (end_dt + timedelta(days=1)))

    batches = q.all()

    result = []
    for b in batches:
        user_name = getattr(getattr(b, 'user', None), 'name', None) or "—"
        items = [{
            'entry_id': e.id,
            'product':  {
                'name':  getattr(e.product, 'name',  None),
                'brand': getattr(e.product, 'brand', None)
            },
            'quantity': e.quantity
        } for e in (b.entries or [])]

        result.append({
            'batch_id':   b.id,
            'user':       {'username': user_name},
            'created_at': (b.created_at.isoformat() if getattr(b, 'created_at', None) else None),
            'items':      items
        })

    return jsonify(result), 200


@app.route('/dashboard')
@login_required
def dashboard():
    # KPIs básicos de operación
    total_products = db.session.query(func.count(Product.id)).scalar() or 0
    total_stock = db.session.query(
        func.coalesce(func.sum(Product.stock), 0)).scalar() or 0
    total_ingresos = db.session.query(
        func.coalesce(func.sum(InventoryEntry.quantity), 0)).scalar() or 0
    total_despachos = db.session.query(
        func.coalesce(func.sum(DispatchEntry.quantity), 0)).scalar() or 0
    total_despachos_batches = db.session.query(
        func.count(DispatchBatch.id)).scalar() or 0
    total_clientes = db.session.query(func.count(Client.id)).scalar() or 0

    # Serie de despachos por día (últimos 14 días) fija en longitud
    today = datetime.utcnow().date()
    start_date = today - timedelta(days=13)
    dispatch_rows = (
        db.session.query(
            func.date(DispatchBatch.created_at).label('day'),
            func.count(DispatchBatch.id).label('count')
        )
        .filter(DispatchBatch.created_at >= start_date)
        .group_by(func.date(DispatchBatch.created_at))
        .order_by(func.date(DispatchBatch.created_at))
        .all()
    )
    dispatch_map = {str(row.day): row.count for row in dispatch_rows}
    dispatch_series = [
        {
            'day': (start_date + timedelta(days=offset)).isoformat(),
            'count': dispatch_map.get((start_date + timedelta(days=offset)).isoformat(), 0)
        }
        for offset in range(14)
    ]

    # Top 3 clientes por volumen despachado
    top_clients = (
        db.session.query(
            Client.name.label('client'),
            func.count(DispatchBatch.id).label('despachos'),
            func.coalesce(func.sum(DispatchEntry.quantity), 0).label('unidades')
        )
        .join(DispatchBatch, DispatchBatch.client_id == Client.id)
        .outerjoin(DispatchEntry, DispatchEntry.batch_id == DispatchBatch.id)
        .group_by(Client.id)
        .order_by(func.coalesce(func.sum(DispatchEntry.quantity), 0).desc())
        .limit(3)
        .all()
    )
    top_clients = [
        {'client': row.client, 'despachos': row.despachos,
            'unidades': row.unidades}
        for row in top_clients
    ]

    # Estado de órdenes de compra
    orders = PurchaseOrder.query.options(
        joinedload(PurchaseOrder.items)
    ).all()
    status_totals = {'completas': 0, 'parciales': 0, 'pendientes': 0}
    for po in orders:
        solicitadas = sum(item.quantity for item in po.items)
        despachadas = db.session.query(func.coalesce(
            func.sum(DispatchEntry.quantity), 0))\
            .join(DispatchBatch, DispatchEntry.batch_id == DispatchBatch.id)\
            .filter(DispatchBatch.order_number == po.number)\
            .scalar() or 0

        if solicitadas <= 0:
            status_totals['pendientes'] += 1
        elif despachadas >= solicitadas:
            status_totals['completas'] += 1
        elif despachadas > 0:
            status_totals['parciales'] += 1
        else:
            status_totals['pendientes'] += 1

    stats = {
        'productos': total_products,
        'stock_total': total_stock,
        'ingresos': total_ingresos,
        'despachos': total_despachos,
        'despachos_batches': total_despachos_batches,
        'clientes': total_clientes,
        'ordenes': len(orders),
        'ordenes_status': status_totals,
        'dispatch_series': dispatch_series,
        'top_clients': top_clients
    }
    return render_view("dashboard.html", stats=stats)


@app.route('/inventario')
@login_required
def inventario():
    return render_view("inventario.html")


@app.route('/api/productos/<int:product_id>/usage', methods=['GET'])
@admin_required
def api_get_product_usage(product_id):
    """Devuelve conteo de referencias del producto en otras tablas."""
    product = Product.query.get(product_id)
    if not product:
        return jsonify(error="Producto no encontrado"), 404

    inv_count = db.session.query(func.count(InventoryEntry.id))\
        .filter(InventoryEntry.product_id == product_id).scalar()
    dsp_count = db.session.query(func.count(DispatchEntry.id))\
        .filter(DispatchEntry.product_id == product_id).scalar()
    poi_count = db.session.query(func.count(PurchaseOrderItem.id))\
        .filter(PurchaseOrderItem.product_id == product_id).scalar()

    return jsonify({
        "product": {
            "id": product.id,
            "name": product.name,
            "brand": product.brand,
            "stock": product.stock
        },
        "usage": {
            "inventory_entries": int(inv_count or 0),
            "dispatch_entries": int(dsp_count or 0),
            "purchase_order_items": int(poi_count or 0),
            "total": int((inv_count or 0) + (dsp_count or 0) + (poi_count or 0))
        }
    }), 200

# === 1) Referencias DETALLADAS por producto ===


@app.route('/api/productos/<int:product_id>/refs_detail', methods=['GET'])
@admin_required
def api_product_refs_detail(product_id):
    """Devuelve referencias detalladas de un producto:
    - Ingresos de inventario (item + cabecera/lote)
    - Despachos (item + número de despacho si existe)
    - Ítems de Orden de Compra (item + número de OC)
    """
    p = Product.query.get(product_id)
    if not p:
        return jsonify(error="Producto no encontrado"), 404

    # ========== INVENTORY ENTRIES ==========
    # InventoryEntry debe tener FK batch_id -> InventoryBatch.id (o relación .batch)
    inv_entries = db.session.query(InventoryEntry)\
        .filter(InventoryEntry.product_id == product_id)\
        .order_by(InventoryEntry.id.desc())\
        .all()

    inv_list = []
    for e in inv_entries:
        batch_id = getattr(e, "batch_id", None)
        batch_number = None
        batch_date = None
        try:
            # Si tienes relación ORM: e.batch
            batch = getattr(e, "batch", None)
            if batch is None and batch_id:
                # <-- ajusta nombre si difiere
                batch = InventoryBatch.query.get(batch_id)
            if batch:
                # En tu histórico muestras "Ingreso #<id>", así que usamos el id de la cabecera
                batch_number = getattr(batch, "id", None) or getattr(
                    batch, "number", None)
                batch_date = getattr(batch, "created_at", None) or getattr(
                    batch, "date", None)
        except NameError:
            # Si no existe InventoryBatch en tu proyecto, simplemente no incluimos datos de cabecera
            pass

        inv_list.append({
            "id": e.id,  # ItemID (fila)
            "batch_id": batch_id,
            "batch_number": batch_number,   # lo que verás como "Ingreso #"
            "quantity": getattr(e, "quantity", None),
            "date": _to_iso(getattr(e, "created_at", None) or getattr(e, "date", None) or batch_date),
            "note": getattr(e, "note", None)
        })

    # ========== DISPATCH ENTRIES ==========
# Queremos el ID REAL DEL DESPACHO (batch/cabecera), no el número de OC.
    dsp_list = []
    dsp_entries = db.session.query(DispatchEntry)\
        .filter(DispatchEntry.product_id == product_id)\
        .order_by(DispatchEntry.id.desc())\
        .all()

    for d in dsp_entries:
        dispatch_id = None        # <-- ID real del despacho (cabecera)
        dispatch_code = None      # <-- si tu cabecera tiene un "número/folio" propio del despacho
        dispatch_date = getattr(
            d, "created_at", None) or getattr(d, "date", None)

        try:
            # Recuperamos la cabecera (batch) del despacho
            batch = None
            if hasattr(d, "batch") and d.batch is not None:
                batch = d.batch
            elif hasattr(d, "batch_id") and d.batch_id:
                batch = DispatchBatch.query.get(
                    d.batch_id)  # ajusta nombre si difiere

            if batch:
                dispatch_id = getattr(batch, "id", None)
            # NOTA: aquí NO usamos order_number
                dispatch_code = getattr(batch, "number", None) or getattr(
                    batch, "folio", None)
                dispatch_date = dispatch_date or getattr(
                    batch, "created_at", None) or getattr(batch, "date", None)
        except NameError:
            pass

        dsp_list.append({
            "id": d.id,                       # ItemID del detalle de despacho
            "quantity": getattr(d, "quantity", None),
            # <-- ID real del despacho (lo que pides)
            "dispatch_id": dispatch_id,
            "dispatch_code": dispatch_code,   # <-- opcional: código propio del despacho
            "date": _to_iso(dispatch_date),
            "note": getattr(d, "note", None)
        })

    # ========== PURCHASE ORDER ITEMS ==========
    # PurchaseOrderItem.product_id -> Product.id ; FK order_id -> PurchaseOrder.id
    poi_list = []
    poi_rows = db.session.query(PurchaseOrderItem, PurchaseOrder)\
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.order_id)\
        .filter(PurchaseOrderItem.product_id == product_id)\
        .order_by(PurchaseOrder.id.desc(), PurchaseOrderItem.id.desc())\
        .all()

    for item, oc in poi_rows:
        poi_list.append({
            "id": item.id,
            "order_id": oc.id,
            "order_number": getattr(oc, "number", None) or getattr(oc, "folio", None) or oc.id,
            "quantity": getattr(item, "quantity", None),
            "date": _to_iso(getattr(oc, "created_at", None) or getattr(oc, "date", None))
        })

    return jsonify({
        "product": {"id": p.id, "name": p.name, "brand": p.brand},
        "inventory_entries": inv_list,
        "dispatch_entries": dsp_list,
        "purchase_order_items": poi_list
    }), 200


# === 2) Borrado selectivo de referencias ===
@app.route('/api/productos/<int:product_id>/refs_delete', methods=['POST'])
@admin_required
def api_product_refs_delete(product_id):
    p = Product.query.get(product_id)
    if not p:
        return jsonify(error="Producto no encontrado"), 404

    data = get_json_body()
    inv_ids = set(map(int, data.get("inventory_entry_ids", []) or []))
    dsp_ids = set(map(int, data.get("dispatch_entry_ids", []) or []))
    poi_ids = set(map(int, data.get("purchase_order_item_ids", []) or []))

    # Seguridad: solo borrar registros que efectivamente correspondan al product_id indicado
    try:
        inv_del = 0
        if inv_ids:
            inv_del = InventoryEntry.query.filter(
                InventoryEntry.id.in_(inv_ids),
                InventoryEntry.product_id == product_id
            ).delete(synchronize_session=False)

        dsp_del = 0
        if dsp_ids:
            dsp_del = DispatchEntry.query.filter(
                DispatchEntry.id.in_(dsp_ids),
                DispatchEntry.product_id == product_id
            ).delete(synchronize_session=False)

        poi_del = 0
        if poi_ids:
            poi_del = PurchaseOrderItem.query.filter(
                PurchaseOrderItem.id.in_(poi_ids),
                PurchaseOrderItem.product_id == product_id
            ).delete(synchronize_session=False)

        # Log opcional
        db.session.add(Log(
            user_id=session.get('user_id'),
            action='delete_product_refs',
            target_table='products',
            target_id=p.id,
            details=json.dumps({
                "inv_deleted": inv_del,
                "dsp_deleted": dsp_del,
                "poi_deleted": poi_del
            })
        ))

        db.session.commit()
        return jsonify(message="Referencias eliminadas",
                       deleted={"inventory_entries": inv_del, "dispatch_entries": dsp_del, "purchase_order_items": poi_del}), 200
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify(error="Error interno al eliminar referencias"), 500


@app.route('/api/productos/<int:product_id>', methods=['DELETE'])
@admin_required
def api_delete_product(product_id):
    """
    Elimina un producto si NO tiene referencias en:
    - InventoryEntry
    - DispatchEntry
    - PurchaseOrderItem

    Si tiene referencias, responde 409 con los conteos.
    """
    product = Product.query.get(product_id)
    if not product:
        return jsonify(error="Producto no encontrado"), 404

    inv_count = db.session.query(func.count(InventoryEntry.id))\
        .filter(InventoryEntry.product_id == product_id).scalar() or 0
    dsp_count = db.session.query(func.count(DispatchEntry.id))\
        .filter(DispatchEntry.product_id == product_id).scalar() or 0
    poi_count = db.session.query(func.count(PurchaseOrderItem.id))\
        .filter(PurchaseOrderItem.product_id == product_id).scalar() or 0
    total = inv_count + dsp_count + poi_count

    if total > 0:
        return jsonify(
            error="El producto tiene referencias y no puede eliminarse. Fusiona antes.",
            usage={
                "inventory_entries": int(inv_count),
                "dispatch_entries": int(dsp_count),
                "purchase_order_items": int(poi_count),
                "total": int(total)
            }
        ), 409

    # OK para eliminar
    try:
        db.session.add(Log(
            user_id=session.get('user_id'),
            action='delete_product',
            target_table='products',
            target_id=product.id,
            details=json.dumps({
                "name": product.name,
                "brand": product.brand,
                "stock": product.stock
            })
        ))
        db.session.delete(product)
        db.session.commit()
        return jsonify(message="Producto eliminado", id=product_id), 200
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify(error="Error interno al eliminar"), 500


@app.route('/api/productos/merge', methods=['POST'])
@admin_required
def api_merge_products():
    """
    Fusiona varios productos en uno solo:
    - body JSON: {"target_id": 123, "sources": [456, 789], "new_name": "opcional", "new_brand": "opcional"}
    - Mueve referencias en InventoryEntry, DispatchEntry, PurchaseOrderItem al target.
    - Suma stock de sources al target.
    - Elimina products de sources.
    """
    data = get_json_body()
    try:
        target_id = int(data.get('target_id'))
    except (TypeError, ValueError):
        return jsonify(error="target_id inválido"), 400

    sources = data.get('sources') or []
    if not isinstance(sources, list) or not sources:
        return jsonify(error="Debes enviar lista 'sources' con IDs de productos a fusionar"), 400

    # normalizar y validar IDs
    try:
        sources = list({int(x) for x in sources})
    except (TypeError, ValueError):
        return jsonify(error="IDs en 'sources' inválidos"), 400

    if target_id in sources:
        return jsonify(error="target_id no puede estar también en 'sources'"), 400

    # cargar destino
    target = Product.query.get(target_id)
    if not target:
        return jsonify(error=f"Producto destino {target_id} no existe"), 404

    # opcionalmente renombrar destino
    new_name = clean_text(data.get('new_name'))
    new_brand = clean_text(data.get('new_brand'))
    if new_name:
        target.name = new_name
    if new_brand:
        target.brand = new_brand

    # cargar fuentes
    src_objs = Product.query.filter(Product.id.in_(sources)).all()
    faltantes = set(sources) - {p.id for p in src_objs}
    if faltantes:
        return jsonify(error=f"Productos fuente inexistentes: {sorted(faltantes)}"), 404

    try:
        # mover referencias en bloque
        for src in src_objs:
            # 1) Inventario (ingresos)
            InventoryEntry.query.filter_by(product_id=src.id).update(
                {"product_id": target.id}, synchronize_session=False)

            # 2) Despachos
            DispatchEntry.query.filter_by(product_id=src.id).update(
                {"product_id": target.id}, synchronize_session=False)

            # 3) Ítems de órdenes de compra
            PurchaseOrderItem.query.filter_by(product_id=src.id).update(
                {"product_id": target.id}, synchronize_session=False)

            # 4) Sumar stock
            target.stock = (target.stock or 0) + (src.stock or 0)

            # 5) Log de cada fusión individual
            db.session.add(Log(
                user_id=session['user_id'],
                action='merge_product',
                target_table='products',
                target_id=target.id,
                details=json.dumps({
                    'source_id': src.id,
                    'moved_entries': True,
                    'stock_added': src.stock
                })
            ))

            # 6) Eliminar el producto fuente
            db.session.delete(src)

        db.session.commit()
        return jsonify(
            message=f"Productos fusionados en {target.id}",
            target={
                'id': target.id,
                'name': target.name,
                'brand': target.brand,
                'stock': target.stock
            },
            merged_sources=sorted([p.id for p in src_objs])
        ), 200

    except SQLAlchemyError as e:
        db.session.rollback()
        return jsonify(error="Error interno durante la fusión"), 500


# --------------------------------------------------------------------------- #
# Exportaciones PDF (despachos y órdenes)
# --------------------------------------------------------------------------- #
@app.route('/despachos/<int:batch_id>/export/pdf')
@login_required
def export_despacho_pdf(batch_id):
    batch = DispatchBatch.query.options(
        joinedload(DispatchBatch.client),
        joinedload(DispatchBatch.user),
        joinedload(DispatchBatch.entries).joinedload(DispatchEntry.product),
        joinedload(DispatchBatch.photos)
    ).get_or_404(batch_id)
    pdf_bytes = _build_dispatch_pdf(batch)
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        download_name=f"despacho_{batch.id}.pdf",
        as_attachment=True
    )


@app.route('/ordenes/<int:order_id>/export/pdf')
@login_required
def export_orden_pdf(order_id):
    order = PurchaseOrder.query.options(
        joinedload(PurchaseOrder.client),
        joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.product)
    ).get_or_404(order_id)
    pdf_bytes = _build_order_pdf(order)
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        download_name=f"orden_{order.number}.pdf",
        as_attachment=True
    )


@app.route('/ingresos/historicos', methods=["GET"])
@login_required
def ingresos_historicos():
    if request.method == "GET":
        return render_view("ingresos_historicos.html")


@app.route('/ingresos/nuevo', methods=["GET", "POST"])
@login_required
def ingresos():
    if request.method == "GET":
        return render_view("ingresos.html")

    payload = get_json_body()
    items = payload.get('items') or [{
        'name':     payload.get('name', ''),
        'brand':    payload.get('brand', ''),
        'quantity': payload.get('quantity', 0)
    }]

    if not isinstance(items, list) or not items:
        return jsonify(
            error="Envía un array 'items' con al menos un producto"
        ), 400

    # 1) Creamos el batch de ingreso
    batch = IngresoBatch(user_id=session['user_id'])
    db.session.add(batch)
    db.session.flush()  # para obtener batch.id

    processed = []
    for idx, it in enumerate(items):
        raw_name = it.get('name', '')
        raw_brand = it.get('brand', '')
        qty = it.get('quantity')

        if not isinstance(qty, int):
            return jsonify(
                error=f"Ítem {idx}: 'quantity' debe ser entero"
            ), 400

        name = clean_text(raw_name)
        brand = clean_text(raw_brand)

        if not name or not brand or qty <= 0:
            return jsonify(error=f"Ítem {idx}: faltan campos o qty≤0"), 400

        # 2) Buscar o crear producto
        prod = Product.query.filter(
            Product.name.ilike(f"%{name}%"),
            Product.brand.ilike(f"%{brand}%")
        ).first()
        if prod:
            prod.stock += qty
        else:
            prod = Product(name=name, brand=brand, stock=qty)
            db.session.add(prod)
            db.session.flush()

        # 3) Registrar la entrada vinculada al batch
        entry = InventoryEntry(
            ingreso_id=batch.id,
            product_id=prod.id,
            quantity=qty
        )
        db.session.add(entry)

        processed.append({
            'entry_id': entry.id,
            'product':  {
                'id': prod.id,
                'name': prod.name,
                'brand': prod.brand
            },
            'quantity': qty
        })
        db.session.add(Log(
            user_id=session['user_id'],
            action='ingreso_producto',
            target_table='inventory_entries',
            target_id=entry.id,
            details=json.dumps({
                'product_id': entry.product_id,
                'quantity':   entry.quantity
            })
        ))
    db.session.commit()
    return jsonify(
        message=f"Ingreso #{batch.id} creado con {len(processed)} ítems",
        batch_id=batch.id,
        items=processed
    ), 201


@app.route('/ingresos/editar/<int:batch_id>', methods=['GET', 'POST'])
@login_required
def ingreso_batch_editar(batch_id):
    # 1) Carga el lote de ingreso y sus items
    batch = IngresoBatch.query.get_or_404(batch_id)
    items = [{
        'entry_id': e.id,
        'product':  e.product.name,
        'brand':    e.product.brand,
        'quantity': e.quantity
    } for e in batch.entries]

    if request.method == 'GET':
        return render_view('editar_ingreso.html', batch_id=batch.id, items=items)

    # 2) POST JSON: client no cambia en ingresos, solo los items
    data = get_json_body()
    items_new = data.get('items') or []
    if not isinstance(items_new, list) or not items_new:
        return jsonify(error="Envíe lista de items"), 400

    existing = {e.id: e for e in batch.entries}
    seen = set()

    try:
        for idx, it in enumerate(items_new):
            eid = it.get('entry_id')  # puede ser None
            qty = it.get('quantity')
            rawn = it.get('product', '').strip()
            rawb = it.get('brand', '').strip()

            # validaciones
            if not rawn or not rawb or not isinstance(qty, int) or qty < 0:
                return jsonify(error=f"Línea {idx+1} inválida"), 400

            name = clean_text(rawn)
            brand = clean_text(rawb)
            # buscar producto existente
            prod = Product.query.filter(
                Product.name.ilike(f"%{name}%"),
                Product.brand.ilike(f"%{brand}%")
            ).first()
            if not prod:
                prod = Product(name=name, brand=brand, stock=0)
                db.session.add(prod)
                db.session.flush()

            if eid and eid in existing:
                # ajustar stock según delta
                entry = existing[eid]
                old_q = entry.quantity
                delta = qty - old_q
                new_st = prod.stock + delta
                if new_st < 0:
                    return jsonify(
                        error=f"Línea {idx+1}: quedaría stock negativo"
                    ), 400
                entry.quantity = qty
                prod.stock = new_st
                seen.add(eid)
            else:
                # nueva línea: sumar al stock
                prod.stock += qty
                entry = InventoryEntry(
                    ingreso_id=batch.id,
                    product_id=prod.id,
                    quantity=qty
                )
                db.session.add(entry)
                db.session.flush()
                seen.add(entry.id)

        # eliminar las que quitaron
        for oid, old in existing.items():
            if oid not in seen:
                prod = old.product
                prod.stock -= old.quantity
                db.session.delete(old)

        db.session.commit()
        return jsonify(message="Ingreso actualizado"), 200

    except SQLAlchemyError:
        db.session.rollback()
        return jsonify(error="Error interno"), 500


# --------------------------------------------------------------------------- #
# Despachos (salidas de inventario)
# --------------------------------------------------------------------------- #
@app.route('/despachos/nuevo', methods=['GET', 'POST'])
@login_required
def nuevos_despachos():
    if request.method == 'GET':
        return render_view('despachos.html')

    # POST JSON
    data = get_json_body()
    client_name = clean_text(data.get('client'))
    items = data.get('items') or []
    order_number = clean_text(data.get('order_number'))

    # 1) Validar cliente
    if not client_name:
        return jsonify(error="Debes indicar el nombre del cliente"), 400

    client = Client.query.filter(Client.name.ilike(client_name)).first()
    if not client:
        client = Client(name=client_name)
        db.session.add(client)
        db.session.flush()

    # 2) Validar ítems
    if not isinstance(items, list) or not items:
        return jsonify(error="Envía array 'items' con al menos un producto"), 400

    warnings = []
    processed = []

    # 3) Crear el lote de despacho
    batch = DispatchBatch(
        client_id=client.id,
        user_id=session['user_id'],
        order_number=order_number
    )
    db.session.add(batch)
    db.session.flush()   # para obtener batch.id

    # 4) Si viene número de orden, sanitizarlo
    order_number = order_number or None

    for idx, it in enumerate(items):
        raw_name = it.get('name', '')
        raw_brand = it.get('brand', '')
        qty = it.get('quantity')

        # validaciones base
        if not isinstance(qty, int) or qty <= 0:
            return jsonify(error=f"Item {idx}: 'quantity' debe ser entero >0"), 400

        name = clean_text(raw_name)
        brand = clean_text(raw_brand)
        if not name or not brand:
            return jsonify(error=f"Item {idx}: faltan 'name' o 'brand'"), 400

        # buscar producto
        prod = Product.query.filter(
            Product.name.ilike(f"%{name}%"),
            Product.brand.ilike(f"%{brand}%")
        ).first()
        if not prod:
            return jsonify(error=f"Item {idx}: producto no existe en inventario"), 400

        # ajustar stock
        dispatched = min(prod.stock, qty)
        if dispatched < qty:
            warnings.append(
                f"Item {idx}: sólo había {prod.stock} unidades de {name}; "
                f"se despacharán {dispatched}"
            )
        prod.stock = max(prod.stock - qty, 0)

        # registrar entry con posible order_number
        entry = DispatchEntry(
            batch_id=batch.id,
            product_id=prod.id,
            quantity=dispatched,
            order_number=order_number
        )
        db.session.add(entry)
        db.session.flush()  # para obtener entry.id

        processed.append({
            'product':   {'id': prod.id, 'name': prod.name, 'brand': prod.brand},
            'requested': qty,
            'dispatched': dispatched
        })

        # log de cada línea
        db.session.add(Log(
            user_id=session['user_id'],
            action='dispatch_product',
            target_table='dispatch_entries',
            target_id=entry.id,
            details=json.dumps({
                'product_id': entry.product_id,
                'requested':  qty,
                'dispatched': dispatched,
                'order_number': order_number
            })
        ))

    # log del batch
    db.session.add(Log(
        user_id=session['user_id'],
        action='create_dispatch_batch',
        target_table='dispatch_batches',
        target_id=batch.id,
        details=json.dumps({
            'client_id':    batch.client_id,
            'order_number': order_number
        })
    ))

    db.session.commit()

    return jsonify(
        message=f"Despacho #{batch.id} registrado",
        batch_id=batch.id,
        processed=processed,
        warnings=warnings
    ), 201


@app.route('/despachos/historico')
@login_required
def historico_despachos():
    # Vista HTML
    return render_view('despachos_historicos.html')


@app.route('/despachos/editar/<int:batch_id>', methods=['GET', 'POST'])
@login_required
def despacho_editar(batch_id):
    batch = DispatchBatch.query.get_or_404(batch_id)

    if request.method == 'GET':
        # Serializamos para rellenar el formulario
        items = [{
            'entry_id': e.id,
            'product_id': e.product_id,
            'name': e.product.name,
            'brand': e.product.brand,
            'quantity': e.quantity
        } for e in batch.entries]
        return render_view('editar_despacho.html',
                           batch_id=batch.id,
                           client_name=batch.client.name,
                           order_number=batch.order_number,
                           items=items)

    # POST JSON
    data = get_json_body()
    cli_raw = clean_text(data.get('client'))
    ord_raw = clean_text(data.get('order_number'))
    items_data = data.get('items') or []

    batch.order_number = ord_raw or None
    # Validaciones básicas
    if not cli_raw or not isinstance(items_data, list) or not items_data:
        return jsonify(error="Datos incompletos"), 400

    # Sanitizar y buscar/crear cliente (igual que antes)...
    client = Client.query.filter(Client.name.ilike(cli_raw)).first()
    if not client:
        client = Client(name=cli_raw)
        db.session.add(client)
        db.session.flush()
    batch.client_id = client.id
    batch.order_number = ord_raw or None

    # Mapear entradas existentes
    existing = {e.id: e for e in batch.entries}
    seen_ids = set()

    try:
        for idx, it in enumerate(items_data):
            eid = it.get('entry_id')    # puede venir None
            raw_n = (it.get('name') or '').strip()
            raw_b = (it.get('brand') or '').strip()
            new_qty = it.get('quantity')

            # Validar datos
            if not raw_n or not raw_b or not isinstance(new_qty, int) or new_qty < 0:
                return jsonify(error=f"Línea {idx+1} inválida"), 400

            # Sanitizar
            name = clean_text(raw_n)
            brand = clean_text(raw_b)

            # Buscar producto
            prod = Product.query.filter(
                Product.name.ilike(f"%{name}%"),
                Product.brand.ilike(f"%{brand}%")
            ).first()
            if not prod:
                return jsonify(error=f"Línea {idx+1}: producto '{name} / {brand}' no existe"), 400

            if eid and eid in existing:
                # Ajuste de cantidad: calculamos delta y stock nuevo
                entry = existing[eid]
                old_qty = entry.quantity
                delta = old_qty - new_qty
                new_stock = prod.stock + delta

                if new_stock < 0:
                    return jsonify(error=f"Línea {idx+1}: no hay suficiente stock para reducir despacho"), 400

                # Aplicar cambios
                entry.quantity = new_qty
                prod.stock = new_stock
                seen_ids.add(eid)

            else:
                # Es una línea nueva: decrementar stock en consecuencia
                if prod.stock < new_qty:
                    return jsonify(error=f"Línea {idx+1}: no hay suficiente stock para despachar {new_qty}"), 400
                prod.stock -= new_qty

                # Crear nueva entrada
                entry = DispatchEntry(
                    batch_id=batch.id,
                    product_id=prod.id,
                    quantity=new_qty,
                    order_number=batch.order_number
                )
                db.session.add(entry)
                db.session.flush()
                seen_ids.add(entry.id)

        # Eliminar entradas que el usuario borró
        for old_id, old_entry in existing.items():
            if old_id not in seen_ids:
                # Al eliminar, devolvemos su qty al stock original
                prod = old_entry.product
                prod.stock += old_entry.quantity
                db.session.delete(old_entry)

        db.session.commit()
        return jsonify(message="Despacho actualizado correctamente"), 200

    except SQLAlchemyError:
        db.session.rollback()
        return jsonify(error="Error interno al actualizar despacho"), 500


@app.route('/api/despachos/historico')
@login_required
def api_despachos_historico():
    # Leer filtros
    op = request.args.get('operator', '').strip()
    cl = request.args.get('client', '').strip()
    start = request.args.get('start')
    end = request.args.get('end')

    # Construir la consulta base
    query = DispatchBatch.query \
        .join(DispatchBatch.user) \
        .join(DispatchBatch.client)

    # Filtrar por operador (Users.username)
    if op:
        query = query.filter(
            DispatchBatch.user.has(Users.username.ilike(f"%{op}%"))
        )

    # Filtrar por cliente (Client.name)
    if cl:
        query = query.filter(
            DispatchBatch.client.has(Client.name.ilike(f"%{cl}%"))
        )

    # Filtrar por rango de fechas
    if start:
        query = query.filter(DispatchBatch.created_at >= f"{start} 00:00:00")
    if end:
        query = query.filter(DispatchBatch.created_at <= f"{end} 23:59:59")

    # Ejecutar y serializar
    batches = query.order_by(DispatchBatch.created_at.desc()).all()
    result = []
    for b in batches:
        result.append({
            'batch_id':   b.id,
            'client':     b.client.name,
            'user':       b.user.name,
            'created_at': b.created_at.strftime('%d/%m/%Y %H:%M:%S'),
            'items': [
                {
                    'product': i.product.name,
                    'brand': i.product.brand,
                    'quantity': i.quantity
                }
                for i in b.entries
            ]
        })
    return jsonify(result)


@app.route('/api/despachos/<int:batch_id>/fotos', methods=['GET', 'POST'])
@login_required
def api_despacho_fotos(batch_id):
    """Gestión de fotos de despacho (salida/entrega)."""
    batch = DispatchBatch.query.get_or_404(batch_id)

    if request.method == 'GET':
        photos = (DispatchPhoto.query
                  .filter_by(batch_id=batch.id)
                  .order_by(DispatchPhoto.created_at.desc())
                  .all())
        out = []
        for p in photos:
            out.append({
                'id': p.id,
                'stage': p.stage,
                'url': url_for('static', filename=p.path),
                'created_at': _to_iso(p.created_at)
            })
        return jsonify(out)

    # POST: carga de foto
    stage = (request.form.get('stage') or '').lower()
    file = request.files.get('photo')
    if stage not in ('salida', 'entrega'):
        return jsonify(error="stage debe ser 'salida' o 'entrega'"), 400
    if not file or not file.filename:
        return jsonify(error="Archivo de imagen requerido"), 400
    if not _allowed_file(file.filename):
        return jsonify(error="Formato no permitido"), 400

    ext = file.filename.rsplit('.', 1)[1].lower()
    fname = secure_filename(f"dispatch_{batch.id}_{stage}_{int(time.time())}.{ext}")
    rel_path = f"uploads/{fname}"
    save_path = os.path.join(UPLOAD_DIR, fname)
    os.makedirs(UPLOAD_DIR, exist_ok=True)  # por si la carpeta fue eliminada

    try:
        file.save(save_path)
        photo = DispatchPhoto(batch_id=batch.id, stage=stage, path=rel_path)
        db.session.add(photo)
        db.session.add(Log(
            user_id=session.get('user_id'),
            action='upload_dispatch_photo',
            target_table='dispatch_batches',
            target_id=batch.id,
            details=json.dumps({'photo_id': None, 'stage': stage})
        ))
        db.session.commit()
        return jsonify(
            message="Foto guardada",
            photo={'stage': stage, 'url': url_for('static', filename=rel_path)}
        ), 201
    except SQLAlchemyError:
        db.session.rollback()
        if os.path.exists(save_path):
            try:
                os.remove(save_path)
            except OSError:
                pass
        return jsonify(error="No se pudo guardar la foto"), 500


def _build_dispatch_pdf(batch: DispatchBatch):
    pdf = FPDF()
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()
    _pdf_header(pdf, f"Despacho #{batch.id}")
    _pdf_add_keyvals(pdf, [
        ("Cliente", getattr(batch.client, 'name', '')),
        ("Operador", getattr(batch.user, 'name', '')),
        ("Fecha", batch.created_at.strftime('%d/%m/%Y %H:%M') if batch.created_at else ''),
        ("Orden #", getattr(batch, 'order_number', '') or '—'),
    ])
    items = []
    for e in batch.entries:
        items.append([
            getattr(e.product, 'name', ''),
            getattr(e.product, 'brand', ''),
            e.quantity
        ])
    _pdf_add_table(pdf, [("Producto", 70), ("Marca", 50), ("Cantidad", 30)], items)
    photos = DispatchPhoto.query.filter_by(batch_id=batch.id).order_by(DispatchPhoto.created_at).all()
    _pdf_add_photos(pdf, photos)
    data = pdf.output(dest='S')
    if isinstance(data, bytearray):
        return bytes(data)
    return data


def _build_order_pdf(order: PurchaseOrder):
    pdf = FPDF()
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()
    _pdf_header(pdf, f"Orden de compra #{order.number}")
    _pdf_add_keyvals(pdf, [
        ("Cliente", getattr(order.client, 'name', '')),
        ("Fecha creación", order.created_at.strftime('%d/%m/%Y %H:%M') if order.created_at else '')
    ])
    items = []
    for it in order.items:
        items.append([it.product.name, it.product.brand, it.quantity])
    _pdf_add_table(pdf, [("Producto", 70), ("Marca", 50), ("Cantidad", 30)], items)

    # Despachos asociados a la orden
    batches = DispatchBatch.query.options(
        joinedload(DispatchBatch.entries).joinedload(DispatchEntry.product),
        joinedload(DispatchBatch.photos)
    ).filter_by(order_number=order.number).all()

    if not batches:
        pdf.cell(0, 8, "Sin despachos asociados.", ln=1)
    else:
        for b in batches:
            pdf.set_font("Helvetica", "B", 13)
            pdf.cell(0, 10, f"Despacho #{b.id}", ln=1)
            pdf.set_font("Helvetica", "", 11)
            _pdf_add_keyvals(pdf, [
                ("Cliente", getattr(b.client, 'name', '')),
                ("Operador", getattr(b.user, 'name', '')),
                ("Fecha", b.created_at.strftime('%d/%m/%Y %H:%M') if b.created_at else '')
            ])
            b_items = []
            for e in b.entries:
                b_items.append([getattr(e.product, 'name', ''), getattr(e.product, 'brand', ''), e.quantity])
            _pdf_add_table(pdf, [("Producto", 70), ("Marca", 50), ("Cantidad", 30)], b_items)
            _pdf_add_photos(pdf, b.photos)

    data = pdf.output(dest='S')
    if isinstance(data, bytearray):
        return bytes(data)
    return data


@app.route('/api/productos/suggest')
@login_required
def suggest_products():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])

    matches = Product.query.filter(
        Product.name.ilike(f"%{q}%")
    ).limit(10).all()

    return jsonify([
        {
            'id':    p.id,
            'name':  p.name,
            'brand': p.brand,
            'stock': p.stock
        }
        for p in matches
    ])


@app.route('/api/inventario')
@login_required
def api_inventario():
    prods = Product.query.order_by(Product.id).all()
    return jsonify([
        {'id': p.id, 'name': p.name, 'brand': p.brand, 'stock': p.stock}
        for p in prods
    ])


@app.route('/api/productos/<int:id>', methods=['PUT'])
@admin_required
def api_update_product(id):
    data = get_json_body()
    # Extraer y validar
    name = clean_text(data.get('name'))
    brand = clean_text(data.get('brand'))
    stock = data.get('stock', None)
    if not name or not brand or not isinstance(stock, int) or stock < 0:
        return jsonify(
            error="name, brand y stock(int≥0) son obligatorios"
        ), 400

    prod = Product.query.get_or_404(id)
    prod.name = name
    prod.brand = brand
    prod.stock = stock
    db.session.add(Log(
        user_id=session['user_id'],
        action='update_product_stock',
        target_table='products',
        target_id=prod.id,
        details=json.dumps({'new_stock': prod.stock})
    ))
    db.session.commit()
    return jsonify(
        message="Producto actualizado",
        product={
            'id': prod.id,
            'name': prod.name,
            'brand': prod.brand,
        'stock': prod.stock
        }
    ), 200


# --------------------------------------------------------------------------- #
# Administración de usuarios
# --------------------------------------------------------------------------- #
@app.route('/usuarios')
@admin_required
def usuarios():
    users = Users.query.order_by(Users.username).all()
    return render_view("usuarios.html", users=users)


@app.route('/usuarios/editar/<id>', methods=["GET", "POST"])
@admin_required
def usuarios_edit(id):
    user = Users.query.get(id)
    if not user:
        abort(404)
    if request.method == "GET":
        return render_view("editar.html", user_id=id, user=user)
    if request.method == "POST":
        data = get_json_body()
        if not data:
            return jsonify({"error": "JSON inválido"}), 400
        try:
            user.name = clean_text(data.get("name"))
            user.email = clean_text(data.get("email"))
            user.is_Admin = bool(data["is_Admin"])
            db.session.add(Log(
                user_id=session['user_id'],
                action='edit_user',
                target_table='users',
                target_id=user.id,
                details=json.dumps({
                    'name':     user.name,
                    'email':    user.email,
                    'is_admin': user.is_Admin
                })
            ))
            db.session.commit()
            return jsonify({"message": "Usuario actualizado"}), 200
        except SQLAlchemyError:
            db.session.rollback()
            return jsonify({"message": "Ocurrio un error"}), 400


@app.route('/usuarios/editar/password/<id>', methods=["POST"])
@admin_required
def usuarios_edit_password(id):
    user = Users.query.get_or_404(id)

    # Ya no hacemos request.get_json() aquí
    default_pwd = "changeme123"
    try:
        user.password = default_pwd
        db.session.add(Log(
            user_id=session['user_id'],
            action='reset_password',
            target_table='users',
            target_id=user.id,
            details=json.dumps({'new_password': default_pwd})
        ))
        db.session.commit()
        return jsonify({
            "message": f"Contraseña de usuario {user.username} restablecida a '{default_pwd}'"
        }), 200

    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "Error al restablecer la contraseña"}), 500


@app.route('/usuarios/eliminar/<id>', methods=["POST"])
@admin_required
def usuarios_delete(id):
    user = Users.query.get(id)
    if not user:
        abort(404)
    try:
        # Marca para borrado y confirma
        db.session.delete(user)
        db.session.add(Log(
            user_id=session['user_id'],
            action='delete_user',
            target_table='users',
            target_id=user.id,
            details=json.dumps({'username': user.username})
        ))
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        # Opcional: loggea e, p.e. logger.error(f"Error borrando usuario: {e}")
        return jsonify({"error": "No se pudo eliminar el usuario"}), 500

    return jsonify({"message": "Usuario eliminado correctamente"}), 200


@app.route('/usuarios/crear', methods=["POST"])
@admin_required
def usuarios_crear():
    try:
        data = get_json_body()
        required = ("username", "password", "email", "name")
        if not all(data.get(field) for field in required):
            return jsonify({"error": "Faltan campos obligatorios"}), 400
        new_user = Users(
            username=clean_text(data.get("username")),
            password=data.get("password"),
            email=clean_text(data.get("email")),
            name=clean_text(data.get("name")),
            is_Admin=bool(data["is_Admin"])
        )
        db.session.add(new_user)
        db.session.add(Log(
            user_id=session['user_id'],
            action='create_user',
            target_table='users',
            target_id=new_user.id,
            details=json.dumps({
                'username': new_user.username,
                'email':    new_user.email
            })
        ))
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        # Opcional: loggea e, p.e. logger.error(f"Error borrando usuario: {e}")
        return jsonify({"error": "No se puede crear el usuario"}), 500

    return jsonify({"message": "Usuario Creado Correctamente"}), 200


# --------------------------------------------------------------------------- #
# Órdenes de compra
# --------------------------------------------------------------------------- #
@app.route('/ordenes', methods=['GET'])
@login_required
def ordenes_listado():
    raw = (
        PurchaseOrder.query
        .order_by(PurchaseOrder.created_at.desc())
        .all()
    )
    orders = []
    for o in raw:
        total = sum(item.quantity for item in o.items)
        dispatched = (
            db.session.query(func.coalesce(
                func.sum(DispatchEntry.quantity), 0))
            .filter(DispatchEntry.order_number == o.number)
            .scalar()
        )
        if dispatched >= total:
            estado, badge = 'Completada', 'success'
        elif dispatched > 0:
            estado, badge = 'Parcial', 'warning text-dark'
        else:
            estado, badge = 'Pendiente', 'secondary'

        orders.append({
            'id':          o.id,
            'number':      o.number,
            'client':      o.client.name,
            'created_at':  o.created_at.strftime('%d/%m/%Y %H:%M'),
            'estado':      estado,
            'badge':       badge
        })

    return render_view('ordenes.html', orders=orders)


@app.route('/ordenes/nuevo', methods=['POST', 'GET'])
@login_required
def ordenes_nuevo():
    if request.method == 'GET':
        return render_view('nueva_orden.html')

    data = get_json_body()
    number = clean_text(data.get('number'))
    client_name = clean_text(data.get('client'))
    items = data.get('items') or []

    # Validaciones básicas
    if not number:
        return jsonify(error="Falta el número de orden"), 400
    if not client_name:
        return jsonify(error="Falta el nombre del cliente"), 400
    if not isinstance(items, list) or not items:
        return jsonify(error="Debes enviar un array 'items' con al menos un producto"), 400

    # Sanitizar inputs
    number_clean = number
    client_clean = client_name

    # Verificar unicidad del número
    if PurchaseOrder.query.filter_by(number=number_clean).first():
        return jsonify(error="Ya existe una orden con ese número"), 400

    # Buscar o crear cliente
    client = Client.query.filter(Client.name.ilike(client_clean)).first()
    if not client:
        client = Client(name=client_clean)
        db.session.add(client)
        db.session.flush()

    # Crear la orden de compra
    po = PurchaseOrder(number=number_clean, client_id=client.id)
    db.session.add(po)
    db.session.flush()  # para obtener po.id

    processed = []
    try:
        for idx, it in enumerate(items):
            raw_name = it.get('name', '')
            raw_brand = it.get('brand', '')
            qty = it.get('quantity')

            # Validar cantidad
            if not isinstance(qty, int) or qty <= 0:
                db.session.rollback()
                return jsonify(error=f"Línea {idx+1}: 'quantity' debe ser entero > 0"), 400

            # Sanitizar campos
            name = clean_text(raw_name)
            brand = clean_text(raw_brand)
            if not name or not brand:
                db.session.rollback()
                return jsonify(error=f"Línea {idx+1}: faltan 'name' o 'brand'"), 400

            # Buscar producto; si no existe, crearlo con stock=0
            prod = Product.query.filter(
                Product.name.ilike(f"%{name}%"),
                Product.brand.ilike(f"%{brand}%")
            ).first()
            if not prod:
                prod = Product(name=name, brand=brand, stock=0)
                db.session.add(prod)
                db.session.flush()

            # Crear ítem de la orden
            poi = PurchaseOrderItem(
                order_id=po.id,
                product_id=prod.id,
                quantity=qty
            )
            db.session.add(poi)
            db.session.flush()

            processed.append({
                'item_id':    poi.id,
                'product_id': prod.id,
                'name':       prod.name,
                'brand':      prod.brand,
                'quantity':   qty
            })

            # Log de cada línea
            db.session.add(Log(
                user_id=session['user_id'],
                action='add_po_item',
                target_table='purchase_order_items',
                target_id=poi.id,
                details=json.dumps({
                    'order_id':   po.id,
                    'product_id': prod.id,
                    'quantity':   qty
                })
            ))

        # Log de creación de la orden
        db.session.add(Log(
            user_id=session['user_id'],
            action='create_purchase_order',
            target_table='purchase_orders',
            target_id=po.id,
            details=json.dumps({
                'number':    po.number,
                'client_id': po.client_id
            })
        ))

        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify(error="Error al guardar la orden"), 500

    return jsonify(
        message=f"Orden '{po.number}' creada con {len(processed)} ítems",
        order_id=po.id,
        items=processed
    ), 201


@app.route('/ordenes/<int:order_id>')
@login_required
def orden_detalle(order_id):
    po = PurchaseOrder.query.options(
        joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.product)
    ).get_or_404(order_id)

    # Detalle por ítem: solicitado / despachado / pendiente
    detail = []
    for it in po.items:
        # suma de despachos del mismo producto para esta OC
        # Ajusta nombres de columnas si difieren: DispatchEntry.product_id, DispatchEntry.batch_id,
        # DispatchBatch.id, DispatchBatch.order_number
        desp = db.session.query(func.coalesce(func.sum(DispatchEntry.quantity), 0))\
            .join(DispatchBatch, DispatchEntry.batch_id == DispatchBatch.id)\
            .filter(
                DispatchEntry.product_id == it.product_id,
                DispatchBatch.order_number == po.number
        ).scalar() or 0

        detail.append({
            'product':    it.product.name,
            'solicitado': it.quantity,
            'despachado': desp,
            'pendiente':  it.quantity - desp
        })

    # Batches/Despachos de esta OC (FUERA del loop, siempre definido)
    batches = DispatchBatch.query.options(
        joinedload(DispatchBatch.user),
        joinedload(DispatchBatch.entries).joinedload(DispatchEntry.product)
    ).filter_by(order_number=po.number)\
     .order_by(DispatchBatch.id.desc())\
     .all()

    # Historial de despachos
    dispatch_history = []
    for b in batches:
        dispatch_history.append({
            'batch_id': b.id,
            'user':     getattr(b.user, 'name', None),
            'created':  getattr(b, 'created_at', None),
            'items': [{
                'product': e.product.name if e.product else None,
                'brand':   e.product.brand if e.product else None,
                'qty':     e.quantity
            } for e in b.entries]
        })

    return render_view(
        'detalle_orden.html',
        order=po,
        detail=detail,
        dispatch_history=dispatch_history
    )


@app.route('/api/ordenes/<int:order_id>', methods=['DELETE'])
@login_required
def eliminar_orden(order_id):
    """
    Elimina una orden de compra y sus ítems.
    Antes, quita la relación con despachos (no borra los despachos).
    """
    po = PurchaseOrder.query.get(order_id)
    if not po:
        return jsonify({'error': 'Orden no encontrada'}), 404

    try:
        # 1) Desasociar despachos que apunten a esta OC
        #    Soporta tanto order_id como order_number según tu esquema.
        #    (Actualiza solo si el atributo existe)
        # --- DispatchBatch (cabeceras de despacho)
        if 'order_id' in DispatchBatch.__table__.columns:
            (db.session.query(DispatchBatch)
             .filter(DispatchBatch.order_id == po.id)
             .update({DispatchBatch.order_id: None}, synchronize_session=False))
        if 'order_number' in DispatchBatch.__table__.columns:
            (db.session.query(DispatchBatch)
             .filter(DispatchBatch.order_number == po.number)
             .update({DispatchBatch.order_number: None}, synchronize_session=False))

        # --- DispatchEntry (líneas de despacho), por si también guardas el vínculo aquí
        if 'order_id' in DispatchEntry.__table__.columns:
            (db.session.query(DispatchEntry)
             .filter(DispatchEntry.order_id == po.id)
             .update({DispatchEntry.order_id: None}, synchronize_session=False))
        if 'order_number' in DispatchEntry.__table__.columns:
            (db.session.query(DispatchEntry)
             .filter(DispatchEntry.order_number == po.number)
             .update({DispatchEntry.order_number: None}, synchronize_session=False))

        # 2) Borrar los ítems de la OC (evita violar NOT NULL en purchase_order_items.order_id)
        db.session.query(PurchaseOrderItem)\
                  .filter(PurchaseOrderItem.order_id == po.id)\
                  .delete(synchronize_session=False)

        # 3) Borrar la OC
        db.session.delete(po)
        db.session.commit()

        return jsonify({'message': 'Orden eliminada y despachos desasociados.'}), 200

    except IntegrityError as e:
        db.session.rollback()
        # Mensaje útil para debug
        return jsonify({'error': 'No se pudo eliminar por restricciones de integridad.',
                        'detail': str(e.orig)}), 409
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Error interno al eliminar la orden.'}), 500


@app.route('/api/ordenes/<order_number>/detalle')
@login_required
def api_orden_detalle(order_number):
    # Busca la orden por su número (text)
    po = PurchaseOrder.query.filter_by(number=order_number).first_or_404()

    result = []
    for it in po.items:
        desp = (
            db.session.query(func.coalesce(
                func.sum(DispatchEntry.quantity), 0))
            .filter(
                DispatchEntry.order_number == po.number,
                DispatchEntry.product_id == it.product_id
            )
            .scalar()
        )
        result.append({
            'product_id': it.product_id,
            'product':     it.product.name,
            'brand':       it.product.brand,
            'solicitado':  it.quantity,
            'despachado':  desp,
            'pendiente':   max(it.quantity - desp, 0)
        })
    return jsonify({
        'order_number': po.number,
        'client':       po.client.name,
        'items':        result
    })


@app.route('/ordenes/editar/<int:order_id>', methods=['GET', 'POST'])
@login_required
def ordenes_editar(order_id):
    po = PurchaseOrder.query.get_or_404(order_id)

    if request.method == 'GET':
        # serializar la orden en JSON para el JS
        items = [{
            'item_id':    it.id,
            'product':    it.product.name,
            'brand':      it.product.brand,
            'quantity':   it.quantity
        } for it in po.items]

        return render_view(
            'editar_orden.html',
            order_number=po.number,
            client_name=po.client.name,
            items=items,
            order_id=po.id
        )

    # POST: procesar JSON de edición
    data = get_json_body()
    number_new = clean_text(data.get('number'))
    client_new = clean_text(data.get('client'))
    items_data = data.get('items') or []

    if not number_new or not client_new or not isinstance(items_data, list):
        return jsonify(error="Datos incompletos"), 400

    # 1) Actualizar número
    if number_new != po.number:
        if PurchaseOrder.query.filter_by(number=number_new).first():
            return jsonify(error="Ya existe otra orden con ese número"), 400
        po.number = number_new

    # 2) Actualizar cliente
    cli = Client.query.filter(Client.name.ilike(client_new)).first()
    if not cli:
        cli = Client(name=client_new)
        db.session.add(cli)
        db.session.flush()
    po.client_id = cli.id

    # 3) Procesar ítems: mapear existente
    existing = {it.id: it for it in po.items}
    incoming_ids = set()

    for idx, it in enumerate(items_data):
        pid = it.get('item_id')   # puede venir None
        raw_n = it.get('product', '')
        raw_b = it.get('brand', '')
        qty = it.get('quantity')

        # validaciones
        if not raw_n or not raw_b or not isinstance(qty, int) or qty <= 0:
            return jsonify(error=f"Línea {idx+1} inválida"), 400

        name = clean_text(raw_n)
        brand = clean_text(raw_b)

        # buscar o crear producto
        prod = Product.query.filter(
            Product.name.ilike(f"%{name}%"),
            Product.brand.ilike(f"%{brand}%")
        ).first()
        if not prod:
            prod = Product(name=name, brand=brand, stock=0)
            db.session.add(prod)
            db.session.flush()

        if pid and pid in existing:
            # actualizar cantidad
            existing[pid].quantity = qty
            incoming_ids.add(pid)
        else:
            # nuevo ítem en la orden
            poi = PurchaseOrderItem(
                order_id=po.id,
                product_id=prod.id,
                quantity=qty
            )
            db.session.add(poi)
            db.session.flush()
            incoming_ids.add(poi.id)

    # 4) Eliminar los items que quedaron fuera
    for eid, itobj in existing.items():
        if eid not in incoming_ids:
            db.session.delete(itobj)

    # 5) Log de edición
    db.session.add(Log(
        user_id=session['user_id'],
        action='edit_purchase_order',
        target_table='purchase_orders',
        target_id=po.id,
        details=json.dumps({
            'number': po.number,
            'client_id': po.client_id,
            'items': list(incoming_ids)
        })
    ))

    db.session.commit()
    return jsonify(message="Orden actualizada"), 200


@app.errorhandler(404)
def not_found(error):
    return render_view('404.html'), 404


@app.errorhandler(500)
def server_error(error):
    return render_view('500.html'), 500


if __name__ == '__main__':
    with app.app_context():
        # 1) Crear todas las tablas
        db.create_all()
    app.run(debug=True)
