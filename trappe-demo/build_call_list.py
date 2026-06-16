#!/usr/bin/env python3
"""Build the Piontrix call list spreadsheet for tomorrow's outreach."""
from pathlib import Path
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

OUT = Path.home() / "Downloads" / "Piontrix_Call_List_2026-06-16.xlsx"

# Ordered by call-time window so you can work top → bottom through the day.
ROWS = [
    {
        "order": 1, "company": "TruFit Pilates", "vertical": "Boutique fitness studio",
        "phone": "(484) 924-9931", "website": "trufitpilates.com",
        "address": "235 Bridge St, Phoenixville", "rating": "Verify on Google (newer studio)",
        "window": "11:30am–1:00pm (midday gap between class blocks)",
        "tech": "Fresha (online booking + payments). Class-based reformer/strength.",
        "workflows": "Reactivation (members who stop booking = churn) · Class reminders & late-cancel recovery · Missed-call/Voice · Reputation (grow reviews)",
        "hook": "\"Boutique studios live and die on whether members keep booking. The ones who drift off for two weeks usually cancel — I catch them and pull them back before they're gone.\"",
        "pain": "Class churn; empty reformer spots from late cancels/no-shows; filling classes; thin review count.",
        "roi": "1 retained member ≈ $180–220/mo recurring. Filling 2 late-cancel spots/class adds up fast.",
        "who": "Owner/lead instructor — call in the midday gap when classes aren't running.",
    },
    {
        "order": 2, "company": "The Mayor Salon", "vertical": "Hair salon (newer / boutique)",
        "phone": "Call (484) 854-6954 · Text (484) 902-8263", "website": "themayorsalon.com",
        "address": "430 W Main St, Collegeville", "rating": "Verify on Google (newer salon)",
        "window": "1:00–2:00pm (early afternoon, between appointments)",
        "tech": "Appointment-based; already TEXTS clients (lists a text line) — owner-send SMS fits perfectly & compliantly.",
        "workflows": "Reminders (no-show recovery) · Reactivation (clients who never rebooked) · Reputation (build reviews) · Missed-call",
        "hook": "\"A no-show in the chair is an hour you can't sell back. And the clients who loved you once but never rebooked — I bring them back with a text from your own number.\"",
        "pain": "No-shows/late cancels; client retention & rebooking; building reviews as a newer salon.",
        "roi": "1 recovered no-show ≈ $60–120 of chair time; reactivated regular = recurring revenue.",
        "who": "Owner — they already text clients, so the owner-send SMS angle lands naturally.",
    },
    {
        "order": 3, "company": "Advanced Dentistry of Phoenixville", "vertical": "Dental practice",
        "phone": "(610) 935-7509", "website": "phoenixvilledentistry.com",
        "address": "119 Nutt Rd, Phoenixville", "rating": "~4.9 stars but LOW review volume (~11 on Yelp)",
        "window": "1:30–3:00pm (after lunch; Fri closes 3pm, Sat till 1)",
        "tech": "Dental PMS (booking). Website has a contact form only.",
        "workflows": "Recall reactivation (6-mo hygiene = biggest $) · Reminders (no-show recovery) · Reputation (4.9 but only ~11 reviews → huge upside) · Missed-call/Voice",
        "hook": "\"You're at 4.9 stars but only a handful of reviews — rivals have hundreds. And every overdue recall is a cleaning you're not billing. I bring both back automatically.\"",
        "pain": "No-shows (empty chair = pure lost margin); overdue 6-month recalls; thin review count vs competitors.",
        "roi": "~23 overdue recalls ≈ $4,600; each no-show ≈ $200+ chair time. Highest-$ vertical on the list.",
        "who": "Office manager (front desk gatekeeps) — ask for them by title; pitch the owner-dentist if possible.",
    },
    {
        "order": 4, "company": "Collegeville Italian Bakery Pizzeria Napoletana", "vertical": "Bakery / pizzeria",
        "phone": "(610) 489-3354", "website": "collegevilleitalianbakery.com",
        "address": "3846 Ridge Pike, Collegeville", "rating": "4.7 stars · ~575 reviews",
        "window": "2:00–4:00pm (mid-afternoon lull after lunch rush)",
        "tech": "ON TOAST (online ordering + POS) + DoorDash. Real POS data → a true daily Digest is possible.",
        "workflows": "Missed-call/Voice (their phone problem!) · Reactivation · Catering lead capture · Digest (real Toast data)",
        "hook": "\"Your own customers are posting on Yelp that they can't get you on the phone — that's catering orders and big tickets walking away. I make sure every call gets answered, even at the lunch rush.\"",
        "pain": "DOCUMENTED: public Yelp post — customers can't reach them by phone after multiple tries. Lost orders/catering. Busy rush.",
        "roi": "A single missed catering call can be hundreds. Don't pitch online ordering (on Toast) — pitch the phone + reactivation.",
        "who": "Owner/manager — they're on Toast, so frame it as 'the layer above your POS,' not a replacement.",
    },
    {
        "order": 5, "company": "Trappe Tavern", "vertical": "Bar / restaurant (tavern)",
        "phone": "(610) 489-8686", "website": "trappetavern.com",
        "address": "416 W Main St, Trappe", "rating": "4.6 stars · 693 reviews (already strong)",
        "window": "2:30–4:30pm (dead zone between lunch and dinner — best window of the day)",
        "tech": "NO first-party online ordering, NO booking system. Reservations by phone. (Your sharpest built demo.)",
        "workflows": "Missed-call/Voice (no host after 9pm) · Reactivation (regulars from the reservation log) · Digest (estimated, no POS yet)",
        "hook": "\"On a packed Friday after 9, who's grabbing the phone? Every missed call is a party booking the place down the street. I catch them — or have the AI answer the line live.\"",
        "pain": "Missed after-hours/rush calls; regulars quietly drifting; no reservation system at all.",
        "roi": "1 recovered large party ≈ $200–500. Reputation is NOT the pitch here (4.6/693) — lead missed-call + reactivation.",
        "who": "Owner — best reached 2:30–4:30; this is the business your Copper Lantern voice demo mirrors.",
    },
]

wb = openpyxl.Workbook()

HEAD_FILL = PatternFill("solid", fgColor="2F2A24")
HEAD_FONT = Font(bold=True, color="E9B873", size=11)
WRAP = Alignment(wrap_text=True, vertical="top")
THIN = Border(bottom=Side(style="thin", color="D9D9D9"))


def style_header(ws, ncols):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill = HEAD_FILL
        cell.font = HEAD_FONT
        cell.alignment = Alignment(vertical="center")
    ws.freeze_panes = "A2"
    ws.row_dimensions[1].height = 22


# ── Sheet 1: Call List (contact + quick reference) ──
ws1 = wb.active
ws1.title = "Call List"
cols1 = [("#", 4), ("Company", 30), ("Vertical", 24), ("Phone", 26), ("Website", 26),
         ("Address", 28), ("Google rating / reviews", 26), ("Call window (Tue)", 34), ("Called?", 10), ("Outcome / next step", 30)]
ws1.append([c[0] for c in cols1])
for r in ROWS:
    ws1.append([r["order"], r["company"], r["vertical"], r["phone"], r["website"],
                r["address"], r["rating"], r["window"], "", ""])
for i, (_, w) in enumerate(cols1, 1):
    ws1.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
for row in ws1.iter_rows(min_row=2):
    for cell in row:
        cell.alignment = WRAP
        cell.border = THIN
style_header(ws1, len(cols1))

# ── Sheet 2: Analysis & Pitch ──
ws2 = wb.create_sheet("Analysis & Pitch")
cols2 = [("#", 4), ("Company", 28), ("Tech they already run", 38), ("Top workflows to pitch", 44),
         ("The hook (opener)", 50), ("Pain points", 40), ("ROI angle", 38), ("Who to ask for", 34)]
ws2.append([c[0] for c in cols2])
for r in ROWS:
    ws2.append([r["order"], r["company"], r["tech"], r["workflows"], r["hook"], r["pain"], r["roi"], r["who"]])
for i, (_, w) in enumerate(cols2, 1):
    ws2.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
for row in ws2.iter_rows(min_row=2):
    for cell in row:
        cell.alignment = WRAP
        cell.border = THIN
    ws2.row_dimensions[row[0].row].height = 90
style_header(ws2, len(cols2))

OUT.parent.mkdir(exist_ok=True)
wb.save(OUT)
print("saved:", OUT)
