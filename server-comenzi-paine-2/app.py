import os, io, re, json, sqlite3, smtplib, base64, unicodedata, threading
from datetime import date, datetime
from decimal import Decimal as D, ROUND_HALF_UP
from email.message import EmailMessage
from xml.sax.saxutils import escape as esc
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify, send_from_directory
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, A5, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

HERE = os.path.dirname(os.path.abspath(__file__))
# ===== CONFIGURARE =====
S = dict(name="ÎI BRĂICĂU MIHAI", cui="RO30481240", regcom="F39/917/2012", street="Sat Mihălceni, Com. Ciorăști",
         city="Ciorăști", county="Jud. Vrancea", code="RO-VN", iban="RO72CECEVR0530RON0642692", bank="CEC Bank")
VAT = D(11)   # %
PRODUCTS = {  # prețuri FĂRĂ TVA (de verificat)
    "rustica800": {"name": "Pâine Rustică 800 g", "price": "6.50"},
    "cartofi": {"name": "Pâine cu cartofi", "price": "6.50"},
    "rusticana400": {"name": "Rusticana cu semințe 400 g", "price": "5.00"},
    "rustica400": {"name": "Pâine Rustică 400 g", "price": "3.50"}}
COUNTIES = {"AB":"Alba","AR":"Arad","AG":"Argeș","BC":"Bacău","BH":"Bihor","BN":"Bistrița-Năsăud","BT":"Botoșani","BV":"Brașov","BR":"Brăila","B":"București","BZ":"Buzău","CS":"Caraș-Severin","CL":"Călărași","CJ":"Cluj","CT":"Constanța","CV":"Covasna","DB":"Dâmbovița","DJ":"Dolj","GL":"Galați","GR":"Giurgiu","GJ":"Gorj","HR":"Harghita","HD":"Hunedoara","IL":"Ialomița","IS":"Iași","IF":"Ilfov","MM":"Maramureș","MH":"Mehedinți","MS":"Mureș","NT":"Neamț","OT":"Olt","PH":"Prahova","SM":"Satu Mare","SJ":"Sălaj","SB":"Sibiu","SV":"Suceava","TR":"Teleorman","TM":"Timiș","TL":"Tulcea","VS":"Vaslui","VL":"Vâlcea","VN":"Vrancea"}
DB_PATH = os.environ.get("DB_PATH", os.path.join(HERE, "data", "orders.db"))   # TREBUIE pe disc persistent
RECEIPT_SERIES = "CH"

pdfmetrics.registerFont(TTFont("F", HERE + "/fonts/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("FB", HERE + "/fonts/DejaVuSans-Bold.ttf"))
app = Flask(__name__, static_folder=None)
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
db = sqlite3.connect(DB_PATH, check_same_thread=False); lock = threading.Lock()
db.executescript("""create table if not exists clients(key text primary key, series text unique, last_no int default 0);
create table if not exists counters(name text primary key, v int default 0);
create table if not exists orders(no int primary key, series text, inv_no int, payload text, emailed int default 0);""")

def q(x): return x.quantize(D("0.01"), ROUND_HALF_UP)
def dmy(iso): return datetime.fromisoformat(iso[:10]).strftime("%d.%m.%Y")
def ascii3(n):
    s = re.sub("[^A-Z]", "", unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode().upper())
    return (s + "XXX")[:3]
def nxt(c, name):
    c.execute("insert into counters(name,v) values(?,0) on conflict(name) do nothing", (name,))
    c.execute("update counters set v=v+1 where name=?", (name,))
    return c.execute("select v from counters where name=?", (name,)).fetchone()[0]

# ---------- PDF ----------
class P:
    def __init__(s, size):
        s.buf = io.BytesIO(); s.c = canvas.Canvas(s.buf, pagesize=size); s.w, s.h = size
    def t(s, x, y, txt, b=0, sz=9, al="l"):
        s.c.setFont("FB" if b else "F", sz)
        {"l": s.c.drawString, "c": s.c.drawCentredString, "r": s.c.drawRightString}[al](x, s.h - y, txt)
    def box(s, x, y, w, h): s.c.rect(x, s.h - y - h, w, h)
    def ln(s, x1, y1, x2, y2): s.c.line(x1, s.h - y1, x2, s.h - y2)
    def done(s): s.c.save(); return s.buf.getvalue()

def cl_lines(o):
    c = o["client"]
    return [c["name"], ("CUI: " + c["cui"]) if c["cui"] else "", ", ".join(x for x in [c["address"], c["locality"], "jud. " + COUNTIES[c["county"]]] if x),
            "Tel.: " + c["phone"], c["email"]]

def table(p, o, y0, cols, heads):
    m = 36; W = p.w - 2 * m
    p.c.setFillGray(.9); p.c.rect(m, p.h - y0 - 16, W, 16, fill=1, stroke=0); p.c.setFillGray(0)
    for i, h in enumerate(heads):
        if i < 2: p.t(cols[i] + 4, y0 + 11, h, 1, 8)
        else: p.t(cols[i + 1] - 4, y0 + 11, h, 1, 8, "r")
    y = y0 + 16
    for j, l in enumerate(o["lines"]):
        y += 18
        vals = [str(j + 1), l["name"], "buc", str(l["qty"]), l["price"], l["val"]][:len(heads)]
        for i, v in enumerate(vals):
            if i < 2: p.t(cols[i] + 4, y - 5, v)
            else: p.t(cols[i + 1] - 4, y - 5, v, 0, 9, "r")
    p.box(m, y0, W, y - y0)
    for x in cols[1:-1]: p.ln(x, y0, x, y)
    return y

def pdf_invoice(o):
    p = P(A4); m = 36; W = p.w - 2 * m; w3 = W / 3
    p.box(m, 36, W, 120); p.ln(m + w3, 36, m + w3, 156); p.ln(m + 2 * w3, 36, m + 2 * w3, 156)
    p.t(m + 6, 50, "FURNIZOR", 1)
    for i, s in enumerate([S["name"], "CUI: " + S["cui"], "Reg. Com.: " + S["regcom"], S["street"], S["county"], "Cont: " + S["iban"], "Banca: " + S["bank"], "Plătitor de TVA"]):
        p.t(m + 6, 64 + i * 12, s, i == 0, 7.5)
    cx = m + 1.5 * w3
    p.t(cx, 70, "FACTURĂ", 1, 18, "c"); p.t(cx, 92, f"Seria {o['series']}  Nr. {o['inv_no']}", 1, 10, "c")
    p.t(cx, 110, "Data facturii (livrării): " + dmy(o["date"]), 0, 8, "c"); p.t(cx, 124, f"Comanda nr. {o['no']}", 0, 8, "c")
    p.t(m + 2 * w3 + 6, 50, "CLIENT", 1)
    for i, s in enumerate(cl_lines(o)): p.t(m + 2 * w3 + 6, 64 + i * 12, s, i == 0, 7.5)
    cols = [m, m + 28, m + 255, m + 295, m + 350, m + 430, m + W]
    y = table(p, o, 176, cols, ["Nr.", "Denumire produs", "U.M.", "Cant.", "Preț fără TVA", "Valoare fără TVA"]) + 22
    for i, (lab, v, b) in enumerate([("Total fără TVA:", o["base"], 0), (f"TVA {VAT}%:", o["vat"], 0), ("TOTAL DE PLATĂ:", o["total"], 1)]):
        p.t(m + W - 110, y + i * 15, lab, b, 10 if b else 9, "r"); p.t(m + W, y + i * 15, v + " lei", b, 10 if b else 9, "r")
    p.t(m, y + 60, "Modalitate de plată: " + ("numerar (cu chitanță)" if o["cash"] else "ordin de plată"), 0, 8.5)
    p.box(m, y + 85, W / 2, 70); p.box(m + W / 2, y + 85, W / 2, 70)
    p.t(m + 6, y + 98, "Semnătura și ștampila furnizorului", 0, 8); p.t(m + W / 2 + 6, y + 98, "Semnătura de primire", 0, 8)
    return p.done()

def pdf_receipt(o):
    p = P(landscape(A5)); m = 36; W = p.w - 2 * m
    p.box(m, 30, W, p.h - 60)
    p.t(m + 12, 52, S["name"], 1, 10); p.t(m + 12, 66, f"CUI: {S['cui']}   Reg. Com.: {S['regcom']}", 0, 8); p.t(m + 12, 78, S["street"] + ", " + S["county"], 0, 8)
    p.t(p.w / 2, 125, "CHITANȚĂ", 1, 22, "c"); p.t(p.w / 2, 148, f"Seria {RECEIPT_SERIES}  Nr. {o['receipt_no']}   Data: {dmy(o['date'])}", 1, 10, "c")
    p.t(m + 20, 195, "Am primit de la: " + o["client"]["name"] + (f"  (CUI {o['client']['cui']})" if o["client"]["cui"] else ""), 0, 10)
    p.t(m + 20, 220, f"Suma de: {o['total']} lei", 1, 12)
    p.t(m + 20, 245, f"Reprezentând: contravaloare factura seria {o['series']} nr. {o['inv_no']} din {dmy(o['date'])}", 0, 10)
    p.t(p.w - m - 20, 300, "Casier (semnătura și ștampila)", 0, 8, "r")
    return p.done()

def pdf_order(o):
    p = P(A4); m = 36; W = p.w - 2 * m
    p.t(m, 60, f"COMANDĂ nr. {o['no']}", 1, 18); p.t(m, 80, "Înregistrată: " + o["created"] + "   |   Furnizor: " + S["name"], 0, 8.5)
    for i, s in enumerate(cl_lines(o)): p.t(m, 108 + i * 13, s, i == 0, 9.5)
    p.t(m, 180, "Livrare / data facturii: " + dmy(o["date"]) + "   |   Plată: " + ("numerar" if o["cash"] else "ordin de plată"), 1, 9.5)
    y = table(p, o, 200, [m, m + 28, m + 330, m + 380, m + W], ["Nr.", "Denumire produs", "U.M.", "Cant."])
    if o["notes"]: p.t(m, y + 25, "Observații: " + o["notes"], 0, 9)
    p.t(m, y + 50, f"Factura emisă: seria {o['series']} nr. {o['inv_no']}" + (f"   |   Chitanța: {RECEIPT_SERIES} nr. {o['receipt_no']}" if o["cash"] else ""), 0, 9)
    return p.done()

# ---------- XML e-Factura (UBL 2.1, CIUS-RO) ----------
def xml_invoice(o):
    c = o["client"]; vid = c["cui"].upper().replace(" ", "")
    def party(name, street, city, sub, vat, cid):
        return (f"<cac:Party><cac:PostalAddress><cbc:StreetName>{esc(street)}</cbc:StreetName><cbc:CityName>{esc(city)}</cbc:CityName>"
                f"<cbc:CountrySubentity>{sub}</cbc:CountrySubentity><cac:Country><cbc:IdentificationCode>RO</cbc:IdentificationCode></cac:Country></cac:PostalAddress>"
                + (f"<cac:PartyTaxScheme><cbc:CompanyID>{esc(vat)}</cbc:CompanyID><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme>" if vat else "")
                + f"<cac:PartyLegalEntity><cbc:RegistrationName>{esc(name)}</cbc:RegistrationName><cbc:CompanyID>{esc(cid)}</cbc:CompanyID></cac:PartyLegalEntity></cac:Party>")
    cat = f"<cac:TaxCategory><cbc:ID>S</cbc:ID><cbc:Percent>{VAT}</cbc:Percent><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:TaxCategory>"
    lines = "".join(
        f'<cac:InvoiceLine><cbc:ID>{i}</cbc:ID><cbc:InvoicedQuantity unitCode="H87">{l["qty"]}</cbc:InvoicedQuantity>'
        f'<cbc:LineExtensionAmount currencyID="RON">{l["val"]}</cbc:LineExtensionAmount><cac:Item><cbc:Name>{esc(l["name"])}</cbc:Name>'
        f'<cac:ClassifiedTaxCategory><cbc:ID>S</cbc:ID><cbc:Percent>{VAT}</cbc:Percent><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:ClassifiedTaxCategory></cac:Item>'
        f'<cac:Price><cbc:PriceAmount currencyID="RON">{l["price"]}</cbc:PriceAmount></cac:Price></cac:InvoiceLine>' for i, l in enumerate(o["lines"], 1))
    pay = ("<cbc:PaymentMeansCode>10</cbc:PaymentMeansCode>" if o["cash"] else
           f"<cbc:PaymentMeansCode>30</cbc:PaymentMeansCode><cac:PayeeFinancialAccount><cbc:ID>{S['iban']}</cbc:ID></cac:PayeeFinancialAccount>")
    return ('<?xml version="1.0" encoding="UTF-8"?>'
        '<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2" xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">'
        "<cbc:CustomizationID>urn:cen.eu:en16931:2017#compliant#urn:efactura.mfinante.ro:CIUS-RO:1.0.1</cbc:CustomizationID>"
        f"<cbc:ID>{o['series']}{o['inv_no']}</cbc:ID><cbc:IssueDate>{o['date']}</cbc:IssueDate><cbc:InvoiceTypeCode>380</cbc:InvoiceTypeCode>"
        "<cbc:DocumentCurrencyCode>RON</cbc:DocumentCurrencyCode>"
        f"<cac:AccountingSupplierParty>{party(S['name'], S['street'], S['city'], S['code'], S['cui'], S['cui'][2:])}</cac:AccountingSupplierParty>"
        f"<cac:AccountingCustomerParty>{party(c['name'], c['address'] or c['locality'], c['locality'], 'RO-' + c['county'], vid if vid.startswith('RO') else '', vid)}</cac:AccountingCustomerParty>"
        f"<cac:PaymentMeans>{pay}</cac:PaymentMeans>"
        f'<cac:TaxTotal><cbc:TaxAmount currencyID="RON">{o["vat"]}</cbc:TaxAmount><cac:TaxSubtotal><cbc:TaxableAmount currencyID="RON">{o["base"]}</cbc:TaxableAmount>'
        f'<cbc:TaxAmount currencyID="RON">{o["vat"]}</cbc:TaxAmount>{cat}</cac:TaxSubtotal></cac:TaxTotal>'
        f'<cac:LegalMonetaryTotal><cbc:LineExtensionAmount currencyID="RON">{o["base"]}</cbc:LineExtensionAmount><cbc:TaxExclusiveAmount currencyID="RON">{o["base"]}</cbc:TaxExclusiveAmount>'
        f'<cbc:TaxInclusiveAmount currencyID="RON">{o["total"]}</cbc:TaxInclusiveAmount><cbc:PayableAmount currencyID="RON">{o["total"]}</cbc:PayableAmount></cac:LegalMonetaryTotal>'
        f"{lines}</Invoice>").encode("utf-8")

# ---------- documente + email ----------
def docs(o):
    inv = f"{o['series']}_{o['inv_no']}"
    f = [(f"Comanda_{o['no']}.pdf", pdf_order(o)), (f"Factura_{inv}.pdf", pdf_invoice(o))]
    if o["cash"]: f.append((f"Chitanta_{RECEIPT_SERIES}_{o['receipt_no']}.pdf", pdf_receipt(o)))
    return f, [(f"Factura_{inv}.xml", xml_invoice(o))]

def send_mail(o):
    host = os.environ.get("SMTP_HOST")
    if not host: app.logger.warning("SMTP_HOST lipsește – email netrimis"); return False
    pdfs, xmls = docs(o); m = EmailMessage()
    m["Subject"] = f"Comanda {o['no']} – {o['client']['name']} – factura {o['series']} {o['inv_no']}"
    m["From"] = os.environ.get("MAIL_FROM", os.environ["SMTP_USER"]); m["To"] = os.environ.get("MAIL_TO", "mihai@moaramihalceni.ro")
    m.set_content(f"Comandă nr. {o['no']} de la {o['client']['name']}\nLivrare/factură: {dmy(o['date'])}\nTotal: {o['total']} lei ({'numerar' if o['cash'] else 'ordin de plată'})\nAtașat: comanda, factura (PDF + XML)" + (", chitanța." if o["cash"] else "."))
    for n, d in pdfs: m.add_attachment(d, maintype="application", subtype="pdf", filename=n)
    for n, d in xmls: m.add_attachment(d, maintype="application", subtype="xml", filename=n)
    port = int(os.environ.get("SMTP_PORT", "587"))
    with (smtplib.SMTP_SSL(host, port) if port == 465 else smtplib.SMTP(host, port)) as s:
        if port != 465: s.starttls()
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"]); s.send_message(m)
    return True

def try_mail(o):
    try:
        ok = send_mail(o)
    except Exception as e:
        app.logger.error("email eșuat: %s", e); ok = False
    if ok:
        with lock: db.execute("update orders set emailed=1 where no=?", (o["no"],)); db.commit()
    return ok

# ---------- rute ----------
@app.get("/")
def index(): return send_from_directory(HERE + "/static", "form.html")

@app.get("/api/config")
def cfg():
    return jsonify(products=[{"id": k, "name": v["name"]} for k, v in PRODUCTS.items()], counties=COUNTIES)

@app.post("/api/order")
def order():
    d = request.get_json(force=True, silent=True) or {}
    c = {k: str(d.get("client", {}).get(k, "")).strip()[:200] for k in ["name", "cui", "phone", "email", "address", "locality", "county"]}
    try:
        if not (c["name"] and c["phone"] and c["locality"]): raise ValueError("Completează firma, telefonul și localitatea.")
        if c["county"] not in COUNTIES: raise ValueError("Alege județul.")
        dd = date.fromisoformat(str(d.get("date", "")))
        if dd < datetime.now(ZoneInfo("Europe/Bucharest")).date(): raise ValueError("Data livrării nu poate fi în trecut.")
        lines, base = [], D(0)
        for it in d.get("items", []):
            pr, n = PRODUCTS.get(it.get("id")), int(it.get("qty", 0))
            if not pr or n <= 0 or n > 10000: continue
            v = q(D(pr["price"]) * n); base += v; lines.append({"name": pr["name"], "qty": n, "price": pr["price"], "val": str(v)})
        if not lines: raise ValueError("Alege cel puțin un produs.")
    except (ValueError, TypeError) as e:
        return jsonify(ok=False, error=str(e) if str(e) and "invalid" not in str(e) else "Date invalide."), 400
    vat = q(base * VAT / 100); cash = bool(d.get("cash"))
    key = re.sub(r"^RO", "", re.sub(r"\W", "", c["cui"]).upper()) or re.sub(r"\W", "", c["name"]).upper()
    with lock:
        cur = db.cursor()
        row = cur.execute("select series,last_no from clients where key=?", (key,)).fetchone()
        if not row:
            base3, ser, i = ascii3(c["name"]), None, 1
            ser = base3
            while cur.execute("select 1 from clients where series=?", (ser,)).fetchone(): i += 1; ser = f"{base3}{i}"
            cur.execute("insert into clients(key,series,last_no) values(?,?,0)", (key, ser)); row = (ser, 0)
        series, inv_no = row[0], row[1] + 1
        cur.execute("update clients set last_no=? where key=?", (inv_no, key))
        no = nxt(cur, "order"); rno = nxt(cur, "receipt") if cash else None
        o = {"no": no, "series": series, "inv_no": inv_no, "receipt_no": rno, "date": dd.isoformat(), "cash": cash,
             "created": datetime.now(ZoneInfo("Europe/Bucharest")).strftime("%d.%m.%Y %H:%M"), "client": c, "notes": str(d.get("notes", ""))[:500],
             "lines": lines, "base": str(q(base)), "vat": str(vat), "total": str(q(base + vat))}
        cur.execute("insert into orders(no,series,inv_no,payload) values(?,?,?,?)", (no, series, inv_no, json.dumps(o, ensure_ascii=False))); db.commit()
    emailed = try_mail(o)
    files = [{"name": n, "b64": base64.b64encode(b).decode()} for n, b in docs(o)[0]]
    return jsonify(ok=True, no=no, invoice=f"{series} {inv_no}", emailed=emailed, files=files)

def admin_ok(): return os.environ.get("ADMIN_KEY") and request.args.get("key") == os.environ["ADMIN_KEY"]

@app.get("/admin/pending")      # comenzi pentru care emailul nu a plecat
def pending():
    if not admin_ok(): return "interzis", 403
    with lock: r = db.execute("select no,series,inv_no from orders where emailed=0").fetchall()
    return jsonify(r)

@app.get("/admin/resend/<int:no>")
def resend(no):
    if not admin_ok(): return "interzis", 403
    with lock: r = db.execute("select payload from orders where no=?", (no,)).fetchone()
    return jsonify(emailed=try_mail(json.loads(r[0]))) if r else ("nu există", 404)
