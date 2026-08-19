import Database from 'better-sqlite3'
import { existsSync } from 'fs'
import { dirname, join } from 'path'
import { config } from './config'
import { logger } from './logger'

/**
 * Read-only access to the solar ad archive.
 *
 * The archive is written by the Python sweep in solar-ad-scraper/ and lives in
 * its own database file rather than mission-control.db: it grows to gigabytes
 * of creatives and per-sweep snapshot rows, and a sweep is a long-running
 * writer. Opening it read-only here means the dashboard can never interfere
 * with a sweep in progress, and the archive never enters the app's
 * migration-managed schema.
 *
 * Every accessor degrades to an empty result when the file does not exist yet,
 * so the panel renders normally before the first sweep has ever run.
 */

const DB_PATH =
  process.env.SOLAR_ADS_DB_PATH || join(dirname(config.dbPath), 'solar-ads.db')

export const CREATIVES_DIR =
  process.env.SOLAR_ADS_CREATIVES_DIR ||
  join(dirname(config.dbPath), 'solar-ads', 'creatives')

let db: Database.Database | null = null
let missingLogged = false

export function getSolarAdsDb(): Database.Database | null {
  if (db) return db
  if (!existsSync(DB_PATH)) {
    if (!missingLogged) {
      logger.info({ path: DB_PATH }, 'solar ads archive not present yet')
      missingLogged = true
    }
    return null
  }
  try {
    db = new Database(DB_PATH, { readonly: true, fileMustExist: true })
    db.pragma('busy_timeout = 5000')
    return db
  } catch (err) {
    logger.error({ err, path: DB_PATH }, 'failed to open solar ads archive')
    return null
  }
}

export function archiveExists(): boolean {
  return getSolarAdsDb() !== null
}

/** Run a query, returning [] if the archive is absent or the query fails. */
export function query<T = Record<string, unknown>>(sql: string, params: unknown[] = []): T[] {
  const conn = getSolarAdsDb()
  if (!conn) return []
  try {
    return conn.prepare(sql).all(...(params as never[])) as T[]
  } catch (err) {
    logger.error({ err, sql }, 'solar ads query failed')
    return []
  }
}

export function queryOne<T = Record<string, unknown>>(sql: string, params: unknown[] = []): T | null {
  return query<T>(sql, params)[0] ?? null
}

export const STATES = ['NSW', 'VIC', 'QLD', 'SA', 'WA', 'TAS', 'NT', 'ACT'] as const
export const CONFIDENCE_RANK: Record<string, number> = { high: 3, medium: 2, low: 1 }

/**
 * SQL fragment resolving one representative state per ad.
 *
 * An ad carries many state signals; for list and aggregate views we need a
 * single label. NATIONAL wins outright (it means the evidence pointed
 * everywhere), otherwise the strongest-confidence state with the most
 * independent signals behind it wins.
 */
export const BEST_STATE_CTE = `
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
`

/** Latest price reading per ad from a given source. */
export const LATEST_PRICE_CTE = `
  latest_price AS (
    SELECT p.* FROM price_observations p
     WHERE p.source = 'regex'
       AND p.id = (SELECT MAX(id) FROM price_observations
                    WHERE ad_archive_id = p.ad_archive_id AND source = 'regex')
  )
`

/** Latest activity snapshot per ad. */
export const LATEST_SNAPSHOT_CTE = `
  latest_snap AS (
    SELECT s.* FROM ad_snapshots s
     WHERE s.id = (SELECT MAX(id) FROM ad_snapshots
                    WHERE ad_archive_id = s.ad_archive_id)
  )
`
