# 2 way OCPP proxy (can also be used as a 1-way simple proxy)
from __future__ import annotations

import asyncio
import logging
import time
from typing import Tuple
import json
from pathlib import Path
from websockets.typing import Data, Subprotocol
from typing import Sequence, cast

import websockets
import websockets.asyncio
import websockets.asyncio.client
import websockets.asyncio.server

from enum import IntEnum
import ssl
import argparse
import configparser
from urllib.parse import urlparse

__version__ = "0.1.0"

config = configparser.ConfigParser()

logging.basicConfig(
format="%(asctime)s %(levelname)s %(name)s: %(message)s",
datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("proxy")

class OCPPMessageType(IntEnum):
Call = 2
CallResult = 3
CallError = 4

# main class
class OCPPDummyServer:

# Utility functions
@staticmethod
def split_url(request_target: str, host_header: str | None = None) -> Tuple[str | None, str | None, int | None, str, str | None]:
"""Split request target into protocol, host, port, context path, and optional charger_id."""
parsed = urlparse(request_target)
protocol = parsed.scheme or None

if parsed.scheme and parsed.netloc:
host = parsed.hostname
port = parsed.port
path = parsed.path or "/"
else:
host = None
port = None
path = request_target.split("?", 1)[0]

if host_header:
host_value = host_header.strip()
if host_value.startswith("[") and "]" in host_value:
bracket_end = host_value.find("]")
host = host_value[1:bracket_end]
if len(host_value) > bracket_end + 1 and host_value[bracket_end + 1] == ":":
port_str = host_value[bracket_end + 2:]
if port_str.isdigit():
port = int(port_str)
elif host_value.count(":") == 1:
host_part, port_str = host_value.split(":", 1)
host = host_part
if port_str.isdigit():
port = int(port_str)
else:
host = host_value

path_parts = [part for part in path.strip("/").split("/") if part]
if not path_parts:
return (protocol, host, port, "/", None)

charger_id = path_parts[-1]
context_parts = path_parts[:-1]
if context_parts:
context_path = "/" + "/".join(context_parts)
else:
context_path = "/"

return (protocol, host, port, context_path, charger_id)

@staticmethod
def decode_ocpp_message(message: Data) -> Tuple[OCPPMessageType, str, int | None]:
"""Decode an OCPP message from websocket data and extract optional connector id."""
if isinstance(message, bytes):
message = message.decode("utf-8")

j = json.loads(message)
connector_id = None

payload = None
if len(j) >= 4 and isinstance(j[3], dict):
payload = j[3]
elif len(j) >= 3 and isinstance(j[2], dict):
payload = j[2]

if payload and "connectorId" in payload:
connector_value = payload["connectorId"]
if isinstance(connector_value, int):
connector_id = connector_value
elif isinstance(connector_value, str) and connector_value.isdigit():
connector_id = int(connector_value)

return (j[0], j[1], connector_id)

def __init__(
self,
websocket: websockets.asyncio.server.ServerConnection,
charger_id: str,
):
# Store the websocket for later
logger.debug(websocket.request)
self.client_connection = websocket
self.charger_id = charger_id
self.user_agent_headers: str | None = None
self.additional_headers: dict[str, str] = {}
self.subprotocols: list[str] = ["ocpp1.6"]
self.tasks = []
self._last_update = time.time()
self.fixed_response_template = config.get(
"responses",
"fixed_response",
fallback='[3, "{message_id}", {"status": "Accepted", "currentTime": "2026-01-01T00:00:00Z", "interval": 300}]',
)

# Chech that charger id looks reasonable
if not charger_id.isalnum():
logger.error(f"Charger ID '{charger_id}' is not alphanumeric")
raise Exception("Charger ID is not alphanumeric")


async def run(self):
"""Main loop for this proxy. This is where all the magic happens."""

# Create connections to the two CSMSes.
# Forward any available Authorization and User-Agent headers
if self.client_connection.request is None:
raise ValueError("Missing request metadata on websocket connection")

request = self.client_connection.request
headers = {}
if "Authorization" in request.headers:
headers["Authorization"] = request.headers["Authorization"]
logger.debug(f'Authorization header set to {headers["Authorization"]}')
self.additional_headers = headers
self.user_agent_headers = request.headers.get("User-Agent", None)
raw_subprotocols = request.headers.get("Sec-WebSocket-Protocol", "")
if raw_subprotocols:
self.subprotocols = [protocol.strip() for protocol in raw_subprotocols.split(",") if protocol.strip()]
else:
self.subprotocols = ["ocpp1.6"]

try:

self._last_update = time.time()
self.tasks.append(asyncio.create_task(self.receive_messages()))
self.tasks.append(asyncio.create_task(self.watchdog()))

# Wait for tasks to complete
done, pending = await asyncio.wait(self.tasks, return_when=asyncio.FIRST_COMPLETED)
logger.debug(f"{self.charger_id} Task(s) completed: {done}, {pending}")

for task in done:
e = task.exception()
if e:
logger.warning(f"{self.charger_id} (Not serious - likely connection loss) Task {task} raised exception {e} related to charger ")

# Cancel any remaining tasks
for task in pending:
task.cancel()

except websockets.exceptions.InvalidURI:
logger.error(f"{self.charger_id} Invalid URI")
except websockets.exceptions.ConnectionClosedError as e:
logger.error(f"{self.charger_id} Connection closed unexpectedly: {e}")
except websockets.exceptions.InvalidHandshake:
logger.error(f"{self.charger_id} Handshake with the external server failed")
except Exception as e:
logger.error(f"{self.charger_id} Unexpected error: {e}")
finally:
for task in self.tasks:
if not task.done():
task.cancel()
if self.client_connection.state is not websockets.protocol.State.CLOSED:
await self.client_connection.close(code=1001, reason="Server shutting down")

async def receive_messages(self):
try:
while True:
# Wait for a message from the charger
message = await self.client_connection.recv()
self._last_update = time.time()
# Process the received message
logger.info(f"Received message {self.charger_id} ^ : {message}")

response = self._build_dummy_response(message)
await self.client_connection.send(response)
logger.info(f"Sent dummy response {self.charger_id} v : {response}")

except Exception as e:
logger.error(f"{self.charger_id} Error in receive_charger_messages: {e}")

def _build_dummy_response(self, message: Data) -> str:
message_id = "dummy-message-id"

if isinstance(message, bytes):
message = message.decode("utf-8")

try:
parsed_message = json.loads(message)
if isinstance(parsed_message, list) and len(parsed_message) >= 2 and isinstance(parsed_message[1], str):
message_id = parsed_message[1]
except Exception:
logger.warning(f"{self.charger_id} Could not parse incoming message id, using fallback id")

response_text = self.fixed_response_template.replace("{message_id}", message_id)

try:
json.loads(response_text)
return response_text
except json.JSONDecodeError:
logger.error(
f"{self.charger_id} Invalid responses.fixed_response in config, using built-in fallback response"
)
return json.dumps([3, message_id, {"status": "Accepted", "currentTime": "2026-01-01T00:00:00Z", "interval": 300}])

async def watchdog(self):
"""Watch time vs. timestamp updated by receiving messages from charger."""
while True:
# And ... sleep
await asyncio.sleep(config.getint("host", "watchdog_interval", fallback=30))

elapsed = time.time() - self._last_update
if elapsed > config.getint("host", "watchdog_stale", fallback=300):
logger.error(f"{self.charger_id} Watch dog no for {elapsed} seconds. Closing connections")
await self.client_connection.close(code=1001, reason="Watchdog timeout")
return

# Connection handler (charger connects)
async def on_connect(websocket: websockets.asyncio.server.ServerConnection):
logger.debug("Connection request %s", websocket.request)
# Determine charger_id (final part of path)

if websocket.request is None:
logger.warning("Connection rejected: missing request metadata")
await websocket.close(code=1008, reason="Missing request metadata")
return

path = websocket.request.path
host_header = websocket.request.headers.get("Host", None)
_, _, _, context_path, charger_id = OCPPDummyServer.split_url(path, host_header)

logger.info(
f"incomming connection context={context_path}, charger_id={charger_id}"
)

if charger_id is None:
logger.warning("Connection rejected: missing charger_id in path")
await websocket.close(code=1008, reason="Missing charger_id in path")
return

try:
# Setup
proxy = OCPPDummyServer(
websocket=websocket,
charger_id=charger_id
)

# Connect and run proxy operations
await proxy.run()

except Exception as e:
logger.error(f'{charger_id} Error creating OCPPDummyServer: {e}')
finally:
logger.info(f"{charger_id} closed/done")


# Main. Decode arguments, setup handler
async def main():
default_config_path = Path(__file__).with_name("ocpp-dummy-server.ini")
parser = argparse.ArgumentParser(
description='ocpp-dummy-server: A dummy OCPP server')
parser.add_argument('--version', action='version',
version=f'%(prog)s {__version__}')
parser.add_argument(
"--config",
type=str,
default=str(default_config_path),
help=f"Configuration file (INI format). Default {default_config_path}",
)
args = parser.parse_args()

# Read config. config object is then available (via config import) to all.
logger.warning(f"Reading config from {args.config}")
read_files = config.read(args.config)
if not read_files:
raise FileNotFoundError(f"Could not read config file: {args.config}")

required_sections = ["logging", "host"]
missing_sections = [section for section in required_sections if not config.has_section(section)]
if missing_sections:
raise KeyError(f"Missing required config section(s): {', '.join(missing_sections)}")

# Adjust log levels
for logger_name in config["logging"]:
logger.warning(f'Setting log level for {logger_name} to {config.get("logging", logger_name)}')
logging.getLogger(logger_name).setLevel(level=config.get("logging", logger_name))

# Get host config
host = config.get("host", "addr")
port = config.getint("host", "port")
cert_chain = config.get("host", "cert_chain", fallback=None)
cert_key = config.get("host", "cert_key", fallback=None)
cert_key_password = config.get("host", "cert_key_password", fallback=None)
if cert_key_password == "":
cert_key_password = None
logger.debug(
f"host: {host}, port: {port}, cert_chain: {cert_chain}, cert_key: {cert_key}, cert_key_password_set: {cert_key_password is not None}"
)

subprotocols: Sequence[Subprotocol] = cast(
Sequence[Subprotocol],
["ocpp1.6", "ocpp2.0.1"],
)

# Start server, either ws:// or wss://
if cert_chain and cert_key:
ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ssl_context.load_cert_chain(
certfile=cert_chain,
keyfile=cert_key,
password=cert_key_password,
)
server = await websockets.serve(
on_connect,
host,
port,
subprotocols=subprotocols,
ssl=ssl_context,
ping_timeout=config.getint("host", "ping_timeout"),
)
else:
server = await websockets.serve(
on_connect,
host,
port,
subprotocols=subprotocols,
ping_timeout=config.getint("host", "ping_timeout"),
)

logger.info("OCPP Dummy Server ready. Waiting for new connections...")
await server.wait_closed()


if __name__ == "__main__":
try:
asyncio.run(main())
except KeyboardInterrupt:
exit(0)