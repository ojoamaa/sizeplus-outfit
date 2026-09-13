from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List
from datetime import datetime, timedelta
import sqlite3, os, hashlib, secrets

BASE=os.path.dirname(__file__); DB=os.path.join(BASE,'sizeplus.db')
app=FastAPI(title='SIZEPLUS Boutique + E-commerce',version='0.2.0')
app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_credentials=True,allow_methods=['*'],allow_headers=['*'])

def now(): return datetime.utcnow().isoformat()
def conn():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c
def hash_pw(p): return hashlib.sha256(p.encode()).hexdigest()
def audit(c,user_id,action,entity,entity_id=None,detail=''):
    c.execute('INSERT INTO audit_logs(user_id,action,entity,entity_id,detail,created_at) VALUES(?,?,?,?,?,?)',(user_id,action,entity,str(entity_id or ''),detail,now()))

def init_db():
    c=conn(); c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,email TEXT UNIQUE,password_hash TEXT,role TEXT,active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id INTEGER,created_at TEXT);
    CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,category TEXT,sku TEXT UNIQUE,description TEXT DEFAULT '',image TEXT DEFAULT '',cost_price REAL DEFAULT 0,selling_price REAL,low_stock_threshold INTEGER DEFAULT 2,featured INTEGER DEFAULT 0,active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS variants(id INTEGER PRIMARY KEY AUTOINCREMENT,product_id INTEGER,size TEXT,color TEXT,barcode TEXT,qty INTEGER DEFAULT 0,reserved_qty INTEGER DEFAULT 0,UNIQUE(product_id,size,color));
    CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,phone TEXT,email TEXT,notes TEXT,created_at TEXT);
    CREATE TABLE IF NOT EXISTS sales(id INTEGER PRIMARY KEY AUTOINCREMENT,sale_no TEXT UNIQUE,channel TEXT,customer_id INTEGER,customer_name TEXT,user_id INTEGER,subtotal REAL,discount REAL,total REAL,payment_method TEXT,payment_status TEXT,created_at TEXT);
    CREATE TABLE IF NOT EXISTS sale_items(id INTEGER PRIMARY KEY AUTOINCREMENT,sale_id INTEGER,variant_id INTEGER,product_name TEXT,size TEXT,color TEXT,qty INTEGER,unit_price REAL,line_total REAL);
    CREATE TABLE IF NOT EXISTS stock_movements(id INTEGER PRIMARY KEY AUTOINCREMENT,variant_id INTEGER,movement_type TEXT,qty INTEGER,reason TEXT,reference TEXT,user_id INTEGER,created_at TEXT);
    CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT,order_no TEXT UNIQUE,access_token TEXT UNIQUE,customer_name TEXT,email TEXT,phone TEXT,address TEXT,city TEXT,state TEXT,delivery_method TEXT,delivery_fee REAL,subtotal REAL,total REAL,payment_method TEXT,payment_status TEXT,order_status TEXT,created_at TEXT,paid_at TEXT);
    CREATE TABLE IF NOT EXISTS order_items(id INTEGER PRIMARY KEY AUTOINCREMENT,order_id INTEGER,variant_id INTEGER,product_name TEXT,size TEXT,color TEXT,qty INTEGER,unit_price REAL,line_total REAL);
    CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,action TEXT,entity TEXT,entity_id TEXT,detail TEXT,created_at TEXT);
    ''')
    if not c.execute('SELECT 1 FROM users').fetchone():
        c.execute('INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)',('SIZEPLUS Owner','owner@sizeplus.local',hash_pw('Owner123!'),'owner'))
        c.execute('INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)',('Sales Representative','sales@sizeplus.local',hash_pw('Sales123!'),'sales_rep'))
    if not c.execute('SELECT 1 FROM products').fetchone():
        samples=[
          ('Curve Maxi Dress','Dresses','SPD-DR-0042','Flowing statement maxi dress with an easy, flattering silhouette.','/static/assets/dress.svg',18000,32000,1,[('14','Black',4),('16','Black',5),('18','Black',3),('20','Black',2),('22','Black',1)]),
          ('Sculpted Midi Dress','Dresses','SPD-DR-0068','Elegant midi dress designed for polished day-to-evening styling.','/static/assets/midi.svg',20500,38500,1,[('14','Wine',3),('16','Wine',4),('18','Wine',4),('20','Wine',2)]),
          ('CurveFit Palazzo','Bottoms','SPD-TR-0022','High-waist wide-leg palazzo trousers with comfortable movement.','/static/assets/palazzo.svg',12000,24500,1,[('14','Emerald',4),('16','Emerald',5),('18','Emerald',5),('20','Emerald',3),('22','Emerald',2)]),
          ('Statement Blouse','Tops','SPD-TP-0031','Soft structured blouse for workwear and elevated casual looks.','/static/assets/blouse.svg',8500,17500,0,[('14','Ivory',5),('16','Ivory',4),('18','Ivory',3),('20','Ivory',3)]),
          ('Classic Handbag','Bags','SPB-BG-0011','Structured everyday handbag with clean hardware detailing.','/static/assets/bag.svg',9000,18500,1,[('One Size','Tan',8)])
        ]
        for name,cat,sku,desc,img,cost,price,feat,vars in samples:
            cur=c.execute('INSERT INTO products(name,category,sku,description,image,cost_price,selling_price,low_stock_threshold,featured) VALUES(?,?,?,?,?,?,?,?,?)',(name,cat,sku,desc,img,cost,price,2,feat)); pid=cur.lastrowid
            for size,color,q in vars: c.execute('INSERT INTO variants(product_id,size,color,qty) VALUES(?,?,?,?)',(pid,size,color,q))
    c.commit(); c.close()
init_db()

class Login(BaseModel): email:str; password:str
class ProductCreate(BaseModel): name:str; category:str; sku:str; description:str=''; cost_price:float=0; selling_price:float; low_stock_threshold:int=2
class VariantCreate(BaseModel): product_id:int; size:str; color:str; qty:int=0
class StockMove(BaseModel): variant_id:int; qty:int=Field(gt=0); reason:str='Stock receipt'; reference:str=''
class CustomerCreate(BaseModel): name:str; phone:str=''; email:str=''; notes:str=''
class SaleLine(BaseModel): variant_id:int; qty:int=Field(gt=0)
class SaleCreate(BaseModel): customer_name:str='Guest Customer'; items:List[SaleLine]; discount:float=0; payment_method:str='POS'; payment_status:str='PAID'
class StoreOrder(BaseModel):
    customer_name:str; email:str=''; phone:str; address:str; city:str='Abuja'; state:str='FCT'; delivery_method:str='Delivery'; payment_method:str='Paystack'; items:List[SaleLine]
class OrderStatus(BaseModel): status:str

def current_user(authorization:Optional[str]=Header(default=None)):
    if not authorization or not authorization.startswith('Bearer '): raise HTTPException(401,'Login required')
    token=authorization.split(' ',1)[1]; c=conn(); r=c.execute('SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND u.active=1',(token,)).fetchone(); c.close()
    if not r: raise HTTPException(401,'Invalid session')
    return dict(r)
def owner_only(u=Depends(current_user)):
    if u['role']!='owner': raise HTTPException(403,'Owner access required')
    return u

def store_products_rows(c):
    return c.execute('''SELECT p.id product_id,p.name,p.category,p.sku,p.description,p.image,p.selling_price,p.featured,p.low_stock_threshold,
      v.id variant_id,v.size,v.color,v.qty,v.reserved_qty,(v.qty-v.reserved_qty) available
      FROM products p JOIN variants v ON v.product_id=p.id WHERE p.active=1 ORDER BY p.featured DESC,p.id DESC,v.id''').fetchall()

@app.post('/api/login')
def login(d:Login):
    c=conn(); u=c.execute('SELECT * FROM users WHERE lower(email)=lower(?) AND password_hash=? AND active=1',(d.email,hash_pw(d.password))).fetchone()
    if not u: c.close(); raise HTTPException(401,'Invalid email or password')
    token=secrets.token_urlsafe(24); c.execute('INSERT INTO sessions VALUES(?,?,?)',(token,u['id'],now())); audit(c,u['id'],'LOGIN','session'); c.commit(); c.close(); return {'token':token,'user':dict(u)}
@app.get('/api/me')
def me(u=Depends(current_user)): return {k:u[k] for k in ('id','name','email','role')}

@app.get('/api/store/products')
def store_products():
    c=conn(); rows=store_products_rows(c); c.close(); grouped={}
    for r in rows:
        d=dict(r); pid=d.pop('product_id'); v={k:d.pop(k) for k in ['variant_id','size','color','qty','reserved_qty','available']}
        if pid not in grouped: grouped[pid]={'id':pid,**d,'variants':[]}
        grouped[pid]['variants'].append(v)
    return list(grouped.values())

@app.post('/api/store/orders')
def create_store_order(d:StoreOrder):
    c=conn(); subtotal=0; prepared=[]
    for line in d.items:
        r=c.execute('SELECT v.*,p.name,p.selling_price FROM variants v JOIN products p ON p.id=v.product_id WHERE v.id=? AND p.active=1',(line.variant_id,)).fetchone()
        if not r: c.close(); raise HTTPException(404,'Product variant not found')
        available=r['qty']-r['reserved_qty']
        if available<line.qty: c.close(); raise HTTPException(400,f"Only {available} of {r['name']} {r['size']}/{r['color']} available")
        lt=r['selling_price']*line.qty; subtotal+=lt; prepared.append((r,line,lt))
    fee=0 if d.delivery_method.lower()=='pickup' else (2500 if d.city.lower()=='abuja' else 5000)
    total=subtotal+fee; order_no='SP-ON-'+datetime.utcnow().strftime('%y%m%d%H%M%S%f')[-12:]; access=secrets.token_urlsafe(14)
    cur=c.execute('''INSERT INTO orders(order_no,access_token,customer_name,email,phone,address,city,state,delivery_method,delivery_fee,subtotal,total,payment_method,payment_status,order_status,created_at)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(order_no,access,d.customer_name,d.email,d.phone,d.address,d.city,d.state,d.delivery_method,fee,subtotal,total,d.payment_method,'AWAITING_PAYMENT','AWAITING_PAYMENT',now())); oid=cur.lastrowid
    for r,line,lt in prepared:
        c.execute('INSERT INTO order_items(order_id,variant_id,product_name,size,color,qty,unit_price,line_total) VALUES(?,?,?,?,?,?,?,?)',(oid,r['id'],r['name'],r['size'],r['color'],line.qty,r['selling_price'],lt))
        c.execute('UPDATE variants SET reserved_qty=reserved_qty+? WHERE id=?',(line.qty,r['id']))
    c.commit(); c.close(); return {'order_id':oid,'order_no':order_no,'access_token':access,'subtotal':subtotal,'delivery_fee':fee,'total':total,'payment_status':'AWAITING_PAYMENT','payment_mode':'DEMO_PAYSTACK','message':'Paystack production keys are not stored in this demo. Use the demo payment confirmation button to test the complete workflow.'}

@app.post('/api/store/orders/{order_id}/demo-pay')
def demo_pay(order_id:int, token:str):
    c=conn(); o=c.execute('SELECT * FROM orders WHERE id=? AND access_token=?',(order_id,token)).fetchone()
    if not o: c.close(); raise HTTPException(404,'Order not found')
    if o['payment_status']=='PAID': c.close(); return {'ok':True,'status':'PAID','order_status':o['order_status']}
    items=c.execute('SELECT * FROM order_items WHERE order_id=?',(order_id,)).fetchall()
    for i in items:
        v=c.execute('SELECT * FROM variants WHERE id=?',(i['variant_id'],)).fetchone()
        if v['qty']<i['qty']: c.close(); raise HTTPException(409,'Stock changed before payment. Staff intervention required.')
        c.execute('UPDATE variants SET qty=qty-?, reserved_qty=max(reserved_qty-?,0) WHERE id=?',(i['qty'],i['qty'],i['variant_id']))
        c.execute('INSERT INTO stock_movements(variant_id,movement_type,qty,reason,reference,user_id,created_at) VALUES(?,?,?,?,?,?,?)',(i['variant_id'],'OUT',i['qty'],'Online paid order',o['order_no'],None,now()))
    c.execute("UPDATE orders SET payment_status='PAID',order_status='PAID_PROCESSING',paid_at=? WHERE id=?",(now(),order_id)); c.commit(); c.close(); return {'ok':True,'status':'PAID','order_status':'PAID_PROCESSING'}

@app.get('/api/store/orders/{order_id}')
def public_order(order_id:int, token:str):
    c=conn(); o=c.execute('SELECT * FROM orders WHERE id=? AND access_token=?',(order_id,token)).fetchone()
    if not o: c.close(); raise HTTPException(404,'Order not found')
    items=[dict(x) for x in c.execute('SELECT * FROM order_items WHERE order_id=?',(order_id,)).fetchall()]; c.close(); return {'order':dict(o),'items':items}

@app.get('/api/dashboard')
def dashboard(u=Depends(current_user)):
    c=conn(); today=datetime.utcnow().date().isoformat();
    walk=c.execute("SELECT count(*) n,coalesce(sum(total),0) r FROM sales WHERE substr(created_at,1,10)=?",(today,)).fetchone()
    online=c.execute("SELECT count(*) n,coalesce(sum(total),0) r FROM orders WHERE substr(created_at,1,10)=? AND payment_status='PAID'",(today,)).fetchone()
    low=c.execute('SELECT count(*) n FROM variants v JOIN products p ON p.id=v.product_id WHERE (v.qty-v.reserved_qty)<=p.low_stock_threshold').fetchone()['n']
    mine=c.execute("SELECT count(*) n,coalesce(sum(total),0) r FROM sales WHERE substr(created_at,1,10)=? AND user_id=?",(today,u['id'])).fetchone()
    out={'today_revenue':walk['r']+online['r'],'walk_in_sales':walk['n'],'online_orders':online['n'],'low_stock':low,'my_sales':mine['n'],'my_revenue':mine['r']}
    if u['role']=='owner': out['inventory_cost_value']=c.execute('SELECT coalesce(sum((v.qty-v.reserved_qty)*p.cost_price),0) v FROM variants v JOIN products p ON p.id=v.product_id').fetchone()['v']
    c.close(); return out

@app.get('/api/products')
def products(u=Depends(current_user)):
    c=conn(); rows=[dict(r) for r in c.execute('''SELECT p.id product_id,p.name,p.category,p.sku,p.selling_price,p.cost_price,p.low_stock_threshold,v.id variant_id,v.size,v.color,v.qty,v.reserved_qty,(v.qty-v.reserved_qty) available FROM products p LEFT JOIN variants v ON v.product_id=p.id WHERE p.active=1 ORDER BY p.name,v.id''')]; c.close()
    if u['role']!='owner':
        for x in rows: x.pop('cost_price',None)
    return rows
@app.post('/api/stock/in')
def stock_in(d:StockMove,u=Depends(current_user)):
    c=conn(); v=c.execute('SELECT * FROM variants WHERE id=?',(d.variant_id,)).fetchone()
    if not v: c.close(); raise HTTPException(404,'Variant not found')
    c.execute('UPDATE variants SET qty=qty+? WHERE id=?',(d.qty,d.variant_id)); c.execute('INSERT INTO stock_movements(variant_id,movement_type,qty,reason,reference,user_id,created_at) VALUES(?,?,?,?,?,?,?)',(d.variant_id,'IN',d.qty,d.reason,d.reference,u['id'],now())); audit(c,u['id'],'STOCK_IN','variant',d.variant_id,f'+{d.qty}; {d.reference}'); c.commit(); c.close(); return {'ok':True}
@app.post('/api/customers')
def add_customer(d:CustomerCreate,u=Depends(current_user)):
    c=conn(); cur=c.execute('INSERT INTO customers(name,phone,email,notes,created_at) VALUES(?,?,?,?,?)',(d.name,d.phone,d.email,d.notes,now())); audit(c,u['id'],'CREATE','customer',cur.lastrowid,d.name); c.commit(); c.close(); return {'id':cur.lastrowid}
@app.get('/api/customers')
def customers(u=Depends(current_user)):
    c=conn(); rows=[dict(x) for x in c.execute('SELECT * FROM customers ORDER BY id DESC LIMIT 200')]; c.close(); return rows
@app.post('/api/sales/walk-in')
def walkin(d:SaleCreate,u=Depends(current_user)):
    c=conn(); subtotal=0; prepared=[]
    for line in d.items:
        r=c.execute('SELECT v.*,p.name,p.selling_price FROM variants v JOIN products p ON p.id=v.product_id WHERE v.id=?',(line.variant_id,)).fetchone();
        if not r: c.close(); raise HTTPException(404,'Variant not found')
        av=r['qty']-r['reserved_qty']
        if av<line.qty: c.close(); raise HTTPException(400,f"Only {av} available for {r['name']} {r['size']}/{r['color']}")
        lt=r['selling_price']*line.qty; subtotal+=lt; prepared.append((r,line,lt))
    discount=max(0,min(d.discount,subtotal)); total=subtotal-discount; sale_no='SP-WI-'+datetime.utcnow().strftime('%y%m%d%H%M%S%f')[-12:]
    cur=c.execute('INSERT INTO sales(sale_no,channel,customer_name,user_id,subtotal,discount,total,payment_method,payment_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(sale_no,'WALK_IN',d.customer_name,u['id'],subtotal,discount,total,d.payment_method,d.payment_status,now())); sid=cur.lastrowid
    for r,line,lt in prepared:
        c.execute('INSERT INTO sale_items(sale_id,variant_id,product_name,size,color,qty,unit_price,line_total) VALUES(?,?,?,?,?,?,?,?)',(sid,r['id'],r['name'],r['size'],r['color'],line.qty,r['selling_price'],lt)); c.execute('UPDATE variants SET qty=qty-? WHERE id=?',(line.qty,r['id'])); c.execute('INSERT INTO stock_movements(variant_id,movement_type,qty,reason,reference,user_id,created_at) VALUES(?,?,?,?,?,?,?)',(r['id'],'OUT',line.qty,'Walk-in sale',sale_no,u['id'],now()))
    audit(c,u['id'],'CREATE','walk_in_sale',sid,f'{sale_no}; total={total}; {d.payment_method}'); c.commit(); c.close(); return {'sale_id':sid,'sale_no':sale_no,'total':total}
@app.get('/api/sales')
def sales(u=Depends(current_user)):
    c=conn(); rows=[dict(x) for x in c.execute('SELECT s.*,u.name sales_rep FROM sales s LEFT JOIN users u ON u.id=s.user_id ORDER BY s.id DESC LIMIT 200')]; c.close(); return rows
@app.get('/api/sales/{sale_id}/receipt')
def receipt(sale_id:int,u=Depends(current_user)):
    c=conn(); s=c.execute('SELECT s.*,u.name sales_rep FROM sales s LEFT JOIN users u ON u.id=s.user_id WHERE s.id=?',(sale_id,)).fetchone()
    if not s: c.close(); raise HTTPException(404,'Sale not found')
    items=[dict(x) for x in c.execute('SELECT * FROM sale_items WHERE sale_id=?',(sale_id,))]; c.close(); return {'sale':dict(s),'items':items}
@app.get('/api/orders')
def orders(u=Depends(current_user)):
    c=conn(); rows=[dict(x) for x in c.execute('SELECT * FROM orders ORDER BY id DESC LIMIT 200')]; c.close(); return rows
@app.patch('/api/orders/{order_id}/status')
def order_status(order_id:int,d:OrderStatus,u=Depends(current_user)):
    allowed=['PAID_PROCESSING','READY_FOR_DISPATCH','DISPATCHED','DELIVERED','CANCELLED']
    if d.status not in allowed: raise HTTPException(400,'Invalid status')
    c=conn(); o=c.execute('SELECT * FROM orders WHERE id=?',(order_id,)).fetchone()
    if not o: c.close(); raise HTTPException(404,'Order not found')
    c.execute('UPDATE orders SET order_status=? WHERE id=?',(d.status,order_id)); audit(c,u['id'],'UPDATE','online_order',order_id,d.status); c.commit(); c.close(); return {'ok':True}
@app.get('/api/reports/sales')
def reports(u=Depends(owner_only)):
    c=conn(); reps=[dict(x) for x in c.execute('SELECT u.name sales_rep,count(s.id) sales_count,coalesce(sum(s.total),0) revenue FROM users u LEFT JOIN sales s ON s.user_id=u.id GROUP BY u.id ORDER BY revenue DESC')]; channels=[dict(x) for x in c.execute("SELECT 'Walk-in' channel,count(*) sales_count,coalesce(sum(total),0) revenue FROM sales UNION ALL SELECT 'Online',count(*),coalesce(sum(total),0) FROM orders WHERE payment_status='PAID'")]; c.close(); return {'by_sales_rep':reps,'by_channel':channels}
@app.get('/api/audit')
def audit_logs(u=Depends(owner_only)):
    c=conn(); rows=[dict(x) for x in c.execute('SELECT a.*,u.name user_name FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 300')]; c.close(); return rows

app.mount('/static',StaticFiles(directory=os.path.join(BASE,'static')),name='static')
@app.get('/')
def home(): return FileResponse(os.path.join(BASE,'static','index.html'))
@app.get('/admin')
def admin(): return FileResponse(os.path.join(BASE,'static','admin.html'))
