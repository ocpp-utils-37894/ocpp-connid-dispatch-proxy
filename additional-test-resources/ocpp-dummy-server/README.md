# OCPP Dummy Server

Lightweight WebSocket-based dummy OCPP server for local testing.

It accepts incoming charger connections and sends a fixed OCPP `CallResult` response for each received message.

## Files

- `ocpp-dummy-server.py`: Dummy server implementation.
- `ocpp-dummy-server.ini`: Runtime configuration.
- `requirements.txt`: Python dependencies.
- `server-cert.pem` / `server-key.pem`: Example TLS files for `wss`.

## Requirements

Install dependencies:

```bash
pip install -r requirements.txt
```

## Run

From this directory:

```bash
python ocpp-dummy-server.py
```

Or with explicit config path:

```bash
python ocpp-dummy-server.py --config ocpp-dummy-server.ini
```

## Configuration (`ocpp-dummy-server.ini`)

### `[logging]`

Per-logger log levels.

Example:

```ini
[logging]
proxy = DEBUG
websockets.client = INFO
websockets.server = INFO
```

### `[host]`

Local listener settings.

- `addr`: Bind address.
- `port`: Bind port.
- `watchdog_stale`: Maximum inactivity in seconds before the server closes the connection.
- `watchdog_interval`: Watchdog check interval in seconds.
- `ping_timeout`: WebSocket ping timeout for `websockets.serve`.
- `cert_chain`: PEM certificate chain file.
- `cert_key`: PEM private key file matching `cert_chain`.
- `cert_key_password`: Optional password for encrypted private key.

TLS behavior:

- If both `cert_chain` and `cert_key` are set, the server starts as `wss`.
- Otherwise, it starts as plain `ws`.

### `[responses]`

Defines the fixed OCPP response payload sent for every incoming message.

- `fixed_response`: JSON array string used as outgoing response.
- The token `{message_id}` is replaced with the incoming OCPP message ID when available.

Default example:

```ini
[responses]
fixed_response = [3, "{message_id}", {"status": "Accepted", "currentTime": "2026-01-01T00:00:00Z", "interval": 300}]
```

## Behavior Notes

- Charger ID is extracted from the last path segment of the incoming URL.
- For each incoming message, the server attempts to parse the message ID and echoes it in the fixed response.
- If `fixed_response` is invalid JSON, a built-in fallback response is used.