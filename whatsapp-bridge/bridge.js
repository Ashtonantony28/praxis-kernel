/**
 * Praxis WhatsApp Bridge
 * ----------------------
 * A local-only HTTP/SSE bridge between Praxis and WhatsApp, powered by
 * @whiskeysockets/baileys.
 *
 * HOW TO RUN:
 *   1. cd whatsapp-bridge && npm install
 *   2. Set env vars (see below) — at minimum PRAXIS_WHATSAPP_ALLOWED_NUMBERS
 *   3. node bridge.js
 *   4. Scan the QR code printed to the terminal with your WhatsApp account
 *
 * ENV VARS:
 *   PRAXIS_WHATSAPP_BRIDGE_PORT      HTTP port (default: 3001)
 *   PRAXIS_WHATSAPP_ALLOWED_NUMBERS  Required. Comma-separated E.164 numbers,
 *                                    e.g. "+12025551234,+441234567890"
 *
 * HTTP ROUTES:
 *   POST /send        { to: "+1...", message: "..." }  -> { ok: true }
 *   GET  /events      SSE stream of inbound messages
 *   GET  /ping        Health check -> { ok: true, connected: bool }
 *
 * SAFETY:
 *   - Binds exclusively to 127.0.0.1 — never exposed externally.
 *   - Inbound message content is never logged; only the sender number is.
 *   - Messages from non-allowed numbers are silently dropped (stderr note only).
 */

import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
} from '@whiskeysockets/baileys';
import { Boom } from '@hapi/boom';
import express from 'express';
import qrcode from 'qrcode-terminal';
import { fileURLToPath } from 'url';
import path from 'path';

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

const PORT = parseInt(process.env.PRAXIS_WHATSAPP_BRIDGE_PORT ?? '3001', 10);

const RAW_ALLOWED = process.env.PRAXIS_WHATSAPP_ALLOWED_NUMBERS ?? '';
if (!RAW_ALLOWED.trim()) {
  process.stderr.write(
    '[bridge] FATAL: PRAXIS_WHATSAPP_ALLOWED_NUMBERS is not set. ' +
      'Provide a comma-separated list of E.164 numbers.\n'
  );
  process.exit(1);
}

/**
 * Normalise a phone string to a bare digit sequence (no '+', spaces, dashes)
 * for comparison against Baileys JIDs which look like "12025551234@s.whatsapp.net".
 */
const normalise = (num) => num.replace(/[^\d]/g, '');

const ALLOWED_NUMBERS = new Set(
  RAW_ALLOWED.split(',')
    .map((n) => n.trim())
    .filter(Boolean)
    .map(normalise)
);

process.stderr.write(
  `[bridge] Allowed inbound numbers: ${[...ALLOWED_NUMBERS].join(', ')}\n`
);

// ---------------------------------------------------------------------------
// Session directory (relative to this file)
// ---------------------------------------------------------------------------

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SESSION_DIR = path.join(__dirname, 'session');

// ---------------------------------------------------------------------------
// SSE client registry
// ---------------------------------------------------------------------------

/** @type {Set<import('express').Response>} */
const sseClients = new Set();

/**
 * Broadcast a JSON-serialisable object to all active SSE clients.
 * @param {object} data
 */
function broadcastEvent(data) {
  const payload = `data: ${JSON.stringify(data)}\n\n`;
  for (const res of sseClients) {
    try {
      res.write(payload);
    } catch {
      sseClients.delete(res);
    }
  }
}

// ---------------------------------------------------------------------------
// Baileys state
// ---------------------------------------------------------------------------

let connected = false;
/** @type {ReturnType<typeof makeWASocket> | null} */
let sock = null;

/**
 * Convert a Baileys JID ("12025551234@s.whatsapp.net" or "12025551234@c.us")
 * to an E.164-style string like "+12025551234".
 * @param {string} jid
 * @returns {string}
 */
function jidToE164(jid) {
  const digits = jid.split('@')[0];
  return `+${digits}`;
}

/**
 * Start (or restart) the Baileys WebSocket session.
 */
async function startBaileys() {
  const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
  const { version } = await fetchLatestBaileysVersion();

  process.stderr.write(`[bridge] Starting Baileys (WA version ${version.join('.')})\n`);

  sock = makeWASocket({
    version,
    auth: state,
    // Suppress Baileys' own logger output to keep stdout clean.
    logger: {
      level: 'silent',
      trace: () => {},
      debug: () => {},
      info: () => {},
      warn: (msg) => process.stderr.write(`[baileys:warn] ${JSON.stringify(msg)}\n`),
      error: (msg) => process.stderr.write(`[baileys:error] ${JSON.stringify(msg)}\n`),
      fatal: (msg) => process.stderr.write(`[baileys:fatal] ${JSON.stringify(msg)}\n`),
      child: () => ({
        level: 'silent',
        trace: () => {},
        debug: () => {},
        info: () => {},
        warn: () => {},
        error: () => {},
        fatal: () => {},
        child: () => ({}),
      }),
    },
    printQRInTerminal: false, // We handle QR ourselves via qrcode-terminal
    browser: ['Praxis Bridge', 'Chrome', '1.0.0'],
  });

  // Save credentials whenever they update
  sock.ev.on('creds.update', saveCreds);

  // Handle QR code display
  sock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      process.stderr.write('[bridge] Scan the QR code below with WhatsApp:\n');
      qrcode.generate(qr, { small: true });
    }

    if (connection === 'open') {
      connected = true;
      process.stderr.write('[bridge] WhatsApp connection established.\n');
    }

    if (connection === 'close') {
      connected = false;
      const reason =
        lastDisconnect?.error instanceof Boom
          ? lastDisconnect.error.output.statusCode
          : undefined;

      process.stderr.write(`[bridge] Connection closed. Reason code: ${reason ?? 'unknown'}\n`);

      // Reconnect unless we were explicitly logged out
      if (reason !== DisconnectReason.loggedOut) {
        process.stderr.write('[bridge] Reconnecting in 3 s…\n');
        setTimeout(startBaileys, 3000);
      } else {
        process.stderr.write('[bridge] Logged out from WhatsApp. Restart to re-scan QR.\n');
      }
    }
  });

  // Handle inbound messages
  sock.ev.on('messages.upsert', ({ messages, type }) => {
    if (type !== 'notify') return;

    for (const msg of messages) {
      // Ignore outbound or status messages
      if (msg.key.fromMe) continue;
      if (!msg.message) continue;

      const jid = msg.key.remoteJid ?? '';
      const digits = jid.split('@')[0];
      const e164 = `+${digits}`;

      // Governance check: only accept messages from allowed numbers
      if (!ALLOWED_NUMBERS.has(digits)) {
        process.stderr.write(
          `[bridge] Inbound from non-allowed number (dropped): +${digits}\n`
        );
        continue;
      }

      // Log sender only — never log message content
      process.stderr.write(`[bridge] Inbound from ${e164}\n`);

      // Extract text from common message types
      const text =
        msg.message.conversation ??
        msg.message.extendedTextMessage?.text ??
        msg.message.imageMessage?.caption ??
        msg.message.videoMessage?.caption ??
        '[non-text message]';

      const event = {
        from: e164,
        message: text,
        timestamp: msg.messageTimestamp
          ? Number(msg.messageTimestamp) * 1000
          : Date.now(),
      };

      broadcastEvent(event);
    }
  });
}

// ---------------------------------------------------------------------------
// Express HTTP server
// ---------------------------------------------------------------------------

const app = express();
app.use(express.json());

/**
 * POST /send
 * Body: { to: string, message: string }
 * Returns: { ok: true } | { ok: false, error: string }
 */
app.post('/send', async (req, res) => {
  const { to, message } = req.body ?? {};

  if (!to || typeof to !== 'string') {
    return res.status(400).json({ ok: false, error: '`to` is required and must be a string' });
  }
  if (!message || typeof message !== 'string') {
    return res.status(400).json({ ok: false, error: '`message` is required and must be a string' });
  }
  if (!sock || !connected) {
    return res.status(503).json({ ok: false, error: 'WhatsApp not connected' });
  }

  try {
    // Convert E.164 -> Baileys JID
    const digits = normalise(to);
    const jid = `${digits}@s.whatsapp.net`;

    await sock.sendMessage(jid, { text: message });
    process.stderr.write(`[bridge] Outbound sent to +${digits}\n`);

    return res.json({ ok: true });
  } catch (err) {
    process.stderr.write(`[bridge] Send error: ${err.message}\n`);
    return res.status(500).json({ ok: false, error: err.message });
  }
});

/**
 * GET /events
 * SSE stream. Each inbound WhatsApp message from an allowed number is pushed
 * as a JSON event: { from, message, timestamp }
 */
app.get('/events', (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.flushHeaders();

  // Send immediate connected confirmation
  res.write('data: {"type":"connected"}\n\n');

  sseClients.add(res);
  process.stderr.write(`[bridge] SSE client connected (total: ${sseClients.size})\n`);

  req.on('close', () => {
    sseClients.delete(res);
    process.stderr.write(`[bridge] SSE client disconnected (total: ${sseClients.size})\n`);
  });
});

/**
 * GET /ping
 * Health check.
 */
app.get('/ping', (_req, res) => {
  res.json({ ok: true, connected });
});

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

const server = app.listen(PORT, '127.0.0.1', () => {
  process.stderr.write(`[bridge] HTTP server listening on 127.0.0.1:${PORT}\n`);
});

// Start Baileys (async — does not block the HTTP server)
startBaileys().catch((err) => {
  process.stderr.write(`[bridge] Failed to start Baileys: ${err.message}\n`);
  process.exit(1);
});

// ---------------------------------------------------------------------------
// Graceful shutdown
// ---------------------------------------------------------------------------

async function shutdown(signal) {
  process.stderr.write(`\n[bridge] Received ${signal}. Shutting down…\n`);

  try {
    if (sock) {
      await sock.logout();
    }
  } catch {
    // Best-effort logout
  }

  server.close(() => {
    process.stderr.write('[bridge] HTTP server closed. Bye.\n');
    process.exit(0);
  });

  // Force-exit if graceful close stalls
  setTimeout(() => process.exit(0), 5000).unref();
}

process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));
