#!/usr/bin/env python3
"""Backfill NJ 2026 state results into flo_results under the existing
'NJ States' 2026 event (uuid 14467831). Mirrors il_to_flo_results.py.

Reads nj_2026_athletes.csv (must be in same dir). Idempotent: deterministic
wrestler_guid per (name,weight) so re-runs upsert, not duplicate.

Usage (Crostini):
  KEY=$(grep -o 'sb_secret_[A-Za-z0-9_-]*' ~/score_all.py | head -1)
  SUPABASE_SECRET="$KEY" python3 nj_to_flo_results.py            # preview
  SUPABASE_SECRET="$KEY" python3 nj_to_flo_results.py --apply    # write
"""
import os, sys, csv, json, uuid, urllib.request

SUPA="https://yewigtsyvxiwprcpicup.supabase.co"
KEY=os.environ["SUPABASE_SECRET"]
APPLY="--apply" in sys.argv
SRC="nj_2026_athletes.csv"
EVENT_UUID="14467831"; EVENT_SHORT="NJ States"; EVENT_YEAR=2026
NFHS={106,113,120,126,132,138,144,150,157,165,175,190,215,285}

def req(method,path,body=None,headers=None):
    h={"apikey":KEY,"Authorization":"Bearer "+KEY,"Content-Type":"application/json"}
    if headers: h.update(headers)
    data=json.dumps(body).encode() if body is not None else None
    r=urllib.request.Request(SUPA+path,data=data,method=method,headers=h)
    with urllib.request.urlopen(r,timeout=60) as resp:
        raw=resp.read(); return resp.status,(json.loads(raw) if raw else None)

def ensure_event():
    _,rows=req("GET",f"/rest/v1/flo_events?event_uuid=eq.{EVENT_UUID}&select=event_uuid,short_name,year")
    if rows: return False
    if APPLY:
        req("POST","/rest/v1/flo_events",
            body={"event_uuid":EVENT_UUID,"name":f"NJSIAA State Championship {EVENT_YEAR}",
                  "short_name":EVENT_SHORT,"year":EVENT_YEAR,"event_mult":0.85,"active":False},
            headers={"Prefer":"resolution=merge-duplicates,return=minimal"})
    return True

def parse():
    out=[]
    for r in csv.DictReader(open(SRC)):
        try: w=int(r["weight"])
        except: continue
        if w not in NFHS: continue
        try: place=int(r["placement"])
        except: continue
        nm=(r["name"] or "").strip()
        if not nm: continue
        first,_,last=nm.partition(" ")
        guid=str(uuid.uuid5(uuid.NAMESPACE_DNS,f"njstates2026-{first}-{last}-{w}").hex)
        out.append({"event_uuid":EVENT_UUID,"wrestler_guid":guid,
                    "first_name":first.strip(),"last_name":last.strip(),
                    "state":"NJ","division":"OPEN","weight":str(w),"placement":place})
    return out

def main():
    created=ensure_event()
    rows=parse()
    print(f"event 'NJ States {EVENT_YEAR}' ({EVENT_UUID}): {'WILL CREATE' if created else 'exists'}")
    by_w={}
    for r in rows: by_w[r['weight']]=by_w.get(r['weight'],0)+1
    print(f"parsed {len(rows)} NJ placers across {len(by_w)} weights")
    for w in sorted(by_w,key=int): print(f"   {w}: {by_w[w]}")
    print("sample rows:")
    for r in rows[:4]: print("  ",r["first_name"],r["last_name"],"NJ",r["weight"],"place",r["placement"])
    if APPLY:
        for i in range(0,len(rows),200):
            req("POST","/rest/v1/flo_results?on_conflict=event_uuid,wrestler_guid",
                body=rows[i:i+200],headers={"Prefer":"resolution=merge-duplicates,return=minimal"})
        print(f"\nupserted {len(rows)} rows into flo_results as NJ States {EVENT_YEAR}.")
        print("next: run board_engine.py including NJ in AFFECTED (or --nj-only).")
    else:
        print("\nPREVIEW only — re-run with --apply to write.")

if __name__=="__main__": main()
