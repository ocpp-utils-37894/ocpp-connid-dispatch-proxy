# OCPP Connector-ID Dispatch Proxy

An asynchronous WebSocket proxy for OCPP traffic.

The proxy accepts incoming charger connections and forwards messages to an upstream OCPP server. It can dispatch requests to dedicated backend connections based on `connectorId`, while still maintaining a primary backend connection for the original charger ID.

## Features

- WebSocket proxy for OCPP 1.6 / 2.0.1 subprotocol negotiation.
- Connector-aware routing via `[charge-point-mapping]`.
- Optional TLS for incoming charger connections (`wss`) using server certificate + key.
- Support for encrypted private keys (`cert_key_password`).
- Optional custom trust certificate for outgoing backend TLS validation (`trusted_cert`).
- Idle cleanup for backend WebSocket connections (`server_idle_timeout`).
- Self signed "localhost" certificates intended for testing only

## Project Structure

- `ocpp-proxy.py`: Main proxy implementation.
- `ocpp-connid-dispatch-proxy.ini`: Runtime configuration.
- `tests/`: Pytest suite for core logic.

## Requirements

- Python 3.12+ (recommended)
- Dependencies from `requirements.txt`

Install runtime dependencies:

```bash
pip install -r requirements.txt
```

Install development/test dependencies:

```bash
pip install -r requirements-dev.txt
```

## Run

```bash
python ocpp-proxy.py --config ocpp-connid-dispatch-proxy.ini
```

## Configuration (INI)

The proxy is configured via `ocpp-connid-dispatch-proxy.ini`.

### `[logging]`

Sets log levels per logger name.

Example:

```ini
[logging]
proxy = DEBUG
websockets.client = INFO
websockets.server = INFO
```

### `[host]`

Settings for the local proxy listener (chargers connect here).

- `addr`: Bind address.
- `port`: Bind port.
- `watchdog_stale`: Session stale threshold in seconds. If exceeded, proxy closes connections.
- `watchdog_interval`: Watchdog check interval in seconds.
- `ping_timeout`: WebSocket ping timeout passed to `websockets.serve`.
- `cert_chain`: TLS certificate chain file for incoming `wss` listener.
- `cert_key`: TLS private key file for incoming `wss` listener.
- `cert_key_password`: Optional password for encrypted `cert_key`.

Notes:

- If `cert_chain` and `cert_key` are set, the proxy starts in secure mode (`wss`).
- If they are not set, the proxy starts as plain `ws`.

### `[charge-point-mapping]`

Maps numeric `connectorId` values from OCPP payloads to backend charge point names.

Format:

```ini
[charge-point-mapping]
0 = CHARGEPOINT0
1 = CHARGEPOINT1
```

Behavior:

- If `connectorId` is mapped, the message is routed to a dedicated backend connection for that mapped charge point name.
- If not mapped, the message falls back to the primary backend connection.
- If `connectorId` is present but has no mapping entry, no dedicated connector backend is created.

### `[ocpp-server]`

Settings for outbound connection(s) from proxy to upstream OCPP backend.

- `url`: Upstream backend URL, for example `ws://host:port` or `wss://host:port`.
- `server_idle_timeout`: Close idle backend connections after *N* seconds (`0` disables idle cleanup).
- `trusted_cert`: Optional CA/certificate PEM file used to validate backend TLS certificate(s) for `wss`.

Notes:

- `trusted_cert` is only relevant when `url` uses `wss://`.
- If `trusted_cert` is omitted, system trust store is used.

## URL and Charger ID Behavior

For incoming paths:

- `wss://host:port/id` → `charger_id = id`, `context = ""` (empty)
- `wss://host:port/context/id` → `charger_id = id`, `context = /context`

Non-alphanumeric charger IDs are allowed (empty IDs are rejected).

## Testing

Run all tests:

```bash
python -m pytest -q
```

Current suite covers:

- URL splitting and context extraction
- OCPP payload decoding (`connectorId` handling)
- Charger ID acceptance rules
- WebSocket liveness helper behavior
- Routing fallback: unmapped `connectorId` uses the primary backend connection

## Example INI

```ini
[logging]
proxy = DEBUG
websockets.client = INFO
websockets.server = INFO

[host]
addr = 0.0.0.0
port = 8322
watchdog_stale = 300
watchdog_interval = 30
ping_timeout = 60
cert_chain = server-cert.pem
cert_key = server-key.pem
cert_key_password = secret

[charge-point-mapping]
0 = CHARGEPOINT0
1 = CHARGEPOINT1

[ocpp-server]
url = wss://localhost:8321
server_idle_timeout = 300
trusted_cert = ca-cert.pem
```

## Third-Party Notices

This project contains code derived from `ocpp-balanz/ocpp-2w-proxy` (MIT).
See `THIRD_PARTY_NOTICES.md` for details and full license text.

## License

This project is licensed under the Apache License 2.0.
See `LICENSE` for details.
