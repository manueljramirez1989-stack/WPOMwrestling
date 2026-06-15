#!/usr/bin/env python3
"""
Backfill IHSA 2026 (Illinois) into flo_results as a first-class 'IL States 2026'
event, from the transcribed il_results.txt. Makes IL recomputable through the
engine like every other state (no more decomposition).

Steps it performs:
  1. ensure flo_events has an 'IL States' 2026 row (creates if missing) -> event_uuid
  2. parse il_results.txt -> rows (name, weight, placement, state=IL, division)
  3. upsert into flo_results on (event_uuid, wrestler_guid)

Placement mapping (IHSA places to 6th): 1st->1, 2nd->2, 3rd->3, 4th->4, 5th->5, 6th->6.
The file lists pairs "1st: A | B" = 1st & 2nd; "3rd: C | D" = 3rd & 4th; "5th: E | F" = 5th & 6th.

Usage (Crostini):
  KEY=$(grep -o 'sb_secret_[A-Za-z0-9_-]*' ~/score_all.py | head -1)
  SUPABASE_SECRET="$KEY" python3 il_to_flo_results.py            # preview
  SUPABASE_SECRET="$KEY" python3 il_to_flo_results.py --apply    # write flo_events + flo_results
"""
import os, sys, re, json, hashlib, urllib.request

SUPA="https://yewigtsyvxiwprcpicup.supabase.co"
KEY=os.environ["SUPABASE_SECRET"]
APPLY="--apply" in sys.argv
SRC="il_results.txt"
EVENT_SHORT="IL States"; EVENT_YEAR=2026
NFHS={106,113,120,126,132,138,144,150,157,165,175,190,215,285}
PAIR_TO_PLACES={"1st":(1,2),"3rd":(3,4),"5th":(5,6)}

def req(method,path,body=None,headers=None):
    h={"apikey":KEY,"Authorization":"Bearer "+KEY,"Content-Type":"application/json"}
    if headers: h.update(headers)
    data=json.dumps(body).encode() if body is not None else None
    r=urllib.request.Request(SUPA+path,data=data,method=method,headers=h)
    with urllib.request.urlopen(r,timeout=60) as resp:
        raw=resp.read()
        return resp.status, (json.loads(raw) if raw else None)

# ── 1. event_uuid for IL States 2026 ─────────────────────────────────
def ensure_event():
    _,rows=req("GET",f"/rest/v1/flo_events?short_name=eq.{urllib.parse.quote(EVENT_SHORT)}&year=eq.{EVENT_YEAR}&select=event_uuid,short_name,year")
    if rows: return rows[0]["event_uuid"], False
    # deterministic uuid so re-runs don't duplicate
    import uuid
    eid=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"wpom-ilstates-{EVENT_YEAR}"))
    if APPLY:
        req("POST","/rest/v1/flo_events",
            body={"event_uuid":eid,"name":f"IHSA Illinois State Championship {EVENT_YEAR}",
                  "short_name":EVENT_SHORT,"year":EVENT_YEAR,"event_mult":0.85,"active":False},
            headers={"Prefer":"resolution=merge-duplicates,return=minimal"})
    return eid, True

import urllib.parse

# ── 2. parse il_results.txt ──────────────────────────────────────────
def parse():
    rows=[]; cur_w=None; cur_div=None
    for raw in open(SRC):
        line=raw.strip()
        if not line: continue
        m=re.match(r'^([123]A)\s+(\d+)$', line)
        if m:
            cls,w=m.group(1),int(m.group(2))
            cur_w = w if w in NFHS else None
            cur_div=f"Class {cls} Boys"
            continue
        m=re.match(r'^(1st|3rd|5th):\s*(.+)$', line)
        if m and cur_w:
            pa,pb=PAIR_TO_PLACES[m.group(1)]
            names=[n.strip() for n in m.group(2).split('|')]
            for place,nm in zip((pa,pb), names):
                nm=re.sub(r'\s*\([^)]*\)\s*$','',nm).strip()   # drop "(School)"
                if not nm: continue
                first,_,last=nm.partition(' ')
                rows.append({"first":first.strip(),"last":last.strip(),
                             "weight":str(cur_w),"placement":place,"division":cur_div})
    return rows

def main():
    eid, created = ensure_event()
    rows=parse()
    print(f"event 'IL States {EVENT_YEAR}': {eid}  ({'WILL CREATE' if created else 'exists'})")
    print(f"parsed {len(rows)} IL placers across {len({r['weight'] for r in rows})} weights")
    by_w={}
    for r in rows: by_w[r['weight']]=by_w.get(r['weight'],0)+1
    for w in sorted(by_w,key=int): print(f"   {w}: {by_w[w]}")
    # build flo_results rows with a deterministic wrestler_guid (so re-runs upsert, not dupe)
    out=[]
    for r in rows:
        guid=str(__import__('uuid').uuid5(__import__('uuid').NAMESPACE_DNS,
              f"ilstates2026-{r['first']}-{r['last']}-{r['weight']}").hex)
        out.append({"event_uuid":eid,"wrestler_guid":guid,"first_name":r["first"],
                    "last_name":r["last"],"state":"IL","division":r["division"],
                    "weight":r["weight"],"placement":r["placement"]})
    print(f"\nsample rows:")
    for r in out[:4]: print("  ",r["first_name"],r["last_name"],"IL",r["weight"],"place",r["placement"])
    if APPLY:
        for i in range(0,len(out),200):
            req("POST","/rest/v1/flo_results?on_conflict=event_uuid,wrestler_guid",
                body=out[i:i+200], headers={"Prefer":"resolution=merge-duplicates,return=minimal"})
        print(f"\nupserted {len(out)} rows into flo_results as IL States {EVENT_YEAR}.")
        print("next: re-run board_engine.py (IL now flows through calc_wpom).")
    else:
        print("\nPREVIEW only — re-run with --apply to write flo_events + flo_results.")

if __name__=="__main__": main()
