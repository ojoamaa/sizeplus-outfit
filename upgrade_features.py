"""Delivery tariffs, quotations, order receipts and safe reservation expiry.
Imported by main.py; no destructive migrations and no new paid-order mutations.
"""
import os
import secrets
import hmac
from datetime import datetime, timedelta
from decimal import Decimal
from html import escape
from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from database import conn, database_backend

DEFAULT_EXPIRY_MINUTES = 45

def now(): return datetime.utcnow().isoformat()
def money(value): return f'₦{Decimal(str(value or 0)):,.2f}'

def initialize():
    c = conn()
    try:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS delivery_tariffs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          state TEXT NOT NULL,
          city TEXT NOT NULL,
          zone TEXT NOT NULL,
          fee REAL NOT NULL,
          active INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(state,city,zone)
        );
        CREATE TABLE IF NOT EXISTS payment_attempts (
          order_id INTEGER PRIMARY KEY,
          started_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payment_receipts (
          order_id INTEGER PRIMARY KEY,
          receipt_no TEXT NOT NULL UNIQUE,
          paystack_reference TEXT NOT NULL UNIQUE,
          issued_at TEXT NOT NULL
        );
        ''')
        c.commit()
    finally: c.close()

def normalize(value): return ' '.join(str(value or '').strip().lower().split())

def lookup_tariff(c, state, city, zone):
    state, city, zone = normalize(state), normalize(city), normalize(zone)
    if not state or not city or not zone:
        raise HTTPException(400, 'Select state, city and delivery zone.')
    row = c.execute('''SELECT * FROM delivery_tariffs
        WHERE lower(state)=? AND lower(city)=? AND lower(zone)=? AND active=1''',
        (state,city,zone)).fetchone()
    if not row:
        raise HTTPException(409, 'Delivery to this destination requires a quotation. Please contact the boutique before paying.')
    return row

def quote(state,city,zone,method):
    if normalize(method)=='pickup': return {'delivery_fee':0,'available':True,'method':'Pickup'}
    c=conn()
    try:
        r=lookup_tariff(c,state,city,zone)
        return {'delivery_fee':float(r['fee']),'available':True,'method':'Delivery','tariff_id':r['id'],'zone':r['zone']}
    finally:c.close()

def list_tariffs():
    c=conn()
    try:return [dict(r) for r in c.execute('SELECT * FROM delivery_tariffs ORDER BY state,city,zone').fetchall()]
    finally:c.close()

def save_tariff(data):
    state,city,zone=(str(data[k]).strip() for k in ('state','city','zone'))
    if not all((state,city,zone)) or len(state)>80 or len(city)>80 or len(zone)>80:
        raise HTTPException(400,'State, city and zone are required (max 80 characters).')
    fee=Decimal(str(data['fee']))
    if not fee.is_finite() or fee<0 or fee>1000000: raise HTTPException(400,'Invalid delivery fee.')
    c=conn()
    try:
        old=c.execute('SELECT id FROM delivery_tariffs WHERE lower(state)=? AND lower(city)=? AND lower(zone)=?',(normalize(state),normalize(city),normalize(zone))).fetchone()
        if old:
            c.execute('UPDATE delivery_tariffs SET fee=?,active=?,updated_at=? WHERE id=?',(float(fee),int(data['active']),now(),old['id']))
            ident=old['id']
        else:
            cur=c.execute('INSERT INTO delivery_tariffs(state,city,zone,fee,active,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(state,city,zone,float(fee),int(data['active']),now(),now()))
            ident=cur.lastrowid
        c.commit();return {'ok':True,'id':ident}
    except Exception:
        c.rollback();raise
    finally:c.close()

def release_expired():
    """Release only unpaid orders older than the expiry period, under a DB write lock."""
    c=conn()
    try:
        if database_backend()=='sqlite': c.execute('BEGIN IMMEDIATE')
        cutoff=(datetime.utcnow()-timedelta(minutes=DEFAULT_EXPIRY_MINUTES)).isoformat()
        rows=c.execute("SELECT id FROM orders WHERE payment_status='AWAITING_PAYMENT' AND created_at<? AND id NOT IN (SELECT order_id FROM payment_attempts)",(cutoff,)).fetchall()
        for order in rows:
            oid=order['id']
            if database_backend()=='postgresql':
                locked=c.execute('SELECT payment_status FROM orders WHERE id=? FOR UPDATE',(oid,)).fetchone()
                if not locked or locked['payment_status']!='AWAITING_PAYMENT':continue
            items=c.execute('SELECT variant_id,qty FROM order_items WHERE order_id=?',(oid,)).fetchall()
            for item in items:
                c.execute('UPDATE variants SET reserved_qty=CASE WHEN reserved_qty>=? THEN reserved_qty-? ELSE 0 END WHERE id=?',(item['qty'],item['qty'],item['variant_id']))
            c.execute("UPDATE orders SET payment_status='EXPIRED',order_status='EXPIRED' WHERE id=? AND payment_status='AWAITING_PAYMENT'",(oid,))
        c.commit();return len(rows)
    except Exception:
        c.rollback();raise
    finally:c.close()

def issue_receipt_in_transaction(c,order,reference):
    """Must be invoked within the paid-order transaction after verification."""
    existing=c.execute('SELECT * FROM payment_receipts WHERE order_id=?',(order['id'],)).fetchone()
    if existing:return dict(existing)
    receipt_no='SP-RC-'+str(order['order_no']).replace('SP-ON-','')
    issued=now()
    c.execute('INSERT INTO payment_receipts(order_id,receipt_no,paystack_reference,issued_at) VALUES(?,?,?,?)',(order['id'],receipt_no,reference,issued))
    return {'order_id':order['id'],'receipt_no':receipt_no,'paystack_reference':reference,'issued_at':issued}

def receipt_data(order_no,token):
    c=conn()
    try:
        order=c.execute('SELECT * FROM orders WHERE order_no=? AND access_token=?',(order_no,token)).fetchone()
        if not order:raise HTTPException(404,'Receipt not found.')
        if order['payment_status']!='PAID':raise HTTPException(409,'Payment has not been confirmed.')
        receipt=c.execute('SELECT * FROM payment_receipts WHERE order_id=?',(order['id'],)).fetchone()
        if not receipt:raise HTTPException(409,'Receipt is being prepared. Please retry shortly.')
        items=[dict(i) for i in c.execute('SELECT * FROM order_items WHERE order_id=?',(order['id'],)).fetchall()]
        return dict(order),dict(receipt),items
    finally:c.close()

def render_receipt(order,receipt,items):
    esc=lambda x:escape(str(x or ''))
    rows=''.join(f'<tr><td>{esc(i["product_name"])}<br><small>{esc(i["size"])} / {esc(i["color"])}</small></td><td>{int(i["qty"])}</td><td>{money(i["line_total"])}</td></tr>' for i in items)
    test_label='<p class="test">TEST PAYMENT — NO REAL FUNDS COLLECTED</p>' if os.getenv('PAYSTACK_SECRET_KEY','').startswith('sk_test_') else ''
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Receipt {esc(receipt['receipt_no'])}</title><style>body{{font:15px Arial,sans-serif;background:#faf6f6;color:#301722;margin:0;padding:24px}}main{{max-width:700px;margin:auto;background:white;padding:30px;border:1px solid #eadfe2;border-radius:12px}}h1{{color:#7b1734}}table{{width:100%;border-collapse:collapse}}td,th{{padding:12px 8px;border-bottom:1px solid #eee;text-align:left}}.total{{font-size:23px;font-weight:bold;text-align:right}}.test{{color:#9b3500;font-weight:bold}}button{{padding:12px 20px;border:0;border-radius:8px;background:#7b1734;color:white;cursor:pointer}}@media print{{body{{padding:0;background:white}}main{{border:0}}button{{display:none}}}}</style></head><body><main><h1>Sizeplus Outfit</h1><p>Shop No. GB 19, Banex Vom (Plaza), Wuse 2, Abuja<br>08033117766 · sizeplus9@gmail.com</p>{test_label}<h2>Payment receipt</h2><p><b>Receipt:</b> {esc(receipt['receipt_no'])}<br><b>Order:</b> {esc(order['order_no'])}<br><b>Issued:</b> {esc(receipt['issued_at'])} UTC<br><b>Customer:</b> {esc(order['customer_name'])}<br><b>Paystack reference:</b> {esc(receipt['paystack_reference'])}<br><b>Payment status:</b> PAID</p><table><tr><th>Item</th><th>Qty</th><th>Amount</th></tr>{rows}</table><p>Subtotal: {money(order['subtotal'])}<br>{esc(order['delivery_method'])} — {esc(order['city'])}, {esc(order['state'])}: {money(order['delivery_fee'])}</p><p class="total">Total paid: {money(order['total'])}</p><button onclick="window.print()">Print / Save PDF</button></main></body></html>'''
