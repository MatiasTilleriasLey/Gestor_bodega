import os
from fpdf import FPDF, HTMLMixin
from fpdf.enums import XPos, YPos
from flask import current_app

from database.db import DispatchBatch, DispatchEntry, DispatchPhoto, PurchaseOrder
from sqlalchemy.orm import joinedload
from .helpers import to_iso


class PDF(FPDF, HTMLMixin):
    pass


def _pdf_sanitize(text):
    s = "-" if text in (None, "") else str(text)
    s = s.replace("—", "-")
    return s.encode('latin-1', 'replace').decode('latin-1')


def _wrap_pdf_text(text, width=50):
    import textwrap
    s = _pdf_sanitize(text)
    return "\n".join(textwrap.fill(s, width=width, break_long_words=True, break_on_hyphens=False).splitlines())


def _pdf_header(pdf: PDF, title: str):
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(
        0,
        10,
        _pdf_sanitize(title),
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,   # reemplaza ln=1
    )
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 11)


def _pdf_add_keyvals(pdf: FPDF, pairs):
    if not pairs:
        return

    html = "<table border='1' width='100%' cellspacing='0' cellpadding='4'><tbody>"
    for label, value in pairs:
        safe_label = _pdf_sanitize(label)
        safe_value = _pdf_sanitize(value if value not in (None, '') else '-')
        html += "<tr>"
        html += f"<td width='35%'><b>{safe_label}</b></td>"
        html += f"<td width='65%'>{safe_value}</td>"
        html += "</tr>"
    html += "</tbody></table>"

    pdf.write_html(html)
    pdf.ln(4)


def _pdf_add_table_html(pdf: PDF, headers, rows):
    """
    Tabla alineada usando HTML básico.
    headers: lista de strings
    rows: lista de listas de strings
    """
    html = "<table border='1' width='100%'><thead><tr>"

    for h in headers:
        html += f"<th>{_pdf_sanitize(h)}</th>"

    html += "</tr></thead><tbody>"

    for row in rows:
        html += "<tr>"
        for col in row:
            html += f"<td>{_pdf_sanitize(str(col))}</td>"
        html += "</tr>"

    html += "</tbody></table>"

    pdf.write_html(html)
    pdf.ln(4)


def _pdf_add_photos(pdf: PDF, photos):
    if not photos:
        pdf.cell(
            0,
            8,
            "Sin fotos adjuntas.",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,  # reemplaza ln=1
        )
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
        pdf.cell(
            0,
            8,
            _pdf_sanitize(f"Fotos {stage}"),
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        pdf.set_font("Helvetica", "", 11)
        for p in plist:
            path = os.path.join(current_app.root_path, 'static', p.path)
            if os.path.exists(path):
                pdf.cell(
                    0,
                    6,
                    _pdf_sanitize(to_iso(p.created_at) or ""),
                    new_x=XPos.LMARGIN,
                    new_y=YPos.NEXT,
                )
                try:
                    avail = pdf.w - pdf.l_margin - pdf.r_margin
                    pdf.image(path, w=min(150, avail))
                    pdf.ln(4)
                except Exception:
                    pdf.cell(
                        0,
                        6,
                        _pdf_sanitize(f"[No se pudo cargar la imagen {p.path}]"),
                        new_x=XPos.LMARGIN,
                        new_y=YPos.NEXT,
                    )
            else:
                pdf.cell(
                    0,
                    6,
                    _pdf_sanitize(f"[Archivo faltante: {p.path}]"),
                    new_x=XPos.LMARGIN,
                    new_y=YPos.NEXT,
                )
        pdf.ln(4)


def build_dispatch_pdf(batch: DispatchBatch):
    pdf = PDF()
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
            str(e.quantity),
        ])

    _pdf_add_table_html(pdf, ["Producto", "Marca", "Cantidad"], items)

    photos = DispatchPhoto.query.filter_by(batch_id=batch.id).order_by(DispatchPhoto.created_at).all()
    _pdf_add_photos(pdf, photos)

    data = pdf.output()
    return bytes(data) if isinstance(data, bytearray) else data


def build_order_pdf(order: PurchaseOrder):
    pdf = PDF()
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()

    _pdf_header(pdf, f"Orden de compra #{order.number}")
    _pdf_add_keyvals(pdf, [
        ("Cliente", getattr(order.client, 'name', '')),
        ("Fecha creación", order.created_at.strftime('%d/%m/%Y %H:%M') if order.created_at else '')
    ])

    items = []
    for it in order.items:
        items.append([
            getattr(it.product, 'name', ''),
            getattr(it.product, 'brand', ''),
            str(it.quantity),
        ])

    _pdf_add_table_html(pdf, ["Producto", "Marca", "Cantidad"], items)

    batches = DispatchBatch.query.options(
        joinedload(DispatchBatch.entries).joinedload(DispatchEntry.product),
        joinedload(DispatchBatch.photos)
    ).filter_by(order_number=order.number).all()

    if not batches:
        pdf.cell(
            0,
            8,
            _pdf_sanitize("Sin despachos asociados."),
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
    else:
        for b in batches:
            pdf.set_font("Helvetica", "B", 13)
            pdf.cell(
                0,
                10,
                _pdf_sanitize(f"Despacho #{b.id}"),
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
            pdf.set_font("Helvetica", "", 11)
            _pdf_add_keyvals(pdf, [
                ("Cliente", getattr(b.client, 'name', '')),
                ("Operador", getattr(b.user, 'name', '')),
                ("Fecha", b.created_at.strftime('%d/%m/%Y %H:%M') if b.created_at else '')
            ])

            b_items = []
            for e in b.entries:
                b_items.append([
                    getattr(e.product, 'name', ''),
                    getattr(e.product, 'brand', ''),
                    str(e.quantity),
                ])

            _pdf_add_table_html(pdf, ["Producto", "Marca", "Cantidad"], b_items)
            _pdf_add_photos(pdf, b.photos)

    data = pdf.output()
    return bytes(data) if isinstance(data, bytearray) else data
