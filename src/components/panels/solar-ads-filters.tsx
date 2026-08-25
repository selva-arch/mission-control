'use client'

import { useMemo, useState } from 'react'

/**
 * Grouped filter bar for the Ads tab.
 *
 * The previous single row held six controls; there are now around twenty. They
 * are grouped into collapsible sections with an active count, so the common case
 * (search plus a state) stays one line and the depth is there when wanted
 * rather than always in the way.
 */

export interface Filters {
  q: string
  state: string
  confidence: string
  category: string
  brand: string
  advertiser: string
  offerType: string
  basis: string
  config: string
  status: string
  priced: boolean
  hideSuspect: boolean
  watchlist: boolean
  advertiserType: string
  hasFinance: boolean
  hasUrgency: boolean
  priceMin: string
  priceMax: string
  kwMin: string
  kwMax: string
  kwhMin: string
  kwhMax: string
  perKwMax: string
  perKwhMax: string
  startedWithin: string
  runningOver: string
}

export const EMPTY_FILTERS: Filters = {
  q: '', state: '', confidence: 'low', category: '', brand: '', advertiser: '',
  offerType: '', basis: '', config: '', status: '', priced: false,
  hideSuspect: false, watchlist: false, advertiserType: '',
  hasFinance: false, hasUrgency: false,
  priceMin: '', priceMax: '', kwMin: '', kwMax: '', kwhMin: '', kwhMax: '',
  perKwMax: '', perKwhMax: '', startedWithin: '', runningOver: '',
}

const STATES = ['NSW', 'VIC', 'QLD', 'SA', 'WA', 'TAS', 'NT', 'ACT']

const CATEGORY_LABELS: Record<string, string> = {
  battery: 'Batteries', solar: 'Solar', solar_battery: 'Solar + battery',
  ev_charger: 'EV chargers', heat_pump: 'Heat pumps', other: 'Other',
}

/** Fields that belong to each collapsible section, for the active-count badge. */
const SECTIONS = {
  Price: ['priceMin', 'priceMax', 'perKwMax', 'perKwhMax', 'priced'],
  Size: ['kwMin', 'kwMax', 'kwhMin', 'kwhMax', 'config'],
  Advertiser: ['advertiser', 'brand', 'watchlist', 'advertiserType'],
  Offer: ['offerType', 'basis', 'hasFinance', 'hasUrgency'],
  Timing: ['startedWithin', 'runningOver', 'status'],
} as const

const isSet = (v: unknown) => v !== '' && v !== false && v !== undefined && v !== null

export function SolarAdsFilters({
  filters, onChange, brands, advertisers, advertiserCounts, configs,
}: {
  filters: Filters
  onChange: (next: Filters) => void
  brands: string[]
  advertisers: string[]
  advertiserCounts?: Array<{ name: string; ads: number }>
  configs: string[]
}) {
  const [open, setOpen] = useState<string | null>(null)
  const set = <K extends keyof Filters>(k: K, v: Filters[K]) =>
    onChange({ ...filters, [k]: v })

  const activeIn = useMemo(() => {
    const out: Record<string, number> = {}
    for (const [name, keys] of Object.entries(SECTIONS)) {
      out[name] = (keys as readonly string[])
        .filter(k => isSet(filters[k as keyof Filters])).length
    }
    return out
  }, [filters])

  const totalActive = useMemo(
    () => Object.keys(EMPTY_FILTERS).filter(k => {
      const key = k as keyof Filters
      // Confidence has a non-empty default, so equality with the default is the
      // right test rather than emptiness.
      return filters[key] !== EMPTY_FILTERS[key]
    }).length,
    [filters],
  )

  const input = "px-2 py-1 text-sm bg-background border border-border rounded w-20"
  const select = "px-2 py-1.5 text-sm bg-background border border-border rounded"

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2 items-center">
        <input
          value={filters.q}
          onChange={e => set('q', e.target.value)}
          placeholder="Search copy or advertiser…"
          className="px-2 py-1.5 text-sm bg-background border border-border rounded
                     flex-1 min-w-[200px]"
        />
        <select value={filters.state} onChange={e => set('state', e.target.value)}
                className={select}>
          <option value="">All states</option>
          {STATES.map(s => <option key={s} value={s}>{s}</option>)}
          <option value="NATIONAL">National</option>
        </select>
        <select value={filters.confidence} onChange={e => set('confidence', e.target.value)}
                className={select} title="Minimum evidence for the state filter">
          <option value="high">High confidence</option>
          <option value="medium">Medium+</option>
          <option value="low">Any evidence</option>
        </select>
        <select value={filters.category} onChange={e => set('category', e.target.value)}
                className={select}>
          <option value="">All products</option>
          {Object.entries(CATEGORY_LABELS).map(([k, v]) =>
            <option key={k} value={k}>{v}</option>)}
        </select>
        {totalActive > 0 && (
          <button onClick={() => onChange(EMPTY_FILTERS)}
                  className="px-2 py-1.5 text-sm text-muted-foreground hover:text-foreground
                             underline">
            Clear {totalActive}
          </button>
        )}
      </div>

      <div className="flex flex-wrap gap-1.5">
        {Object.keys(SECTIONS).map(name => (
          <button
            key={name}
            onClick={() => setOpen(open === name ? null : name)}
            className={`px-2.5 py-1 text-xs rounded border transition-colors ${
              open === name
                ? 'border-primary text-foreground'
                : 'border-border text-muted-foreground hover:text-foreground'
            }`}
          >
            {name}
            {activeIn[name] > 0 && (
              <span className="ml-1.5 px-1 rounded bg-primary/20 text-primary">
                {activeIn[name]}
              </span>
            )}
          </button>
        ))}
      </div>

      {open === 'Price' && (
        <div className="flex flex-wrap items-center gap-2 p-2.5 rounded border border-border
                        bg-card text-sm">
          <span className="text-muted-foreground text-xs">Price</span>
          <input type="number" placeholder="min" className={input}
                 value={filters.priceMin} onChange={e => set('priceMin', e.target.value)} />
          <input type="number" placeholder="max" className={input}
                 value={filters.priceMax} onChange={e => set('priceMax', e.target.value)} />
          <span className="text-muted-foreground text-xs ml-2">Max $/kW</span>
          <input type="number" placeholder="any" className={input}
                 value={filters.perKwMax} onChange={e => set('perKwMax', e.target.value)} />
          <span className="text-muted-foreground text-xs ml-2">Max $/kWh</span>
          <input type="number" placeholder="any" className={input}
                 value={filters.perKwhMax} onChange={e => set('perKwhMax', e.target.value)} />
          <label className="flex items-center gap-1.5 ml-2">
            <input type="checkbox" checked={filters.priced}
                   onChange={e => set('priced', e.target.checked)} />
            Priced only
          </label>
        </div>
      )}

      {open === 'Size' && (
        <div className="flex flex-wrap items-center gap-2 p-2.5 rounded border border-border
                        bg-card text-sm">
          <span className="text-muted-foreground text-xs">Solar kW</span>
          <input type="number" placeholder="min" className={input}
                 value={filters.kwMin} onChange={e => set('kwMin', e.target.value)} />
          <input type="number" placeholder="max" className={input}
                 value={filters.kwMax} onChange={e => set('kwMax', e.target.value)} />
          <span className="text-muted-foreground text-xs ml-2">Battery kWh</span>
          <input type="number" placeholder="min" className={input}
                 value={filters.kwhMin} onChange={e => set('kwhMin', e.target.value)} />
          <input type="number" placeholder="max" className={input}
                 value={filters.kwhMax} onChange={e => set('kwhMax', e.target.value)} />
          <select value={filters.config} onChange={e => set('config', e.target.value)}
                  className={select + ' ml-2'}>
            <option value="">Any configuration</option>
            {configs.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
      )}

      {open === 'Advertiser' && (
        <div className="flex flex-wrap items-center gap-2 p-2.5 rounded border border-border
                        bg-card text-sm">
          <select value={filters.advertiser} onChange={e => set('advertiser', e.target.value)}
                  className={select}>
            <option value="">Any advertiser ({advertisers.length})</option>
            {(advertiserCounts?.length ? advertiserCounts : advertisers.map(name => ({ name, ads: 0 })))
              .map(a => (
                <option key={a.name} value={a.name}>
                  {a.name}{a.ads ? ` (${a.ads})` : ''}
                </option>
              ))}
          </select>
          <select value={filters.brand} onChange={e => set('brand', e.target.value)}
                  className={select}>
            <option value="">Any brand</option>
            {brands.map(b => <option key={b} value={b}>{b}</option>)}
          </select>
          <select value={filters.advertiserType}
                  onChange={e => set('advertiserType', e.target.value)}
                  className={select}>
            <option value="">Any advertiser type</option>
            <option value="installer">Installers</option>
            <option value="manufacturer">Manufacturers</option>
            <option value="platform">Platforms</option>
            <option value="unknown">Unclassified</option>
          </select>
          <label className="flex items-center gap-1.5"
                 title="Only the competitors listed in watchlist.yaml">
            <input type="checkbox" checked={filters.watchlist}
                   onChange={e => set('watchlist', e.target.checked)} />
            Watchlist only
          </label>
          {!brands.length && (
            <span className="text-xs text-muted-foreground">
              Brands appear once the AI enrichment pass has run.
            </span>
          )}
        </div>
      )}

      {open === 'Offer' && (
        <div className="flex flex-wrap items-center gap-2 p-2.5 rounded border border-border
                        bg-card text-sm">
          <select value={filters.offerType} onChange={e => set('offerType', e.target.value)}
                  className={select}>
            <option value="">Any offer type</option>
            {['discount', 'financing', 'bundle', 'free_upgrade', 'quote_only', 'other']
              .map(o => <option key={o} value={o}>{o.replace('_', ' ')}</option>)}
          </select>
          <select value={filters.basis} onChange={e => set('basis', e.target.value)}
                  className={select}>
            <option value="">Any rebate basis</option>
            <option value="post_rebate">After rebate</option>
            <option value="pre_rebate">Before rebate</option>
            <option value="unknown">Not stated</option>
          </select>
          <label className="flex items-center gap-1.5">
            <input type="checkbox" checked={filters.hasFinance}
                   onChange={e => set('hasFinance', e.target.checked)} />
            Has finance terms
          </label>
          <label className="flex items-center gap-1.5">
            <input type="checkbox" checked={filters.hasUrgency}
                   onChange={e => set('hasUrgency', e.target.checked)} />
            Uses urgency
          </label>
          <label className="flex items-center gap-1.5">
            <input type="checkbox" checked={filters.hideSuspect}
                   onChange={e => set('hideSuspect', e.target.checked)} />
            Hide check-parse
          </label>
        </div>
      )}

      {open === 'Timing' && (
        <div className="flex flex-wrap items-center gap-2 p-2.5 rounded border border-border
                        bg-card text-sm">
          <select value={filters.startedWithin}
                  onChange={e => set('startedWithin', e.target.value)} className={select}>
            <option value="">Started any time</option>
            <option value="7">Started last 7 days</option>
            <option value="30">Started last 30 days</option>
            <option value="90">Started last 90 days</option>
          </select>
          <span className="text-muted-foreground text-xs ml-2">Running over (days)</span>
          <input type="number" placeholder="any" className={input}
                 value={filters.runningOver} onChange={e => set('runningOver', e.target.value)} />
          <select value={filters.status} onChange={e => set('status', e.target.value)}
                  className={select + ' ml-2'}>
            <option value="">Any status</option>
            <option value="active">Active</option>
            <option value="ended">Ended</option>
          </select>
          <span className="text-xs text-muted-foreground ml-1">
            Long-running ads carry the most stable prices.
          </span>
        </div>
      )}
    </div>
  )
}
