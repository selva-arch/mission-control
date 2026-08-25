import { NextRequest, NextResponse } from 'next/server'
import { requireRole } from '@/lib/auth'
import { logger } from '@/lib/logger'
import { archiveExists, query, queryOne, BEST_STATE_CTE, LATEST_PRICE_CTE } from '@/lib/solar-ads-db'

/**
 * GET /api/solar-ads/stats — aggregates for the dashboard overview.
 *
 * State counts are reported per confidence tier and exclude ads flagged
 * NATIONAL from the per-state totals, since a national campaign is not
 * evidence of local activity in eight separate markets.
 */
export async function GET(request: NextRequest) {
  const auth = requireRole(request, 'viewer')
  if ('error' in auth) return NextResponse.json({ error: auth.error }, { status: auth.status })

  try {
    if (!archiveExists()) return NextResponse.json({ archiveReady: false })

    const totals = queryOne<Record<string, number>>(`
      SELECT (SELECT COUNT(*) FROM ads)          AS ads,
             (SELECT COUNT(*) FROM advertisers)  AS advertisers,
             (SELECT COUNT(*) FROM creatives)    AS creatives,
             (SELECT COUNT(*) FROM sweeps)       AS sweeps
    `) || {}

    // Per-state ad counts by confidence tier, national campaigns excluded.
    const byState = query(`
      SELECT s.state,
             COUNT(DISTINCT CASE WHEN s.confidence = 'high' THEN s.ad_archive_id END) AS high,
             COUNT(DISTINCT CASE WHEN s.confidence = 'medium' THEN s.ad_archive_id END) AS medium,
             COUNT(DISTINCT CASE WHEN s.confidence = 'low' THEN s.ad_archive_id END) AS low,
             COUNT(DISTINCT s.ad_archive_id) AS total
        FROM ad_states s
       WHERE s.state != 'NATIONAL'
         AND s.ad_archive_id NOT IN (SELECT ad_archive_id FROM ad_states
                                      WHERE state = 'NATIONAL')
       GROUP BY s.state
       ORDER BY high DESC, total DESC
    `)

    const nationalCount = queryOne<{ n: number }>(
      `SELECT COUNT(DISTINCT ad_archive_id) AS n FROM ad_states WHERE state = 'NATIONAL'`
    )?.n ?? 0

    const unattributed = queryOne<{ n: number }>(`
      SELECT COUNT(*) AS n FROM ads
       WHERE ad_archive_id NOT IN (SELECT DISTINCT ad_archive_id FROM ad_states)
    `)?.n ?? 0

    const byCategory = query(`
      SELECT COALESCE(o.product_category, 'unclassified') AS category,
             COUNT(*) AS n
        FROM ads a
        LEFT JOIN ad_offers o ON o.ad_archive_id = a.ad_archive_id
       GROUP BY category ORDER BY n DESC
    `)

    // Median $/kWh per state. SQLite has no median aggregate, so rank rows
    // within each state and average the middle one or two — more honest than a
    // mean on a long-tailed price distribution with parse errors in it.
    const priceByState = query(`
      WITH ${BEST_STATE_CTE}, ${LATEST_PRICE_CTE},
      joined AS (
        SELECT bs.state, p.dollars_per_kwh AS v
          FROM latest_price p
          JOIN best_state bs ON bs.ad_archive_id = p.ad_archive_id
         WHERE p.dollars_per_kwh IS NOT NULL AND COALESCE(p.flag, '') = ''
           AND bs.state IS NOT NULL AND bs.state != 'NATIONAL'
      ),
      ranked AS (
        SELECT state, v,
               ROW_NUMBER() OVER (PARTITION BY state ORDER BY v) AS rn,
               COUNT(*)     OVER (PARTITION BY state)            AS cnt
          FROM joined
      )
      SELECT state,
             MAX(cnt) AS n,
             MIN(v)   AS min,
             MAX(v)   AS max,
             AVG(v)   AS mean,
             AVG(CASE WHEN rn IN ((cnt + 1) / 2, (cnt + 2) / 2) THEN v END) AS median
        FROM ranked
       GROUP BY state
       ORDER BY median
    `)

    const topAdvertisers = query(`
      SELECT a.page_name, a.page_id, COUNT(*) AS ads,
             SUM(CASE WHEN s.is_active = 1 THEN 1 ELSE 0 END) AS active,
             (SELECT GROUP_CONCAT(DISTINCT st.state) FROM ad_states st
               JOIN ads a2 ON a2.ad_archive_id = st.ad_archive_id
              WHERE a2.page_id = a.page_id AND st.confidence = 'high'
                AND st.state != 'NATIONAL') AS states
        FROM ads a
        LEFT JOIN ad_snapshots s ON s.ad_archive_id = a.ad_archive_id
             AND s.id = (SELECT MAX(id) FROM ad_snapshots
                          WHERE ad_archive_id = a.ad_archive_id)
       WHERE COALESCE(a.page_name, '') != ''
       GROUP BY a.page_name, a.page_id
       ORDER BY ads DESC LIMIT 25
    `)

    const sweeps = query(`
      SELECT sweep_id, started_at, finished_at, mode, status,
             targets_total, targets_done, ads_seen
        FROM sweeps ORDER BY sweep_id DESC LIMIT 10
    `)

    // Signal mix — shows how much of the state picture rests on weak evidence.
    const signalMix = query(`
      SELECT signal, confidence, COUNT(*) AS n
        FROM ad_states GROUP BY signal, confidence ORDER BY n DESC
    `)

    // Filter options must span the whole archive. Deriving them from the
    // current page of results hides every advertiser outside it — which is how
    // a Page with ads in the database still went missing from the dropdown.
    const facets = {
      advertisers: query<{ name: string; ads: number }>(`
        SELECT page_name AS name, COUNT(*) AS ads FROM ads
         WHERE COALESCE(page_name, '') != ''
         GROUP BY page_name ORDER BY ads DESC, name
      `),
      brands: query<{ name: string }>(`
        SELECT DISTINCT brand AS name FROM ad_market
         WHERE COALESCE(brand, '') != '' ORDER BY name
      `).map(r => r.name),
      configs: query<{ name: string }>(`
        SELECT DISTINCT COALESCE(config_kw, config_kwh) AS name FROM ad_market
         WHERE COALESCE(config_kw, config_kwh) IS NOT NULL
      `).map(r => r.name).sort((a, b) => (parseFloat(a) || 0) - (parseFloat(b) || 0)),
      offerTypes: query<{ name: string }>(`
        SELECT DISTINCT offer_type AS name FROM ad_market
         WHERE COALESCE(offer_type, '') != '' ORDER BY name
      `).map(r => r.name),
      watchlist: query<{ page_name: string; label: string; advertiser_type: string; ads: number }>(`
        SELECT t.page_name, t.label, t.advertiser_type,
               (SELECT COUNT(*) FROM ads a WHERE a.page_name = t.page_name) AS ads
          FROM advertiser_types t WHERE t.on_watchlist = 1
         ORDER BY t.advertiser_type, t.label
      `),
    }

    return NextResponse.json({
      archiveReady: true, totals, byState, nationalCount, unattributed,
      byCategory, priceByState, topAdvertisers, sweeps, signalMix, facets,
    })
  } catch (error) {
    logger.error({ err: error }, 'GET /api/solar-ads/stats failed')
    return NextResponse.json({ error: 'Failed to compute stats' }, { status: 500 })
  }
}
