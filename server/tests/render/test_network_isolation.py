"""No network: each isolation layer is tested on its own, against a canary server that must stay
silent. A positive control proves the canary really detects a leak, so a silent canary means
something."""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from playwright.async_api import Page, async_playwright

from blogwriter.render.browser import RenderSession, chromium_args
from blogwriter.render.html import STATIC_CSP
from blogwriter.render.testing import Canary

FIXTURES = Path(__file__).resolve().parent / "fixtures"

pytestmark = [pytest.mark.chromium, pytest.mark.asyncio(loop_scope="module")]

IMAGE_WIDTHS_JS = "() => Object.fromEntries([...document.images].map(i => [i.id, i.naturalWidth]))"


def remote_assets_html(canary: Canary, *, csp: str | None = None) -> str:
    env = Environment(
        loader=FileSystemLoader(FIXTURES / "templates"), autoescape=True, undefined=StrictUndefined
    )
    return env.get_template("remote_assets.html.j2").render(canary=canary.base, csp=csp)


async def _load(page: Page, html: str) -> dict[str, int]:
    await page.set_content(html, wait_until="load")
    await asyncio.sleep(0.3)  # give any stray prefetch or background load time to reach the canary
    widths: dict[str, int] = await page.evaluate(IMAGE_WIDTHS_JS)
    return widths


async def test_route_guard_aborts_http_and_https_loads_and_records_them(
    session: RenderSession, canary: Canary
) -> None:
    before = len(session.guard.blocked)
    async with session.static_page(width=800, height=600) as page:
        widths = await _load(page, remote_assets_html(canary))
    blocked = {b.url for b in session.guard.blocked[before:]}
    assert "https://example.com/tracker.png" in blocked
    for path in ("pixel.png", "background.png", "import.css", "link.css", "frame.html"):
        assert f"{canary.base}/{path}" in blocked, path
    assert widths["https"] == 0
    assert widths["http"] == 0
    assert widths["file"] == 0
    assert widths["data"] == 1  # data: URIs are the one thing that loads
    assert canary.hits == []
    assert {b.scheme for b in session.guard.blocked[before:]} <= {"http", "https"}


async def test_csp_alone_blocks_remote_loads_before_the_route_guard(
    session: RenderSession, canary: Canary
) -> None:
    before = len(session.guard.blocked)
    async with session.static_page(width=800, height=600) as page:
        widths = await _load(page, remote_assets_html(canary, csp=STATIC_CSP))
    assert widths["data"] == 1
    assert widths["http"] == 0
    # The document CSP refused the loads, so the guard never even saw them.
    assert [b.url for b in session.guard.blocked[before:] if "pixel.png" in b.url] == []
    assert canary.hits == []


async def test_launch_flags_alone_stop_requests_when_no_route_is_installed(
    session: RenderSession, canary: Canary
) -> None:
    """Dead-end proxy (Playwright forces loopback through it too) and NOTFOUND DNS."""
    ctx = await session.browser.new_context(java_script_enabled=False)  # no route guard
    try:
        page = await ctx.new_page()
        widths = await _load(page, remote_assets_html(canary))
        assert widths["http"] == 0
        assert widths["https"] == 0
        assert canary.hits == []
    finally:
        await ctx.close()


async def test_positive_control_plain_chromium_does_reach_the_canary(canary: Canary) -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page()
            await page.goto(f"{canary.base}/direct")  # a real request with no isolation at all
        finally:
            await browser.close()
    assert canary.hits, "canary saw nothing: the isolation tests above would prove nothing"


async def test_js_contexts_run_jitless_without_webassembly(session: RenderSession) -> None:
    assert any("--jitless" in arg for arg in chromium_args(session.settings))
    ctx = await session.new_context(width=100, height=100, javascript=True)
    try:
        page = await ctx.new_page()
        await page.set_content("<p>probe</p>")
        assert await page.evaluate("typeof WebAssembly") == "undefined"
    finally:
        await ctx.close()


async def test_static_pages_never_run_page_script(session: RenderSession) -> None:
    """Slides, cards, OG images and rasters run with JavaScript off."""
    html = (
        "<html><head><title>original</title></head><body>"
        "<script>document.title = 'script ran'</script>"
        "<img src='data:image/png;base64,AAAA' onerror=\"document.title = 'handler ran'\">"
        "</body></html>"
    )
    async with session.static_page(width=200, height=200) as page:
        await page.set_content(html, wait_until="load")
        assert await page.title() == "original"


# --- Spike reviewer additions: traffic the route guard never sees. --------------------------------
# Playwright's route() does not intercept WebSockets or beacons, and nothing HTTP-level sees
# WebRTC's UDP. These run in a JS-enabled context with no CSP: the situation an exploit in the
# Mermaid page would be in.

JS_EGRESS = """async (base) => {
  const settle = (ms) => new Promise((r) => setTimeout(r, ms));
  try {
    const ws = new WebSocket(base.replace('http', 'ws') + '/ws'); await settle(800); ws.close();
  } catch (e) {}
  try { navigator.sendBeacon(base + '/beacon', 'x'); } catch (e) {}
  try { await fetch(base + '/fetch', {mode: 'no-cors'}); } catch (e) {}
  try { const es = new EventSource(base + '/es'); await settle(300); es.close(); } catch (e) {}
}"""


async def test_js_page_without_csp_cannot_reach_loopback_by_websocket_or_beacon(
    session: RenderSession, canary: Canary
) -> None:
    ctx = await session.new_context(width=100, height=100, javascript=True)
    try:
        page = await ctx.new_page()
        await page.set_content("<p>no CSP on this page</p>")
        await page.evaluate(JS_EGRESS, canary.base)
        await asyncio.sleep(0.5)  # beacons are sent after the call returns
    finally:
        await ctx.close()
    assert canary.hits == []  # the dead-end proxy (Playwright forces loopback through it) held


WEBRTC_JS = """async (url) => {
  const pc = new RTCPeerConnection({iceServers: [{urls: url}]});
  pc.createDataChannel('probe');
  await pc.setLocalDescription(await pc.createOffer());
  await new Promise((resolve) => {
    pc.onicegatheringstatechange = () => { if (pc.iceGatheringState === 'complete') resolve(); };
    setTimeout(resolve, 1500);
  });
  pc.close();
}"""


class _UdpCanary(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.hits: list[int] = []

    def datagram_received(self, data: bytes, addr: object) -> None:
        self.hits.append(len(data))


def _lan_ip() -> str | None:
    """The outbound interface address (WebRTC does not gather on loopback). Sends nothing."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
        except OSError:
            return None
        ip: str = s.getsockname()[0]
    return None if ip.startswith("127.") or ip == "0.0.0.0" else ip  # noqa: S104


async def _stun_hits(page: Page, ip: str) -> list[int]:
    loop = asyncio.get_running_loop()
    proto = _UdpCanary()
    transport, _ = await loop.create_datagram_endpoint(lambda: proto, local_addr=(ip, 0))
    try:
        port: int = transport.get_extra_info("sockname")[1]
        await page.evaluate(WEBRTC_JS, f"stun:{ip}:{port}")
        await asyncio.sleep(0.2)
    finally:
        transport.close()
    return proto.hits


async def test_webrtc_cannot_send_udp_from_a_js_page(session: RenderSession) -> None:
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in chromium_args(
        session.settings
    )
    ip = _lan_ip()
    if ip is None:
        pytest.skip("no non-loopback interface: WebRTC has nothing to send from on this host")
    # Positive control: plain Chromium does send STUN datagrams straight to the IP.
    async with async_playwright() as pw:
        plain = await pw.chromium.launch()
        try:
            control = await _stun_hits(await plain.new_page(), ip)
        finally:
            await plain.close()
    if not control:
        pytest.skip("plain Chromium sent no STUN on this host, so a silent canary proves nothing")
    ctx = await session.new_context(width=100, height=100, javascript=True)
    try:
        page = await ctx.new_page()
        await page.set_content("<p>no CSP on this page</p>")
        assert await _stun_hits(page, ip) == []
    finally:
        await ctx.close()
