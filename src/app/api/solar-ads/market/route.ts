import { NextRequest, NextResponse } from 'next/server'
import { requireRole } from '@/lib/auth'
import { logger } from '@/lib/logger'
import { archiveExists, query, queryOne } from '@/lib/solar-ads-db'

/**
 * GET /api/solar-ads/market — what the market charges per standard configuration.
 *
 * Query params: dim=kw|kwh (system size or battery capacity), state, category.
 *
 * The central rule here: **prices stated on different rebate bases are never
 * blended.** A $12,000 pre-rebate battery and an $8,990 post-rebate one describe
 * different things, and a median across both describes neither. Each basis gets
 * its own column, its own n, and its own spread.
 */

const MIN_SAMPLE = 5 // below this a "median" is noise; the UI labels it indicative

type Row = { config: string; basis: string; price: number; per_unit: number; adv: string }

/** Median of an ascending array, averaging the middle pair when even. */
function median(sorted: number[]): number | null {
  if (!sorted.length) return null
  const m = Math.floor(sorted.length / 2)
  return sorted.length % 2 ? sorted[m] : (sorted[m - 1] + sorted[m]) / 2
}

function quartiles(sorted: number[]) {
  if (sorted.length < 4) return { q1: null, q3: null }
  const q = (p: number) => {
    const i = (sorted.length - 1) * p
    const lo = Math.floor(i), hi = Math.ceil(i)
    return lo === hi ? sorted[lo] : sorted[lo] + (sorted[hi] - sorted[lo]) * (i - lo)
  }
  return { q1: q(0.25), q3: q(0.75) }
}

function summarise(values: number[]) {
  const s = [...values].sort((a, b) => a - b)
  return {
    n: s.length,
    median: median(s),
    min: s[0] ?? null,
    max: s[s.length - 1] ?? null,
    ...quartiles(s),
    // Below the threshold the figure is shown but must not be read as a market
    // rate. Saying so in the payload keeps every consumer honest, not just this
    // dashboard.
    indicative: s.length > 0 && s.length < MIN_SAMPLE,
  }
}

export async function GET(request: NextRequest) {
  const auth = requireRole(request, 'viewer')
  if ('error' in auth) return NextResponse.json({ error: auth.error }, { status: auth.status })

  try {
    if (!archiveExists()) return NextResponse.json({ archiveReady: false })

    const sp = new URL(request.url).searchParams
    const dim = sp.get('dim') === 'kwh' ? 'kwh' : 'kw'
    const configCol = dim === 'kwh' ? 'config_kwh' : 'config_kw'
    const perCol = dim === 'kwh' ? 'per_kwh' : 'per_kw'

    const where: string[] = [
      `m.${configCol} IS NOT NULL`,
      `m.price IS NOT NULL`,
      `m.suspect = 0`, // a flagged parse must never enter a market statistic
      // Manufacturers and platforms do not sell installed systems: a panel
      // maker's brand ad or a lead-gen site's teaser price is not what a
      // homeowner pays. Note this EXCLUDES known non-installers rather than
      // including only known installers — only a handful of advertisers are
      // classified, so the latter would collapse the market to those few.
      `m.advertiser_type NOT IN ('manufacturer', 'platform')`,
    ]
    const params: unknown[] = []

    const state = sp.get('state')
    if (state) {
      where.push(`EXISTS (SELECT 1 FROM ad_states x
                           WHERE x.ad_archive_id = m.ad_archive_id
                             AND x.state = ? AND x.confidence = 'high')`)
      params.push(state)
    }
    const category = sp.get('category')
    if (category) {
      where.push(`m.category = ?`)
      params.push(category)
    }

    const rows = query<Row>(`
      SELECT m.${configCol} AS config, m.basis, m.price,
             m.${perCol} AS per_unit, COALESCE(m.page_name, '') AS adv
        FROM ad_market m
       WHERE ${where.join(' AND ')}
    `, params)

    // Group in JS rather than SQL: three bases x two measures x quartiles is far
    // clearer here than as nested SQLite window functions.
    const byConfig = new Map<string, Row[]>()
    for (const r of rows) {
      if (!byConfig.has(r.config)) byConfig.set(r.config, [])
      byConfig.get(r.config)!.push(r)
    }

    const configs = [...byConfig.entries()].map(([config, rs]) => {
      const forBasis = (b: string) => rs.filter(r => r.basis === b)
      const build = (subset: Row[]) => ({
        price: summarise(subset.map(r => r.price)),
        perUnit: summarise(subset.map(r => r.per_unit).filter(v => v != null)),
        advertisers: new Set(subset.map(r => r.adv).filter(Boolean)).size,
      })
      return {
        config,
        total: rs.length,
        post: build(forBasis('post_rebate')),
        pre: build(forBasis('pre_rebate')),
        unknown: build(forBasis('unknown')),
      }
    })

    // Order by size rather than alphabetically: '10kW' before '6.6kW' reads as a
    // sorting bug to anyone scanning the table.
    const num = (c: string) => parseFloat(c) || 0
    configs.sort((a, b) => num(a.config) - num(b.config))

    // Per-advertiser positions, so "where do I sit against the market" is
    // answerable rather than merely implied by the medians.
    const positions = configs.flatMap(c => {
      const rs = byConfig.get(c.config)!
      // Compare each advertiser against the median for THEIR OWN basis. Using a
      // single median across all rows would report a pre-rebate price as wildly
      // above a post-rebate market — the exact comparison this endpoint splits
      // the bases to avoid. No median for that basis means no percentage.
      const medianFor: Record<string, number | null> = {
        post_rebate: c.post.price.median,
        pre_rebate: c.pre.price.median,
        unknown: c.unknown.price.median,
      }
      const seen = new Map<string, { adv: string; price: number; basis: string }>()
      for (const r of rs) {
        if (!r.adv) continue
        // One row per advertiser per config: the cheapest they advertise.
        const prev = seen.get(r.adv)
        if (!prev || r.price < prev.price) {
          seen.set(r.adv, { adv: r.adv, price: r.price, basis: r.basis })
        }
      }
      return [...seen.values()]
        .sort((a, b) => a.price - b.price)
        .map(v => {
          const med = medianFor[v.basis] ?? null
          return {
            config: c.config, ...v,
            vsMedian: med ? Math.round(((v.price - med) / med) * 100) : null,
            // n behind that basis, so a percentage against two ads is not read
            // with the same weight as one against fifty.
            basisN: v.basis === 'post_rebate' ? c.post.price.n
                  : v.basis === 'pre_rebate' ? c.pre.price.n
                  : c.unknown.price.n,
          }
        })
    })

    const coverage = queryOne<{
      known: number; total: number; suspect: number; nonInstaller: number
    }>(`
      SELECT SUM(CASE WHEN basis != 'unknown' THEN 1 ELSE 0 END) AS known,
             COUNT(*) AS total,
             SUM(suspect) AS suspect,
             SUM(CASE WHEN advertiser_type IN ('manufacturer', 'platform')
                      THEN 1 ELSE 0 END) AS nonInstaller
        FROM ad_market WHERE price IS NOT NULL
    `)

    return NextResponse.json({
      archiveReady: true, dim, configs, positions,
      coverage: coverage ?? { known: 0, total: 0, suspect: 0, nonInstaller: 0 },
      minSample: MIN_SAMPLE,
    })
  } catch (error) {
    logger.error({ err: error }, 'GET /api/solar-ads/market failed')
    return NextResponse.json({ error: 'Failed to compute market stats' }, { status: 500 })
  }
}
