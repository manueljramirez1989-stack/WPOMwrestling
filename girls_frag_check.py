#!/usr/bin/env python3
"""Diagnose name fragmentation on the GIRLS side BEFORE recomputing.
For each girl appearing in flo_results, count how many distinct wrestler_guids
her (first,last,state) maps to. >1 guid = fragmented = her events won't stack.
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

ev={e['event_uuid']:(e.get('short_name'),e.get('division') or '') for e in g('/rest/v1/flo_events?select=event_uuid,short_name,division')}
# pull all results; filter to girls divisions
rows=g('/rest/v1/flo_results?select=wrestler_guid,first_name,last_name,state,division,event_uuid,placement')
GIRL_TOK=('girl','women','woman','female','wmn',"girls",'wmn')
def is_girl_row(r):
    d=(r.get('division') or '').lower()
    evd=(ev.get(r['event_uuid'],('',''))[1] or '').lower()
    return any(t in d for t in GIRL_TOK) or any(t in evd for t in GIRL_TOK)

girls=[r for r in rows if is_girl_row(r)]
byname=defaultdict(set); byname_ev=defaultdict(list)
for r in girls:
    key=(r['first_name'].strip().lower(), r['last_name'].strip().lower(), (r.get('state') or '').upper())
    byname[key].add(r['wrestler_guid'])
    byname_ev[key].append((ev.get(r['event_uuid'],('?',''))[0], r.get('placement')))

frag=[(k,v) for k,v in byname.items() if len(v)>1]
print(f"girls in flo_results: {len(girls)} rows, {len(byname)} unique (name,state)")
print(f"FRAGMENTED (>1 guid): {len(frag)} girls  ({100*len(frag)//max(1,len(byname))}%)")
print("\nworst offenders (most split identities):")
for k,v in sorted(frag,key=lambda x:-len(x[1]))[:15]:
    evs=byname_ev[k]
    print(f"  {k[0]} {k[1]} ({k[2]}): {len(v)} guids | events: {evs[:6]}")
