#!/usr/bin/env python3
"""
Turn the scraped solar-ads.csv into a single self-contained, interactive HTML
report — sortable columns and live filters (kWh range, max $/kWh, min ad age,
hide suspect parses, advertiser search). No internet or dependencies needed to
view it; just open the file in a browser.

    python report_html.py                 # reads out/solar-ads.csv -> out/solar-ads.html
    python report_html.py --open          # also open it in your browser
"""

from __future__ import annotations

import argparse
import csv
import json
import webbrowser
from pathlib import Path

HERE = Path(__file__).parent


def to_num(v):
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return None


def load_rows(csv_path: Path) -> list[dict]:
    with open(csv_path) as f:
        raw = list(csv.DictReader(f))
    rows = []
    for r in raw:
        rows.append({
            "advertiser": r.get("page_name", ""),
            "price": to_num(r.get("price_aud")),
            "kwh": to_num(r.get("capacity_kwh")),
            "per_kwh": to_num(r.get("dollars_per_kwh")),
            "rebate": to_num(r.get("est_rebate_aud")),
            "pre_rebate": to_num(r.get("price_if_pre_rebate")),
            "days": to_num(r.get("days_running")),
            "copies": to_num(r.get("copies_running")),
            "status": r.get("status", ""),
            "ended": r.get("ended", ""),
            "flag": r.get("flag", ""),
            "landing": r.get("landing_url", ""),
            "library": r.get("library_url", ""),
            # Prefer the locally downloaded creative (permanent); fall back to
            # the original Facebook CDN url (may expire). HTML lives in out/,
            # images in ../images/, so prefix the local path with "../".
            "img": ("../" + r["image_file"]) if r.get("image_file")
                   else r.get("image_url", ""),
        })
    return rows


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Solar Battery Ad Deals</title>
<style>
  :root { --good:#1a7f37; --warn:#9a6700; --bad:#cf222e; --line:#d0d7de; }
  * { box-sizing: border-box; }
  body { font: 14px/1.45 -apple-system, system-ui, sans-serif; margin: 0; color: #1f2328; background: #f6f8fa; }
  header { padding: 16px 20px; background: #0d1117; color: #fff; }
  header h1 { margin: 0 0 4px; font-size: 18px; }
  header .sub { color: #9da7b3; font-size: 13px; }
  .controls { display: flex; flex-wrap: wrap; gap: 14px; align-items: flex-end;
              padding: 14px 20px; background: #fff; border-bottom: 1px solid var(--line); position: sticky; top: 0; z-index: 5; }
  .controls label { display: flex; flex-direction: column; font-size: 11px; color: #57606a; gap: 3px; text-transform: uppercase; letter-spacing: .03em; }
  .controls input[type=number], .controls input[type=text] { width: 110px; padding: 5px 7px; border: 1px solid var(--line); border-radius: 6px; font-size: 13px; }
  .controls input[type=text] { width: 200px; }
  .controls .chk { flex-direction: row; align-items: center; gap: 6px; text-transform: none; letter-spacing: 0; font-size: 13px; color: #1f2328; }
  .controls button { padding: 6px 12px; border: 1px solid var(--line); background: #f6f8fa; border-radius: 6px; cursor: pointer; font-size: 13px; }
  .count { padding: 8px 20px; color: #57606a; font-size: 13px; background: #fff; border-bottom: 1px solid var(--line); }
  .wrap { overflow-x: auto; padding: 0 8px 40px; }
  table { border-collapse: collapse; width: 100%; background: #fff; }
  th, td { padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--line); white-space: nowrap; }
  th { background: #f6f8fa; cursor: pointer; user-select: none; position: sticky; top: 64px; font-size: 12px; }
  th:hover { background: #eaeef2; }
  th .arrow { color: #57606a; font-size: 10px; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  tr:hover td { background: #f6f8fa; }
  .pill { padding: 1px 7px; border-radius: 999px; font-size: 11px; font-weight: 600; }
  .pill.good { background: #dafbe1; color: var(--good); }
  .pill.mid { background: #fff4d6; color: var(--warn); }
  .pill.bad { background: #ffe0e0; color: var(--bad); }
  .flagcell { color: var(--bad); font-size: 11px; font-weight: 600; }
  a { color: #0969da; text-decoration: none; }
  a:hover { text-decoration: underline; }
  .note { padding: 12px 20px; color: #57606a; font-size: 12px; }
  td.thumb { padding: 4px; }
  td.thumb img { width: 84px; height: 84px; object-fit: cover; border-radius: 6px; border: 1px solid var(--line); display: block; }
  /* Gallery view */
  #gallery { display: none; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; padding: 16px 20px 50px; }
  .card { background: #fff; border: 1px solid var(--line); border-radius: 10px; overflow: hidden; display: flex; flex-direction: column; }
  .card img { width: 100%; height: 200px; object-fit: cover; background: #f0f0f0; }
  .card .body { padding: 10px 12px; font-size: 13px; }
  .card .adv { font-weight: 600; margin-bottom: 4px; }
  .card .meta { color: #57606a; font-size: 12px; }
  .card .big { font-size: 17px; font-weight: 700; }
  .card a.open { display: block; padding: 8px 12px; border-top: 1px solid var(--line); font-size: 12px; }
</style>
</head>
<body>
<header>
  <h1>Solar Battery Ad Deals &middot; Meta Ad Library (AU)</h1>
  <div class="sub">__SUBTITLE__ &middot; click a column to sort &middot; rows colour-coded by $/usable kWh</div>
</header>

<div class="controls">
  <label>Min kWh <input type="number" id="minKwh" placeholder="any"></label>
  <label>Max kWh <input type="number" id="maxKwh" placeholder="any"></label>
  <label>Max $/kWh <input type="number" id="maxPerKwh" placeholder="any"></label>
  <label>Min days live <input type="number" id="minDays" placeholder="any"></label>
  <label>Search advertiser <input type="text" id="search" placeholder="name..."></label>
  <label>Status
    <select id="status" style="padding:5px 7px;border:1px solid var(--line);border-radius:6px;font-size:13px;">
      <option value="">All</option>
      <option value="Active">Active only</option>
      <option value="Ended">Ended only</option>
    </select>
  </label>
  <label class="chk"><input type="checkbox" id="pricedOnly" checked> Priced only</label>
  <label class="chk"><input type="checkbox" id="hideFlagged" checked> Hide bad parses</label>
  <button id="viewToggle">Gallery view</button>
  <button id="reset">Reset</button>
</div>
<div class="count" id="count"></div>
<div class="wrap" id="tableWrap">
  <table id="tbl">
    <thead><tr></tr></thead>
    <tbody></tbody>
  </table>
</div>
<div id="gallery"></div>
<div class="note">
  Tip: a price <em>below</em> the rebate estimate means the advertised figure is already your
  out-of-pocket (post-rebate) price. Always confirm with the installer: post-rebate?, usable vs
  nominal kWh?, inverter + install included?, and can it charge from the grid off-peak?
</div>

<script>
const DATA = __DATA__;
const COLS = [
  {key:"thumb",      label:"Ad",         num:false},
  {key:"advertiser", label:"Advertiser", num:false},
  {key:"kwh",        label:"kWh",        num:true},
  {key:"price",      label:"Price $",    num:true},
  {key:"per_kwh",    label:"$/kWh",      num:true},
  {key:"rebate",     label:"Est rebate", num:true},
  {key:"pre_rebate", label:"If pre-rebate", num:true},
  {key:"days",       label:"Days run",   num:true},
  {key:"status",     label:"Status",     num:false},
  {key:"ended",      label:"Ended",      num:false},
  {key:"copies",     label:"Copies",     num:true},
  {key:"flag",       label:"Flag",       num:false},
  {key:"links",      label:"Links",      num:false},
];
let sortKey = "per_kwh", sortAsc = true, view = "table";

function dealClass(v){ if(v==null) return ""; if(v<=300) return "good"; if(v<=450) return "mid"; return "bad"; }

function buildHead(){
  const tr = document.querySelector("thead tr");
  tr.innerHTML = "";
  for(const c of COLS){
    const th = document.createElement("th");
    const arrow = (c.key===sortKey) ? (sortAsc?" ▲":" ▼") : "";
    th.innerHTML = c.label + '<span class="arrow">'+arrow+'</span>';
    if(c.key!=="links" && c.key!=="thumb") th.onclick = ()=>{ if(sortKey===c.key) sortAsc=!sortAsc; else {sortKey=c.key; sortAsc=c.num?true:true;} render(); };
    tr.appendChild(th);
  }
}

function fmt(v){ return v==null ? "" : (typeof v==="number" ? v.toLocaleString() : v); }

function filtered(){
  const minKwh=parseFloat(minKwhEl.value), maxKwh=parseFloat(maxKwhEl.value);
  const maxPK=parseFloat(maxPerKwhEl.value), minD=parseFloat(minDaysEl.value);
  const q=searchEl.value.trim().toLowerCase();
  const st=statusEl.value;
  return DATA.filter(r=>{
    if(pricedOnlyEl.checked && (r.per_kwh==null)) return false;
    if(hideFlaggedEl.checked && r.flag) return false;
    if(st && r.status!==st) return false;
    if(!isNaN(minKwh) && (r.kwh==null || r.kwh<minKwh)) return false;
    if(!isNaN(maxKwh) && (r.kwh==null || r.kwh>maxKwh)) return false;
    if(!isNaN(maxPK) && (r.per_kwh==null || r.per_kwh>maxPK)) return false;
    if(!isNaN(minD) && (r.days==null || r.days<minD)) return false;
    if(q && !(r.advertiser||"").toLowerCase().includes(q)) return false;
    return true;
  });
}

function render(){
  buildHead();
  let rows = filtered();
  rows.sort((a,b)=>{
    let x=a[sortKey], y=b[sortKey];
    if(x==null) return 1; if(y==null) return -1;
    if(typeof x==="string"){ x=x.toLowerCase(); y=(y||"").toLowerCase(); }
    return (x<y?-1:x>y?1:0) * (sortAsc?1:-1);
  });
  if(view==="gallery"){ renderGallery(rows); countEl.textContent = rows.length + " of " + DATA.length + " ads shown"; return; }
  const tb = document.querySelector("tbody");
  tb.innerHTML = "";
  for(const r of rows){
    const tr = document.createElement("tr");
    const cells = COLS.map(c=>{
      if(c.key==="thumb"){
        if(!r.img) return '<td class="thumb"></td>';
        const link = r.library || r.landing || r.img;
        return '<td class="thumb"><a href="'+link+'" target="_blank">'
             + '<img loading="lazy" src="'+r.img+'" onerror="this.style.display=\'none\'"></a></td>';
      }
      if(c.key==="links"){
        let h="";
        if(r.library) h+='<a href="'+r.library+'" target="_blank">ad</a>';
        if(r.landing) h+=(h?' &middot; ':'')+'<a href="'+r.landing+'" target="_blank">site</a>';
        return '<td>'+h+'</td>';
      }
      if(c.key==="per_kwh"){
        if(r.per_kwh==null) return '<td class="num"></td>';
        return '<td class="num"><span class="pill '+dealClass(r.per_kwh)+'">'+r.per_kwh.toLocaleString()+'</span></td>';
      }
      if(c.key==="flag") return '<td class="flagcell">'+(r.flag||"")+'</td>';
      const cls = c.num ? "num" : "";
      return '<td class="'+cls+'">'+fmt(r[c.key])+'</td>';
    });
    tr.innerHTML = cells.join("");
    tb.appendChild(tr);
  }
  countEl.textContent = rows.length + " of " + DATA.length + " ads shown";
}

function renderGallery(rows){
  const g = document.getElementById("gallery");
  g.innerHTML = "";
  for(const r of rows){
    const link = r.library || r.landing || "#";
    const img = r.img ? '<img loading="lazy" src="'+r.img+'" onerror="this.style.visibility=\'hidden\'">' : '<div style="height:200px"></div>';
    const pk = r.per_kwh!=null ? '<span class="pill '+dealClass(r.per_kwh)+'">$'+r.per_kwh.toLocaleString()+'/kWh</span>' : '';
    const price = r.price!=null ? '$'+r.price.toLocaleString() : 'quote only';
    const kwh = r.kwh!=null ? r.kwh+' kWh' : '';
    const st = r.status ? ' &middot; '+r.status : '';
    const days = r.days!=null ? ' &middot; '+r.days+'d' : '';
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML =
      '<a href="'+link+'" target="_blank">'+img+'</a>'
      + '<div class="body"><div class="adv">'+(r.advertiser||'')+'</div>'
      + '<div class="big">'+price+' '+pk+'</div>'
      + '<div class="meta">'+kwh+st+days+(r.flag?' &middot; ⚠ '+r.flag:'')+'</div></div>'
      + '<a class="open" href="'+link+'" target="_blank">Open ad ↗</a>';
    g.appendChild(card);
  }
}

const minKwhEl=document.getElementById("minKwh"), maxKwhEl=document.getElementById("maxKwh"),
  maxPerKwhEl=document.getElementById("maxPerKwh"), minDaysEl=document.getElementById("minDays"),
  searchEl=document.getElementById("search"), statusEl=document.getElementById("status"),
  pricedOnlyEl=document.getElementById("pricedOnly"),
  hideFlaggedEl=document.getElementById("hideFlagged"), countEl=document.getElementById("count");
[minKwhEl,maxKwhEl,maxPerKwhEl,minDaysEl,searchEl,statusEl,pricedOnlyEl,hideFlaggedEl].forEach(el=>{
  el.addEventListener("input", render);
});
document.getElementById("viewToggle").onclick=(e)=>{
  view = (view==="table") ? "gallery" : "table";
  document.getElementById("tableWrap").style.display = (view==="table") ? "" : "none";
  document.getElementById("gallery").style.display = (view==="gallery") ? "grid" : "none";
  e.target.textContent = (view==="table") ? "Gallery view" : "Table view";
  render();
};
document.getElementById("reset").onclick=()=>{
  [minKwhEl,maxKwhEl,maxPerKwhEl,minDaysEl,searchEl].forEach(el=>el.value="");
  statusEl.value=""; pricedOnlyEl.checked=true; hideFlaggedEl.checked=true; sortKey="per_kwh"; sortAsc=true; render();
};
render();
</script>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Build interactive HTML report")
    ap.add_argument("--csv", default=str(HERE / "out" / "solar-ads.csv"))
    ap.add_argument("--out", default=str(HERE / "out" / "solar-ads.html"))
    ap.add_argument("--open", action="store_true", help="open in browser when done")
    args = ap.parse_args()

    rows = load_rows(Path(args.csv))
    priced = sum(1 for r in rows if r["per_kwh"] is not None)
    subtitle = f"{len(rows)} ads &middot; {priced} with a price"

    html = (HTML_TEMPLATE
            .replace("__DATA__", json.dumps(rows))
            .replace("__SUBTITLE__", subtitle))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    print(f"[html] {len(rows)} ads ({priced} priced) -> {out}")
    if args.open:
        webbrowser.open(out.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
