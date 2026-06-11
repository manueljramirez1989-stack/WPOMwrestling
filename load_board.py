#!/usr/bin/env python3
# Fetch LIVE wpomwrestling.com, extract HS_DATA + HS_DATA_GIRLS exactly (via node),
# upsert into public.hs_board. No file transfers needed.
# Usage on Crostini:
#   SUPABASE_SECRET="sb_secret_..." python3 load_board.py
import os, json, urllib.request, subprocess, tempfile

SUPA = "https://yewigtsyvxiwprcpicup.supabase.co"
KEY  = os.environ["SUPABASE_SECRET"]
SITE = os.environ.get("SITE", "https://wpomwrestling.com")

print("fetching", SITE, "...")
req = urllib.request.Request(SITE, headers={"User-Agent": "Mozilla/5.0"})
html = urllib.request.urlopen(req, timeout=120).read().decode("utf-8")
print("page bytes:", len(html))
hp = tempfile.NamedTemporaryFile(suffix=".html", delete=False); hp.write(html.encode()); hp.close()

NODE = r'''
const fs=require("fs");
const html=fs.readFileSync(process.argv[2],"utf8");
function grab(name){
  const kw=html.indexOf("const "+name);
  if(kw<0) throw new Error(name+" not found");
  const bo=html.indexOf("{",kw);
  let d=0,ins=false,e=false,i=bo;
  for(;i<html.length;i++){const c=html[i];
    if(ins){ if(e)e=false; else if(c==="\\")e=true; else if(c==="'")ins=false; }
    else { if(c==="'")ins=true; else if(c==="{")d++; else if(c==="}"){d--; if(d===0){i++;break;}}}
  }
  return eval("("+html.slice(bo,i)+")");
}
const boys=grab("HS_DATA"), girls=grab("HS_DATA_GIRLS");
const rows=[];
for(const w of Object.keys(boys))  rows.push({board:"boys", weight:Number(w), athletes:boys[w]});
for(const w of Object.keys(girls)) rows.push({board:"girls",weight:Number(w), athletes:girls[w]});
process.stdout.write(JSON.stringify(rows));
'''
np = tempfile.NamedTemporaryFile(suffix=".js", delete=False); np.write(NODE.encode()); np.close()
out = subprocess.run(["node", np.name, hp.name], capture_output=True, text=True)
if out.returncode != 0:
    raise SystemExit("node extraction failed: " + out.stderr[:400])
rows = json.loads(out.stdout)
nb = sum(len(r["athletes"]) for r in rows if r["board"] == "boys")
ng = sum(len(r["athletes"]) for r in rows if r["board"] == "girls")
print("extracted:", nb, "boys,", ng, "girls,", len(rows), "rows")

ok = 0
for row in rows:
    body = json.dumps(row).encode()
    rq = urllib.request.Request(
        SUPA + "/rest/v1/hs_board?on_conflict=board,weight",
        data=body, method="POST",
        headers={"apikey": KEY, "Authorization": "Bearer " + KEY,
                 "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=minimal"})
    with urllib.request.urlopen(rq, timeout=60) as r:
        ok += 1
        print("  %s %s: %d athletes" % (row["board"], row["weight"], len(row["athletes"])))
print("loaded %d/%d rows into hs_board" % (ok, len(rows)))
print("verify in SQL editor: select board,weight,jsonb_array_length(athletes) n from hs_board order by 1,2;")
