import io
from flask import jsonify, request, render_template, session, send_file
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from sqlalchemy.orm import joinedload

from core.helpers import clean_text, get_json_body, login_required, render_view
from core.pdf_utils import build_order_pdf
from database.db import (
    Client,
    DispatchBatch,
    DispatchEntry,
    Log,
    Product,
    PurchaseOrder,
    PurchaseOrderItem,
    db,
)


def register_orders(app):
    @app.route('/ordenes', methods=['GET'])
    @login_required
    def ordenes_listado():
        raw = PurchaseOrder.query.order_by(PurchaseOrder.created_at.desc()).all()
        orders = []
        for o in raw:
            total = sum(item.quantity for item in o.items)
            dispatched = (
                db.session.query(func.coalesce(func.sum(DispatchEntry.quantity), 0))
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
                'id': o.id,
                'number': o.number,
                'client': o.client.name,
                'created_at': o.created_at.strftime('%d/%m/%Y %H:%M'),
                'estado': estado,
                'badge': badge
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

        if not number:
            return jsonify(error="Falta el número de orden"), 400
        if not client_name:
            return jsonify(error="Falta el nombre del cliente"), 400
        if not isinstance(items, list) or not items:
            return jsonify(error="Debes enviar un array 'items' con al menos un producto"), 400

        number_clean = number
        client_clean = client_name

        if PurchaseOrder.query.filter_by(number=number_clean).first():
            return jsonify(error="Ya existe una orden con ese número"), 400

        client = Client.query.filter(Client.name.ilike(client_clean)).first()
        if not client:
            client = Client(name=client_clean)
            db.session.add(client)
            db.session.flush()

        po = PurchaseOrder(number=number_clean, client_id=client.id)
        db.session.add(po)
        db.session.flush()

        processed = []
        try:
            for idx, it in enumerate(items):
                raw_name = it.get('name', '')
                raw_brand = it.get('brand', '')
                qty = it.get('quantity')

                if not isinstance(qty, int) or qty <= 0:
                    db.session.rollback()
                    return jsonify(error=f"Línea {idx+1}: 'quantity' debe ser entero > 0"), 400

                name = clean_text(raw_name)
                brand = clean_text(raw_brand)
                if not name or not brand:
                    db.session.rollback()
                    return jsonify(error=f"Línea {idx+1}: faltan 'name' o 'brand'"), 400

                prod = Product.query.filter(
                    Product.name.ilike(f"%{name}%"),
                    Product.brand.ilike(f"%{brand}%")
                ).first()
                if not prod:
                    prod = Product(name=name, brand=brand, stock=0)
                    db.session.add(prod)
                    db.session.flush()

                poi = PurchaseOrderItem(
                    order_id=po.id,
                    product_id=prod.id,
                    quantity=qty
                )
                db.session.add(poi)
                db.session.flush()

                processed.append({
                    'item_id': poi.id,
                    'product_id': prod.id,
                    'name': prod.name,
                    'brand': prod.brand,
                    'quantity': qty
                })

                db.session.add(Log(
                    user_id=session['user_id'],
                    action='add_po_item',
                    target_table='purchase_order_items',
                    target_id=poi.id,
                    details=f"Agregó a OC {po.number} el producto {prod.name} / {prod.brand} (id {prod.id}) por {qty} unidad(es)"
                ))

            db.session.add(Log(
                user_id=session['user_id'],
                action='create_purchase_order',
                target_table='purchase_orders',
                target_id=po.id,
                details=f"Creó orden de compra {po.number} para cliente {client.name} con {len(processed)} ítems"
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

        detail = []
        for it in po.items:
            desp = db.session.query(func.coalesce(func.sum(DispatchEntry.quantity), 0)) \
                .join(DispatchBatch, DispatchEntry.batch_id == DispatchBatch.id) \
                .filter(
                DispatchEntry.product_id == it.product_id,
                DispatchBatch.order_number == po.number
            ).scalar() or 0

            detail.append({
                'product': it.product.name,
                'solicitado': it.quantity,
                'despachado': desp,
                'pendiente': it.quantity - desp
            })

        batches = DispatchBatch.query.options(
            joinedload(DispatchBatch.user),
            joinedload(DispatchBatch.entries).joinedload(DispatchEntry.product)
        ).filter_by(order_number=po.number) \
            .order_by(DispatchBatch.id.desc()) \
            .all()

        dispatch_history = []
        for b in batches:
            dispatch_history.append({
                'batch_id': b.id,
                'user': getattr(b.user, 'name', None),
                'created': getattr(b, 'created_at', None),
                'items': [{
                    'product': e.product.name if e.product else None,
                    'brand': e.product.brand if e.product else None,
                    'qty': e.quantity
                } for e in b.entries]
            })

        return render_view(
            'detalle_orden.html',
            order=po,
            detail=detail,
            dispatch_history=dispatch_history
        )

    @app.route('/ordenes/<int:order_id>/export/pdf')
    @login_required
    def export_orden_pdf(order_id):
        order = PurchaseOrder.query.options(
            joinedload(PurchaseOrder.client),
            joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.product)
        ).get_or_404(order_id)
        pdf_bytes = build_order_pdf(order)
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype='application/pdf',
            download_name=f"orden_{order.number}.pdf",
            as_attachment=True
        )

    @app.route('/api/ordenes/<int:order_id>', methods=['DELETE'])
    @login_required
    def eliminar_orden(order_id):
        po = PurchaseOrder.query.get(order_id)
        if not po:
            return jsonify({'error': 'Orden no encontrada'}), 404

        try:
            if 'order_id' in DispatchBatch.__table__.columns:
                (db.session.query(DispatchBatch)
                 .filter(DispatchBatch.order_id == po.id)
                 .update({DispatchBatch.order_id: None}, synchronize_session=False))
            if 'order_number' in DispatchBatch.__table__.columns:
                (db.session.query(DispatchBatch)
                 .filter(DispatchBatch.order_number == po.number)
                 .update({DispatchBatch.order_number: None}, synchronize_session=False))

            if 'order_id' in DispatchEntry.__table__.columns:
                (db.session.query(DispatchEntry)
                 .filter(DispatchEntry.order_id == po.id)
                 .update({DispatchEntry.order_id: None}, synchronize_session=False))
            if 'order_number' in DispatchEntry.__table__.columns:
                (db.session.query(DispatchEntry)
                 .filter(DispatchEntry.order_number == po.number)
                 .update({DispatchEntry.order_number: None}, synchronize_session=False))

            db.session.query(PurchaseOrderItem) \
                .filter(PurchaseOrderItem.order_id == po.id) \
                .delete(synchronize_session=False)

            db.session.delete(po)
            db.session.commit()

            return jsonify({'message': 'Orden eliminada y despachos desasociados.'}), 200

        except IntegrityError as e:
            db.session.rollback()
            return jsonify({'error': 'No se pudo eliminar por restricciones de integridad.',
                            'detail': str(e.orig)}), 409
        except Exception:
            db.session.rollback()
            return jsonify({'error': 'Error interno al eliminar la orden.'}), 500

    @app.route('/api/ordenes/<order_number>/detalle')
    @login_required
    def api_orden_detalle(order_number):
        po = PurchaseOrder.query.filter_by(number=order_number).first_or_404()

        result = []
        for it in po.items:
            desp = (
                db.session.query(func.coalesce(func.sum(DispatchEntry.quantity), 0))
                .filter(
                    DispatchEntry.order_number == po.number,
                    DispatchEntry.product_id == it.product_id
                )
                .scalar()
            )
            result.append({
                'product_id': it.product_id,
                'product': it.product.name,
                'brand': it.product.brand,
                'solicitado': it.quantity,
                'despachado': desp,
                'pendiente': max(it.quantity - desp, 0)
            })
        return jsonify({
            'order_number': po.number,
            'client': po.client.name,
            'items': result
        })

    @app.route('/ordenes/editar/<int:order_id>', methods=['GET', 'POST'])
    @login_required
    def ordenes_editar(order_id):
        po = PurchaseOrder.query.get_or_404(order_id)

        if request.method == 'GET':
            items = [{
                'item_id': it.id,
                'product': it.product.name,
                'brand': it.product.brand,
                'quantity': it.quantity
            } for it in po.items]

            return render_view(
                'editar_orden.html',
                order_number=po.number,
                client_name=po.client.name,
                items=items,
                order_id=po.id
            )

        data = get_json_body()
        number_new = clean_text(data.get('number'))
        client_new = clean_text(data.get('client'))
        items_data = data.get('items') or []

        if not number_new or not client_new or not isinstance(items_data, list):
            return jsonify(error="Datos incompletos"), 400

        old_number = po.number
        if number_new != po.number:
            if PurchaseOrder.query.filter_by(number=number_new).first():
                return jsonify(error="Ya existe otra orden con ese número"), 400
            po.number = number_new
            db.session.query(DispatchBatch) \
                .filter(DispatchBatch.order_number == old_number) \
                .update({DispatchBatch.order_number: number_new}, synchronize_session=False)
            db.session.query(DispatchEntry) \
                .filter(DispatchEntry.order_number == old_number) \
                .update({DispatchEntry.order_number: number_new}, synchronize_session=False)

        cli = Client.query.filter(Client.name.ilike(client_new)).first()
        if not cli:
            cli = Client(name=client_new)
            db.session.add(cli)
            db.session.flush()
        po.client_id = cli.id

        existing = {it.id: it for it in po.items}
        incoming_ids = set()

        for idx, it in enumerate(items_data):
            pid = it.get('item_id')
            raw_n = it.get('product', '')
            raw_b = it.get('brand', '')
            qty = it.get('quantity')

            if not raw_n or not raw_b or not isinstance(qty, int) or qty <= 0:
                return jsonify(error=f"Línea {idx+1} inválida"), 400

            name = clean_text(raw_n)
            brand = clean_text(raw_b)

            prod = Product.query.filter(
                Product.name.ilike(f"%{name}%"),
                Product.brand.ilike(f"%{brand}%")
            ).first()
            if not prod:
                prod = Product(name=name, brand=brand, stock=0)
                db.session.add(prod)
                db.session.flush()

            if pid and pid in existing:
                existing[pid].quantity = qty
                incoming_ids.add(pid)
            else:
                poi = PurchaseOrderItem(
                    order_id=po.id,
                    product_id=prod.id,
                    quantity=qty
                )
                db.session.add(poi)
                db.session.flush()
                incoming_ids.add(poi.id)

        for eid, itobj in existing.items():
            if eid not in incoming_ids:
                db.session.delete(itobj)

        db.session.add(Log(
            user_id=session['user_id'],
            action='edit_purchase_order',
            target_table='purchase_orders',
            target_id=po.id,
            details=f"Editó orden de compra {po.number}: cliente {cli.name}, ítems vigentes {sorted(incoming_ids)}"
        ))

        db.session.commit()
        return jsonify(message="Orden actualizada"), 200
