from datetime import timedelta
from flask import jsonify, request, session
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload

from core.helpers import (
    admin_required,
    clean_text,
    get_json_body,
    login_required,
    parse_dmy,
    render_view,
    to_iso,
)
from database.db import (
    DispatchBatch,
    DispatchEntry,
    InventoryEntry,
    IngresoBatch,
    Log,
    Product,
    PurchaseOrder,
    PurchaseOrderItem,
    db,
)


def register_inventory(app):
    @app.route('/api/ingresos/historico', methods=['GET'])
    @login_required
    def api_ingresos_historico():
        start_s = request.args.get('start', '')
        end_s = request.args.get('end', '')

        q = IngresoBatch.query.options(
            joinedload(IngresoBatch.user),
            joinedload(IngresoBatch.entries).joinedload(InventoryEntry.product)
        ).order_by(IngresoBatch.id.desc())

        start_dt = parse_dmy(start_s)
        end_dt = parse_dmy(end_s)
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
                'product': {
                    'name': getattr(e.product, 'name', None),
                    'brand': getattr(e.product, 'brand', None)
                },
                'quantity': e.quantity
            } for e in (b.entries or [])]

            result.append({
                'batch_id': b.id,
                'user': {'username': user_name},
                'created_at': (b.created_at.isoformat() if getattr(b, 'created_at', None) else None),
                'items': items
            })

        return jsonify(result), 200

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
            'name': payload.get('name', ''),
            'brand': payload.get('brand', ''),
            'quantity': payload.get('quantity', 0)
        }]

        if not isinstance(items, list) or not items:
            return jsonify(error="Envía un array 'items' con al menos un producto"), 400

        batch = IngresoBatch(user_id=session['user_id'])
        db.session.add(batch)
        db.session.flush()

        processed = []
        for idx, it in enumerate(items):
            raw_name = it.get('name', '')
            raw_brand = it.get('brand', '')
            qty = it.get('quantity')

            if not isinstance(qty, int):
                return jsonify(error=f"Ítem {idx}: 'quantity' debe ser entero"), 400

            name = clean_text(raw_name)
            brand = clean_text(raw_brand)

            if not name or not brand or qty <= 0:
                return jsonify(error=f"Ítem {idx}: faltan campos o qty≤0"), 400

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

            entry = InventoryEntry(
                ingreso_id=batch.id,
                product_id=prod.id,
                quantity=qty
            )
            db.session.add(entry)

            processed.append({
                'entry_id': entry.id,
                'product': {
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
                details=f"Registró ingreso de {qty} unidad(es) del producto {prod.name} / {prod.brand} (id {entry.product_id}) en lote {batch.id}"
            ))
        db.session.add(Log(
            user_id=session['user_id'],
            action='create_ingreso_batch',
            target_table='ingreso_batches',
            target_id=batch.id,
            details=f"Creó ingreso #{batch.id} con {len(processed)} ítems"
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
        batch = IngresoBatch.query.get_or_404(batch_id)
        items = [{
            'entry_id': e.id,
            'product': e.product.name,
            'brand': e.product.brand,
            'quantity': e.quantity
        } for e in batch.entries]

        if request.method == 'GET':
            return render_view('editar_ingreso.html', batch_id=batch.id, items=items)

        data = get_json_body()
        items_new = data.get('items') or []
        if not isinstance(items_new, list) or not items_new:
            return jsonify(error="Envíe lista de items"), 400

        existing = {e.id: e for e in batch.entries}
        seen = set()

        try:
            for idx, it in enumerate(items_new):
                eid = it.get('entry_id')
                qty = it.get('quantity')
                rawn = it.get('product', '').strip()
                rawb = it.get('brand', '').strip()

                if not rawn or not rawb or not isinstance(qty, int) or qty < 0:
                    return jsonify(error=f"Línea {idx+1} inválida"), 400

                name = clean_text(rawn)
                brand = clean_text(rawb)
                prod = Product.query.filter(
                    Product.name.ilike(f"%{name}%"),
                    Product.brand.ilike(f"%{brand}%")
                ).first()
                if not prod:
                    prod = Product(name=name, brand=brand, stock=0)
                    db.session.add(prod)
                    db.session.flush()

                if eid and eid in existing:
                    entry = existing[eid]
                    old_q = entry.quantity
                    delta = qty - old_q
                    new_st = prod.stock + delta
                    if new_st < 0:
                        return jsonify(error=f"Línea {idx+1}: quedaría stock negativo"), 400
                    entry.quantity = qty
                    prod.stock = new_st
                    seen.add(eid)
                else:
                    prod.stock += qty
                    entry = InventoryEntry(
                        ingreso_id=batch.id,
                        product_id=prod.id,
                        quantity=qty
                    )
                    db.session.add(entry)
                    db.session.flush()
                    seen.add(entry.id)

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

    @app.route('/inventario')
    @login_required
    def inventario():
        return render_view("inventario.html")

    @app.route('/api/productos/<int:product_id>/usage', methods=['GET'])
    @admin_required
    def api_get_product_usage(product_id):
        product = Product.query.get(product_id)
        if not product:
            return jsonify(error="Producto no encontrado"), 404

        inv_count = db.session.query(func.count(InventoryEntry.id)).filter(InventoryEntry.product_id == product_id).scalar()
        dsp_count = db.session.query(func.count(DispatchEntry.id)).filter(DispatchEntry.product_id == product_id).scalar()
        poi_count = db.session.query(func.count(PurchaseOrderItem.id)).filter(PurchaseOrderItem.product_id == product_id).scalar()

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

    @app.route('/api/productos/<int:product_id>/refs_detail', methods=['GET'])
    @admin_required
    def api_product_refs_detail(product_id):
        p = Product.query.get(product_id)
        if not p:
            return jsonify(error="Producto no encontrado"), 404

        inv_entries = db.session.query(InventoryEntry) \
            .filter(InventoryEntry.product_id == product_id) \
            .order_by(InventoryEntry.id.desc()) \
            .all()

        inv_list = []
        for e in inv_entries:
            batch_id = getattr(e, "batch_id", None)
            batch_number = None
            batch_date = None
            try:
                batch = getattr(e, "batch", None)
                if batch is None and batch_id:
                    from database.db import InventoryBatch
                    batch = InventoryBatch.query.get(batch_id)
                if batch:
                    batch_number = getattr(batch, "id", None) or getattr(batch, "number", None)
                    batch_date = getattr(batch, "created_at", None) or getattr(batch, "date", None)
            except NameError:
                pass

            inv_list.append({
                "id": e.id,
                "batch_id": batch_id,
                "batch_number": batch_number,
                "quantity": getattr(e, "quantity", None),
                "date": to_iso(getattr(e, "created_at", None) or getattr(e, "date", None) or batch_date),
                "note": getattr(e, "note", None)
            })

        dsp_list = []
        dsp_entries = db.session.query(DispatchEntry) \
            .filter(DispatchEntry.product_id == product_id) \
            .order_by(DispatchEntry.id.desc()) \
            .all()

        for d in dsp_entries:
            dispatch_id = None
            dispatch_code = None
            dispatch_date = getattr(d, "created_at", None) or getattr(d, "date", None)

            try:
                batch = None
                if hasattr(d, "batch") and d.batch is not None:
                    batch = d.batch
                elif hasattr(d, "batch_id") and d.batch_id:
                    batch = DispatchBatch.query.get(d.batch_id)

                if batch:
                    dispatch_id = getattr(batch, "id", None)
                    dispatch_code = getattr(batch, "number", None) or getattr(batch, "folio", None)
                    dispatch_date = dispatch_date or getattr(batch, "created_at", None) or getattr(batch, "date", None)
            except NameError:
                pass

            dsp_list.append({
                "id": d.id,
                "quantity": getattr(d, "quantity", None),
                "dispatch_id": dispatch_id,
                "dispatch_code": dispatch_code,
                "date": to_iso(dispatch_date),
                "note": getattr(d, "note", None)
            })

        poi_list = []
        poi_rows = db.session.query(PurchaseOrderItem, PurchaseOrder) \
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.order_id) \
            .filter(PurchaseOrderItem.product_id == product_id) \
            .order_by(PurchaseOrder.id.desc(), PurchaseOrderItem.id.desc()) \
            .all()

        for item, oc in poi_rows:
            poi_list.append({
                "id": item.id,
                "order_id": oc.id,
                "order_number": getattr(oc, "number", None) or getattr(oc, "folio", None) or oc.id,
                "quantity": getattr(item, "quantity", None),
                "date": to_iso(getattr(oc, "created_at", None) or getattr(oc, "date", None))
            })

        return jsonify({
            "product": {"id": p.id, "name": p.name, "brand": p.brand},
            "inventory_entries": inv_list,
            "dispatch_entries": dsp_list,
            "purchase_order_items": poi_list
        }), 200

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

            db.session.add(Log(
                user_id=session.get('user_id'),
                action='delete_product_refs',
                target_table='products',
                target_id=p.id,
                details=f"Eliminó referencias del producto {p.id}: inventario {inv_del}, despachos {dsp_del}, OC {poi_del}"
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
        product = Product.query.get(product_id)
        if not product:
            return jsonify(error="Producto no encontrado"), 404

        inv_count = db.session.query(func.count(InventoryEntry.id)).filter(InventoryEntry.product_id == product_id).scalar() or 0
        dsp_count = db.session.query(func.count(DispatchEntry.id)).filter(DispatchEntry.product_id == product_id).scalar() or 0
        poi_count = db.session.query(func.count(PurchaseOrderItem.id)).filter(PurchaseOrderItem.product_id == product_id).scalar() or 0
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

        try:
            db.session.add(Log(
                user_id=session.get('user_id'),
                action='delete_product',
                target_table='products',
                target_id=product.id,
                details=f"Eliminó producto {product.name} / {product.brand} (id {product.id}, stock {product.stock})"
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
        data = get_json_body()
        try:
            target_id = int(data.get('target_id'))
        except (TypeError, ValueError):
            return jsonify(error="target_id inválido"), 400

        sources = data.get('sources') or []
        if not isinstance(sources, list) or not sources:
            return jsonify(error="Debes enviar lista 'sources' con IDs de productos a fusionar"), 400

        try:
            sources = list({int(x) for x in sources})
        except (TypeError, ValueError):
            return jsonify(error="IDs en 'sources' inválidos"), 400

        if target_id in sources:
            return jsonify(error="target_id no puede estar también en 'sources'"), 400

        target = Product.query.get(target_id)
        if not target:
            return jsonify(error=f"Producto destino {target_id} no existe"), 404

        new_name = clean_text(data.get('new_name'))
        new_brand = clean_text(data.get('new_brand'))
        if new_name:
            target.name = new_name
        if new_brand:
            target.brand = new_brand

        src_objs = Product.query.filter(Product.id.in_(sources)).all()
        faltantes = set(sources) - {p.id for p in src_objs}
        if faltantes:
            return jsonify(error=f"Productos fuente inexistentes: {sorted(faltantes)}"), 404

        try:
            for src in src_objs:
                InventoryEntry.query.filter_by(product_id=src.id).update({"product_id": target.id}, synchronize_session=False)
                DispatchEntry.query.filter_by(product_id=src.id).update({"product_id": target.id}, synchronize_session=False)
                PurchaseOrderItem.query.filter_by(product_id=src.id).update({"product_id": target.id}, synchronize_session=False)

                target.stock = (target.stock or 0) + (src.stock or 0)

                db.session.add(Log(
                    user_id=session['user_id'],
                    action='merge_product',
                    target_table='products',
                    target_id=target.id,
                    details=f"Fusionó producto {src.id} en {target.id}; referencias movidas y stock sumado: {src.stock}"
                ))

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

        except SQLAlchemyError:
            db.session.rollback()
            return jsonify(error="Error interno durante la fusión"), 500

    @app.route('/api/productos/suggest')
    @login_required
    def suggest_products():
        q = request.args.get('q', '').strip()
        if not q:
            return jsonify([])

        matches = Product.query.filter(Product.name.ilike(f"%{q}%")).limit(10).all()

        return jsonify([
            {
                'id': p.id,
                'name': p.name,
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
        name = clean_text(data.get('name'))
        brand = clean_text(data.get('brand'))
        stock = data.get('stock', None)
        if not name or not brand or not isinstance(stock, int) or stock < 0:
            return jsonify(error="name, brand y stock(int≥0) son obligatorios"), 400

        prod = Product.query.get_or_404(id)
        prod.name = name
        prod.brand = brand
        prod.stock = stock
        db.session.add(Log(
            user_id=session['user_id'],
            action='update_product_stock',
            target_table='products',
            target_id=prod.id,
            details=f"Actualizó producto {prod.name} / {prod.brand} (id {prod.id}) con stock {prod.stock}"
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
