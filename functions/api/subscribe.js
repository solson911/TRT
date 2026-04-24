// POST /api/subscribe
// Registers an email with Loops (https://loops.so) via their public contacts API.
// Secrets are set in the Cloudflare Pages project as environment variables:
//   LOOPS_API_KEY       - Loops API key (required)
//   LOOPS_MAILING_LIST  - optional mailing list ID to opt the contact into
//
// Kept intentionally small. Real list management (suppression, double opt-in,
// welcome sends) lives in Loops. This endpoint's only job is to hand Loops an
// email, a source tag, and a mailing-list flag.

const RATE_LIMIT = { maxBodyBytes: 8 * 1024 };

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
    },
  });
}

function isValidEmail(str) {
  if (typeof str !== 'string') return false;
  if (str.length < 5 || str.length > 254) return false;
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(str);
}

export async function onRequestOptions() {
  return new Response(null, {
    status: 204,
    headers: {
      'Allow': 'POST, OPTIONS',
    },
  });
}

export async function onRequestPost({ request, env }) {
  const apiKey = env.LOOPS_API_KEY;
  if (!apiKey) {
    return jsonResponse({ error: 'Subscribe endpoint is not configured yet.' }, 503);
  }

  const contentLength = parseInt(request.headers.get('content-length') || '0', 10);
  if (contentLength > RATE_LIMIT.maxBodyBytes) {
    return jsonResponse({ error: 'Request too large.' }, 413);
  }

  let payload;
  try {
    payload = await request.json();
  } catch {
    return jsonResponse({ error: 'Invalid JSON.' }, 400);
  }

  const email = (payload?.email || '').toString().trim().toLowerCase();
  const source = (payload?.source || 'unknown').toString().slice(0, 64);

  if (!isValidEmail(email)) {
    return jsonResponse({ error: 'Please enter a valid email.' }, 400);
  }

  const country = request.headers.get('cf-ipcountry') || null;

  const contactBody = {
    email,
    source,
    subscribed: true,
    userGroup: 'trt-index',
    country,
  };
  if (env.LOOPS_MAILING_LIST) {
    contactBody.mailingLists = { [env.LOOPS_MAILING_LIST]: true };
  }

  let res;
  try {
    res = await fetch('https://app.loops.so/api/v1/contacts/create', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(contactBody),
    });
  } catch {
    return jsonResponse({ error: 'Could not reach subscription service.' }, 502);
  }

  if (res.ok) {
    return jsonResponse({ ok: true });
  }

  // Loops returns 409 on duplicate contact - treat that as success so users
  // who resubscribe from a different form see the normal confirmation.
  if (res.status === 409) {
    return jsonResponse({ ok: true, alreadySubscribed: true });
  }

  let detail = null;
  try { detail = await res.json(); } catch { /* ignore */ }
  return jsonResponse({ error: detail?.message || 'Could not subscribe.' }, 502);
}
