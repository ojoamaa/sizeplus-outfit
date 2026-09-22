from fastapi import FastAPI, HTTPException, Depends, Header, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
import sqlite3, os, hashlib, secrets, shutil
from database import conn, DatabaseIntegrityError
import cloudinary
import cloudinary.uploader
import httpx

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True,
)

BASE=os.path.dirname(__file__); DB=os.path.join(BASE,'sizeplus.db'); UPLOADS=os.path.join(BASE,'uploads','products')
os.makedirs(UPLOADS,exist_ok=True)
app=FastAPI(title='Sizeplus Outfit Retail Management + E-commerce',version='0.7.0-development')
app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_credentials=True,allow_methods=['*'],allow_headers=['*'])

def now(): return datetime.utcnow().isoformat()
def hash_pw(p): return hashlib.sha256(p.encode()).hexdigest()
def audit(c,user_id,action,entity,entity_id=None,detail=''):
    c.execute('INSERT INTO audit_logs(user_id,action,entity,entity_id,detail,created_at) VALUES(?,?,?,?,?,?)',(user_id,action,entity,str(entity_id or ''),detail,now()))

def init_db():
    c=conn(); c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,email TEXT UNIQUE,password_hash TEXT,role TEXT,active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id INTEGER,created_at TEXT);
    CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,category TEXT,sku TEXT UNIQUE,description TEXT DEFAULT '',image TEXT DEFAULT '',cost_price REAL DEFAULT 0,selling_price REAL,low_stock_threshold INTEGER DEFAULT 2,featured INTEGER DEFAULT 0,active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS variants(id INTEGER PRIMARY KEY AUTOINCREMENT,product_id INTEGER,size TEXT,color TEXT,barcode TEXT,qty INTEGER DEFAULT 0,reserved_qty INTEGER DEFAULT 0,UNIQUE(product_id,size,color));
    CREATE TABLE IF NOT EXISTS product_images(id INTEGER PRIMARY KEY AUTOINCREMENT,product_id INTEGER,image_url TEXT,is_primary INTEGER DEFAULT 0,created_at TEXT);
    CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,phone TEXT,email TEXT,notes TEXT,created_at TEXT);
    CREATE TABLE IF NOT EXISTS sales(id INTEGER PRIMARY KEY AUTOINCREMENT,sale_no TEXT UNIQUE,channel TEXT,customer_id INTEGER,customer_name TEXT,user_id INTEGER,subtotal REAL,discount REAL,total REAL,payment_method TEXT,payment_status TEXT,created_at TEXT);
    CREATE TABLE IF NOT EXISTS sale_items(id INTEGER PRIMARY KEY AUTOINCREMENT,sale_id INTEGER,variant_id INTEGER,product_name TEXT,size TEXT,color TEXT,qty INTEGER,unit_price REAL,line_total REAL);
    CREATE TABLE IF NOT EXISTS stock_movements(id INTEGER PRIMARY KEY AUTOINCREMENT,variant_id INTEGER,movement_type TEXT,qty INTEGER,reason TEXT,reference TEXT,user_id INTEGER,created_at TEXT);
    CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT,order_no TEXT UNIQUE,access_token TEXT UNIQUE,customer_name TEXT,email TEXT,phone TEXT,address TEXT,city TEXT,state TEXT,delivery_method TEXT,delivery_fee REAL,subtotal REAL,total REAL,payment_method TEXT,payment_status TEXT,order_status TEXT,created_at TEXT,paid_at TEXT);
    CREATE TABLE IF NOT EXISTS order_items(id INTEGER PRIMARY KEY AUTOINCREMENT,order_id INTEGER,variant_id INTEGER,product_name TEXT,size TEXT,color TEXT,qty INTEGER,unit_price REAL,line_total REAL);
    CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,action TEXT,entity TEXT,entity_id TEXT,detail TEXT,created_at TEXT);
    '''); c.commit(); c.close()
init_db()

class Login(BaseModel): email:str; password:str
class ProductCreate(BaseModel):
    name:str; category:str; sku:str; description:str=''; cost_price:float=0; selling_price:float; low_stock_threshold:int=2; featured:bool=False; active:bool=True
class ProductUpdate(BaseModel):
    name:Optional[str]=None; category:Optional[str]=None; description:Optional[str]=None; cost_price:Optional[float]=None; selling_price:Optional[float]=None; low_stock_threshold:Optional[int]=None; featured:Optional[bool]=None; active:Optional[bool]=None
class VariantCreate(BaseModel): product_id:int; size:str; color:str; qty:int=0; barcode:str=''
class StockMove(BaseModel): variant_id:int; qty:int=Field(gt=0); reason:str='Stock receipt'; reference:str=''
class StockAdjust(BaseModel): variant_id:int; quantity_change:int; reason:str; reference:str=''
class CustomerCreate(BaseModel): name:str; phone:str=''; email:str=''; notes:str=''
class SaleLine(BaseModel): variant_id:int; qty:int=Field(gt=0)
class SaleCreate(BaseModel): customer_name:str='Guest Customer'; items:List[SaleLine]; discount:float=0; payment_method:str='POS'; payment_status:str='PAID'
class StoreOrder(BaseModel): customer_name:str; email:str=''; phone:str; address:str; city:str='Abuja'; state:str='FCT'; delivery_method:str='Delivery'; payment_method:str='Paystack'; items:List[SaleLine]
class OrderStatus(BaseModel): status:str
class StaffCreate(BaseModel): name:str; email:str; password:str; role:str='sales_rep'

def current_user(authorization:Optional[str]=Header(default=None)):
    if not authorization or not authorization.startswith('Bearer '): raise HTTPException(401,'Login required')
    token=authorization.split(' ',1)[1]; c=conn(); r=c.execute('SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND u.active=1',(token,)).fetchone(); c.close()
    if not r: raise HTTPException(401,'Invalid session')
    return dict(r)
def owner_only(u=Depends(current_user)):
    if u['role']!='owner': raise HTTPException(403,'Owner access required')
    return u

def grouped_store_products(c):
    rows=c.execute('''SELECT p.*,v.id variant_id,v.size,v.color,v.qty,v.reserved_qty,(v.qty-v.reserved_qty) available
    FROM products p JOIN variants v ON v.product_id=p.id WHERE p.active=1 ORDER BY p.featured DESC,p.id DESC,v.id''').fetchall(); grouped={}
    for rr in rows:
        r=dict(rr); pid=r['id'];
        if pid not in grouped:
            imgs=[x['image_url'] for x in c.execute('SELECT image_url FROM product_images WHERE product_id=? ORDER BY is_primary DESC,id',(pid,)).fetchall()]
            grouped[pid]={'id':pid,'name':r['name'],'category':r['category'],'sku':r['sku'],'description':r['description'],'image':imgs[0] if imgs else r['image'],'images':imgs,'selling_price':r['selling_price'],'featured':r['featured'],'low_stock_threshold':r['low_stock_threshold'],'variants':[]}
        grouped[pid]['variants'].append({'variant_id':r['variant_id'],'size':r['size'],'color':r['color'],'qty':r['qty'],'reserved_qty':r['reserved_qty'],'available':r['available']})
    return list(grouped.values())

@app.post('/api/login')
def login(d:Login):
    c=conn(); u=c.execute('SELECT * FROM users WHERE lower(email)=lower(?) AND password_hash=? AND active=1',(d.email,hash_pw(d.password))).fetchone()
    if not u: c.close(); raise HTTPException(401,'Invalid email or password')
    token=secrets.token_urlsafe(24); c.execute('INSERT INTO sessions VALUES(?,?,?)',(token,u['id'],now())); audit(c,u['id'],'LOGIN','session'); c.commit(); c.close(); return {'token':token,'user':dict(u)}
@app.get('/api/me')
def me(u=Depends(current_user)): return {k:u[k] for k in ('id','name','email','role')}

@app.get('/api/store/products')
def store_products():
    c=conn(); d=grouped_store_products(c); c.close(); return d
# Nigeria-wide delivery pricing.
# These are configurable application rates and can later be replaced
# by live quotes from an approved logistics provider.
DELIVERY_RATES = {
    # FCT / local
    "FCT": 2500,

    # North Central
    "Benue": 4500,
    "Kogi": 4000,
    "Kwara": 5000,
    "Nasarawa": 3500,
    "Niger": 4000,
    "Plateau": 4500,

    # North West
    "Jigawa": 5500,
    "Kaduna": 4500,
    "Kano": 5500,
    "Katsina": 6000,
    "Kebbi": 6500,
    "Sokoto": 6500,
    "Zamfara": 6000,

    # North East
    "Adamawa": 6500,
    "Bauchi": 5500,
    "Borno": 7000,
    "Gombe": 6000,
    "Taraba": 6500,
    "Yobe": 7000,

    # South West
    "Ekiti": 5500,
    "Lagos": 5500,
    "Ogun": 5500,
    "Ondo": 5500,
    "Osun": 5500,
    "Oyo": 5500,

    # South East
    "Abia": 6000,
    "Anambra": 5500,
    "Ebonyi": 5500,
    "Enugu": 5500,
    "Imo": 6000,

    # South South
    "Akwa Ibom": 6500,
    "Bayelsa": 6500,
    "Cross River": 7000,
    "Delta": 6000,
    "Edo": 5500,
    "Rivers": 6500,
}


def calculate_delivery_fee(state, delivery_method):
    if delivery_method.strip().lower() == "pickup":
        return 0

    state_name = state.strip()

    for configured_state, fee in DELIVERY_RATES.items():
        if configured_state.lower() == state_name.lower():
            return fee

    raise HTTPException(
        status_code=400,
        detail="Please select a valid Nigerian delivery state."
    )
@app.post('/api/store/orders')
def create_store_order(d:StoreOrder):
    c=conn(); subtotal=0; prepared=[]
    for line in d.items:
        r=c.execute('SELECT v.*,p.name,p.selling_price FROM variants v JOIN products p ON p.id=v.product_id WHERE v.id=? AND p.active=1',(line.variant_id,)).fetchone()
        if not r: c.close(); raise HTTPException(404,'Product variant not found')
        av=r['qty']-r['reserved_qty']
        if av<line.qty: c.close(); raise HTTPException(400,f"Only {av} of {r['name']} available")
        lt=r['selling_price']*line.qty; subtotal+=lt; prepared.append((r,line,lt))
    fee=calculate_delivery_fee(d.state,d.delivery_method); total=subtotal+fee
    order_no='SP-ON-'+datetime.utcnow().strftime('%y%m%d%H%M%S%f')[-12:]; access=secrets.token_urlsafe(14)
    cur=c.execute('''INSERT INTO orders(order_no,access_token,customer_name,email,phone,address,city,state,delivery_method,delivery_fee,subtotal,total,payment_method,payment_status,order_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(order_no,access,d.customer_name,d.email,d.phone,d.address,d.city,d.state,d.delivery_method,fee,subtotal,total,d.payment_method,'AWAITING_PAYMENT','AWAITING_PAYMENT',now())); oid=cur.lastrowid
    for r,line,lt in prepared:
        c.execute('INSERT INTO order_items(order_id,variant_id,product_name,size,color,qty,unit_price,line_total) VALUES(?,?,?,?,?,?,?,?)',(oid,r['id'],r['name'],r['size'],r['color'],line.qty,r['selling_price'],lt)); c.execute('UPDATE variants SET reserved_qty=reserved_qty+? WHERE id=?',(line.qty,r['id']))
    c.commit()
    c.close()
    return {
        'order_id': oid,
        'order_no': order_no,
        'token': access,
        'subtotal': subtotal,
        'delivery_fee': fee,
        'total': total
    }
@app.post('/api/store/orders/{order_id}/paystack/initialize')
def initialize_paystack(order_id: int, token: str):
    secret_key = os.getenv('PAYSTACK_SECRET_KEY')

    if not secret_key:
        raise HTTPException(
            status_code=500,
            detail='Paystack is not configured.'
        )

    c = conn()
    order = c.execute(
        'SELECT * FROM orders WHERE id=? AND access_token=?',
        (order_id, token)
    ).fetchone()

    if not order:
        c.close()
        raise HTTPException(status_code=404, detail='Order not found')

    if order['payment_status'] == 'PAID':
        c.close()
        raise HTTPException(status_code=409, detail='Order is already paid')

    if not order['email']:
        c.close()
        raise HTTPException(
            status_code=400,
            detail='Customer email is required for Paystack payment.'
        )

    amount_kobo = int(round(float(order['total']) * 100))

    payload = {
    'email': order['email'],
    'amount': amount_kobo,
    'reference': order['order_no'],
    'callback_url': 'https://sizeplusoutfit.com/api/store/paystack/callback',
    'metadata': {
        'order_id': order['id'],
        'order_no': order['order_no']
    }
}
    headers = {
        'Authorization': f'Bearer {secret_key}',
        'Content-Type': 'application/json'
    }

    try:
        response = httpx.post(
            'https://api.paystack.co/transaction/initialize',
            json=payload,
            headers=headers,
            timeout=20.0
        )
    except httpx.RequestError:
        c.close()
        raise HTTPException(
            status_code=502,
            detail='Could not connect to Paystack.'
        )

    c.close()

    try:
        result = response.json()
    except ValueError:
        raise HTTPException(
            status_code=502,
            detail='Invalid response received from Paystack.'
        )

    if response.status_code >= 400 or not result.get('status'):
        raise HTTPException(
            status_code=502,
            detail=result.get('message', 'Paystack initialization failed.')
        )

    data = result['data']

    return {
        'authorization_url': data['authorization_url'],
        'access_code': data['access_code'],
        'reference': data['reference']
    }
@app.get('/api/store/paystack/callback')
def paystack_callback(reference: str):
    secret_key = os.getenv('PAYSTACK_SECRET_KEY')

    if not secret_key:
        raise HTTPException(
            status_code=500,
            detail='Paystack is not configured.'
        )

    headers = {
        'Authorization': f'Bearer {secret_key}'
    }

    try:
        response = httpx.get(
            f'https://api.paystack.co/transaction/verify/{reference}',
            headers=headers,
            timeout=20.0
        )
    except httpx.RequestError:
        raise HTTPException(
            status_code=502,
            detail='Could not verify payment with Paystack.'
        )

    try:
        result = response.json()
    except ValueError:
        raise HTTPException(
            status_code=502,
            detail='Invalid response received from Paystack.'
        )

    if response.status_code >= 400 or not result.get('status'):
        raise HTTPException(
            status_code=400,
            detail='Paystack payment verification failed.'
        )

    data = result.get('data', {})

    if data.get('status') != 'success':
        raise HTTPException(
            status_code=400,
            detail='Payment was not successful.'
        )

    c = conn()

    order = c.execute(
        'SELECT * FROM orders WHERE order_no=?',
        (reference,)
    ).fetchone()

    if not order:
        c.close()
        raise HTTPException(
            status_code=404,
            detail='Order not found.'
        )

    if order['payment_status'] == 'PAID':
        c.close()
        return {
            'ok': True,
            'status': 'PAID',
            'order_no': order['order_no']
        }

    expected_amount = int(round(float(order['total']) * 100))

    if int(data.get('amount', 0)) != expected_amount:
        c.close()
        raise HTTPException(
            status_code=400,
            detail='Payment amount does not match this order.'
        )

    if data.get('reference') != order['order_no']:
        c.close()
        raise HTTPException(
            status_code=400,
            detail='Payment reference does not match this order.'
        )

    items = c.execute(
        'SELECT * FROM order_items WHERE order_id=?',
        (order['id'],)
    ).fetchall()

    for item in items:
        variant = c.execute(
            'SELECT * FROM variants WHERE id=?',
            (item['variant_id'],)
        ).fetchone()

        if not variant or variant['qty'] < item['qty']:
            c.close()
            raise HTTPException(
                status_code=409,
                detail='Stock changed before payment confirmation.'
            )

    for item in items:
        c.execute(
            '''
            UPDATE variants
            SET qty=qty-?,
                reserved_qty=GREATEST(reserved_qty-?,0)
            WHERE id=?
            ''',
            (item['qty'], item['qty'], item['variant_id'])
        )

        c.execute(
            '''
            INSERT INTO stock_movements(
                variant_id,
                movement_type,
                qty,
                reason,
                reference,
                user_id,
                created_at
            )
            VALUES(?,?,?,?,?,?,?)
            ''',
            (
                item['variant_id'],
                'OUT',
                item['qty'],
                'Online Paystack order',
                order['order_no'],
                None,
                now()
            )
        )

    c.execute(
        '''
        UPDATE orders
        SET payment_status='PAID',
            order_status='PAID_PROCESSING',
            paid_at=?
        WHERE id=?
        ''',
        (now(), order['id'])
    )

    c.commit()
    c.close()

    return {
        'ok': True,
        'status': 'PAID',
        'order_no': order['order_no']
    }
@app.post('/api/store/orders/{order_id}/demo-pay')
def demo_pay(order_id:int, token:str):
    c=conn(); o=c.execute('SELECT * FROM orders WHERE id=? AND access_token=?',(order_id,token)).fetchone()
    if not o: c.close(); raise HTTPException(404,'Order not found')
    if o['payment_status']=='PAID': c.close(); return {'ok':True,'status':'PAID'}
    items=c.execute('SELECT * FROM order_items WHERE order_id=?',(order_id,)).fetchall()
    for i in items:
        v=c.execute('SELECT * FROM variants WHERE id=?',(i['variant_id'],)).fetchone()
        if v['qty']<i['qty']: c.close(); raise HTTPException(409,'Stock changed before payment')
        c.execute('UPDATE variants SET qty=qty-?,reserved_qty=GREATEST(reserved_qty-?,0) WHERE id=?',(i['qty'],i['qty'],i['variant_id'])); c.execute('INSERT INTO stock_movements(variant_id,movement_type,qty,reason,reference,user_id,created_at) VALUES(?,?,?,?,?,?,?)',(i['variant_id'],'OUT',i['qty'],'Online paid order',o['order_no'],None,now()))
    c.execute("UPDATE orders SET payment_status='PAID',order_status='PAID_PROCESSING',paid_at=? WHERE id=?",(now(),order_id)); c.commit(); c.close(); return {'ok':True,'status':'PAID'}

@app.get('/api/dashboard')
def dashboard(u=Depends(current_user)):
    c=conn(); today=datetime.utcnow().date().isoformat(); walk=c.execute("SELECT count(*) n,coalesce(sum(total),0) r FROM sales WHERE substr(created_at,1,10)=?",(today,)).fetchone(); online=c.execute("SELECT count(*) n,coalesce(sum(total),0) r FROM orders WHERE substr(created_at,1,10)=? AND payment_status='PAID'",(today,)).fetchone(); mine=c.execute("SELECT count(*) n,coalesce(sum(total),0) r FROM sales WHERE substr(created_at,1,10)=? AND user_id=?",(today,u['id'])).fetchone(); low=c.execute('SELECT count(*) n FROM variants v JOIN products p ON p.id=v.product_id WHERE (v.qty-v.reserved_qty)<=p.low_stock_threshold AND (v.qty-v.reserved_qty)>0').fetchone()['n']; outstock=c.execute('SELECT count(*) n FROM variants v JOIN products p ON p.id=v.product_id WHERE (v.qty-v.reserved_qty)<=0 AND p.active=1').fetchone()['n']; pending=c.execute("SELECT count(*) n FROM orders WHERE payment_status='PAID' AND order_status NOT IN ('DELIVERED','CANCELLED')").fetchone()['n']
    out={'today_revenue':walk['r']+online['r'],'walk_in_sales':walk['n'],'online_orders':online['n'],'low_stock':low,'out_of_stock':outstock,'pending_deliveries':pending,'my_sales':mine['n'],'my_revenue':mine['r']}
    if u['role']=='owner': out['inventory_cost_value']=c.execute('SELECT coalesce(sum((v.qty-v.reserved_qty)*p.cost_price),0) v FROM variants v JOIN products p ON p.id=v.product_id').fetchone()['v']
    c.close(); return out

@app.get('/api/products')
def products(u=Depends(current_user)):
    c=conn(); rows=[dict(r) for r in c.execute('''SELECT p.id product_id,p.name,p.category,p.sku,p.description,p.image,p.selling_price,p.cost_price,p.low_stock_threshold,p.featured,p.active,v.id variant_id,v.size,v.color,v.barcode,v.qty,v.reserved_qty,(v.qty-v.reserved_qty) available FROM products p LEFT JOIN variants v ON v.product_id=p.id ORDER BY p.name,v.id''')]; c.close()
    if u['role']!='owner':
        for x in rows: x.pop('cost_price',None)
    return rows
@app.post('/api/products')
def create_product(d:ProductCreate,u=Depends(owner_only)):
    c=conn()
    try: cur=c.execute('INSERT INTO products(name,category,sku,description,cost_price,selling_price,low_stock_threshold,featured,active) VALUES(?,?,?,?,?,?,?,?,?)',(d.name,d.category,d.sku,d.description,d.cost_price,d.selling_price,d.low_stock_threshold,int(d.featured),int(d.active)))
    except DatabaseIntegrityError: c.close(); raise HTTPException(400,'SKU already exists')
    audit(c,u['id'],'CREATE','product',cur.lastrowid,d.sku); c.commit(); pid=cur.lastrowid; c.close(); return {'id':pid}
@app.patch('/api/products/{pid}')
def update_product(pid:int,d:ProductUpdate,u=Depends(owner_only)):
    fields=[]; vals=[]
    for k,v in d.model_dump(exclude_none=True).items():
        fields.append(k+'=?'); vals.append(int(v) if isinstance(v,bool) else v)
    if not fields: return {'ok':True}
    vals.append(pid); c=conn(); c.execute('UPDATE products SET '+','.join(fields)+' WHERE id=?',vals); audit(c,u['id'],'UPDATE','product',pid,','.join(fields)); c.commit(); c.close(); return {'ok':True}
@app.post('/api/variants')
def add_variant(d:VariantCreate,u=Depends(owner_only)):
    c=conn()
    try: cur=c.execute('INSERT INTO variants(product_id,size,color,qty,barcode) VALUES(?,?,?,?,?)',(d.product_id,d.size,d.color,d.qty,d.barcode))
    except DatabaseIntegrityError: c.close(); raise HTTPException(400,'This size/colour variant already exists')
    if d.qty: c.execute('INSERT INTO stock_movements(variant_id,movement_type,qty,reason,reference,user_id,created_at) VALUES(?,?,?,?,?,?,?)',(cur.lastrowid,'IN',d.qty,'Opening stock','PRODUCT_CREATE',u['id'],now()))
    audit(c,u['id'],'CREATE','variant',cur.lastrowid,f'{d.size}/{d.color}; opening={d.qty}'); c.commit(); c.close(); return {'id':cur.lastrowid}
@app.post('/api/products/{pid}/images')
async def upload_image(
    pid: int,
    image: UploadFile = File(...),
    u=Depends(owner_only)
):
    ext = os.path.splitext(image.filename or '')[1].lower()

    if ext not in ['.jpg', '.jpeg', '.png', '.webp']:
        raise HTTPException(400, 'Use JPG, PNG or WEBP images')

    try:
        result = cloudinary.uploader.upload(
            image.file,
            folder='sizeplus-outfit/products',
            public_id=f'product_{pid}_{secrets.token_hex(6)}',
            resource_type='image',
            overwrite=False
        )

        url = result['secure_url']

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f'Image upload failed: {str(e)}'
        )

    c = conn()

    exists = c.execute(
        'SELECT 1 FROM product_images WHERE product_id=?',
        (pid,)
    ).fetchone()

    c.execute(
        '''INSERT INTO product_images
        (product_id,image_url,is_primary,created_at)
        VALUES(?,?,?,?)''',
        (pid, url, 0 if exists else 1, now())
    )

    if not exists:
        c.execute(
            'UPDATE products SET image=? WHERE id=?',
            (url, pid)
        )

    audit(
        c,
        u['id'],
        'UPLOAD_IMAGE',
        'product',
        pid,
        url
    )

    c.commit()
    c.close()

    return {'image_url': url}
@app.get('/api/products/{pid}/images')
def product_images(pid:int,u=Depends(current_user)):
    c=conn(); d=[dict(x) for x in c.execute('SELECT * FROM product_images WHERE product_id=? ORDER BY is_primary DESC,id',(pid,))]; c.close(); return d

@app.post('/api/stock/in')
def stock_in(d:StockMove,u=Depends(current_user)):
    c=conn(); v=c.execute('SELECT * FROM variants WHERE id=?',(d.variant_id,)).fetchone()
    if not v: c.close(); raise HTTPException(404,'Variant not found')
    c.execute('UPDATE variants SET qty=qty+? WHERE id=?',(d.qty,d.variant_id)); c.execute('INSERT INTO stock_movements(variant_id,movement_type,qty,reason,reference,user_id,created_at) VALUES(?,?,?,?,?,?,?)',(d.variant_id,'IN',d.qty,d.reason,d.reference,u['id'],now())); audit(c,u['id'],'STOCK_IN','variant',d.variant_id,f'+{d.qty}; {d.reference}'); c.commit(); c.close(); return {'ok':True}
@app.post('/api/stock/adjust')
def stock_adjust(d:StockAdjust,u=Depends(owner_only)):
    if d.quantity_change==0: raise HTTPException(400,'Adjustment cannot be zero')
    c=conn(); v=c.execute('SELECT * FROM variants WHERE id=?',(d.variant_id,)).fetchone()
    if not v: c.close(); raise HTTPException(404,'Variant not found')
    if v['qty']+d.quantity_change<0: c.close(); raise HTTPException(400,'Adjustment would make physical stock negative')
    c.execute('UPDATE variants SET qty=qty+? WHERE id=?',(d.quantity_change,d.variant_id)); typ='ADJUST_IN' if d.quantity_change>0 else 'ADJUST_OUT'; c.execute('INSERT INTO stock_movements(variant_id,movement_type,qty,reason,reference,user_id,created_at) VALUES(?,?,?,?,?,?,?)',(d.variant_id,typ,abs(d.quantity_change),d.reason,d.reference,u['id'],now())); audit(c,u['id'],'STOCK_ADJUST','variant',d.variant_id,f'{d.quantity_change:+}; {d.reason}'); c.commit(); c.close(); return {'ok':True}
@app.get('/api/stock/movements')
def stock_movements(u=Depends(current_user)):
    c=conn(); d=[dict(x) for x in c.execute('''SELECT m.*,p.name,p.sku,v.size,v.color,u.name user_name FROM stock_movements m JOIN variants v ON v.id=m.variant_id JOIN products p ON p.id=v.product_id LEFT JOIN users u ON u.id=m.user_id ORDER BY m.id DESC LIMIT 300''')]; c.close(); return d

@app.post('/api/customers')
def add_customer(d:CustomerCreate,u=Depends(current_user)):
    c=conn(); cur=c.execute('INSERT INTO customers(name,phone,email,notes,created_at) VALUES(?,?,?,?,?)',(d.name,d.phone,d.email,d.notes,now())); audit(c,u['id'],'CREATE','customer',cur.lastrowid,d.name); c.commit(); c.close(); return {'id':cur.lastrowid}
@app.get('/api/customers')
def customers(u=Depends(current_user)):
    c=conn(); d=[dict(x) for x in c.execute('SELECT * FROM customers ORDER BY id DESC LIMIT 200')]; c.close(); return d
@app.post('/api/sales/walk-in')
def walkin(d:SaleCreate,u=Depends(current_user)):
    c=conn(); subtotal=0; prepared=[]
    for line in d.items:
        r=c.execute('SELECT v.*,p.name,p.selling_price FROM variants v JOIN products p ON p.id=v.product_id WHERE v.id=?',(line.variant_id,)).fetchone();
        if not r: c.close(); raise HTTPException(404,'Variant not found')
        av=r['qty']-r['reserved_qty']
        if av<line.qty: c.close(); raise HTTPException(400,f"Only {av} available for {r['name']}")
        lt=r['selling_price']*line.qty; subtotal+=lt; prepared.append((r,line,lt))
    discount=max(0,min(d.discount,subtotal)); total=subtotal-discount; sale_no='SP-WI-'+datetime.utcnow().strftime('%y%m%d%H%M%S%f')[-12:]; cur=c.execute('INSERT INTO sales(sale_no,channel,customer_name,user_id,subtotal,discount,total,payment_method,payment_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(sale_no,'WALK_IN',d.customer_name,u['id'],subtotal,discount,total,d.payment_method,d.payment_status,now())); sid=cur.lastrowid
    for r,line,lt in prepared:
        c.execute('INSERT INTO sale_items(sale_id,variant_id,product_name,size,color,qty,unit_price,line_total) VALUES(?,?,?,?,?,?,?,?)',(sid,r['id'],r['name'],r['size'],r['color'],line.qty,r['selling_price'],lt)); c.execute('UPDATE variants SET qty=qty-? WHERE id=?',(line.qty,r['id'])); c.execute('INSERT INTO stock_movements(variant_id,movement_type,qty,reason,reference,user_id,created_at) VALUES(?,?,?,?,?,?,?)',(r['id'],'OUT',line.qty,'Walk-in sale',sale_no,u['id'],now()))
    audit(c,u['id'],'CREATE','walk_in_sale',sid,f'{sale_no}; total={total}; {d.payment_method}'); c.commit(); c.close(); return {'sale_id':sid,'sale_no':sale_no,'total':total}
@app.get('/api/sales')
def sales(u=Depends(current_user)):
    c=conn(); d=[dict(x) for x in c.execute('SELECT s.*,u.name sales_rep FROM sales s LEFT JOIN users u ON u.id=s.user_id ORDER BY s.id DESC LIMIT 200')]; c.close(); return d
@app.get('/api/sales/{sale_id}/receipt')
def receipt(sale_id:int,u=Depends(current_user)):
    c=conn(); s=c.execute('SELECT s.*,u.name sales_rep FROM sales s LEFT JOIN users u ON u.id=s.user_id WHERE s.id=?',(sale_id,)).fetchone(); items=[dict(x) for x in c.execute('SELECT * FROM sale_items WHERE sale_id=?',(sale_id,))]; c.close(); return {'sale':dict(s),'items':items}
@app.get('/api/orders')
def orders(u=Depends(current_user)):
    c=conn(); d=[dict(x) for x in c.execute('SELECT * FROM orders ORDER BY id DESC LIMIT 200')]; c.close(); return d
@app.get('/api/orders/{order_id}')
def order_detail(order_id:int,u=Depends(current_user)):
    c=conn(); o=c.execute('SELECT * FROM orders WHERE id=?',(order_id,)).fetchone()
    if not o: c.close(); raise HTTPException(404,'Order not found')
    items=[dict(x) for x in c.execute('SELECT * FROM order_items WHERE order_id=? ORDER BY id',(order_id,))]
    c.close(); return {'order':dict(o),'items':items}

@app.patch('/api/orders/{order_id}/status')
def order_status(order_id:int,d:OrderStatus,u=Depends(current_user)):
    allowed=['PAID_PROCESSING','READY_FOR_DISPATCH','DISPATCHED','DELIVERED','CANCELLED']
    if d.status not in allowed: raise HTTPException(400,'Invalid status')
    c=conn(); c.execute('UPDATE orders SET order_status=? WHERE id=?',(d.status,order_id)); audit(c,u['id'],'UPDATE','online_order',order_id,d.status); c.commit(); c.close(); return {'ok':True}
@app.get('/api/reports/sales')
def reports(u=Depends(owner_only)):
    c=conn(); reps=[dict(x) for x in c.execute('SELECT u.name sales_rep,count(s.id) sales_count,coalesce(sum(s.total),0) revenue FROM users u LEFT JOIN sales s ON s.user_id=u.id GROUP BY u.id ORDER BY revenue DESC')]; channels=[dict(x) for x in c.execute("SELECT 'Walk-in' channel,count(*) sales_count,coalesce(sum(total),0) revenue FROM sales UNION ALL SELECT 'Online',count(*),coalesce(sum(total),0) FROM orders WHERE payment_status='PAID'")]; c.close(); return {'by_sales_rep':reps,'by_channel':channels}
@app.get('/api/audit')
def audit_logs(u=Depends(owner_only)):
    c=conn(); d=[dict(x) for x in c.execute('SELECT a.*,u.name user_name FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 300')]; c.close(); return d
@app.get('/api/staff')
def staff(u=Depends(owner_only)):
    c=conn(); d=[dict(x) for x in c.execute("SELECT id,name,email,role,active FROM users ORDER BY id")]; c.close(); return d
@app.post('/api/staff')
def create_staff(d:StaffCreate,u=Depends(owner_only)):
    if d.role not in ['sales_rep']: raise HTTPException(400,'Only Sales Representative accounts can be created here')
    c=conn()
    try: cur=c.execute('INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)',(d.name,d.email,hash_pw(d.password),d.role))
    except DatabaseIntegrityError: c.close(); raise HTTPException(400,'Email already exists')
    audit(c,u['id'],'CREATE','staff',cur.lastrowid,d.email); c.commit(); c.close(); return {'id':cur.lastrowid}

app.mount('/static',StaticFiles(directory=os.path.join(BASE,'static')),name='static'); app.mount('/uploads',StaticFiles(directory=os.path.join(BASE,'uploads')),name='uploads')
@app.get('/')
def home(): return FileResponse(os.path.join(BASE,'static','index.html'))
@app.get('/admin')
def admin(): return FileResponse(os.path.join(BASE,'static','admin.html'))

@app.get('/staff')
def staff_portal(): return FileResponse(os.path.join(BASE,'static','admin.html'))
