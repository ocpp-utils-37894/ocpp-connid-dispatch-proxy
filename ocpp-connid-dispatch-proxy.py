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
class OCPPConnectorIdDispatchingProxy:
    # Static dict of OCPPConnectorIdDispatchingProxy instances. key is charger_id
    proxy_list: dict[str, "OCPPConnectorIdDispatchingProxy"] = {}

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
            return (protocol, host, port, "", None)

        charger_id = path_parts[-1]
        context_parts = path_parts[:-1]
        if context_parts:
            context_path = "/" + "/".join(context_parts)
        else:
            context_path = ""

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
        request_path: str,
        protocol: str |None,
        host: str,
        port: int | None,
        context_path: str,
    ):
        # Store the websocket for later     
        logger.debug(websocket.request)
        self.client_connection = websocket
        self.request_path = request_path
        self.protocol = protocol
        self.host = host
        self.port = port
        self.request_context = context_path
        self.charger_id = charger_id
        self.primary_connection: websockets.asyncio.client.ClientConnection | None = None
        self.connector_connections: dict[int, websockets.asyncio.client.ClientConnection] = {}
        self.server_ws_last_activity: dict[websockets.asyncio.client.ClientConnection, float] = {}
        self.user_agent_headers: str | None = None
        self.additional_headers: dict[str, str] = {}
        self.subprotocols: list[str] = ["ocpp1.6"]
        self.tasks = []

        if not charger_id:
            logger.error("Charger ID is empty")
            raise Exception("Charger ID is empty")

        # Initialize table of CSMS call ids sent to the charger in order to respond back 
        self.primary_call_ids = set()
        self.secondary_call_ids = set()

        # Insert new OCPPConnectorIdDispatchingProxy instance in the (static) dict of instances.
        self.proxy_list[charger_id] = self

    async def close(self):
        """Close all connections to the charger and primary, secondary server"""
        for connection in [self.client_connection, self.primary_connection, *self.connector_connections.values()]:
            if connection is None:
                continue
            try:
                await connection.close()
            except Exception:
                logger.debug(f"{self.charger_id} Ignoring close failure for {type(connection).__name__}")
        self.server_ws_last_activity.clear()

    def mark_server_ws_activity(self, connection: websockets.asyncio.client.ClientConnection):
        self.server_ws_last_activity[connection] = time.time()

    async def close_idle_server_connections(self, idle_timeout: int):
        if idle_timeout <= 0:
            return

        now = time.time()

        if self.primary_connection is not None:
            last_seen = self.server_ws_last_activity.get(self.primary_connection)
            if last_seen is not None and now - last_seen > idle_timeout:
                logger.info(f"{self.charger_id} Closing idle primary backend connection after {int(now - last_seen)}s")
                try:
                    await self.primary_connection.close()
                except Exception:
                    logger.debug(f"{self.charger_id} Ignoring close failure for idle primary backend connection")
                self.server_ws_last_activity.pop(self.primary_connection, None)
                self.primary_connection = None

        for connector_id, connection in list(self.connector_connections.items()):
            last_seen = self.server_ws_last_activity.get(connection)
            if last_seen is None or now - last_seen <= idle_timeout:
                continue
            logger.info(
                f"{self.charger_id} Closing idle backend connection for connectorId={connector_id} "
                f"after {int(now - last_seen)}s"
            )
            try:
                await connection.close()
            except Exception:
                logger.debug(f"{self.charger_id} Ignoring close failure for idle connector backend connection")
            self.server_ws_last_activity.pop(connection, None)
            del self.connector_connections[connector_id]

    @staticmethod
    async def check_delete_old(charger_id: str):
        """Check if there are any old instances of this charger in the proxy list"""
        if charger_id in OCPPConnectorIdDispatchingProxy.proxy_list:
            logger.info(f"Charger ID {charger_id} already exists. Closing and deleting")
            proxy: OCPPConnectorIdDispatchingProxy = OCPPConnectorIdDispatchingProxy.proxy_list[charger_id]
            await proxy.close()
            del OCPPConnectorIdDispatchingProxy.proxy_list[charger_id]

    async def setup_connection(
        self,
        protocol: str | None,
        host: str,
        port: int | None,
        context: str,
        charger_id: str,
    ) -> websockets.asyncio.client.ClientConnection:
        """Create the primary backend websocket connection."""
        context_path = context or ""
        if context_path and not context_path.startswith("/"):
            context_path = "/" + context_path

        host_with_port = host
        if port is not None:
            host_with_port = f"{host}:{port}"

        url = f"{protocol}://{host_with_port}{context_path}/{charger_id}"

        typed_subprotocols: list[Subprotocol] | None = (
            [Subprotocol(p) for p in self.subprotocols]
            if self.subprotocols
            else None
        )

        ssl_context: ssl.SSLContext | None = None
        if protocol and protocol.lower() == "wss":
            trusted_cert = config.get("ocpp-server", "trusted_cert", fallback=None)
            if trusted_cert:
                trusted_cert = trusted_cert.strip()
            if trusted_cert:
                ssl_context = ssl.create_default_context(
                    purpose=ssl.Purpose.SERVER_AUTH,
                    cafile=trusted_cert,
                )
                logger.debug(f"Using custom trusted certificate for OCPP server: {trusted_cert}")
            else:
                ssl_context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)

        connection = await websockets.connect(
            uri=url,
            user_agent_header=self.user_agent_headers,
            additional_headers=self.additional_headers,
            subprotocols=typed_subprotocols,
            ssl=ssl_context,
        )
        self.mark_server_ws_activity(connection)

        self.tasks.append(asyncio.create_task(self.receive_server_messages(charger_id=charger_id, websocket=connection)))

        logger.info(f"Connected to server @ {url}")
        return connection

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
            self.primary_connection = await self.setup_connection(
                protocol=self.protocol,
                host=self.host,
                port=self.port,
                context=self.request_context,
                charger_id=self.charger_id,
            )
            
            self._last_charger_update = time.time()
            self.tasks.append(asyncio.create_task(self.receive_charger_messages()))
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
            # Always close stuff. close is well tempered, so can close even if not stablished
            await self.close()

    async def is_ws_alive(self, ws, timeout: float = 5.0) -> bool:
        if ws is None:
            return False
        closed = getattr(ws, "closed", None)
        if isinstance(closed, bool) and closed:
            return False
        try:
            pong_waiter = await ws.ping()
            await asyncio.wait_for(pong_waiter, timeout=timeout)
            return True
        except Exception:
            return False

    async def receive_charger_messages(self):
        try:
            while True:
                # Wait for a message from the charger
                message = await self.client_connection.recv()
                self._last_charger_update = time.time()
                # Process the received message
                logger.info(f"{self.charger_id} ^ : {message}")
            
                [message_type, message_id, connector_id] = OCPPConnectorIdDispatchingProxy.decode_ocpp_message(message)
                if connector_id is not None:
                    connector_connection = self.connector_connections.get(connector_id)
                    if connector_connection is not None:
                        logger.debug(f"Found existing connection for connectorId={connector_id}. Trying to relay message to dedicated OCPP Server: {message}")
                        if await self.is_ws_alive(connector_connection):
                            await connector_connection.send(message)
                            self.mark_server_ws_activity(connector_connection)
                            logger.debug(f"Send successfull")
                            continue

                    charge_point_name = config.get(
                        "charge-point-mapping",
                        str(connector_id),
                        fallback=None,
                    )

                    if charge_point_name is not None:
                        logger.debug(
                        f"{self.charger_id} mapped charge point for connectorId={connector_id}: {charge_point_name}"
                        )   

                        self.connector_connections[connector_id] = await self.setup_connection(
                            protocol=self.protocol, 
                            host=self.host,
                            port=self.port,
                            context=self.request_context,
                            charger_id=charge_point_name,
                        )
                        if await self.is_ws_alive(self.connector_connections[connector_id]):
                            await self.connector_connections[connector_id].send(message)
                            self.mark_server_ws_activity(self.connector_connections[connector_id])
                            logger.debug(f"Send successfull")
                            continue
                    else:
                        logger.warning(f"{self.charger_id} No mapping found for connectorId={connector_id}. Please add a mapping in the config file. Relaying message to primary connection.")
                       
                #no connectorId present --> relay original message with original charger_id
                charge_point_name = self.charger_id
                logger.debug(f"{self.charger_id} Trying to relay message to primary connection OCPP Server: {message}")
                if self.primary_connection is not None and await self.is_ws_alive(self.primary_connection):
                    await self.primary_connection.send(message)
                    self.mark_server_ws_activity(self.primary_connection)
                else:
                    self.primary_connection = await self.setup_connection(
                        protocol=self.protocol,
                        host=self.host,
                        port=self.port,
                        context=self.request_context,
                        charger_id=self.charger_id,
                    )
                    await self.primary_connection.send(message)
                    self.mark_server_ws_activity(self.primary_connection)
                logger.debug(f"Send successfull")

        except websockets.exceptions.ConnectionClosedOK:
            logger.info(f"{self.charger_id} Charger connection closed normally")
        except Exception as e:
            logger.error(f"{self.charger_id} Error in receive_charger_messages: {e}")

    async def receive_server_messages(self, charger_id: str, websocket: websockets.asyncio.client.ClientConnection):
        try:
            while True:
                # Wait for a message from the OCPP server
                message = await websocket.recv()
                self.mark_server_ws_activity(websocket)
                self._last_charger_update = time.time()
                logger.info(f"{charger_id} Received message from OCPP server for {charger_id}: {message}")

                if await self.is_ws_alive(self.client_connection):
                    await self.client_connection.send(message)
                else:
                    logger.error(f"Client connection to charger is closed. Cannot relay message from server.")
                    raise Exception("Client connection to charger is closed")
        except websockets.exceptions.ConnectionClosedOK:
            logger.info(f"{charger_id} Backend connection closed normally")
            return
        except Exception as e:
            logger.error(f"Error {e}")
            raise Exception("Cannot relay message from OCPP server to charger.")

    async def watchdog(self):
        """Watch time vs. timestamp updated by receiving messages from charger."""
        while True:
            # And ... sleep
            await asyncio.sleep(config.getint("host", "watchdog_interval", fallback=30))

            await self.close_idle_server_connections(
                idle_timeout=config.getint("ocpp-server", "server_idle_timeout", fallback=0)
            )

            elapsed = time.time() - self._last_charger_update
            if elapsed > config.getint("host", "watchdog_stale", fallback=300):
                logger.error(f"{self.charger_id} Watch dog no for {elapsed} seconds. Closing connections")
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
    _, _, _, context_path, charger_id = OCPPConnectorIdDispatchingProxy.split_url(path, host_header)
    protocol, server_host, server_port, _, _ = OCPPConnectorIdDispatchingProxy.split_url(
        config.get("ocpp-server", "url"),
        None,
    )

    if server_host is None:
        logger.error("Invalid server URL in config: missing host")
        await websocket.close(code=1011, reason="Invalid server configuration")
        return
    
    logger.info(
        f"request_protocol={protocol}, server_host={server_host}, server_port={server_port}, "
        f"context={context_path}, charger_id={charger_id}"
    )

    if not charger_id:
        logger.warning("Connection rejected: missing charger_id in request path")
        await websocket.close(code=1008, reason="Missing charger_id")
        return

    logger.info(f'{charger_id} connection request')

    try:
        # Setup
        proxy = OCPPConnectorIdDispatchingProxy(
            websocket=websocket,
            charger_id=charger_id,
            request_path=path,
            protocol=protocol,
            host=server_host,
            port=server_port,
            context_path=context_path,
        )

        # Connect and run proxy operations
        await proxy.run()

    except Exception as e:
        logger.error(f'{charger_id} Error creating OCPPConnectorIdDispatchingProxy: {e}')
    finally:
        logger.info(f"{charger_id} closed/done")


# Main. Decode arguments, setup handler
async def main():
    default_config_path = Path(__file__).with_name("ocpp-connid-dispatch-proxy.ini")
    parser = argparse.ArgumentParser(
        description='ocpp-2w-proxy: A two way OCPP proxy')
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

    required_sections = ["logging", "host", "ocpp-server", "charge-point-mapping"]
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
    logger.debug(
        f"host: {host}, port: {port}, cert_chain: {cert_chain}, cert_key: {cert_key}, "
        f"cert_key_password set: {bool(cert_key_password)}"
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

    logger.info("Proxy ready. Waiting for new connections...")
    await server.wait_closed()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        exit(0)
