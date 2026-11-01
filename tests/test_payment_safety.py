import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
import tempfile, os, sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database

class PaymentSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = database.SQLITE_DB
        database.SQLITE_DB = str(Path(self.temp.name)/'test.db')
        import main
        main.init_db()
        import upgrade_features as uf
        uf.initialize()
        self.main=main
        self.uf=uf
        self.db=database
        c=database.conn()
        c.execute("INSERT INTO products(name,category,sku,selling_price,active) VALUES('Test','Clothing','UNITTEST',1000,1)")
        c.execute("INSERT INTO variants(product_id,size,color,qty,reserved_qty) VALUES(1,'M','Black',5,2)")
        c.execute("INSERT INTO orders(order_no,access_token,total,payment_status,order_status,created_at) VALUES('SP-ON-TEST','tok',2000,'AWAITING_PAYMENT','AWAITING_PAYMENT','2020-01-01T00:00:00')")
        c.execute("INSERT INTO order_items(order_id,variant_id,qty) VALUES(1,1,2)")
        c.execute("INSERT INTO payment_attempts(order_id,started_at) VALUES(1,'2020-01-01T00:00:00')")
        c.commit();c.close()
    def tearDown(self):
        database.SQLITE_DB=self.original
        self.temp.cleanup()
    def check_state(self):
        c=self.db.conn(); o=c.execute('SELECT payment_status FROM orders WHERE id=1').fetchone()[0]; v=c.execute('SELECT qty,reserved_qty FROM variants WHERE id=1').fetchone();c.close();return o, v['qty'], v['reserved_qty']
    def test_unknown_status_does_not_release(self):
        from payment_reconciliation import reconcile_stale
        r=MagicMock(status_code=200);r.json.return_value={'status':True,'data':{'status':'pending','reference':'SP-ON-TEST'}}
        with patch.object(self.main,'verify_paystack_transaction',side_effect=Exception('pending')),patch('payment_reconciliation.httpx.get',return_value=r),patch.dict(os.environ,{'PAYSTACK_SECRET_KEY':'sk_test_dummy'}):
            result=reconcile_stale()
        self.assertEqual(result['deferred'],1)
        self.assertEqual(self.check_state(),('AWAITING_PAYMENT',5,2))
    def test_failed_status_releases_once(self):
        from payment_reconciliation import reconcile_stale
        r=MagicMock(status_code=200);r.json.return_value={'status':True,'data':{'status':'failed','reference':'SP-ON-TEST','currency':'NGN'}}
        with patch.object(self.main,'verify_paystack_transaction',side_effect=Exception('not paid')),patch('payment_reconciliation.httpx.get',return_value=r),patch.dict(os.environ,{'PAYSTACK_SECRET_KEY':'sk_test_dummy'}):
            first=reconcile_stale(); second=reconcile_stale()
        self.assertEqual(first['released'],1);self.assertEqual(second['released'],0)
        self.assertEqual(self.check_state(),('EXPIRED',5,0))
    def test_verified_success_with_finalize_error_keeps_stock(self):
        from payment_reconciliation import reconcile_stale
        with patch.object(self.main,'verify_paystack_transaction',return_value={'status':'success','reference':'SP-ON-TEST'}),patch.object(self.main,'finalize_paystack_payment',side_effect=Exception('DB error')):
            result=reconcile_stale()
        self.assertEqual(result['deferred'],1)
        self.assertEqual(self.check_state(),('AWAITING_PAYMENT',5,2))
    def test_paid_success_is_idempotent(self):
        from payment_reconciliation import reconcile_stale
        data={'status':'success','currency':'NGN','reference':'SP-ON-TEST','amount':200000}
        with patch.object(self.main,'verify_paystack_transaction',return_value=data):
            first=reconcile_stale();second=reconcile_stale()
        self.assertEqual(first['paid'],1);self.assertEqual(second['checked'],0)
        self.assertEqual(self.check_state(),('PAID',3,0))

if __name__=='__main__': unittest.main()
