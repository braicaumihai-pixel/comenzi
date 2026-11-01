# Server comenzi pâine – ÎI Brăicău Mihai

La „Trimite comanda": număr consecutiv de comandă, serie de factură per client (ex. „ALT 1, ALT 2"), factură cu data = data livrării,
chitanță dacă e bifat numerar. Pe email ajung: comanda, factura PDF, factura XML (e-Factura) și chitanța.

## Pornire pe Render (Web Service, Python)
- Build: `pip install -r requirements.txt`
- Start: `gunicorn app:app --workers 1 --threads 4`   (un singur worker, din cauza numerotării)
- **Disc persistent** montat (ex. /var/data) și variabila `DB_PATH=/var/data/orders.db`.
  Fără disc, la fiecare repornire se pierd numerele facturilor.
- Variabile email: `SMTP_HOST`, `SMTP_PORT` (587 sau 465), `SMTP_USER`, `SMTP_PASS`, opțional `MAIL_TO`, `MAIL_FROM`
- `ADMIN_KEY=<parolă>` → `/admin/pending?key=...` (comenzi cu email netrimis) și `/admin/resend/<nr>?key=...`

## De verificat
- Prețurile din `PRODUCTS` (în app.py) sunt FĂRĂ TVA; TVA = 11%.
- XML-ul: validează-l în validatorul ANAF înainte de prima încărcare în SPV.
- Numerele sunt rezervate la trimitere; o comandă anulată lasă un gol în serie (se anulează prin stornare).

## Varianta rapidă
Pune toate fișierele într-un repository GitHub privat, apoi în Render: New → Blueprint → alege repository-ul.
`render.yaml` creează serviciul, discul și variabilele; Render îți cere doar SMTP_HOST, SMTP_USER, SMTP_PASS.
(Gmail: SMTP_HOST=smtp.gmail.com, parolă de aplicație, nu parola contului.)
