from datetime import datetime, timedelta
from flask import render_template
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from core.helpers import login_required, render_view
from database.db import Client, DispatchBatch, DispatchEntry, Product, PurchaseOrder, PurchaseOrderItem, db


def register_dashboard(app):
    @app.route('/dashboard')
    @login_required
    def dashboard():
        total_products = db.session.query(func.count(Product.id)).scalar() or 0
        total_stock = db.session.query(func.coalesce(func.sum(Product.stock), 0)).scalar() or 0
        total_despachos_batches = db.session.query(func.count(DispatchBatch.id)).scalar() or 0
        total_clientes = db.session.query(func.count(Client.id)).scalar() or 0
        stock_critico = db.session.query(func.count(Product.id)).filter(Product.stock <= 5).scalar() or 0
        clientes_ult_30 = db.session.query(func.count(func.distinct(DispatchBatch.client_id))) \
            .filter(DispatchBatch.created_at >= datetime.utcnow() - timedelta(days=30)) \
            .scalar() or 0

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
            {'client': row.client, 'despachos': row.despachos, 'unidades': row.unidades}
            for row in top_clients
        ]

        orders = PurchaseOrder.query.options(joinedload(PurchaseOrder.items)).all()
        status_totals = {'completas': 0, 'parciales': 0, 'pendientes': 0}
        order_totals = {
            row.number: row.solicitadas
            for row in db.session.query(
                PurchaseOrder.number,
                func.coalesce(func.sum(PurchaseOrderItem.quantity), 0).label('solicitadas')
            )
            .outerjoin(PurchaseOrderItem, PurchaseOrderItem.order_id == PurchaseOrder.id)
            .group_by(PurchaseOrder.number)
            .all()
        }
        dispatch_totals = {
            row.order_number: row.despachadas
            for row in db.session.query(
                DispatchBatch.order_number,
                func.coalesce(func.sum(DispatchEntry.quantity), 0).label('despachadas')
            )
            .join(DispatchEntry, DispatchEntry.batch_id == DispatchBatch.id)
            .filter(DispatchBatch.order_number.isnot(None))
            .group_by(DispatchBatch.order_number)
            .all()
        }
        for po in orders:
            solicitadas = order_totals.get(po.number, 0)
            despachadas = dispatch_totals.get(po.number, 0)

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
            'despachos_batches': total_despachos_batches,
            'clientes': total_clientes,
            'ordenes': len(orders),
            'stock_critico': stock_critico,
            'clientes_ult_30': clientes_ult_30,
            'ordenes_status': status_totals,
            'dispatch_series': dispatch_series,
            'top_clients': top_clients
        }
        return render_view("dashboard.html", stats=stats)
