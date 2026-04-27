'use strict';
// Puppeteer-based fallback fetcher for clinics whose homepage was empty
// from urllib (typically modern JS-rendered SPAs).
//
// Mirrors fetch_clinic_pages.py: tries homepage + a couple of candidate
// service/about paths, extracts text from <main>/<article>/<body>, caps at
// 8000 chars per page, requires >=300 chars to count.
//
// Resumable: skips placeIds whose pages JSON already has 'pages' array
// with content unless --force is passed.
//
// Usage: node scripts/fetch_clinic_pages_js.js --pids /tmp/foo.txt [--force] [--limit N]

const fs = require('fs');
const path = require('path');

const ROOT = path.dirname(__dirname);
const DATA = path.join(ROOT, 'data', 'clinics.min.json');
const OUT_DIR = path.join(ROOT, 'data', 'clinic_pages');
const CANDIDATE_PATHS = ['/', '/services/', '/trt/', '/about/', '/testosterone/', '/hormone-therapy/'];
const MAX_PER_PAGE = 8000;
const MIN_TEXT = 300;
const MAX_PAGES = 3;
const NAV_TIMEOUT = 9000;
const HYDRATE_WAIT = 1200;

const args = process.argv.slice(2);
function getArg(name) {
  const i = args.indexOf('--' + name);
  if (i === -1) return null;
  return args[i + 1];
}
const FORCE = args.includes('--force');
const LIMIT = parseInt(getArg('limit') || '0', 10);
const PIDS_FILE = getArg('pids');
const SLEEP_MS = parseInt(getArg('sleep') || '800', 10);

if (!PIDS_FILE) {
  console.error('usage: node scripts/fetch_clinic_pages_js.js --pids <file> [--force] [--limit N] [--sleep MS]');
  process.exit(2);
}

const pids = fs.readFileSync(PIDS_FILE, 'utf8').split(/[\s,]+/).map(s => s.trim()).filter(Boolean);
const clinics = JSON.parse(fs.readFileSync(DATA, 'utf8'));
const byId = new Map(clinics.map(c => [c.placeId, c]));

function originOf(url) {
  try {
    const u = new URL(url.includes('://') ? url : 'http://' + url);
    return `${u.protocol}//${u.host}`;
  } catch { return null; }
}

function cleanText(html) {
  // Server-side text extraction handled in page.evaluate; this is a fallback.
  return html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
}

async function extractFromPage(page) {
  return await page.evaluate(() => {
    if (!document.body) return '';
    // Use innerText on a clone of <body> so layout-driven whitespace is
    // preserved. Strip only truly non-content tags; many marketing sites
    // wrap hero copy in <header> or <nav>, so we leave those alone and rely
    // on length-based filtering downstream.
    const clone = document.body.cloneNode(true);
    ['script', 'style', 'noscript', 'svg', 'iframe', 'form'].forEach(tag =>
      clone.querySelectorAll(tag).forEach(el => el.remove())
    );
    let text = clone.innerText || clone.textContent || '';
    text = text.replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
    return text;
  });
}

(async () => {
  const puppeteerPath = '/home/claw/.openclaw/scratch/node_modules/puppeteer';
  const puppeteer = require(puppeteerPath);
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome',
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
  });

  let ok = 0, failed = 0, skipped = 0;
  const todo = LIMIT ? pids.slice(0, LIMIT) : pids;

  for (let i = 0; i < todo.length; i++) {
    const pid = todo[i];
    const c = byId.get(pid);
    if (!c) { console.error(`[skip-missing] ${pid}`); continue; }
    const outPath = path.join(OUT_DIR, `${pid}.json`);
    if (!FORCE && fs.existsSync(outPath)) {
      try {
        const existing = JSON.parse(fs.readFileSync(outPath, 'utf8'));
        if (existing.pages && existing.pages.length > 0) { skipped++; continue; }
      } catch {}
    }
    const website = c.website;
    if (!website) { failed++; continue; }
    const origin = originOf(website);
    if (!origin) {
      fs.writeFileSync(outPath, JSON.stringify({error: 'bad-url', website}));
      failed++;
      continue;
    }

    process.stderr.write(`[${i+1}/${todo.length}] ${c.name} (${c.stateSlug}/${c.citySlug}) - ${origin}\n`);

    const out = { origin, pages: [], placeId: pid, clinicName: c.name };
    const seen = new Set();
    const page = await browser.newPage();
    await page.setUserAgent('Mozilla/5.0 (compatible; TRTIndexBot/0.2; +https://trtindex.com)');
    page.setDefaultTimeout(NAV_TIMEOUT);

    let homepageOk = false;
    for (let pi = 0; pi < CANDIDATE_PATHS.length; pi++) {
      if (out.pages.length >= MAX_PAGES) break;
      // If the homepage itself is unreachable, don't waste time on subpaths.
      if (pi > 0 && !homepageOk) break;
      const url = origin + CANDIDATE_PATHS[pi];
      if (seen.has(url)) continue;
      seen.add(url);

      try {
        const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: NAV_TIMEOUT });
        if (pi === 0 && resp) homepageOk = true;
        if (!resp || !resp.ok()) continue;
        const ct = (resp.headers()['content-type'] || '').toLowerCase();
        if (!ct.includes('html')) continue;
        await new Promise(r => setTimeout(r, HYDRATE_WAIT));
        const text = await extractFromPage(page);
        if (!text || text.length < MIN_TEXT) continue;
        out.pages.push({ url, text: text.slice(0, MAX_PER_PAGE) });
      } catch (e) {
        // navigation timeout/network err — try next path (or break if homepage)
      }
    }

    await page.close();

    if (out.pages.length === 0) {
      fs.writeFileSync(outPath, JSON.stringify({error: 'fetch-failed', website}));
      failed++;
    } else {
      fs.writeFileSync(outPath, JSON.stringify(out));
      ok++;
    }

    if ((ok + failed) % 20 === 0 && ok + failed > 0) {
      process.stderr.write(`  ...checkpoint ok=${ok} failed=${failed} skipped=${skipped}\n`);
    }
    await new Promise(r => setTimeout(r, SLEEP_MS));
  }

  await browser.close();
  console.log(`done; ok=${ok} skipped=${skipped} failed=${failed}`);
})().catch(e => { console.error(e); process.exit(1); });
