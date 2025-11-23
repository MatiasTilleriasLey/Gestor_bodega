import io
import os
import time
from flask import jsonify, redirect, render_template, request, session, send_file, url_for, current_app
from werkzeug.utils import secure_filename
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload

from core.helpers import clean_text, get_json_body, is_allowed_file, login_required, render_view, to_iso
from core.pdf_utils import build_dispatch_pdf
from database.db import (
    Client,
    DispatchBatch,
    DispatchEntry,
    DispatchPhoto,
    Log,
    Product,
    Users,
    db,
)


def register_dispatches(app):
    @app.route('/despachos/nuevo', methods=['GET', 'POST'])
    @login_required
    def nuevos_despachos():
        if request.method == 'GET':
            return render_view('despachos.html')

        data = get_json_body()
        client_name = clean_text(data.get('client'))
        items = data.get('items') or []
        order_number = clean_text(data.get('order_number'))

        if not client_name:
            return jsonify(error="Debes indicar el nombre del cliente"), 400

        client = Client.query.filter(Client.name.ilike(client_name)).first()
        if not client:
            client = Client(name=client_name)
            db.session.add(client)
            db.session.flush()

        if not isinstance(items, list) or not items:
            return jsonify(error="Envía array 'items' con al menos un producto"), 400

        warnings = []
        processed = []

        batch = DispatchBatch(
            client_id=client.id,
            user_id=session['user_id'],
            order_number=order_number
        )
        db.session.add(batch)
        db.session.flush()

        order_number = order_number or None

        for idx, it in enumerate(items):
            raw_name = it.get('name', '')
            raw_brand = it.get('brand', '')
            qty = it.get('quantity')

            if not isinstance(qty, int) or qty <= 0:
                return jsonify(error=f"Item {idx}: 'quantity' debe ser entero >0"), 400

            name = clean_text(raw_name)
            brand = clean_text(raw_brand)
            if not name or not brand:
                return jsonify(error=f"Item {idx}: faltan 'name' o 'brand'"), 400

            prod = Product.query.filter(
                Product.name.ilike(f"%{name}%"),
                Product.brand.ilike(f"%{brand}%")
            ).first()
            if not prod:
                return jsonify(error=f"Item {idx}: producto no existe en inventario"), 400

            dispatched = min(prod.stock, qty)
            if dispatched < qty:
                warnings.append(
                    f"Item {idx}: sólo había {prod.stock} unidades de {name}; "
                    f"se despacharán {dispatched}"
                )
            prod.stock = max(prod.stock - qty, 0)

            entry = DispatchEntry(
                batch_id=batch.id,
                product_id=prod.id,
                quantity=dispatched,
                order_number=order_number
            )
            db.session.add(entry)
            db.session.flush()

            processed.append({
                'product': {'id': prod.id, 'name': prod.name, 'brand': prod.brand},
                'requested': qty,
                'dispatched': dispatched
            })

            db.session.add(Log(
                user_id=session['user_id'],
                action='dispatch_product',
                target_table='dispatch_entries',
                target_id=entry.id,
                details=f"Despachó producto {prod.name} / {prod.brand} (id {entry.product_id}): solicitado {qty}, enviado {dispatched}, OC {order_number or 'sin OC'}"
            ))

        db.session.add(Log(
            user_id=session['user_id'],
            action='create_dispatch_batch',
            target_table='dispatch_batches',
            target_id=batch.id,
            details=f"Creó despacho #{batch.id} para cliente {client.name} (OC {order_number or 'sin OC'})"
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
        return render_view('despachos_historicos.html')

    @app.route('/despachos/editar/<int:batch_id>', methods=['GET', 'POST'])
    @login_required
    def despacho_editar(batch_id):
        batch = DispatchBatch.query.get_or_404(batch_id)

        if request.method == 'GET':
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

        data = get_json_body()
        cli_raw = clean_text(data.get('client'))
        ord_raw = clean_text(data.get('order_number'))
        items_data = data.get('items') or []

        batch.order_number = ord_raw or None
        if not cli_raw or not isinstance(items_data, list) or not items_data:
            return jsonify(error="Datos incompletos"), 400

        client = Client.query.filter(Client.name.ilike(cli_raw)).first()
        if not client:
            client = Client(name=cli_raw)
            db.session.add(client)
            db.session.flush()
        batch.client_id = client.id
        batch.order_number = ord_raw or None

        existing = {e.id: e for e in batch.entries}
        seen_ids = set()

        try:
            for idx, it in enumerate(items_data):
                eid = it.get('entry_id')
                raw_n = (it.get('name') or '').strip()
                raw_b = (it.get('brand') or '').strip()
                new_qty = it.get('quantity')

                if not raw_n or not raw_b or not isinstance(new_qty, int) or new_qty < 0:
                    return jsonify(error=f"Línea {idx+1} inválida"), 400

                name = clean_text(raw_n)
                brand = clean_text(raw_b)

                prod = Product.query.filter(
                    Product.name.ilike(f"%{name}%"),
                    Product.brand.ilike(f"%{brand}%")
                ).first()
                if not prod:
                    return jsonify(error=f"Línea {idx+1}: producto '{name} / {brand}' no existe"), 400

                if eid and eid in existing:
                    entry = existing[eid]
                    old_qty = entry.quantity
                    delta = old_qty - new_qty
                    new_stock = prod.stock + delta

                    if new_stock < 0:
                        return jsonify(error=f"Línea {idx+1}: no hay suficiente stock para reducir despacho"), 400

                    entry.quantity = new_qty
                    prod.stock = new_stock
                    seen_ids.add(eid)

                else:
                    if prod.stock < new_qty:
                        return jsonify(error=f"Línea {idx+1}: no hay suficiente stock para despachar {new_qty}"), 400
                    prod.stock -= new_qty

                    entry = DispatchEntry(
                        batch_id=batch.id,
                        product_id=prod.id,
                        quantity=new_qty,
                        order_number=batch.order_number
                    )
                    db.session.add(entry)
                    db.session.flush()
                    seen_ids.add(entry.id)

            for old_id, old_entry in existing.items():
                if old_id not in seen_ids:
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
        op = request.args.get('operator', '').strip()
        cl = request.args.get('client', '').strip()
        start = request.args.get('start')
        end = request.args.get('end')

        query = DispatchBatch.query.join(DispatchBatch.user).join(DispatchBatch.client)

        if op:
            query = query.filter(DispatchBatch.user.has(Users.username.ilike(f"%{op}%")))

        if cl:
            query = query.filter(DispatchBatch.client.has(Client.name.ilike(f"%{cl}%")))

        if start:
            query = query.filter(DispatchBatch.created_at >= f"{start} 00:00:00")
        if end:
            query = query.filter(DispatchBatch.created_at <= f"{end} 23:59:59")

        batches = query.order_by(DispatchBatch.created_at.desc()).all()
        result = []
        for b in batches:
            result.append({
                'batch_id': b.id,
                'client': b.client.name,
                'user': b.user.name,
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
                    'created_at': to_iso(getattr(p, "created_at", None))
                })
            return jsonify(out)

        stage = (request.form.get('stage') or '').lower()
        file = request.files.get('photo')
        if stage not in ('salida', 'entrega'):
            return jsonify(error="stage debe ser 'salida' o 'entrega'"), 400
        if not file or not file.filename:
            return jsonify(error="Archivo de imagen requerido"), 400
        if not is_allowed_file(file.filename):
            return jsonify(error="Formato no permitido"), 400

        ext = file.filename.rsplit('.', 1)[1].lower()
        fname = secure_filename(f"dispatch_{batch.id}_{stage}_{int(time.time())}.{ext}")
        rel_path = f"uploads/{fname}"
        save_path = os.path.join(current_app.config['UPLOAD_DIR'], fname)
        os.makedirs(current_app.config['UPLOAD_DIR'], exist_ok=True)

        try:
            file.save(save_path)
            photo = DispatchPhoto(batch_id=batch.id, stage=stage, path=rel_path)
            db.session.add(photo)
            db.session.add(Log(
                user_id=session.get('user_id'),
                action='upload_dispatch_photo',
                target_table='dispatch_batches',
                target_id=batch.id,
                details=f"Subió foto de etapa '{stage}' para despacho #{batch.id}"
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

    @app.route('/api/despachos/<int:batch_id>/fotos/<int:photo_id>', methods=['DELETE'])
    @login_required
    def api_despacho_foto_delete(batch_id, photo_id):
        photo = DispatchPhoto.query.filter_by(id=photo_id, batch_id=batch_id).first_or_404()
        file_path = os.path.join(current_app.root_path, 'static', photo.path)
        try:
            db.session.delete(photo)
            db.session.add(Log(
                user_id=session.get('user_id'),
                action='delete_dispatch_photo',
                target_table='dispatch_batches',
                target_id=batch_id,
                details=f"Eliminó foto {photo_id} etapa '{photo.stage}' del despacho #{batch_id}"
            ))
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            return jsonify(error="No se pudo eliminar la foto"), 500

        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass

        return jsonify(message="Foto eliminada"), 200

    @app.route('/despachos/<int:batch_id>/export/pdf')
    @login_required
    def export_despacho_pdf(batch_id):
        batch = DispatchBatch.query.options(
            joinedload(DispatchBatch.client),
            joinedload(DispatchBatch.user),
            joinedload(DispatchBatch.entries).joinedload(DispatchEntry.product),
            joinedload(DispatchBatch.photos)
        ).get_or_404(batch_id)
        pdf_bytes = build_dispatch_pdf(batch)
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype='application/pdf',
            download_name=f"despacho_{batch.id}.pdf",
            as_attachment=True
        )
