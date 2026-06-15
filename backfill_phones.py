#!/usr/bin/env python3
"""Find + save each outreach lead's phone number from their website (tel: links
+ US phone regex). Idempotent — skips leads that already have a phone."""
import json, re, requests
LEADS = "piontrix_leads.json"
PAT = re.compile(r"tel:([+\d\-\.\(\) ]{7,20})|(\(?\d{3}\)?[\s\-\.]\d{3}[\s\-\.]\d{4})")
def find_phone(url):
    try:
        html = requests.get(url, timeout=12, headers={"User-Agent":"Mozilla/5.0"}).text[:200000]
    except Exception:
        return ""
    for m in PAT.finditer(html):
        p = (m.group(1) or m.group(2) or "").strip()
        digits = re.sub(r"\D","",p)
        if 10 <= len(digits) <= 11:
            return p
    return ""
data = json.load(open(LEADS))
leads = data if isinstance(data,list) else data.get("leads",[])
n=0
for l in leads:
    if "phone" in l or not l.get("website"): continue
    p = find_phone(l["website"])
    l["phone"]=p
    if p: n+=1
json.dump(data, open(LEADS,"w"), indent=2)
print(f"phones added: {n} / {len(leads)} leads")
