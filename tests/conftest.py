
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def ocpp_proxy_module():
module_path = Path(__file__).resolve().parents[1] / "ocpp-proxy.py"
spec = importlib.util.spec_from_file_location("ocpp_proxy", module_path)
if spec is None or spec.loader is None:
raise RuntimeError(f"Could not load module from {module_path}")

module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
return module


@pytest.fixture(autouse=True)
def clear_proxy_registry(ocpp_proxy_module):
ocpp_proxy_module.OCPPConnectorIdDispatchingProxy.proxy_list.clear()
yield
ocpp_proxy_module.OCPPConnectorIdDispatchingProxy.proxy_list.clear()

