# -----------------------------------------------------------------------------
# Predbat Home Battery System
# This application may be used for personal use only and not for commercial use
# -----------------------------------------------------------------------------
"""Exercise the optional modern UI with the real router and mocked HA controls."""

import asyncio
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp import web as aiohttp_web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from web import WebInterface, build_apps_json_schema


async def check_modern_routes(my_predbat):
    """Check both router modes, bundled assets and dashboard API controls."""
    interface = WebInterface(my_predbat, web_port=5053)
    captured = []

    def capture_runner(app):
        """Capture the production router without starting its long-running loop."""
        captured.append(app)
        return SimpleNamespace(setup=AsyncMock(), cleanup=AsyncMock())

    for mode in ("legacy", "modern"):
        interface.get_web_ui = lambda: mode
        interface.api_stop = True
        with patch("web.web.AppRunner", side_effect=capture_runner), patch("web.web.TCPSite", return_value=SimpleNamespace(start=AsyncMock())):
            await interface.start()
        app = captured[-1]
        routes = {(route.method, route.resource.canonical): route.handler for route in app.router.routes()}
        assert routes[("GET", "/")] == (interface.html_modern_ui if mode == "modern" else interface.html_default)
        assert routes[("GET", "/apps")] == (interface.html_modern_ui if mode == "modern" else interface.html_apps)
        assert routes[("GET", "/legacy_apps")] == interface.html_apps
        assert routes[("GET", "/legacy_annual")] == interface.annual_page.html_annual
        assert routes[("GET", "/legacy_chat")] == interface.chat_page.html_chat
        assert routes[("POST", "/plan_override")] == interface.html_plan_override
        assert routes[("POST", "/rate_override")] == interface.html_rate_override
        if mode == "legacy":
            assert ("GET", "/assets/{filename}") not in routes
            continue

        async with TestClient(TestServer(app)) as client:
            for path in ("/", "/plan", "/dash", "/apps", "/apps_editor", "/charts", "/config", "/log", "/compare", "/components", "/discovery", "/internals", "/browse", "/docs", "/annual", "/chat"):
                response = await client.get(path)
                assert response.status == 200, (path, response.status, await response.text())
                assert '<div id="root"></div>' in await response.text(), path
                assert response.headers["Cache-Control"] == "no-store"

            archive_path = Path(__file__).parents[1] / "frontend.zip"
            with zipfile.ZipFile(archive_path) as archive:
                assert archive.testzip() is None
                assets = [name for name in archive.namelist() if name.startswith("dist/assets/") and not name.endswith("/")]
                browser_code = b"".join(archive.read(name) for name in assets if name.endswith(".js"))
                assert b"3.4.16" in browser_code, "Patched sanitizer must be included in the served bundle"
                assert b"3.1.7" not in browser_code, "Old embedded sanitizer must not be shipped"
                for name in assets:
                    response = await client.get("/" + name.removeprefix("dist/"))
                    assert response.status == 200, name
                    assert await response.read() == archive.read(name), name
            assert (await client.get("/assets/missing.js")).status == 404
            assert (await client.get("/api/chart_data?chart=unknown")).status == 400
            assert (await client.get("/api/status")).status == 200
            assert (await (await client.get("/api/status")).json())["version"].startswith("v9.3.3")
            assert (await client.get("/api/internals/threads")).status == 200

            with patch.object(interface, "set_state_external", new_callable=AsyncMock) as setter:
                cases = (("mode", "Monitor"), ("active", True), ("debug_enable", False), ("set_read_only", True))
                for control, value in cases:
                    response = await client.post("/api/dashboard_control", json={"control": control, "value": value})
                    assert response.status == 200
                    domain = "select" if control == "mode" else "switch"
                    setter.assert_awaited_with(f"{domain}.{interface.prefix}_{control}", value)
                setter.reset_mock()
                for payload in ({"control": "active", "value": "false"}, {"control": "mode", "value": True}, {"control": "unknown", "value": True}):
                    assert (await client.post("/api/dashboard_control", json=payload)).status == 400
                setter.assert_not_awaited()

    for filename in ("../apps.yaml", "/apps.yaml", "assets/../../apps.yaml"):
        try:
            await interface.html_modern_ui_asset(make_mocked_request("GET", "/"), filename)
        except aiohttp_web.HTTPNotFound:
            pass
        else:
            raise AssertionError(f"Archive traversal accepted: {filename}")
    with patch("web.zipfile.ZipFile", side_effect=FileNotFoundError):
        assert (await interface.html_modern_ui(None)).status == 503


async def check_modern_data(my_predbat):
    """Check power sign conventions, non-mutating plan data and stale editor saves."""
    interface = WebInterface(my_predbat, web_port=5053)
    values = {"grid_power": -2500, "battery_power": -500, "pv_power": 1000, "load_power": 3000, "soc_percent": 50, "car_charging_power_configured": True, "car_charging_power": 1200, "car_energy_reported_load": True}
    with patch.multiple(my_predbat, **values):
        response = await interface.html_api_power_flow(None)
        data = json.loads(response.text)
        assert data["grid_importing"] and data["battery_charging"]
        assert not data["battery_discharging"]
        assert data["house_power"] == 1800 and data["car"]["charging"]
    with patch.multiple(my_predbat, **{**values, "car_energy_reported_load": False, "grid_power": 2500, "battery_power": 500}):
        data = json.loads((await interface.html_api_power_flow(None)).text)
        assert data["house_power"] == 3000 and not data["grid_importing"] and data["battery_discharging"]

    original_plan = {"timestamp": "2026-10-01T10:00:00+0000", "rows": [{"soc": 50}]}
    with patch.object(interface, "get_state_wrapper", side_effect=lambda entity_id, attribute, default: original_plan if attribute == "raw" else None):
        request = make_mocked_request("GET", "/api/plan_data")
        data = json.loads((await interface.html_api_plan_data(request)).text)
        assert "car_energy_reported_load" in data["plan"]
        assert "car_energy_reported_load" not in original_plan
        assert data["plan"]["rows"] == original_plan["rows"]

    # Every file write below is confined to this temporary fixture, never live apps.yaml.
    original_directory = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="predbat-modern-test-") as directory:
        try:
            os.chdir(directory)
            original = "pred_bat:\n  module: predbat\n  class: PredBat\n"
            Path("apps.yaml").write_text(original)
            source = json.loads((await interface.html_api_apps_yaml(None)).text)
            assert source == {"content": original, "checksum": hashlib.md5(original.encode()).hexdigest()}
            changed = original + "  web_ui: modern\r\n"
            stale = SimpleNamespace(json=AsyncMock(return_value={"content": changed, "checksum": "stale"}))
            assert (await interface.html_api_apps_yaml_post(stale)).status == 409
            assert Path("apps.yaml").read_text() == original
            invalid = SimpleNamespace(json=AsyncMock(return_value={"content": None, "checksum": source["checksum"]}))
            assert (await interface.html_api_apps_yaml_post(invalid)).status == 400
            save = SimpleNamespace(json=AsyncMock(return_value={"content": changed, "checksum": source["checksum"]}))
            assert (await interface.html_api_apps_yaml_post(save)).status == 200
            assert Path("apps.yaml.backup").read_text() == original
            assert Path("apps.yaml").read_text() == changed.replace("\r\n", "\n")
        finally:
            os.chdir(original_directory)

    schema = build_apps_json_schema()["$defs"]["predbatApp"]["properties"]
    assert schema["web_ui"]["enum"] == ["legacy", "modern"]
    assert "ha_key" in schema and "load_today" in schema


def run_modern_ui_tests(my_predbat):
    """Run the modern port's router, asset and data regressions."""
    print("**** Testing modern UI routing, all bundled assets, controls, power flow, plan copy and editor saves ****")
    asyncio.run(check_modern_routes(my_predbat))
    asyncio.run(check_modern_data(my_predbat))
    return False
