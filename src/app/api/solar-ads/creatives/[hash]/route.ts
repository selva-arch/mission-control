import { NextRequest, NextResponse } from 'next/server'
import { readFile } from 'fs/promises'
import { join, normalize } from 'path'
import { requireRole } from '@/lib/auth'
import { logger } from '@/lib/logger'
import { CREATIVES_DIR, queryOne } from '@/lib/solar-ads-db'

/**
 * GET /api/solar-ads/creatives/[hash] — serve an archived ad creative.
 *
 * Facebook CDN URLs expire, so the sweep keeps a local copy; this serves it
 * back to the panel. The filename comes from the database rather than the URL
 * so a crafted hash cannot address arbitrary files, and the resolved path is
 * re-checked against the creatives root before any read.
 */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ hash: string }> }
) {
  const auth = requireRole(request, 'viewer')
  if ('error' in auth) return NextResponse.json({ error: auth.error }, { status: auth.status })

  try {
    const { hash } = await params
    if (!/^[0-9a-f]{8,40}$/.test(hash)) {
      return NextResponse.json({ error: 'Invalid creative id' }, { status: 400 })
    }

    const row = queryOne<{ local_path: string }>(
      'SELECT local_path FROM creatives WHERE sha1 = ?', [hash]
    )
    if (!row?.local_path) {
      return NextResponse.json({ error: 'Not found' }, { status: 404 })
    }

    const root = normalize(CREATIVES_DIR)
    const full = normalize(join(root, row.local_path))
    if (!full.startsWith(root)) {
      logger.warn({ hash }, 'creative path escaped the archive root')
      return NextResponse.json({ error: 'Not found' }, { status: 404 })
    }

    const data = await readFile(full)
    return new NextResponse(new Uint8Array(data), {
      headers: {
        'Content-Type': 'image/jpeg',
        // Content is immutable: the filename is derived from its own hash.
        'Cache-Control': 'private, max-age=86400, immutable',
      },
    })
  } catch {
    return NextResponse.json({ error: 'Not found' }, { status: 404 })
  }
}
