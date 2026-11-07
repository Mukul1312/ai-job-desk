// api/notes.js — per-job status and notes.
//
// Deliberately optional. With no connection string the endpoint answers 501 and
// the page falls back to browser storage, so the site deploys and is useful
// before any database exists. Add the env var later and notes start syncing
// across devices with no code change.
//
// Driver is postgres.js rather than a vendor SDK, so the same code runs against
// Supabase, Neon, or any other Postgres. Storage choice stays reversible.
//
// Notes outlive the job data on purpose: job_id is a hash of the listing URL,
// computed identically in the browser, so a note written three weeks ago still
// resolves after that day's JSON has been pruned.

import postgres from 'postgres';

// Vercel's marketplace integrations inject different names depending on the
// provider, so accept whichever one is present instead of demanding one.
const CONN =
  process.env.DATABASE_URL ||
  process.env.POSTGRES_URL ||
  process.env.POSTGRES_PRISMA_URL ||
  process.env.SUPABASE_POSTGRES_URL ||
  process.env.POSTGRES_URL_NON_POOLING ||
  '';

const TOKEN = process.env.NOTES_TOKEN || '';

let sql = null;
let ready = false;

function conn() {
  if (!CONN) return null;
  if (!sql) {
    // Serverless: keep the pool tiny and let idle sockets die, otherwise
    // concurrent lambdas exhaust the provider's connection limit.
    sql = postgres(CONN, {
      max: 1,
      idle_timeout: 20,
      connect_timeout: 10,
      prepare: false, // pgbouncer in transaction mode rejects prepared statements
    });
  }
  return sql;
}

async function ensureTable(db) {
  if (ready) return;
  await db`
    CREATE TABLE IF NOT EXISTS job_notes (
      job_id     text PRIMARY KEY,
      status     text NOT NULL DEFAULT 'none',
      note       text NOT NULL DEFAULT '',
      title      text,
      company    text,
      url        text,
      updated_at timestamptz NOT NULL DEFAULT now()
    )`;
  ready = true;
}

export default async function handler(req, res) {
  res.setHeader('Cache-Control', 'no-store');

  // Optional shared secret. A deployment URL is guessable, so set this if the
  // link is ever shared; without it anyone with the URL can read and write.
  if (TOKEN && req.headers['x-notes-token'] !== TOKEN) {
    return res.status(401).json({ error: 'bad token' });
  }

  const db = conn();
  if (!db) {
    return res.status(501).json({
      error: 'no_database',
      hint: 'Add a Postgres store in Vercel to sync notes. Until then the page keeps them in this browser only.',
    });
  }

  try {
    await ensureTable(db);

    if (req.method === 'GET') {
      const rows = await db`
        SELECT job_id, status, note, title, company, url, updated_at
        FROM job_notes ORDER BY updated_at DESC LIMIT 2000`;
      const notes = {};
      for (const r of rows) {
        notes[r.job_id] = {
          status: r.status,
          note: r.note,
          title: r.title,
          company: r.company,
          url: r.url,
          updatedAt: r.updated_at,
        };
      }
      return res.status(200).json({ notes });
    }

    if (req.method === 'POST' || req.method === 'PUT') {
      const body = typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || {});
      const id = String(body.jobId || '').slice(0, 64);
      if (!id) return res.status(400).json({ error: 'jobId required' });

      const status = String(body.status || 'none').slice(0, 24);
      const note = String(body.note || '').slice(0, 8000);
      const title = String(body.title || '').slice(0, 300);
      const company = String(body.company || '').slice(0, 200);
      const url = String(body.url || '').slice(0, 1000);

      // Clearing both fields removes the row rather than leaving an empty
      // shell that would still show as "touched" in a status filter.
      if (status === 'none' && !note.trim()) {
        await db`DELETE FROM job_notes WHERE job_id = ${id}`;
        return res.status(200).json({ ok: true, deleted: true });
      }

      const rows = await db`
        INSERT INTO job_notes (job_id, status, note, title, company, url, updated_at)
        VALUES (${id}, ${status}, ${note}, ${title}, ${company}, ${url}, now())
        ON CONFLICT (job_id) DO UPDATE SET
          status     = EXCLUDED.status,
          note       = EXCLUDED.note,
          title      = COALESCE(NULLIF(EXCLUDED.title, ''), job_notes.title),
          company    = COALESCE(NULLIF(EXCLUDED.company, ''), job_notes.company),
          url        = COALESCE(NULLIF(EXCLUDED.url, ''), job_notes.url),
          updated_at = now()
        RETURNING updated_at`;
      return res.status(200).json({ ok: true, updatedAt: rows[0].updated_at });
    }

    res.setHeader('Allow', 'GET, POST');
    return res.status(405).json({ error: 'method not allowed' });
  } catch (err) {
    console.error('notes error', err);
    return res.status(500).json({ error: 'server_error', detail: String(err.message || err) });
  }
}
