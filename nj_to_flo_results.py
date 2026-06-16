#!/usr/bin/env python3
"""NJ 2026 backfill -> flo_results event 'NJ States' (14467831).
Self-fetching: pulls nj_2026_athletes.csv from the repo raw URL, so the CSV
never needs to be on Crostini's filesystem. Idempotent.

  KEY=$(grep -o 'sb_secret_[A-Za-z0-9_-]*' ~/score_all.py | head -1)
  SUPABASE_SECRET="$KEY" python3 nj_to_flo_results.py            # preview
  SUPABASE_SECRET="$KEY" python3 nj_to_flo_results.py --apply    # write
"""
import os, sys, csv, io, json, uuid, urllib.request

SUPA="https://yewigtsyvxiwprcpicup.supabase.co"; KEY=os.environ["SUPABASE_SECRET"]
APPLY="--apply" in sys.argv
EV="14467831"
CSV_URL=os.environ.get("NJ_CSV_URL",
  "https://raw.githubusercontent.com/manueljramirez1989-stack/WPOMwrestling/main/nj_2026_athletes.csv")
NFHS={106,113,120,126,132,138,144,150,157,165,175,190,215,285}

def req(method,path,b=None,h=None):
    H={"apikey":KEY,"Authorization":"Bearer "+KEY,"Content-Type":"application/json"}
    if h:H.update(h)
    d=json.dumps(b).encode() if b is not None else None
    r=urllib.request.Request(SUPA+path,data=d,method=method,headers=H)
    with urllib.request.urlopen(r,timeout=60) as x:
        raw=x.read(); return x.status,(json.loads(raw) if raw else None)

def fetch_csv():
    print("fetching CSV:", CSV_URL)
    txt=urllib.request.urlopen(CSV_URL,timeout=60).read().decode("utf-8")
    if txt.strip().startswith("404") or len(txt)<100:
        raise SystemExit("CSV fetch failed (got %d bytes) — check it's committed to the repo." % len(txt))
    return list(csv.DictReader(io.StringIO(txt)))

def ensure_event():
    _,rows=req("GET",f"/rest/v1/flo_events?event_uuid=eq.{EV}&select=event_uuid")
    if rows: return False
    if APPLY:
        req("POST","/rest/v1/flo_events",b={"event_uuid":EV,"name":"NJSIAA State Championship 2026",
            "short_name":"NJ States","year":2026,"event_mult":0.85,"active":False},
            h={"Prefer":"resolution=merge-duplicates,return=minimal"})
    return True

def main():
    src=fetch_csv()
    created=ensure_event()
    out=[]
    for r in src:
        try: w=int(r["weight"]); pl=int(r["placement"])
        except: continue
        if w not in NFHS: continue
        nm=(r["name"] or "").strip()
        if not nm: continue
        first,_,last=nm.partition(" ")
        guid=str(uuid.uuid5(uuid.NAMESPACE_DNS,f"njstates2026-{first}-{last}-{w}").hex)
        out.append({"event_uuid":EV,"wrestler_guid":guid,"first_name":first.strip(),
                    "last_name":last.strip(),"state":"NJ","division":"OPEN",
                    "weight":str(w),"placement":pl})
    by={}
    for r in out: by[r["weight"]]=by.get(r["weight"],0)+1
    print(f"event NJ States 2026 ({EV}): {'WILL CREATE' if created else 'exists'}")
    print(f"{len(out)} NJ placers across {len(by)} weights")
    for w in sorted(by,key=int): print(f"   {w}: {by[w]}")
    for r in out[:4]: print("  ",r["first_name"],r["last_name"],"NJ",r["weight"],"place",r["placement"])
    if APPLY:
        for i in range(0,len(out),200):
            req("POST","/rest/v1/flo_results?on_conflict=event_uuid,wrestler_guid",
                b=out[i:i+200],h={"Prefer":"resolution=merge-duplicates,return=minimal"})
        print(f"\nupserted {len(out)} rows into flo_results as NJ States 2026.")
        print("next: SUPABASE_SECRET=\"$KEY\" python3 board_engine.py --nj-only")
    else:
        print("\nPREVIEW only — re-run with --apply to write.")

if __name__=="__main__": main()
