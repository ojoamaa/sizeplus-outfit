"""Reconcile stale Paystack attempts before releasing inventory.

Run periodically in a single worker or scheduler. No stock is released when
Paystack cannot conclusively establish that payment did not succeed.
"""
from datetime import datetime, timedelta
import os
import httpx
from database import conn, database_backend

EXPIRY_MINUTES = 45

def reconcile_stale(limit=50):
    from main import finalize_paystack_payment, verify_paystack_transaction
    cutoff = (datetime.utcnow()-timedelta(minutes=EXPIRY_MINUTES)).isoformat()
    c=conn()
    try:
        rows=c.execute("""SELECT o.id,o.order_no FROM orders o
            JOIN payment_attempts a ON a.order_id=o.id
            WHERE o.payment_status='AWAITING_PAYMENT' AND a.started_at<?
            ORDER BY a.started_at LIMIT ?""",(cutoff,limit)).fetchall()
        candidates=[dict(r) for r in rows]
    finally:c.close()
    result={'checked':0,'paid':0,'released':0,'deferred':0}
    for order in candidates:
        result['checked']+=1
        try:
            data=verify_paystack_transaction(order['order_no'])
        except Exception:
            # Unknown payment state: inspect authoritative provider status below.
            pass
        else:
            # A successful payment must never be released, even if local
            # finalization fails (e.g. transient DB or receipt error).
            try:
                finalize_paystack_payment(order['order_no'], data)
            except Exception:
                result['deferred']+=1
            else:
                result['paid']+=1
            continue
        secret=os.getenv('PAYSTACK_SECRET_KEY','')
        if not secret:
            result['deferred']+=1; continue
        try:
            r=httpx.get('https://api.paystack.co/transaction/verify/'+order['order_no'],
                headers={'Authorization':'Bearer '+secret},timeout=20)
            payload=r.json()
            data=payload.get('data') or {}
            status=data.get('status')
            if r.status_code!=200 or not payload.get('status') or status not in ('failed','abandoned'):
                result['deferred']+=1;continue
            if data.get('reference')!=order['order_no'] or data.get('currency') not in (None,'NGN'):
                result['deferred']+=1;continue
        except (httpx.HTTPError,ValueError,TypeError):
            result['deferred']+=1;continue
        c=conn()
        try:
            if database_backend()=='sqlite':c.execute('BEGIN IMMEDIATE')
            locked=c.execute('SELECT payment_status FROM orders WHERE id=?'+(' FOR UPDATE' if database_backend()=='postgresql' else ''),(order['id'],)).fetchone()
            if not locked or locked['payment_status']!='AWAITING_PAYMENT':
                c.rollback();continue
            items=c.execute('SELECT variant_id,qty FROM order_items WHERE order_id=?',(order['id'],)).fetchall()
            for item in items:
                c.execute('UPDATE variants SET reserved_qty=CASE WHEN reserved_qty>=? THEN reserved_qty-? ELSE 0 END WHERE id=?',
                    (item['qty'],item['qty'],item['variant_id']))
            c.execute("UPDATE orders SET payment_status='EXPIRED',order_status='EXPIRED' WHERE id=?",(order['id'],))
            c.execute('DELETE FROM payment_attempts WHERE order_id=?',(order['id'],))
            c.commit();result['released']+=1
        except Exception:
            c.rollback();result['deferred']+=1
        finally:c.close()
    return result

if __name__=='__main__':
    print(reconcile_stale())
