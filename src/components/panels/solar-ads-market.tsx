'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'

/**
 * What the market charges per standard configuration.
 *
 * The form is a dot-strip plot rather than a bar chart on purpose: the question
 * is "where does the market sit and where does each competitor sit within it",
 * which needs the individual points and the spread, not a single aggregate bar.
 *
 * Colour encodes rebate basis. Prices stated on different bases are never
 * blended into one median — a pre-rebate and a post-rebate figure describe
 * different things — so each basis carries its own median marker.
 *
 * Palette: categorical slots 1-3 (blue/orange/aqua), dark steps, validated with
 * the dataviz validator against this dashboard's card surface (#0e121b) on the
 * all-pairs list: worst CVD ΔE 9.4, normal-vision ΔE 20.9, all contrast ≥ 3:1.
 */

const BASIS = [
  { key: 'post', label: 'After rebate', color: '#3987e5' },
  { key: 'pre', label: 'Before rebate', color: '#d95926' },
  { key: 'unknown', label: 'Not stated', color: '#199e70' },
] as const

type BasisKey = typeof BASIS[number]['key']

interface Summary {
  n: number; median: number | null; min: number | null; max: number | null
  q1: number | null; q3: number | null; indicative: boolean
}
interface Slice { price: Summary; perUnit: Summary; advertisers: number }
interface Config { config: string; total: number; post: Slice; pre: Slice; unknown: Slice }
interface Position {
  config: string; adv: string; price: number; basis: string
  vsMedian: number | null; basisN: number
}
interface MarketData {
  archiveReady: boolean
  dim?: 'kw' | 'kwh'
  configs?: Config[]
  positions?: Position[]
  coverage?: { known: number; total: number; suspect: number; nonInstaller: number }
  minSample?: number
}

const money = (v: number | null | undefined) =>
  v == null ? '—' : '$' + Math.round(v).toLocaleString()

export function SolarAdsMarket({ state, category }: { state?: string; category?: string }) {
  const [dim, setDim] = useState<'kw' | 'kwh'>('kw')
  const [measure, setMeasure] = useState<'price' | 'perUnit'>('price')
  const [showTable, setShowTable] = useState(false)
  const [data, setData] = useState<MarketData | null>(null)
  const [loading, setLoading] = useState(true)
  const [openConfig, setOpenConfig] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const p = new URLSearchParams({ dim })
      if (state) p.set('state', state)
      if (category) p.set('category', category)
      const r = await fetch(`/api/solar-ads/market?${p}`)
      setData(await r.json())
    } catch {
      setData({ archiveReady: false })
    } finally {
      setLoading(false)
    }
  }, [dim, state, category])

  useEffect(() => { load() }, [load])

  const configs = useMemo(() => data?.configs ?? [], [data])
  const unit = dim === 'kw' ? '/kW' : '/kWh'

  // One shared scale across configurations. Per-lane scales would make the
  // spreads look identical when they are not.
  const scaleMax = useMemo(() => {
    let max = 0
    for (const c of configs) {
      for (const b of BASIS) {
        const s = c[b.key][measure]
        if (s.max != null) max = Math.max(max, s.max)
      }
    }
    return max || 1
  }, [configs, measure])

  if (loading && !data) {
    return <p className="text-sm text-muted-foreground p-6">Loading market data…</p>
  }
  if (!configs.length) {
    return (
      <div className="rounded-lg border border-border bg-card p-6 m-4">
        <p className="text-sm text-muted-foreground">
          No priced ads fall into a standard configuration yet
          {state ? ` for ${state}` : ''}. Market rates need ads stating both a price
          and a system size — run a fuller sweep, or clear the filters.
        </p>
      </div>
    )
  }

  const cov = data?.coverage
  const minSample = data?.minSample ?? 5
  const pct = (v: number) => `${(v / scaleMax) * 100}%`

  return (
    <div className="p-4 md:p-6 space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex rounded-md border border-border overflow-hidden">
          {(['kw', 'kwh'] as const).map(d => (
            <button
              key={d}
              onClick={() => setDim(d)}
              className={`px-3 py-1.5 text-sm ${dim === d
                ? 'bg-primary text-primary-foreground font-medium'
                : 'bg-card text-muted-foreground hover:text-foreground'}`}
            >
              {d === 'kw' ? 'Solar (kW)' : 'Battery (kWh)'}
            </button>
          ))}
        </div>
        <div className="flex rounded-md border border-border overflow-hidden">
          {([['price', 'Total price'], ['perUnit', `$${unit}`]] as const).map(([m, label]) => (
            <button
              key={m}
              onClick={() => setMeasure(m)}
              className={`px-3 py-1.5 text-sm ${measure === m
                ? 'bg-primary text-primary-foreground font-medium'
                : 'bg-card text-muted-foreground hover:text-foreground'}`}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="flex-1" />
        <Button variant="outline" size="sm" onClick={() => setShowTable(t => !t)}>
          {showTable ? 'Show chart' : 'Show table'}
        </Button>
      </div>

      {/* Identity is never colour alone: every basis is named in the legend and
          again in the per-configuration table below. */}
      <div className="flex flex-wrap items-center gap-4 text-xs">
        {BASIS.map(b => (
          <span key={b.key} className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-full inline-block"
                  style={{ backgroundColor: b.color }} />
            <span className="text-muted-foreground">{b.label}</span>
          </span>
        ))}
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-0.5 h-3 bg-foreground" />
          <span className="text-muted-foreground">median</span>
        </span>
      </div>

      {cov && (
        <p className="text-xs text-muted-foreground">
          Rebate basis is known for <strong>{cov.known.toLocaleString()}</strong> of{' '}
          {cov.total.toLocaleString()} priced ads — the rest are grouped under
          &ldquo;not stated&rdquo; and cannot be compared against the other two.
          {cov.suspect > 0 && <> {cov.suspect.toLocaleString()} ads with implausible
          price parses are excluded from every figure here.</>}
          {cov.nonInstaller > 0 && <> {cov.nonInstaller.toLocaleString()} ads from
          manufacturers and platforms are also excluded &mdash; they advertise
          brands and services, not installed systems, so their prices are not
          what a customer pays.</>}
        </p>
      )}

      {showTable ? (
        <div className="rounded-lg border border-border bg-card overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-xs text-muted-foreground">
              <tr className="text-left">
                <th className="p-2.5">Configuration</th>
                <th className="p-2.5">Basis</th>
                <th className="p-2.5 text-right">Ads</th>
                <th className="p-2.5 text-right">Advertisers</th>
                <th className="p-2.5 text-right">Median</th>
                <th className="p-2.5 text-right">Range</th>
                <th className="p-2.5 text-right">Middle half</th>
              </tr>
            </thead>
            <tbody>
              {configs.flatMap(c => BASIS.filter(b => c[b.key][measure].n > 0).map(b => {
                const s = c[b.key][measure]
                return (
                  <tr key={`${c.config}-${b.key}`} className="border-t border-border/50">
                    <td className="p-2.5 font-medium">{c.config}</td>
                    <td className="p-2.5">
                      <span className="inline-flex items-center gap-1.5">
                        <span className="w-2 h-2 rounded-full inline-block"
                              style={{ backgroundColor: b.color }} />
                        {b.label}
                      </span>
                    </td>
                    <td className="p-2.5 text-right tabular-nums">{s.n}</td>
                    <td className="p-2.5 text-right tabular-nums">{c[b.key].advertisers}</td>
                    <td className="p-2.5 text-right tabular-nums font-medium">
                      {money(s.median)}
                      {s.indicative && (
                        <span className="ml-1 text-[10px] text-amber-400">indicative</span>
                      )}
                    </td>
                    <td className="p-2.5 text-right tabular-nums text-muted-foreground">
                      {money(s.min)}–{money(s.max)}
                    </td>
                    <td className="p-2.5 text-right tabular-nums text-muted-foreground">
                      {s.q1 != null ? `${money(s.q1)}–${money(s.q3)}` : '—'}
                    </td>
                  </tr>
                )
              }))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="space-y-2">
          {configs.map(c => (
            <div key={c.config} className="rounded-lg border border-border bg-card p-3">
              <div className="flex items-baseline justify-between mb-2">
                <button
                  onClick={() => setOpenConfig(openConfig === c.config ? null : c.config)}
                  className="text-sm font-semibold hover:underline"
                >
                  {c.config}
                  <span className="ml-2 text-xs font-normal text-muted-foreground">
                    {c.total} ad{c.total === 1 ? '' : 's'} · who&rsquo;s where ▾
                  </span>
                </button>
                <div className="flex gap-3 text-xs">
                  {BASIS.filter(b => c[b.key][measure].n > 0).map(b => {
                    const s = c[b.key][measure]
                    return (
                      <span key={b.key} className="tabular-nums" style={{ color: b.color }}>
                        {money(s.median)}
                        <span className="text-muted-foreground">
                          {' '}({s.n}{s.indicative ? ', indicative' : ''})
                        </span>
                      </span>
                    )
                  })}
                </div>
              </div>

              {/* One lane per basis. Separate lanes rather than one shared row
                  keeps the bases visually as separate as they are statistically. */}
              <div className="space-y-1">
                {BASIS.filter(b => c[b.key][measure].n > 0).map(b => {
                  const s = c[b.key][measure]
                  return (
                    <div key={b.key} className="relative h-6 rounded bg-muted/40">
                      {s.q1 != null && s.q3 != null && (
                        <div
                          className="absolute top-0 bottom-0 rounded"
                          style={{
                            left: pct(s.q1), width: pct(s.q3 - s.q1),
                            backgroundColor: b.color, opacity: 0.18,
                          }}
                          title={`Middle half: ${money(s.q1)}–${money(s.q3)}`}
                        />
                      )}
                      {s.min != null && s.max != null && (
                        <div
                          className="absolute top-1/2 h-0.5 -translate-y-1/2 rounded"
                          style={{
                            left: pct(s.min), width: pct(Math.max(s.max - s.min, 0)),
                            backgroundColor: b.color, opacity: 0.5,
                          }}
                        />
                      )}
                      {[s.min, s.max].map((v, i) => v == null ? null : (
                        <span
                          key={i}
                          className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2
                                     w-2.5 h-2.5 rounded-full"
                          style={{
                            left: pct(v), backgroundColor: b.color,
                            boxShadow: '0 0 0 2px hsl(var(--card))',
                          }}
                          title={`${b.label}: ${money(v)}`}
                        />
                      ))}
                      {s.median != null && (
                        <span
                          className="absolute top-0.5 bottom-0.5 w-0.5 -translate-x-1/2
                                     bg-foreground rounded"
                          style={{ left: pct(s.median) }}
                          title={`${b.label} median: ${money(s.median)} (n=${s.n})`}
                        />
                      )}
                    </div>
                  )
                })}
              </div>

              {openConfig === c.config && (
                <div className="mt-3 border-t border-border/60 pt-2">
                  <table className="w-full text-xs">
                    <thead className="text-muted-foreground">
                      <tr className="text-left">
                        <th className="pb-1.5">Advertiser</th>
                        <th className="pb-1.5">Basis</th>
                        <th className="pb-1.5 text-right">Price</th>
                        <th className="pb-1.5 text-right">vs median</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(data?.positions ?? [])
                        .filter(p => p.config === c.config)
                        .map(p => {
                          const b = BASIS.find(x =>
                            (x.key === 'post' && p.basis === 'post_rebate') ||
                            (x.key === 'pre' && p.basis === 'pre_rebate') ||
                            (x.key === 'unknown' && p.basis === 'unknown'))
                          return (
                            <tr key={p.adv + p.price} className="border-t border-border/40">
                              <td className="py-1.5">{p.adv}</td>
                              <td className="py-1.5">
                                <span className="inline-flex items-center gap-1.5">
                                  <span className="w-1.5 h-1.5 rounded-full inline-block"
                                        style={{ backgroundColor: b?.color }} />
                                  <span className="text-muted-foreground">{b?.label}</span>
                                </span>
                              </td>
                              <td className="py-1.5 text-right tabular-nums">
                                {money(p.price)}
                              </td>
                              <td className="py-1.5 text-right tabular-nums">
                                {p.vsMedian == null ? (
                                  <span className="text-muted-foreground">—</span>
                                ) : (
                                  <span className={p.vsMedian < 0 ? 'text-emerald-400'
                                                 : p.vsMedian > 0 ? 'text-amber-400'
                                                 : 'text-muted-foreground'}>
                                    {p.vsMedian > 0 ? '+' : ''}{p.vsMedian}%
                                  </span>
                                )}
                                {p.basisN < minSample && (
                                  <span className="ml-1 text-[10px] text-muted-foreground">
                                    (n={p.basisN})
                                  </span>
                                )}
                              </td>
                            </tr>
                          )
                        })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <p className="text-xs text-muted-foreground">
        Medians drawn from fewer than {minSample} ads are marked{' '}
        <span className="text-amber-400">indicative</span> — treat them as a hint,
        not a market rate. A percentage against a small sample says more about the
        sample than the market.
      </p>
    </div>
  )
}
