#!/usr/bin/env python3
"""Diagnose name fragmentation on the GIRLS side BEFORE recomputing.
Girls detected via flo_results.division (flo_events has no division column).
For each girl, count how many distinct wrestler_guids her (first,last,state) maps to.
>1 guid = fragmented = her events won't stack under the new ladder.
  KEY=$(grep -o 'sb_secret_[A-Za-z0-9_-]*' ~/score_all.py | head -1)
  SUPABASE_SECRET="$KEY" python3 girls_frag_check.py
"""
import os,urllib.request,json,gzip
from collections import defaultdict
S='https://yewigtsyvxiwprcpicup.supabase.co'; K=os.environ['SUPABASE_SECRET']
def g(p):
    r=urllib.request.Request(S+p,headers={'apikey':K,'Authorization':'Bearer '+K,'Accept-Encoding':'gzip'})
    d=urllib.request.urlopen(r,timeout=120).read()
    try: d=gzip.decompress(d)
    except: pass
    return json.loads(d)

evname={e['event_uuid']:e.get('short_name') for e in g('/rest/v1/flo_events?select=event_uuid,short_name')}
rows=g('/rest/v1/flo_results?select=wrestler_guid,first_name,last_name,state,division,event_uuid,placement')

GIRL_TOK=('girl','women','woman','female','wmn',"girls",' wm')
def is_girl_row(r):
    return any(t in (r.get('division') or '').lower() for t in GIRL_TOK)

girls=[r for r in rows if is_girl_row(r)]
# show what division strings exist so we trust the detection
from collections import Counter
divs=Counter((r.get('division') or '∅') for r in rows)
print("ALL division values in flo_results (top 20):")
for d,n in divs.most_common(20): print(f"   {n:>6}  {d!r}")
print(f"\nrows flagged GIRLS: {len(girls)} of {len(rows)}")

byname=defaultdict(set); byname_ev=defaultdict(list)
for r in girls:
    key=(r['first_name'].strip().lower(), r['last_name'].strip().lower(), (r.get('state') or '').upper())
    byname[key].add(r['wrestler_guid'])
    byname_ev[key].append((evname.get(r['event_uuid'],'?'), r.get('placement')))

frag=[(k,v) for k,v in byname.items() if len(v)>1]
uniq=len(byname) or 1
print(f"\nunique girls (name,state): {len(byname)}")
print(f"FRAGMENTED (>1 guid): {len(frag)}  ({100*len(frag)//uniq}%)")
print("\nworst offenders (most split identities):")
for k,v in sorted(frag,key=lambda x:-len(x[1]))[:15]:
    print(f"  {k[0]} {k[1]} ({k[2]}): {len(v)} guids | {byname_ev[k][:6]}")
