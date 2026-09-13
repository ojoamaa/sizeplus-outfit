# SIZEPLUS V0.2 — Boutique + E-commerce

Integrated FastAPI boutique operations and public e-commerce storefront.

## Run locally

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

Open:
- Storefront: http://127.0.0.1:8000/
- Staff dashboard: http://127.0.0.1:8000/admin
- API docs: http://127.0.0.1:8000/docs

## Demo staff accounts
- Owner: owner@sizeplus.local / Owner123!
- Sales Representative: sales@sizeplus.local / Sales123!

## V0.2 features
- Public responsive fashion storefront
- Product catalogue by category
- Size/colour variant selection
- Shared inventory across online + walk-in channels
- Cart and checkout
- Abuja vs nationwide delivery fee logic
- Online stock reservation before payment
- Demo Paystack confirmation workflow (no secret key embedded)
- Online order and delivery-status management
- Walk-in POS with documented sales representative
- Cash, POS, bank transfer and online payment records
- Stock in / stock out and low-stock visibility
- Customer records
- Owner vs Sales Representative role separation
- Owner revenue-by-channel and staff-sales reports
- Audit trail

## Production payment note
The public checkout intentionally uses a simulated Paystack confirmation button. For production, configure Paystack server-side with environment variables and verify transactions via Paystack's API/webhook before marking an order paid. Never place a Paystack secret key in HTML or committed source files.

## Recommended V0.3 production work
- PostgreSQL instead of SQLite
- Real Paystack initialize/verify/webhook integration
- Cloud-hosted product image upload
- Delivery zones/rider or courier integration
- WhatsApp/email/SMS order notifications
- Returns/exchanges workflow
- Customer accounts and order history
- Product management UI for owner
- Deployment configuration for Render/Vercel or a single Render service
