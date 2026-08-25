import { NextRequest, NextResponse } from 'next/server'
import { requireRole } from '@/lib/auth'
import { logger } from '@/lib/logger'
import { archiveExists, query, BEST_STATE_CTE } from '@/lib/solar-ads-db'

/**
 * GET /api/solar-ads — filterable list of archived ads.
 *
 * Price, capacity and size come from the `ad_market` view rather than being
 * re-resolved here, so this list, the market statistics and the shareable site
 * all agree on what a given ad offers.
 *
 * Filters: state, confidence, category, advertiser, brand, offerType, status,
 * priced, hideSuspect, q, price/kw/kwh/perKw/perKwh ranges, hasFinance,
 * hasUrgency, startedWithin, runningOver.
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

    // Simple equality filters, declared once rather than repeated inline.
    const eq: Array<[string, string]> = [
      ['category', 'm.category'],
      ['brand', 'm.brand'],
      ['offerType', 'm.offer_type'],
      ['advertiser', 'a.page_name'],
      ['basis', 'm.basis'],
      ['advertiserType', 'm.advertiser_type'],
      ['config', 'COALESCE(m.config_kw, m.config_kwh)'],
    ]
    for (const [param, col] of eq) {
      const v = sp.get(param)
      if (v) { where.push(`${col} = ?`); params.push(v) }
    }

    // Numeric ranges. Each is optional and independently applied.
    const ranges: Array<[string, string, '>=' | '<=']> = [
      ['priceMin', 'm.price', '>='], ['priceMax', 'm.price', '<='],
      ['kwMin', 'm.kw', '>='],       ['kwMax', 'm.kw', '<='],
      ['kwhMin', 'm.kwh', '>='],     ['kwhMax', 'm.kwh', '<='],
      ['perKwMax', 'm.per_kw', '<='], ['perKwhMax', 'm.per_kwh', '<='],
    ]
    for (const [param, col, op] of ranges) {
      const raw = sp.get(param)
      if (raw === null || raw === '') continue
      const v = Number(raw)
      if (!Number.isFinite(v)) continue
      where.push(`${col} ${op} ?`)
      params.push(v)
    }

    const status = sp.get('status')
    if (status === 'active') where.push(`m.is_active = 1`)
    if (status === 'ended') where.push(`m.is_active = 0`)

    if (sp.get('watchlist') === '1') where.push(`m.on_watchlist = 1`)
    if (sp.get('priced') === '1') where.push(`m.price IS NOT NULL`)
    if (sp.get('hideSuspect') === '1') where.push(`m.suspect = 0`)
    if (sp.get('hasFinance') === '1') where.push(`COALESCE(m.finance_terms, '') != ''`)
    if (sp.get('hasUrgency') === '1')
      where.push(`COALESCE(m.urgency_tactics, '') NOT IN ('', '[]')`)

    // Timing. start_date is a unix timestamp; days_running comes from the
    // latest snapshot.
    const startedWithin = Number(sp.get('startedWithin') || '')
    if (Number.isFinite(startedWithin) && startedWithin > 0) {
      where.push(`m.start_date >= ?`)
      params.push(Math.floor(Date.now() / 1000) - startedWithin * 86400)
    }
    const runningOver = Number(sp.get('runningOver') || '')
    if (Number.isFinite(runningOver) && runningOver > 0) {
      where.push(`m.days_running >= ?`)
      params.push(runningOver)
    }

    const q = sp.get('q')
    if (q) {
      where.push(`(a.body_text LIKE ? OR a.title LIKE ? OR a.page_name LIKE ?)`)
      const like = `%${q}%`
      params.push(like, like, like)
    }

    const whereSql = where.length ? `WHERE ${where.join(' AND ')}` : ''

    const withClause = `WITH ${BEST_STATE_CTE}`
    const fromClause = `
      FROM ads a
      JOIN ad_market m ON m.ad_archive_id = a.ad_archive_id
      LEFT JOIN best_state bs ON bs.ad_archive_id = a.ad_archive_id
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
             m.price AS price_aud, m.kwh AS capacity_kwh, m.kw AS system_kw,
             m.per_kwh AS dollars_per_kwh, m.per_kw AS dollars_per_kw,
             m.price_source, m.basis AS price_basis, m.suspect,
             m.is_active, m.collation_count, m.days_running,
             m.category AS product_category, m.brand, m.offer_type,
             m.advertiser_type, m.on_watchlist,
             m.finance_terms, m.urgency_tactics,
             COALESCE(m.config_kw, m.config_kwh) AS config,
             CASE WHEN m.suspect = 1 THEN 'check-parse' ELSE '' END AS flag,
             (SELECT sha1 FROM creatives WHERE ad_archive_id = a.ad_archive_id
               ORDER BY rowid LIMIT 1) AS creative_sha,
             (SELECT COUNT(*) FROM creatives WHERE ad_archive_id = a.ad_archive_id)
               AS creative_count,
             (SELECT GROUP_CONCAT(DISTINCT state) FROM ad_states
               WHERE ad_archive_id = a.ad_archive_id AND confidence = 'high')
               AS states_high
      ${fromClause}
      ORDER BY (m.price IS NULL),
               m.suspect,
               m.per_kwh,
               a.last_seen DESC
      LIMIT ? OFFSET ?
    `, [...params, limit, offset])

    return NextResponse.json({ ads, total, limit, offset, archiveReady: true })
  } catch (error) {
    logger.error({ err: error }, 'GET /api/solar-ads failed')
    return NextResponse.json({ error: 'Failed to query ad archive' }, { status: 500 })
  }
}
