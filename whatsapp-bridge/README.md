# Praxis WhatsApp Bridge

A local-only HTTP/SSE bridge that connects the Praxis agentic OS kernel to
WhatsApp, using the [Baileys](https://github.com/WhiskeySockets/Baileys) library.

## Requirements

- Node.js 20 or newer (`node --version` to check)
- A dedicated WhatsApp account / phone number to link

## Setup

1. Install dependencies (you must run this yourself — not automated):

   ```bash
   cd whatsapp-bridge
   npm install
   ```

2. Set the required environment variable:

   ```bash
   export PRAXIS_WHATSAPP_ALLOWED_NUMBERS="+12025551234,+441234567890"
   ```

   Only messages from these numbers will be forwarded to Praxis.
   Use E.164 format (country code prefix, no spaces).

3. Optional: change the HTTP port (default `3001`):

   ```bash
   export PRAXIS_WHATSAPP_BRIDGE_PORT=3001
   ```

4. Start the bridge:

   ```bash
   node bridge.js
   ```

5. On first run, a QR code is printed to the terminal. Open WhatsApp on your
   dedicated phone, go to **Settings > Linked Devices > Link a Device**, and
   scan the code. The session is saved in `session/` so subsequent runs will
   reconnect automatically without re-scanning.

## HTTP API

All routes bind to `127.0.0.1` only — never exposed externally.

| Method | Path      | Description                                    |
|--------|-----------|------------------------------------------------|
| POST   | `/send`   | Send a message: `{"to":"+1...","message":"..."}` |
| GET    | `/events` | SSE stream of inbound messages                 |
| GET    | `/ping`   | Health check: `{"ok":true,"connected":bool}`   |

### SSE event format

```json
{"from": "+12025551234", "message": "Hello", "timestamp": 1717200000000}
```

A `{"type":"connected"}` event is sent immediately on stream connect.

## Security notes

- The bridge only ever listens on `127.0.0.1` — it is not reachable from the
  network.
- Message content is never written to logs; only the sender's number is.
- Inbound messages from numbers not in `PRAXIS_WHATSAPP_ALLOWED_NUMBERS` are
  silently dropped (a note is written to stderr).

## Session files

The `session/` directory stores Baileys authentication credentials. It is
gitignored to prevent committing personal account data. Back it up if you want
to avoid re-scanning the QR code after a fresh clone.
