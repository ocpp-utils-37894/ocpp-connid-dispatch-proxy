from __future__ import annotations

import configparser
from types import SimpleNamespace

import pytest


def make_proxy(ocpp_proxy_module, charger_id: str = "charger-01"):
dummy_ws = SimpleNamespace(request=None)
return ocpp_proxy_module.OCPPConnectorIdDispatchingProxy(
websocket=dummy_ws,
charger_id=charger_id,
request_path=f"/{charger_id}",
protocol="wss",
host="localhost",
port=443,
context_path="",
)


def test_split_url_without_context(ocpp_proxy_module):
protocol, host, port, context_path, charger_id = ocpp_proxy_module.OCPPConnectorIdDispatchingProxy.split_url(
"wss://host.example:9443/id"
)

assert protocol == "wss"
assert host == "host.example"
assert port == 9443
assert context_path == ""
assert charger_id == "id"


def test_split_url_with_context(ocpp_proxy_module):
protocol, host, port, context_path, charger_id = ocpp_proxy_module.OCPPConnectorIdDispatchingProxy.split_url(
"wss://host.example:9443/ocpp/backend/CP-1"
)

assert protocol == "wss"
assert host == "host.example"
assert port == 9443
assert context_path == "/ocpp/backend"
assert charger_id == "CP-1"


def test_decode_ocpp_message_connector_id_int(ocpp_proxy_module):
msg_type, msg_id, connector_id = ocpp_proxy_module.OCPPConnectorIdDispatchingProxy.decode_ocpp_message(
'[2, "m-1", "StartTransaction", {"connectorId": 7}]'
)

assert msg_type == 2
assert msg_id == "m-1"
assert connector_id == 7


def test_decode_ocpp_message_connector_id_string(ocpp_proxy_module):
msg_type, msg_id, connector_id = ocpp_proxy_module.OCPPConnectorIdDispatchingProxy.decode_ocpp_message(
'[2, "m-2", "StartTransaction", {"connectorId": "12"}]'
)

assert msg_type == 2
assert msg_id == "m-2"
assert connector_id == 12


def test_decode_ocpp_message_without_connector_id(ocpp_proxy_module):
msg_type, msg_id, connector_id = ocpp_proxy_module.OCPPConnectorIdDispatchingProxy.decode_ocpp_message(
'[3, "m-3", {"status": "Accepted"}]'
)

assert msg_type == 3
assert msg_id == "m-3"
assert connector_id is None


def test_non_alphanumeric_charger_id_is_allowed(ocpp_proxy_module):
proxy = make_proxy(ocpp_proxy_module, charger_id="CP-01_A/B")
assert proxy.charger_id == "CP-01_A/B"


@pytest.mark.asyncio
async def test_is_ws_alive_uses_ping_successfully(ocpp_proxy_module):
proxy = make_proxy(ocpp_proxy_module)

class FakeWS:
async def ping(self):
async def _pong():
return None

return _pong()

assert await proxy.is_ws_alive(FakeWS()) is True


@pytest.mark.asyncio
async def test_is_ws_alive_returns_false_for_missing_ws(ocpp_proxy_module):
proxy = make_proxy(ocpp_proxy_module)
assert await proxy.is_ws_alive(None) is False


@pytest.mark.asyncio
async def test_unmapped_connector_id_uses_primary_connection(ocpp_proxy_module):
module_config = configparser.ConfigParser()
module_config.add_section("charge-point-mapping")
ocpp_proxy_module.config = module_config

class FakePrimaryConnection:
def __init__(self):
self.sent_messages = []

async def send(self, message):
self.sent_messages.append(message)

async def ping(self):
async def _pong():
return None

return _pong()

message = '[2, "m-9", "MeterValues", {"connectorId": 99}]'

class FakeChargerConnection:
def __init__(self):
self._first = True

async def recv(self):
if self._first:
self._first = False
return message
raise RuntimeError("stop loop")

proxy = make_proxy(ocpp_proxy_module, charger_id="CP-A")
proxy.client_connection = FakeChargerConnection()

primary_connection = FakePrimaryConnection()
setup_calls = []

async def fake_setup_connection(protocol, host, port, context, charger_id):
setup_calls.append(
{
"protocol": protocol,
"host": host,
"port": port,
"context": context,
"charger_id": charger_id,
}
)
return primary_connection

proxy.setup_connection = fake_setup_connection

await proxy.receive_charger_messages()

assert len(setup_calls) == 1
assert setup_calls[0]["charger_id"] == "CP-A"
assert primary_connection.sent_messages == [message]
assert 99 not in proxy.connector_connections