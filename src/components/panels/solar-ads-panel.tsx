'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { SolarAdsMarket } from '@/components/panels/solar-ads-market'
import { SolarAdsFilters, EMPTY_FILTERS, type Filters }
  from '@/components/panels/solar-ads-filters'

/**
 * Australian solar/battery advertising intelligence.
 *
 * Data comes from the Meta Ad Library sweep in solar-ad-scraper/. An important
 * caveat is surfaced in the UI rather than buried: Meta publishes no state
 * targeting for Australian commercial ads, so every state figure here is
 * inferred from ad copy, advertiser location and search provenance. The
 * confidence control lets you see how much of the picture rests on strong
 * evidence.
 */

const STATES = ['NSW', 'VIC', 'QLD', 'SA', 'WA', 'TAS', 'NT', 'ACT']

const CATEGORY_LABELS: Record<string, string> = {
  battery: 'Batteries',
  solar: 'Solar',
  solar_battery: 'Solar + battery',
  ev_charger: 'EV chargers',
  heat_pump: 'Heat pumps',
  other: 'Other',
  unclassified: 'Unclassified',
}

// Deliberately not a rainbow: one hue family, varied in lightness, so the bars
// read as a single measure rather than eight unrelated categories.
const BAR_COLORS = ['#0ea5e9', '#38bdf8', '#7dd3fc']

interface Ad {
  ad_archive_id: string
  page_name: string
  title: string
  body_text: string
  cta_text: string
  link_url: string
  best_state: string | null
  conf_rank: number | null
  states_high: string | null
  price_aud: number | null
  capacity_kwh: number | null
  system_kw: number | null
  dollars_per_kwh: number | null
  dollars_per_kw: number | null
  flag: string | null
  is_active: number | null
  collation_count: number | null
  days_running: number | null
  product_category: string | null
  brand: string | null
  offer_type: string | null
  config: string | null
  price_source: string | null
  price_basis: string | null
  creative_sha: string | null
  creative_count: number
}

interface Stats {
  archiveReady: boolean
  totals?: { ads: number; advertisers: number; creatives: number; sweeps: number }
  byState?: Array<{ state: string; high: number; medium: number; low: number; total: number }>
  nationalCount?: number
  unattributed?: number
  byCategory?: Array<{ category: string; n: number }>
  priceByState?: Array<{ state: string; n: number; median: number; min: number; max: number }>
  topAdvertisers?: Array<{ page_name: string; ads: number; active: number; states: string | null }>
  sweeps?: Array<{ sweep_id: number; started_at: number; finished_at: number | null; mode: string; status: string; targets_total: number; targets_done: number; ads_seen: number }>
  signalMix?: Array<{ signal: string; confidence: string; n: number }>
}

type Tab = 'overview' | 'market' | 'ads' | 'advertisers' | 'sweeps'

function fmtMoney(v: number | null | undefined, dp = 0): string {
  if (v === null || v === undefined) return '—'
  return '$' + v.toLocaleString(undefined, { maximumFractionDigits: dp })
}

function fmtDate(ts: number | null | undefined): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleDateString()
}

export function SolarAdsPanel() {
  const [tab, setTab] = useState<Tab>('overview')
  const [stats, setStats] = useState<Stats | null>(null)
  const [ads, setAds] = useState<Ad[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)

  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS)
  const [page, setPage] = useState(0)
  const { state, category } = filters

  const PAGE_SIZE = 50

  const loadStats = useCallback(async () => {
    try {
      const r = await fetch('/api/solar-ads/stats')
      setStats(await r.json())
    } catch {
      setStats({ archiveReady: false })
    }
  }, [])

  const loadAds = useCallback(async () => {
    setLoading(true)
    try {
      const p = new URLSearchParams({
        limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE),
      })
      // Send only what differs from the defaults, so the request carries the
      // filters actually in use rather than a predicate per untouched control.
      for (const [k, v] of Object.entries(filters)) {
        if (v === EMPTY_FILTERS[k as keyof Filters]) continue
        p.set(k, v === true ? '1' : String(v))
      }
      const r = await fetch(`/api/solar-ads?${p}`)
      const data = await r.json()
      setAds(data.ads || [])
      setTotal(data.total || 0)
    } catch {
      setAds([])
    } finally {
      setLoading(false)
    }
  }, [filters, page])

  useEffect(() => { loadStats() }, [loadStats])
  useEffect(() => { loadAds() }, [loadAds])
  useEffect(() => { setPage(0) }, [filters])

  // Dropdown options come from the loaded rows: offering a brand that matches
  // nothing in view would be a dead end.
  const facets = useMemo(() => ({
    brands: [...new Set(ads.map(a => a.brand).filter(Boolean) as string[])].sort(),
    advertisers: [...new Set(ads.map(a => a.page_name).filter(Boolean))].sort(),
    configs: [...new Set(ads.map(a => a.config).filter(Boolean) as string[])]
      .sort((x, y) => (parseFloat(x) || 0) - (parseFloat(y) || 0)),
  }), [ads])

  const maxStateCount = useMemo(
    () => Math.max(1, ...(stats?.byState || []).map(s => s.total)),
    [stats]
  )

  if (stats && !stats.archiveReady) {
    return (
      <div className="p-6">
        <h1 className="text-xl font-semibold mb-2">Solar Ad Intelligence</h1>
        <div className="rounded-lg border border-border bg-card p-6 mt-4">
          <p className="text-sm text-muted-foreground mb-3">
            No ad archive found yet. Run a sweep to populate it:
          </p>
          <pre className="text-xs bg-muted p-3 rounded overflow-x-auto">
{`cd solar-ad-scraper
pip install -r requirements.txt
python -m playwright install chromium
python run.py --pilot     # ~10 min validation sweep
python run.py             # full sweep (hours, resumable)`}
          </pre>
        </div>
      </div>
    )
  }

  return (
    <div className="p-4 md:p-6 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Solar Ad Intelligence</h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Australian solar, battery, EV-charger and hot-water advertising from the Meta Ad Library
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => { loadStats(); loadAds() }}>
          Refresh
        </Button>
      </div>

      {/* The single most important caveat about this dataset. */}
      <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-200/90">
        <strong>State figures are inferred, not reported.</strong> Meta publishes no
        geographic targeting for Australian commercial ads — states here are derived from
        ad copy, rebate-scheme mentions, advertiser location and search provenance. Use the
        confidence filter to see how much rests on strong evidence.
      </div>

      <div className="flex gap-1 border-b border-border">
        {(['overview', 'market', 'ads', 'advertisers', 'sweeps'] as Tab[]).map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-3 py-2 text-sm capitalize transition-colors ${
              tab === t
                ? 'border-b-2 border-primary text-foreground font-medium'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === 'overview' && stats?.totals && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {[
              ['Ads archived', stats.totals.ads],
              ['Advertisers', stats.totals.advertisers],
              ['Creatives stored', stats.totals.creatives],
              ['Sweeps run', stats.totals.sweeps],
            ].map(([label, value]) => (
              <div key={label as string} className="rounded-lg border border-border bg-card p-3">
                <div className="text-xs text-muted-foreground">{label}</div>
                <div className="text-2xl font-semibold tabular-nums">
                  {(value as number).toLocaleString()}
                </div>
              </div>
            ))}
          </div>

          <div className="grid md:grid-cols-2 gap-4">
            <div className="rounded-lg border border-border bg-card p-4">
              <h2 className="text-sm font-medium mb-1">Ads by state</h2>
              <p className="text-xs text-muted-foreground mb-3">
                Local campaigns only. {stats.nationalCount ?? 0} national{' '}
                {(stats.nationalCount ?? 0) === 1 ? 'campaign' : 'campaigns'} and{' '}
                {stats.unattributed ?? 0} unattributed{' '}
                {(stats.unattributed ?? 0) === 1 ? 'ad is' : 'ads are'} excluded.
              </p>
              <div className="space-y-2">
                {(stats.byState || []).map(s => (
                  <div key={s.state} className="flex items-center gap-2">
                    <span className="w-10 text-xs font-mono text-muted-foreground">{s.state}</span>
                    <div className="flex-1 h-5 bg-muted rounded overflow-hidden flex">
                      {([['high', s.high], ['medium', s.medium], ['low', s.low]] as const).map(
                        ([tier, n], i) => n > 0 && (
                          <div
                            key={tier}
                            style={{
                              width: `${(n / maxStateCount) * 100}%`,
                              backgroundColor: BAR_COLORS[i],
                            }}
                            title={`${n} ads — ${tier} confidence`}
                          />
                        )
                      )}
                    </div>
                    <span className="w-10 text-right text-xs tabular-nums">{s.total}</span>
                  </div>
                ))}
                {!(stats.byState || []).length && (
                  <p className="text-xs text-muted-foreground">No state signals yet.</p>
                )}
              </div>
              <div className="flex gap-3 mt-3 text-[10px] text-muted-foreground">
                {['high', 'medium', 'low'].map((tier, i) => (
                  <span key={tier} className="flex items-center gap-1">
                    <span className="w-2 h-2 rounded-sm inline-block"
                          style={{ backgroundColor: BAR_COLORS[i] }} />
                    {tier} confidence
                  </span>
                ))}
              </div>
            </div>

            <div className="rounded-lg border border-border bg-card p-4">
              <h2 className="text-sm font-medium mb-3">Median $/usable kWh by state</h2>
              {(stats.priceByState || []).length ? (
                <table className="w-full text-xs">
                  <thead className="text-muted-foreground">
                    <tr className="text-left">
                      <th className="pb-2">State</th>
                      <th className="pb-2 text-right">Ads</th>
                      <th className="pb-2 text-right">Median</th>
                      <th className="pb-2 text-right">Range</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stats.priceByState!.map(p => (
                      <tr key={p.state} className="border-t border-border/50">
                        <td className="py-1.5 font-mono">{p.state}</td>
                        <td className="py-1.5 text-right tabular-nums">{p.n}</td>
                        <td className="py-1.5 text-right tabular-nums font-medium">
                          {fmtMoney(p.median)}
                        </td>
                        <td className="py-1.5 text-right tabular-nums text-muted-foreground">
                          {fmtMoney(p.min)}–{fmtMoney(p.max)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="text-xs text-muted-foreground">
                  No priced ads with a confident state yet.
                </p>
              )}
            </div>

            <div className="rounded-lg border border-border bg-card p-4">
              <h2 className="text-sm font-medium mb-3">Product mix</h2>
              <div className="space-y-1.5">
                {(stats.byCategory || []).map(c => (
                  <div key={c.category} className="flex items-center justify-between text-xs">
                    <span>{CATEGORY_LABELS[c.category] || c.category}</span>
                    <span className="tabular-nums text-muted-foreground">{c.n}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="rounded-lg border border-border bg-card p-4">
              <h2 className="text-sm font-medium mb-1">State signal mix</h2>
              <p className="text-xs text-muted-foreground mb-3">
                What the state attribution actually rests on.
              </p>
              <div className="space-y-1.5">
                {(stats.signalMix || []).map(s => (
                  <div key={`${s.signal}-${s.confidence}`}
                       className="flex items-center justify-between text-xs">
                    <span className="font-mono">{s.signal}</span>
                    <span className="flex items-center gap-2">
                      <span className={
                        s.confidence === 'high' ? 'text-sky-400'
                        : s.confidence === 'medium' ? 'text-amber-400'
                        : 'text-muted-foreground'
                      }>{s.confidence}</span>
                      <span className="tabular-nums text-muted-foreground w-12 text-right">{s.n}</span>
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {tab === 'market' && (
        <SolarAdsMarket state={state} category={category} />
      )}

      {tab === 'ads' && (
        <div className="space-y-3">
          <SolarAdsFilters
            filters={filters}
            onChange={setFilters}
            brands={facets.brands}
            advertisers={facets.advertisers}
            configs={facets.configs}
          />

          <div className="text-xs text-muted-foreground">
            {loading ? 'Loading…' : `${total.toLocaleString()} ads`}
          </div>

          <div className="space-y-2">
            {ads.map(ad => (
              <div key={ad.ad_archive_id}
                   className="rounded-lg border border-border bg-card p-3 flex gap-3">
                {ad.creative_sha ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={`/api/solar-ads/creatives/${ad.creative_sha}`}
                    alt=""
                    className="w-24 h-24 object-cover rounded bg-muted flex-shrink-0"
                    loading="lazy"
                  />
                ) : (
                  <div className="w-24 h-24 rounded bg-muted flex-shrink-0 flex items-center
                                  justify-center text-xs text-muted-foreground">
                    no image
                  </div>
                )}

                <div className="flex-1 min-w-0">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="font-medium text-sm truncate">
                        {ad.page_name || 'Unknown advertiser'}
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {ad.is_active === 1 ? (
                          <span className="text-green-400">Active</span>
                        ) : ad.is_active === 0 ? (
                          <span>Ended</span>
                        ) : null}
                        {ad.days_running != null && ` · ${ad.days_running}d running`}
                        {ad.collation_count ? ` · ${ad.collation_count} variants` : ''}
                        {ad.creative_count > 1 && ` · ${ad.creative_count} creatives`}
                      </div>
                    </div>
                    <div className="text-right flex-shrink-0">
                      {ad.dollars_per_kwh && (
                        <div className={`text-sm font-semibold tabular-nums ${
                          ad.flag ? 'text-amber-400' : ''
                        }`}>
                          {fmtMoney(ad.dollars_per_kwh)}/kWh
                        </div>
                      )}
                      {ad.dollars_per_kw && !ad.dollars_per_kwh && (
                        <div className="text-sm font-semibold tabular-nums">
                          {fmtMoney(ad.dollars_per_kw)}/kW
                        </div>
                      )}
                      {ad.price_aud && (
                        <div className="text-xs text-muted-foreground tabular-nums">
                          {fmtMoney(ad.price_aud)}
                          {ad.capacity_kwh ? ` · ${ad.capacity_kwh}kWh` : ''}
                          {ad.system_kw ? ` · ${ad.system_kw}kW` : ''}
                        </div>
                      )}
                    </div>
                  </div>

                  <p className="text-xs text-muted-foreground mt-1.5 line-clamp-2">
                    {ad.title ? `${ad.title} — ` : ''}{ad.body_text}
                  </p>

                  <div className="flex flex-wrap items-center gap-1.5 mt-2 text-[10px]">
                    {ad.best_state && (
                      // Weak attribution must not look like a confident one: an
                      // ad whose only state evidence is the search that found it
                      // is shown muted and marked, not as an equal claim.
                      <span
                        className={`px-1.5 py-0.5 rounded ${
                          (ad.conf_rank ?? 0) >= 3
                            ? 'bg-sky-500/15 text-sky-300'
                            : (ad.conf_rank ?? 0) === 2
                              ? 'bg-amber-500/15 text-amber-300'
                              : 'bg-muted text-muted-foreground'
                        }`}
                        title={
                          (ad.conf_rank ?? 0) >= 3 ? 'Strong evidence in ad copy'
                          : (ad.conf_rank ?? 0) === 2 ? 'Medium confidence — advertiser location or area code'
                          : 'Weak — inferred only from the search that surfaced this ad'
                        }
                      >
                        {ad.best_state}
                        {(ad.conf_rank ?? 0) < 3 && '?'}
                      </span>
                    )}
                    {ad.states_high && ad.states_high.split(',')
                      .filter(s => s && s !== ad.best_state).map(s => (
                        <span key={s} className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
                          {s}
                        </span>
                      ))}
                    {ad.product_category && (
                      <span className="px-1.5 py-0.5 rounded bg-muted">
                        {CATEGORY_LABELS[ad.product_category] || ad.product_category}
                      </span>
                    )}
                    {ad.brand && (
                      <span className="px-1.5 py-0.5 rounded bg-muted">{ad.brand}</span>
                    )}
                    {ad.offer_type && (
                      <span className="px-1.5 py-0.5 rounded bg-muted">{ad.offer_type}</span>
                    )}
                    {ad.flag && (
                      <span className="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300"
                            title="Price/capacity pairing looks implausible — verify before trusting">
                        {ad.flag}
                      </span>
                    )}
                    <a
                      href={`https://www.facebook.com/ads/library/?id=${ad.ad_archive_id}`}
                      target="_blank" rel="noopener noreferrer"
                      className="px-1.5 py-0.5 rounded bg-muted hover:bg-muted/70 underline"
                    >
                      Ad Library
                    </a>
                    {ad.link_url && (
                      <a href={ad.link_url} target="_blank" rel="noopener noreferrer"
                         className="px-1.5 py-0.5 rounded bg-muted hover:bg-muted/70 underline truncate max-w-[200px]">
                        Landing page
                      </a>
                    )}
                  </div>
                </div>
              </div>
            ))}
            {!loading && !ads.length && (
              <p className="text-sm text-muted-foreground py-8 text-center">
                No ads match these filters.
              </p>
            )}
          </div>

          {total > PAGE_SIZE && (
            <div className="flex items-center justify-between">
              <Button variant="outline" size="sm" disabled={page === 0}
                      onClick={() => setPage(p => Math.max(0, p - 1))}>
                Previous
              </Button>
              <span className="text-xs text-muted-foreground">
                {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} of {total}
              </span>
              <Button variant="outline" size="sm"
                      disabled={(page + 1) * PAGE_SIZE >= total}
                      onClick={() => setPage(p => p + 1)}>
                Next
              </Button>
            </div>
          )}
        </div>
      )}

      {tab === 'advertisers' && (
        <div className="rounded-lg border border-border bg-card overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-xs text-muted-foreground">
              <tr className="text-left">
                <th className="p-2.5">Advertiser</th>
                <th className="p-2.5 text-right">Ads</th>
                <th className="p-2.5 text-right">Active</th>
                <th className="p-2.5">States (high confidence)</th>
              </tr>
            </thead>
            <tbody>
              {(stats?.topAdvertisers || []).map(a => (
                <tr key={a.page_name} className="border-t border-border/50 hover:bg-muted/30">
                  <td className="p-2.5">{a.page_name}</td>
                  <td className="p-2.5 text-right tabular-nums">{a.ads}</td>
                  <td className="p-2.5 text-right tabular-nums">{a.active || 0}</td>
                  <td className="p-2.5 text-xs text-muted-foreground">{a.states || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!(stats?.topAdvertisers || []).length && (
            <p className="text-sm text-muted-foreground p-6 text-center">No advertisers yet.</p>
          )}
        </div>
      )}

      {tab === 'sweeps' && (
        <div className="rounded-lg border border-border bg-card overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-xs text-muted-foreground">
              <tr className="text-left">
                <th className="p-2.5">#</th>
                <th className="p-2.5">Mode</th>
                <th className="p-2.5">Status</th>
                <th className="p-2.5">Started</th>
                <th className="p-2.5 text-right">Progress</th>
                <th className="p-2.5 text-right">Ads seen</th>
              </tr>
            </thead>
            <tbody>
              {(stats?.sweeps || []).map(s => (
                <tr key={s.sweep_id} className="border-t border-border/50">
                  <td className="p-2.5 tabular-nums">{s.sweep_id}</td>
                  <td className="p-2.5">{s.mode}</td>
                  <td className="p-2.5">
                    <span className={
                      s.status === 'done' ? 'text-green-400'
                      : s.status === 'running' ? 'text-sky-400'
                      : 'text-amber-400'
                    }>{s.status}</span>
                  </td>
                  <td className="p-2.5 text-xs text-muted-foreground">{fmtDate(s.started_at)}</td>
                  <td className="p-2.5 text-right tabular-nums text-xs">
                    {s.targets_done}/{s.targets_total}
                  </td>
                  <td className="p-2.5 text-right tabular-nums">{s.ads_seen}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!(stats?.sweeps || []).length && (
            <p className="text-sm text-muted-foreground p-6 text-center">No sweeps recorded yet.</p>
          )}
        </div>
      )}
    </div>
  )
}
