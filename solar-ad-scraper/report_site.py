#!/usr/bin/env python3
"""
Build a self-contained, password-protectable static site from the ad archive,
ready to deploy to Netlify.

Why a generated site rather than hosting Mission Control: the app is a Node
server build that depends on better-sqlite3 (a native addon) and reads creative
images off local disk. Netlify Functions are ephemeral with no persistent disk,
so running it there would mean swapping SQLite for a hosted database and moving
every creative to object storage. A static snapshot needs none of that.

    python report_site.py                  # build out/netlify/
    python report_site.py --images all     # every creative, not one per ad
    python report_site.py --images none    # text only, ~2 MB

The site mirrors the dashboard's filters and, importantly, its caveats: state
figures are inferred rather than reported by Meta, and weakly-attributed states
are shown muted so a shared link cannot overstate what the data supports.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import store

HERE = Path(__file__).parent
CREATIVES_DIR = HERE.parent / ".data" / "solar-ads" / "creatives"

# Ported from src/lib/solar-ads-db.ts so the site and the dashboard resolve the
# same ad to the same state, price and status. Divergence here would be worse
# than useless: two views of one archive that quietly disagree.
BEST_STATE_CTE = """
  best_state AS (
    SELECT ad_archive_id,
           CASE WHEN MAX(state = 'NATIONAL') = 1 THEN 'NATIONAL'
                ELSE (
                  SELECT s2.state FROM ad_states s2
                   WHERE s2.ad_archive_id = s1.ad_archive_id
                     AND s2.state != 'NATIONAL'
                   GROUP BY s2.state
                   ORDER BY MAX(CASE s2.confidence WHEN 'high' THEN 3
                                                   WHEN 'medium' THEN 2 ELSE 1 END) DESC,
                            COUNT(*) DESC
                   LIMIT 1)
           END AS state,
           MAX(CASE confidence WHEN 'high' THEN 3
                               WHEN 'medium' THEN 2 ELSE 1 END) AS conf_rank
      FROM ad_states s1
     GROUP BY ad_archive_id
  )
"""

LATEST_PRICE_CTE = """
  latest_price AS (
    SELECT p.* FROM price_observations p
     WHERE p.source = 'regex'
       AND p.id = (SELECT MAX(id) FROM price_observations
                    WHERE ad_archive_id = p.ad_archive_id AND source = 'regex')
  )
"""

LATEST_SNAPSHOT_CTE = """
  latest_snap AS (
    SELECT s.* FROM ad_snapshots s
     WHERE s.id = (SELECT MAX(id) FROM ad_snapshots
                    WHERE ad_archive_id = s.ad_archive_id)
  )
"""

LATEST_OFFER_CTE = """
  latest_offer AS (
    SELECT o.* FROM ad_offers o
     WHERE o.id = (SELECT MAX(id) FROM ad_offers
                    WHERE ad_archive_id = o.ad_archive_id)
  )
"""


def load_ads(conn, limit: int | None = None) -> list[dict]:
    sql = f"""
      WITH {BEST_STATE_CTE}, {LATEST_PRICE_CTE}, {LATEST_SNAPSHOT_CTE}, {LATEST_OFFER_CTE}
      SELECT a.ad_archive_id, a.page_name, a.title, a.body_text, a.link_url,
             a.start_date,
             bs.state AS best_state, bs.conf_rank,
             p.price_aud, p.capacity_kwh, p.system_kw, p.dollars_per_kwh,
             p.dollars_per_kw, p.est_rebate_aud, p.flag,
             snap.is_active, snap.collation_count, snap.days_running,
             o.product_category, o.brand, o.offer_type, o.price_basis,
             (SELECT sha1 FROM creatives WHERE ad_archive_id = a.ad_archive_id
               ORDER BY rowid LIMIT 1) AS creative_sha,
             (SELECT GROUP_CONCAT(DISTINCT state) FROM ad_states
               WHERE ad_archive_id = a.ad_archive_id AND confidence = 'high'
                 AND state != 'NATIONAL') AS states_high
        FROM ads a
        LEFT JOIN best_state   bs   ON bs.ad_archive_id   = a.ad_archive_id
        LEFT JOIN latest_price p    ON p.ad_archive_id    = a.ad_archive_id
        LEFT JOIN latest_snap  snap ON snap.ad_archive_id = a.ad_archive_id
        LEFT JOIN latest_offer o    ON o.ad_archive_id    = a.ad_archive_id
       ORDER BY (p.dollars_per_kwh IS NULL),
                (COALESCE(p.flag, '') != ''),
                p.dollars_per_kwh
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    out = []
    for r in conn.execute(sql):
        out.append({
            "id": r["ad_archive_id"],
            "adv": r["page_name"] or "",
            "title": r["title"] or "",
            "body": (r["body_text"] or "")[:1200],
            "url": r["link_url"] or "",
            "state": r["best_state"] or "",
            "conf": r["conf_rank"] or 0,
            "states": [s for s in (r["states_high"] or "").split(",") if s],
            "price": r["price_aud"],
            "kwh": r["capacity_kwh"],
            "kw": r["system_kw"],
            "perKwh": r["dollars_per_kwh"],
            "perKw": r["dollars_per_kw"],
            "flag": r["flag"] or "",
            "active": r["is_active"],
            "variants": r["collation_count"],
            "days": r["days_running"],
            "cat": r["product_category"] or "",
            "brand": r["brand"] or "",
            "offer": r["offer_type"] or "",
            "sha": r["creative_sha"] or "",
        })
    return out


def load_stats(conn) -> dict:
    def rows(sql, args=()):
        return [dict(r) for r in conn.execute(sql, args)]

    totals = dict(conn.execute("""
        SELECT (SELECT COUNT(*) FROM ads)         AS ads,
               (SELECT COUNT(*) FROM advertisers) AS advertisers,
               (SELECT COUNT(*) FROM creatives)   AS creatives,
               (SELECT COUNT(*) FROM ad_offers)   AS enriched
    """).fetchone())

    by_state = rows("""
        SELECT s.state,
               COUNT(DISTINCT CASE WHEN s.confidence='high' THEN s.ad_archive_id END) AS high,
               COUNT(DISTINCT s.ad_archive_id) AS total
          FROM ad_states s
         WHERE s.state != 'NATIONAL'
           AND s.ad_archive_id NOT IN (SELECT ad_archive_id FROM ad_states
                                        WHERE state='NATIONAL')
         GROUP BY s.state ORDER BY high DESC, total DESC
    """)

    advertisers = rows("""
        SELECT a.page_name AS name, COUNT(*) AS ads,
               (SELECT GROUP_CONCAT(DISTINCT st.state) FROM ad_states st
                 JOIN ads a2 ON a2.ad_archive_id = st.ad_archive_id
                WHERE a2.page_id = a.page_id AND st.confidence='high'
                  AND st.state != 'NATIONAL') AS states
          FROM ads a
         WHERE COALESCE(a.page_name,'') != ''
         GROUP BY a.page_name, a.page_id
         ORDER BY ads DESC LIMIT 60
    """)

    by_cat = rows("""
        SELECT COALESCE(o.product_category,'unclassified') AS cat, COUNT(*) AS n
          FROM ads a LEFT JOIN ad_offers o ON o.ad_archive_id = a.ad_archive_id
         GROUP BY cat ORDER BY n DESC
    """)

    national = conn.execute(
        "SELECT COUNT(DISTINCT ad_archive_id) FROM ad_states WHERE state='NATIONAL'"
    ).fetchone()[0]

    sweeps = rows("SELECT mode, status, started_at, ads_seen FROM sweeps "
                  "ORDER BY sweep_id DESC LIMIT 5")

    return {"totals": totals, "byState": by_state, "advertisers": advertisers,
            "byCat": by_cat, "national": national, "sweeps": sweeps}


def copy_images(conn, ads: list[dict], out_images: Path, mode: str) -> int:
    """Resize creatives into the site. Returns how many were written.

    Full-size creatives run to hundreds of megabytes; thumbnails at 400px are
    indistinguishable in a card grid and a fraction of the upload.
    """
    if mode == "none":
        for a in ads:
            a["sha"] = ""
        return 0
    try:
        from PIL import Image
    except ImportError:
        print("  ! Pillow not available — building without images")
        for a in ads:
            a["sha"] = ""
        return 0

    out_images.mkdir(parents=True, exist_ok=True)
    if mode == "all":
        wanted = [r["sha1"] for r in conn.execute("SELECT sha1 FROM creatives")]
    else:
        wanted = [a["sha"] for a in ads if a["sha"]]

    paths = {r["sha1"]: r["local_path"] for r in
             conn.execute("SELECT sha1, local_path FROM creatives")}

    written, missing = 0, 0
    for sha in wanted:
        rel = paths.get(sha)
        if not rel:
            continue
        src = CREATIVES_DIR / rel
        if not src.exists():
            missing += 1
            continue
        dest = out_images / f"{sha}.jpg"
        if dest.exists():
            written += 1
            continue
        try:
            with Image.open(src) as im:
                if im.mode not in ("RGB", "L"):
                    im = im.convert("RGB")
                im.thumbnail((400, 400))
                im.save(dest, "JPEG", quality=72, optimize=True)
            written += 1
        except Exception:
            missing += 1

    # An ad whose thumbnail failed must not render a broken image box.
    have = {p.stem for p in out_images.glob("*.jpg")}
    for a in ads:
        if a["sha"] and a["sha"] not in have:
            a["sha"] = ""
    if missing:
        print(f"  ({missing} creatives unreadable or missing on disk, skipped)")
    return written


# ---------------------------------------------------------------------------
# The page. Data is embedded inline rather than fetched from a separate JSON
# file so the build opens straight from disk — browsers block fetch() on
# file:// URLs, which would make local preview impossible.
# ---------------------------------------------------------------------------

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>__TITLE__</title>
<style>
  :root { --line:#d0d7de; --muted:#57606a; --warn:#9a6700; --accent:#0969da; }
  * { box-sizing: border-box; }
  body { font: 14px/1.5 -apple-system, system-ui, sans-serif; margin:0;
         color:#1f2328; background:#f6f8fa; }
  header { padding:18px 22px; background:#0d1117; color:#fff; }
  header h1 { margin:0 0 4px; font-size:19px; }
  header .sub { color:#9da7b3; font-size:13px; }
  .caveat { background:#fff8c5; border-bottom:1px solid #d4a72c;
            padding:10px 22px; font-size:12.5px; color:#4d3800; }
  .caveat strong { color:#3d2e00; }
  .tabs { display:flex; gap:2px; padding:0 22px; background:#fff;
          border-bottom:1px solid var(--line); }
  .tabs button { padding:11px 15px; border:0; background:none; cursor:pointer;
                 font-size:14px; color:var(--muted); border-bottom:2px solid transparent; }
  .tabs button.on { color:#1f2328; font-weight:600; border-bottom-color:var(--accent); }
  .controls { display:flex; flex-wrap:wrap; gap:10px; align-items:center;
              padding:12px 22px; background:#fff; border-bottom:1px solid var(--line); }
  .controls input[type=text] { width:230px; padding:6px 9px; border:1px solid var(--line);
                               border-radius:6px; font-size:13px; }
  .controls select { padding:6px 9px; border:1px solid var(--line);
                     border-radius:6px; font-size:13px; background:#fff; }
  .controls label.chk { display:flex; align-items:center; gap:6px; font-size:13px; }
  .count { padding:9px 22px; color:var(--muted); font-size:13px; }
  .grid { padding:0 22px 50px; display:flex; flex-direction:column; gap:10px; }
  .card { background:#fff; border:1px solid var(--line); border-radius:10px;
          padding:12px; display:flex; gap:13px; }
  .card img { width:104px; height:104px; object-fit:cover; border-radius:7px;
              border:1px solid var(--line); background:#f0f0f0; flex:0 0 auto; }
  .card .noimg { width:104px; height:104px; border-radius:7px; background:#eef1f4;
                 display:flex; align-items:center; justify-content:center;
                 color:#8c959f; font-size:11px; flex:0 0 auto; }
  .card .main { flex:1; min-width:0; }
  .card .adv { font-weight:600; }
  .card .meta { color:var(--muted); font-size:12px; margin-top:1px; }
  .card .copy { color:#424a53; font-size:12.5px; margin-top:7px;
                display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical;
                overflow:hidden; }
  .card .price { text-align:right; flex:0 0 auto; }
  .card .price .big { font-size:16px; font-weight:700; }
  .card .price .sub { color:var(--muted); font-size:12px; }
  .tags { margin-top:8px; display:flex; flex-wrap:wrap; gap:5px; }
  .tag { font-size:10.5px; padding:2px 7px; border-radius:999px;
         background:#eef1f4; color:#424a53; }
  .tag.state { background:#ddf4ff; color:#0550ae; }
  .tag.weak  { background:#eef1f4; color:#8c959f; }
  .tag.flag  { background:#fff1e5; color:var(--warn); }
  .tag a, .tags a { color:var(--accent); }
  table { border-collapse:collapse; width:100%; background:#fff; }
  th, td { padding:9px 11px; text-align:left; border-bottom:1px solid var(--line);
           font-size:13px; }
  th { background:#f6f8fa; font-size:12px; color:var(--muted); }
  td.num { text-align:right; font-variant-numeric:tabular-nums; }
  .bar { height:17px; background:#ddf4ff; border-radius:3px; }
  .pane { display:none; } .pane.on { display:block; }
  .tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
           gap:12px; padding:16px 22px; }
  .tile { background:#fff; border:1px solid var(--line); border-radius:10px; padding:13px; }
  .tile .k { color:var(--muted); font-size:12px; }
  .tile .v { font-size:24px; font-weight:600; font-variant-numeric:tabular-nums; }
  .sect { padding:0 22px 26px; }
  .sect h2 { font-size:15px; margin:14px 0 9px; }
  .foot { padding:20px 22px 60px; color:var(--muted); font-size:12px; }
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <div class="sub">__SUBTITLE__</div>
</header>

<div class="caveat">
  <strong>State figures are inferred, not reported.</strong>
  Meta publishes no geographic targeting for Australian commercial ads &mdash; states
  here are derived from ad copy, rebate-scheme mentions, advertiser location and
  search provenance. Tags marked <span class="tag weak">SA?</span> rest on weak
  evidence. Prices tagged <span class="tag flag">check-parse</span> are unverified reads.
  __PILOTNOTE__
</div>

<div class="tabs">
  <button data-p="overview" class="on">Overview</button>
  <button data-p="ads">Ads</button>
  <button data-p="advertisers">Advertisers</button>
</div>

<div class="pane on" id="p-overview">
  <div class="tiles" id="tiles"></div>
  <div class="sect">
    <h2>Ads by state <span style="font-weight:400;color:#57606a;font-size:12px">
      (local campaigns only)</span></h2>
    <table id="stateTbl"></table>
  </div>
  <div class="sect">
    <h2>Product mix</h2>
    <table id="catTbl"></table>
  </div>
</div>

<div class="pane" id="p-ads">
  <div class="controls">
    <input type="text" id="q" placeholder="Search copy or advertiser...">
    <select id="fState"><option value="">All states</option></select>
    <select id="fConf">
      <option value="3">High confidence</option>
      <option value="2">Medium+</option>
      <option value="1" selected>Any evidence</option>
    </select>
    <select id="fCat"><option value="">All products</option></select>
    <select id="fStatus">
      <option value="">Any status</option>
      <option value="1">Active</option>
      <option value="0">Ended</option>
    </select>
    <label class="chk"><input type="checkbox" id="fPriced"> Priced only</label>
    <label class="chk"><input type="checkbox" id="fClean"> Hide check-parse</label>
  </div>
  <div class="count" id="count"></div>
  <div class="grid" id="grid"></div>
</div>

<div class="pane" id="p-advertisers">
  <div class="sect"><h2>Most active advertisers</h2><table id="advTbl"></table></div>
</div>

<div class="foot">__FOOTER__</div>

<script id="payload" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('payload').textContent);
const ADS = D.ads, S = D.stats;
const CATS = {battery:'Batteries', solar:'Solar', solar_battery:'Solar + battery',
              ev_charger:'EV chargers', heat_pump:'Heat pumps', other:'Other',
              unclassified:'Unclassified'};
const money = v => v==null ? '—' : '$' + Math.round(v).toLocaleString();
const esc = s => (s||'').replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

// Tabs
document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => {
  document.querySelectorAll('.tabs button').forEach(x => x.classList.remove('on'));
  document.querySelectorAll('.pane').forEach(x => x.classList.remove('on'));
  b.classList.add('on');
  document.getElementById('p-' + b.dataset.p).classList.add('on');
});

// Overview
document.getElementById('tiles').innerHTML = [
  ['Ads archived', S.totals.ads], ['Advertisers', S.totals.advertisers],
  ['Creatives', S.totals.creatives], ['AI-labelled', S.totals.enriched],
].map(([k,v]) => `<div class="tile"><div class="k">${k}</div>
  <div class="v">${v.toLocaleString()}</div></div>`).join('');

const maxState = Math.max(1, ...S.byState.map(r => r.total));
document.getElementById('stateTbl').innerHTML =
  '<tr><th>State</th><th>High confidence</th><th></th><th class="num">Total</th></tr>' +
  S.byState.map(r => `<tr><td>${r.state}</td><td class="num">${r.high}</td>
    <td style="width:55%"><div class="bar" style="width:${r.total/maxState*100}%"></div></td>
    <td class="num">${r.total}</td></tr>`).join('') +
  `<tr><td colspan="4" style="color:#57606a;font-size:12px">
    ${S.national} national campaigns excluded from per-state counts.</td></tr>`;

document.getElementById('catTbl').innerHTML =
  '<tr><th>Product</th><th class="num">Ads</th></tr>' +
  S.byCat.map(r => `<tr><td>${CATS[r.cat]||r.cat}</td>
    <td class="num">${r.n}</td></tr>`).join('');

document.getElementById('advTbl').innerHTML =
  '<tr><th>Advertiser</th><th class="num">Ads</th><th>States (high confidence)</th></tr>' +
  S.advertisers.map(r => `<tr><td>${esc(r.name)}</td><td class="num">${r.ads}</td>
    <td style="color:#57606a">${esc(r.states||'—')}</td></tr>`).join('');

// Filter dropdowns
const states = [...new Set(ADS.flatMap(a => a.states).concat(
  ADS.map(a => a.state).filter(Boolean)))].sort();
document.getElementById('fState').innerHTML +=
  states.map(s => `<option>${s}</option>`).join('');
const cats = [...new Set(ADS.map(a => a.cat).filter(Boolean))].sort();
document.getElementById('fCat').innerHTML +=
  cats.map(c => `<option value="${c}">${CATS[c]||c}</option>`).join('');

function render() {
  const q = document.getElementById('q').value.toLowerCase();
  const st = document.getElementById('fState').value;
  const conf = +document.getElementById('fConf').value;
  const cat = document.getElementById('fCat').value;
  const status = document.getElementById('fStatus').value;
  const priced = document.getElementById('fPriced').checked;
  const clean = document.getElementById('fClean').checked;

  const rows = ADS.filter(a => {
    if (q && !(a.adv + ' ' + a.body + ' ' + a.title).toLowerCase().includes(q)) return false;
    // State filter respects the confidence floor: an ad only counts for a state
    // if its evidence is at least as strong as the selected level.
    if (st) {
      const strong = a.states.includes(st);
      const weak = a.state === st;
      if (conf >= 3 ? !strong : !(strong || weak)) return false;
      if (conf === 2 && !strong && a.conf < 2) return false;
    }
    if (cat && a.cat !== cat) return false;
    if (status !== '' && String(a.active) !== status) return false;
    if (priced && a.price == null) return false;
    if (clean && a.flag) return false;
    return true;
  });

  document.getElementById('count').textContent =
    rows.length.toLocaleString() + ' of ' + ADS.length.toLocaleString() + ' ads';

  document.getElementById('grid').innerHTML = rows.slice(0, 400).map(a => {
    const img = a.sha ? `<img loading="lazy" src="images/${a.sha}.jpg" alt="">`
                      : `<div class="noimg">no image</div>`;
    const per = a.perKwh ? `<div class="big">${money(a.perKwh)}/kWh</div>`
              : a.perKw ? `<div class="big">${money(a.perKw)}/kW</div>` : '';
    const sub = a.price ? `<div class="sub">${money(a.price)}${
      a.kwh ? ' · ' + a.kwh + 'kWh' : ''}${a.kw ? ' · ' + a.kw + 'kW' : ''}</div>` : '';
    const tags = [];
    if (a.state) tags.push(a.conf >= 3
      ? `<span class="tag state">${a.state}</span>`
      : `<span class="tag weak" title="weak evidence">${a.state}?</span>`);
    a.states.filter(s => s !== a.state).forEach(s =>
      tags.push(`<span class="tag state">${s}</span>`));
    if (a.cat) tags.push(`<span class="tag">${CATS[a.cat]||a.cat}</span>`);
    if (a.brand) tags.push(`<span class="tag">${esc(a.brand)}</span>`);
    if (a.offer) tags.push(`<span class="tag">${esc(a.offer)}</span>`);
    if (a.flag) tags.push(`<span class="tag flag">${a.flag}</span>`);
    tags.push(`<span class="tag"><a target="_blank" rel="noopener"
      href="https://www.facebook.com/ads/library/?id=${a.id}">Ad Library</a></span>`);
    return `<div class="card">${img}<div class="main">
      <div class="adv">${esc(a.adv) || 'Unknown advertiser'}</div>
      <div class="meta">${a.active === 1 ? 'Active' : a.active === 0 ? 'Ended' : ''}${
        a.days != null ? ' · ' + a.days + 'd running' : ''}${
        a.variants ? ' · ' + a.variants + ' variants' : ''}</div>
      <div class="copy">${esc(a.title ? a.title + ' — ' : '')}${esc(a.body)}</div>
      <div class="tags">${tags.join('')}</div>
    </div><div class="price">${per}${sub}</div></div>`;
  }).join('') + (rows.length > 400
    ? `<div class="count">Showing the first 400 of ${rows.length.toLocaleString()
       } matches — narrow the filters to see more.</div>` : '');
}
['q','fState','fConf','fCat','fStatus','fPriced','fClean'].forEach(id =>
  document.getElementById(id).addEventListener('input', render));
render();
</script>
</body>
</html>
"""

NETLIFY_TOML = """# Generated by report_site.py — do not edit by hand.
[build]
  publish = "public"

[[headers]]
  for = "/*"
  [headers.values]
    X-Robots-Tag = "noindex, nofollow"
"""

# Basic Auth at the edge. This runs before static assets are served, so it
# protects the images too — not just the HTML. A client-side password prompt
# would leave every asset publicly fetchable by direct URL.
EDGE_AUTH = """// Generated by report_site.py — do not edit by hand.
// Gates the whole site behind HTTP Basic Auth using the SITE_PASSWORD
// environment variable set in the Netlify UI or via `netlify env:set`.
export default async (request: Request, context: any) => {
  const expected = Deno.env.get("SITE_PASSWORD");

  // Fail closed. If the variable is missing the site stays shut rather than
  // silently publishing the archive.
  if (!expected) {
    return new Response(
      "SITE_PASSWORD is not set on this site. Run: netlify env:set SITE_PASSWORD '...'",
      { status: 503, headers: { "content-type": "text/plain" } },
    );
  }

  const header = request.headers.get("authorization") || "";
  if (header.startsWith("Basic ")) {
    try {
      const decoded = atob(header.slice(6));
      const supplied = decoded.slice(decoded.indexOf(":") + 1);
      // Length-independent comparison to avoid leaking the password by timing.
      let diff = supplied.length ^ expected.length;
      for (let i = 0; i < Math.max(supplied.length, expected.length); i++) {
        diff |= supplied.charCodeAt(i % supplied.length || 0) ^
                expected.charCodeAt(i % expected.length || 0);
      }
      if (diff === 0) return context.next();
    } catch { /* malformed header falls through to the challenge */ }
  }

  return new Response("Authentication required", {
    status: 401,
    headers: {
      "WWW-Authenticate": 'Basic realm="Solar Ad Intelligence", charset="UTF-8"',
      "content-type": "text/plain",
    },
  });
};

export const config = { path: "/*" };
"""


def build(db_path: str | None, out_dir: Path, images: str,
          limit: int | None = None, title: str = "Solar Ad Intelligence") -> dict:
    """Generate the deployable folder. Returns a summary dict."""
    conn = store.connect(db_path)
    ads = load_ads(conn, limit)
    stats = load_stats(conn)

    public = out_dir / "public"
    public.mkdir(parents=True, exist_ok=True)
    n_images = copy_images(conn, ads, public / "images", images)

    captured = datetime.now(timezone.utc).astimezone()
    subtitle = (f"{stats['totals']['ads']:,} ads · "
                f"{stats['totals']['advertisers']:,} advertisers · "
                f"captured {captured:%d %b %Y}")

    # State counts skew hard toward whatever was queried. Saying so on the page
    # matters more than it might seem: a reader shown NSW 639 vs NT 7 will
    # otherwise read it as market share.
    top = stats["byState"][:2]
    pilot = ""
    if top and top[0]["high"] > 4 * (stats["byState"][-1]["high"] or 1):
        names = " and ".join(r["state"] for r in top)
        pilot = (f"<strong>{names} are over-represented</strong> because the sweep "
                 f"that produced this snapshot queried those states specifically. "
                 f"Per-state totals are not market share.")

    enriched = stats["totals"]["enriched"]
    total = stats["totals"]["ads"]
    footer = (f"Snapshot generated {captured:%d %b %Y %H:%M %Z} from the Meta Ad "
              f"Library archive. Product categories come from an AI pass that had "
              f"covered {enriched:,} of {total:,} ads at capture time, so the "
              f"product mix undercounts. This page is a point-in-time export and "
              f"does not update itself. Ad creatives remain the property of the "
              f"advertisers shown.")

    payload = json.dumps({"ads": ads, "stats": stats}, separators=(",", ":"))
    html = (PAGE
            .replace("__TITLE__", title)
            .replace("__SUBTITLE__", subtitle)
            .replace("__PILOTNOTE__", pilot)
            .replace("__FOOTER__", footer)
            # Guard against the payload closing its own <script> tag.
            .replace("__DATA__", payload.replace("</", "<\\/")))
    (public / "index.html").write_text(html)

    (out_dir / "netlify.toml").write_text(NETLIFY_TOML)
    edge = out_dir / "netlify" / "edge-functions"
    edge.mkdir(parents=True, exist_ok=True)
    (edge / "auth.ts").write_text(EDGE_AUTH)

    conn.close()
    size_mb = sum(f.stat().st_size for f in out_dir.rglob("*") if f.is_file()) / 1e6
    return {"ads": len(ads), "images": n_images, "size_mb": round(size_mb, 1),
            "out": out_dir}


def main() -> int:
    ap = argparse.ArgumentParser(description="Build a deployable static site "
                                             "from the ad archive")
    ap.add_argument("--db", default=None, help="archive path (default .data/solar-ads.db)")
    ap.add_argument("--out", default=str(HERE / "out" / "netlify"))
    ap.add_argument("--images", choices=["first", "all", "none"], default="first",
                    help="first = one thumbnail per ad (default)")
    ap.add_argument("--limit", type=int, help="cap the number of ads included")
    ap.add_argument("--title", default="Solar Ad Intelligence")
    ap.add_argument("--clean", action="store_true",
                    help="delete the output folder first")
    args = ap.parse_args()

    out = Path(args.out)
    if args.clean and out.exists():
        shutil.rmtree(out)

    r = build(args.db, out, args.images, args.limit, args.title)
    print(f"[site] {r['ads']:,} ads, {r['images']:,} images, {r['size_mb']} MB")
    print(f"[site] -> {r['out']}")
    print(f"\nPreview locally:  open {out / 'public' / 'index.html'}")
    print("\nDeploy:")
    print(f"  cd {out}")
    print("  netlify deploy --prod")
    print("  netlify env:set SITE_PASSWORD 'your-password'")
    print("  netlify deploy --prod        # redeploy so the gate picks it up")
    return 0


if __name__ == "__main__":
    sys.exit(main())
