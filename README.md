# Sizeplus Outfit V0.6.1 — Navigation & Staff Access Refinement


This release is the final design/UX refinement baseline before production hardening.

## What changed
- Integrated the supplied Sizeplus Outfit model photograph into the homepage hero.
- Rebuilt the homepage around the approved boutique identity and contact details.
- Separated New Arrivals from the general Shop catalogue: only the New Arrivals section shows the NEW ARRIVAL badge.
- Kept Owner-controlled New Arrival/Featured status from the Products dashboard.
- Fixed duplicate product variant selector IDs between New Arrivals and Shop.
- Added collection/category navigation, top search, mobile navigation, contact bar, service strip and stronger footer.
- Preserved shared inventory, POS, online checkout, stock reservation, orders, product uploads, staff access and reporting.

## Run locally
```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Storefront: http://127.0.0.1:8000/
Admin: http://127.0.0.1:8000/admin

Owner login: owner@sizeplus.local / Owner123!
Sales Representative: sales@sizeplus.local / Sales123!

## Production-ready phase after V0.6
The next phase should focus on PostgreSQL, real Paystack verification/webhooks, cloud image storage, environment secrets, secure password hashing, reservation expiry, delivery settings, backups, security hardening and deployment.


## V0.6.1 fixes
- Added Staff Login to desktop and mobile storefront navigation.
- Added `/staff` as a clear staff portal route using the existing secure Owner/Sales Representative login.
- Replaced the footer-only About anchor with a dedicated About section.
- Added a dedicated Contact section and staff-access card.
- Added anchor scroll offsets so sticky navigation does not cover sections.
