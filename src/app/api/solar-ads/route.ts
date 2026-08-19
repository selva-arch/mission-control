import { NextRequest, NextResponse } from 'next/server'
import { requireRole } from '@/lib/auth'
import { logger } from '@/lib/logger'
import {
  archiveExists, query, BEST_STATE_CTE, LATEST_PRICE_CTE, LATEST_SNAPSHOT_CTE,
} from '@/lib/solar-ads-db'

/**
 * GET /api/solar-ads — filterable list of archived ads.
 *
 * Query params: state, category, advertiser, status (active|ended),
 * confidence (high|medium|low — minimum signal strength for the state filter),
 * priced (1 = only ads with a parsed price), q (free text), limit, offset.
 */
export async function GET(request: NextRequest) {
  const auth = requireRole(request, 'viewer')
  if ('error' in auth) return NextResponse.json({ error: auth.error }, { status: auth.status })

  try {
    if (!archiveExists()) {
      return NextResponse.json({ ads: [], total: 0, archiveReady: false })
    }

    const sp = new URL(request.url).searchParams
    const limit = Math.min(parseInt(sp.get('limit') || '50', 10) || 50, 500)
    const offset = parseInt(sp.get('offset') || '0', 10) || 0

    const where: string[] = []
    const params: unknown[] = []

    const state = sp.get('state')
    if (state) {
      // Filter on the raw signals, not the collapsed best-state, so an ad with
      // genuine evidence for several states appears under each of them.
      const minRank = { high: 3, medium: 2, low: 1 }[sp.get('confidence') || 'low'] ?? 1
      where.push(`EXISTS (SELECT 1 FROM ad_states x
                           WHERE x.ad_archive_id = a.ad_archive_id
                             AND x.state = ?
                             AND CASE x.confidence WHEN 'high' THEN 3
                                                   WHEN 'medium' THEN 2 ELSE 1 END >= ?)`)
      params.push(state, minRank)
    }

    const category = sp.get('category')
    if (category) {
      where.push(`COALESCE(o.product_category, '') = ?`)
      params.push(category)
    }

    const advertiser = sp.get('advertiser')
    if (advertiser) {
      where.push(`a.page_name = ?`)
      params.push(advertiser)
    }

    const status = sp.get('status')
    if (status === 'active') where.push(`snap.is_active = 1`)
    if (status === 'ended') where.push(`snap.is_active = 0`)

    if (sp.get('priced') === '1') where.push(`p.price_aud IS NOT NULL`)

    const q = sp.get('q')
    if (q) {
      where.push(`(a.body_text LIKE ? OR a.title LIKE ? OR a.page_name LIKE ?)`)
      const like = `%${q}%`
      params.push(like, like, like)
    }

    const whereSql = where.length ? `WHERE ${where.join(' AND ')}` : ''

    // CTEs must precede SELECT, so the WITH clause and the FROM body are kept
    // separate and composed per-query rather than concatenated blindly.
    const withClause = `
      WITH ${BEST_STATE_CTE}, ${LATEST_PRICE_CTE}, ${LATEST_SNAPSHOT_CTE},
      latest_offer AS (
        SELECT o.* FROM ad_offers o
         WHERE o.id = (SELECT MAX(id) FROM ad_offers
                        WHERE ad_archive_id = o.ad_archive_id)
      )
    `

    const fromClause = `
      FROM ads a
      LEFT JOIN best_state    bs   ON bs.ad_archive_id   = a.ad_archive_id
      LEFT JOIN latest_price  p    ON p.ad_archive_id    = a.ad_archive_id
      LEFT JOIN latest_snap   snap ON snap.ad_archive_id = a.ad_archive_id
      LEFT JOIN latest_offer  o    ON o.ad_archive_id    = a.ad_archive_id
      ${whereSql}
    `

    const total =
      query<{ n: number }>(`${withClause} SELECT COUNT(*) AS n ${fromClause}`, params)[0]?.n ?? 0

    const ads = query(`
      ${withClause}
      SELECT a.ad_archive_id, a.page_id, a.page_name, a.title, a.body_text,
             a.cta_text, a.link_url, a.display_format, a.collation_id,
             a.start_date, a.end_date,
             bs.state AS best_state, bs.conf_rank,
             p.price_aud, p.capacity_kwh, p.system_kw, p.dollars_per_kwh,
             p.dollars_per_kw, p.est_rebate_aud, p.flag,
             snap.is_active, snap.collation_count, snap.days_running,
             o.product_category, o.brand, o.offer_type, o.price_basis,
             o.urgency_tactics, o.claimed_rebates,
             (SELECT sha1 FROM creatives WHERE ad_archive_id = a.ad_archive_id
               ORDER BY rowid LIMIT 1) AS creative_sha,
             (SELECT COUNT(*) FROM creatives WHERE ad_archive_id = a.ad_archive_id)
               AS creative_count,
             (SELECT GROUP_CONCAT(DISTINCT state) FROM ad_states
               WHERE ad_archive_id = a.ad_archive_id AND confidence = 'high')
               AS states_high
      ${fromClause}
      ORDER BY (p.dollars_per_kwh IS NULL), p.dollars_per_kwh, a.last_seen DESC
      LIMIT ? OFFSET ?
    `, [...params, limit, offset])

    return NextResponse.json({ ads, total, limit, offset, archiveReady: true })
  } catch (error) {
    logger.error({ err: error }, 'GET /api/solar-ads failed')
    return NextResponse.json({ error: 'Failed to query ad archive' }, { status: 500 })
  }
}
