SIZEPLUS OUTFIT CLIENT REVIEW - DEPLOYMENT NOTES

Recommended prototype flow:
1. Create a private GitHub repository, for example: sizeplus-outfit-prototype.
2. Upload the contents of this folder to the repository root.
3. Connect the repository to Render.
4. Render can use the included render.yaml configuration.
5. Keep Auto Deploy OFF during client UAT so the review baseline does not change unexpectedly.
6. Share only the Render public URL and UAT/Consent document with the client.

Build command:
pip install -r requirements.txt

Start command:
uvicorn main:app --host 0.0.0.0 --port $PORT

Important prototype limitations:
- Payment is demo only.
- SQLite is used for the review build and should not be treated as the production database.
- Uploaded local images may not persist across host redeploys without persistent storage. Production will use cloud image storage.
- Do not place real Paystack secret keys in this prototype repository.
