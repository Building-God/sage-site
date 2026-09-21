"""Live public bridge to Sage's renderer and room-authorized exhibit actions."""
import asyncio
import json
from pathlib import Path
from aiohttp import web, ClientSession, ClientTimeout, WSMsgType, ClientError
from board_audio import AudioService, AUDIO_HTTP

UPSTREAM = "http://127.0.0.1:9300"
SITE_ORIGIN = "https://sage.ridingoneggshells.chatgpt.site"
BOARD_ORIGIN = "https://grunty.tail197337.ts.net"
PORT = 19301
ROOT = Path(__file__).resolve().parent
# Long live sessions include complete talk-time histories (already >12 MiB).
# Keep a finite upstream bound without rejecting that snapshot before the ledger.
UPSTREAM_MAX_MESSAGE_BYTES = 64 * 1024 * 1024
EVENTS = frozenset({"session_info", "history_begin", "history_end", "ledger_event",
    "phoenix_reset", "voice_activity", "talk_time_update", "sidebar_update",
    "discussion_assessment_update", "clear_placed_notes", "transcript_line", "keepalive",
    "exhibit_playback"})
PARTICIPANT_GET = frozenset({"/auth/discord/start", "/auth/discord/callback", "/api/exhibits/me"})
PARTICIPANT_POST = frozenset({"/api/exhibits/annotate", "/api/exhibits/playback"})
OFFLINE = "<!doctype html><html lang=en><meta name=viewport content='width=device-width,initial-scale=1'><title>Sage board offline</title><body style='background:#0f1117;color:#e8eaf0;font:18px system-ui;padding:3rem'><h1>The board is offline</h1><p>No live Sage session is available. Please check back during a discussion.</p></body></html>"

async def live_state(client):
    try:
        async with client.get(UPSTREAM + "/api/status", timeout=ClientTimeout(total=3)) as r:
            if r.status != 200:
                return None
            d = await r.json()
            if d.get("session_mode") == "live" and not d.get("replay_running") and d.get("session_id"):
                return str(d["session_id"])
    except (OSError, asyncio.TimeoutError, ValueError, ClientError):
        pass
    return None

@web.middleware
async def guard(request, handler):
    readable = {"/", "/tokens.json", "/status", "/ws", "/listen.js", "/listen.css", "/audio.mp3"} | PARTICIPANT_GET
    if not ((request.method == "GET" and request.path in readable)
            or (request.method == "POST" and request.path in PARTICIPANT_POST)):
        return web.Response(status=404, text="Not available")
    try:
        response = await handler(request)
    except (OSError, asyncio.TimeoutError, ClientError):
        response = web.Response(status=503, text="Board unavailable")
    if request.path != "/ws":
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "Access-Control-Allow-Origin": SITE_ORIGIN,
            "Content-Security-Policy": "frame-ancestors 'self' " + SITE_ORIGIN})
    return response


async def participant_route(request):
    """Proxy only the OAuth and exhibit-action routes through the public guard.

    The upstream sees the fixed public Host and a public-viewer marker. Cookies,
    CSRF, and OAuth state stay within this board origin; private Sage APIs never
    pass through this gateway.
    """
    from urllib.parse import urlsplit

    board = urlsplit(BOARD_ORIGIN)
    if (board.scheme != "https" or not board.netloc or board.path or board.query
            or board.fragment or request.host.lower() != board.netloc.lower()):
        return web.Response(status=403, text="Board origin unavailable")
    if request.path != "/auth/discord/callback" and not await live_state(request.app["client"]):
        return web.Response(status=503, text="No live board")
    if request.method == "POST":
        if request.headers.get("Origin") != BOARD_ORIGIN:
            return web.Response(status=403, text="Same-origin submission required")
        limit = 4096 if request.path == "/api/exhibits/annotate" else 1024
        if request.content_length is not None and request.content_length > limit:
            return web.Response(status=413, text="Request too large")
        body = await request.content.read(limit + 1)
        if len(body) > limit or request.content_type != "application/json":
            return web.Response(status=413 if len(body) > limit else 415,
                                text="Invalid request body")
    else:
        body = None
    path = request.path_qs
    headers = {"Host": board.netloc, "Cf-Connecting-Ip": "192.0.2.1"}
    if request.method == "POST":
        headers.update({"Origin": BOARD_ORIGIN, "Content-Type": "application/json",
                        "X-Sage-CSRF": request.headers.get("X-Sage-CSRF", "")})
    # Never forward arbitrary browser cookies, credentials, or proxy headers.
    cookie_names = ("sage_oauth_state", "sage_participant")
    cookies = [f"{key}={request.cookies[key]}" for key in cookie_names
               if key in request.cookies]
    if cookies:
        headers["Cookie"] = "; ".join(cookies)
    async with request.app["client"].request(
            request.method, UPSTREAM + path, headers=headers, data=body,
            allow_redirects=False, timeout=ClientTimeout(total=25)) as upstream:
        raw = await upstream.read()
        response = web.Response(body=raw, status=upstream.status)
        for key in ("Content-Type", "Location"):
            if key in upstream.headers:
                response.headers[key] = upstream.headers[key]
        for value in upstream.headers.getall("Set-Cookie", []):
            response.headers.add("Set-Cookie", value)
        return response

async def status(request):
    result = {"live": bool(await live_state(request.app["client"]))}
    audio = request.app.get("audio")
    if audio is not None:
        result["audio"] = result["live"] and audio.available
    return web.json_response(result)

async def listen_asset(request):
    return web.FileResponse(ROOT / request.path.lstrip("/"))

async def audio_stream(request):
    audio = request.app.get("audio")
    if audio is None or not await live_state(request.app["client"]):
        return web.Response(status=503, text="Live audio unavailable")
    # Local preview fallback. Public /audio.mp3 routes straight to the audio
    # service, so board snapshots cannot stall audio delivery on this loop.
    async with request.app["client"].get(AUDIO_HTTP + "/audio.mp3", timeout=ClientTimeout(total=None, sock_read=5)) as upstream:
        response = web.StreamResponse(status=upstream.status, headers={
            "Content-Type": "audio/mpeg", "Cache-Control": "no-store"})
        await response.prepare(request)
        try:
            async for chunk in upstream.content.iter_any():
                await asyncio.wait_for(response.write(chunk), timeout=2)
        except (ConnectionError, asyncio.TimeoutError):
            pass
        return response

async def asset(request):
    client = request.app["client"]
    if not await live_state(client):
        return web.Response(text=OFFLINE, content_type="text/html", status=503)
    path = "/phoenix" if request.path == "/" else "/tokens.json"
    async with client.get(UPSTREAM + path, timeout=ClientTimeout(total=8)) as r:
        if r.status != 200:
            return web.Response(status=503, text="Board unavailable")
        raw = await r.read()
    if path == "/phoenix":
        html = raw.decode("utf-8")
        html = html.replace("<body>", "<body><style>#ctrl,#replay-error-banner{display:none!important}</style>", 1)
        html = html.replace("</head>", '<link rel="stylesheet" href="/listen.css"></head>', 1)
        html = html.replace("</body>", '<div id="live-audio"><button id="listen" type="button" aria-pressed="false" disabled>Listen</button><span id="listen-status" role="status">Checking audio...</span></div><script src="/listen.js" defer></script></body>', 1)
        return web.Response(text=html, content_type="text/html")
    return web.Response(body=raw, content_type="application/json")

async def socket(request):
    client = request.app["client"]
    session_id = await live_state(client)
    if not session_id:
        return web.Response(status=503, text="No live board")
    # Browser clients negotiate per-message deflate here. Long-running rooms
    # can carry a highly-compressible, multi-MiB talk-time history.
    downstream = web.WebSocketResponse(
        heartbeat=20, max_msg_size=4096, compress=True)
    async with client.ws_connect(UPSTREAM + "/ws", max_msg_size=UPSTREAM_MAX_MESSAGE_BYTES) as upstream:
        initial = await upstream.receive(timeout=5)
        if initial.type != WSMsgType.TEXT:
            return web.Response(status=503)
        try:
            hello = json.loads(initial.data)
        except ValueError:
            return web.Response(status=503)
        if hello.get("type") != "session_info" or hello.get("mode") != "live" or hello.get("session_id") != session_id:
            return web.Response(status=503)
        await downstream.prepare(request)
        await downstream.send_json(hello)
        async def consume_viewer():
            async for _ in downstream:
                pass  # Viewer input is never forwarded upstream.
            await upstream.close()
        async def watch_session():
            while not downstream.closed:
                await asyncio.sleep(3)
                if await live_state(client) != session_id:
                    await downstream.close(code=1001, message=b"Live session ended")
                    await upstream.close()
                    return
        tasks = [asyncio.create_task(consume_viewer()), asyncio.create_task(watch_session())]
        # The currently running source may predate the canonical ordering fix
        # and emit large sidecar snapshots before history_begin. Hold that
        # finite prefix so the actual board can bootstrap and reveal first.
        before_history = True
        deferred_prefix = []
        try:
            async for message in upstream:
                if downstream.closed:
                    break
                if message.type == WSMsgType.ERROR:
                    await downstream.close(code=1011, message=b"Board source connection failed")
                    break
                if message.type != WSMsgType.TEXT:
                    continue
                try:
                    payload = json.loads(message.data)
                except ValueError:
                    continue
                if payload.get("type") == "session_info" and (payload.get("mode") != "live" or payload.get("session_id") != session_id):
                    break
                kind = payload.get("type")
                if kind not in EVENTS:
                    continue
                if before_history and kind != "history_begin":
                    deferred_prefix.append(payload)
                    continue
                if kind == "history_begin":
                    before_history = False
                await downstream.send_json(payload)
                if kind == "history_end" and deferred_prefix:
                    for deferred in deferred_prefix:
                        await downstream.send_json(deferred)
                    deferred_prefix.clear()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await downstream.close()
    return downstream

async def client_context(app):
    async with ClientSession() as client:
        app["client"] = client
        yield

async def audio_context(app):
    audio = AudioService()
    app["audio"] = audio
    await audio.start()
    try:
        yield
    finally:
        await audio.close()

def create_app(*, with_audio=True):
    app = web.Application(middlewares=[guard])
    app.cleanup_ctx.append(client_context)
    if with_audio:
        app.cleanup_ctx.append(audio_context)
    app.router.add_get("/status", status)
    app.router.add_get("/ws", socket)
    app.router.add_get("/", asset)
    app.router.add_get("/tokens.json", asset)
    app.router.add_get("/listen.js", listen_asset)
    app.router.add_get("/listen.css", listen_asset)
    app.router.add_get("/audio.mp3", audio_stream)
    for path in PARTICIPANT_GET:
        app.router.add_get(path, participant_route)
    for path in PARTICIPANT_POST:
        app.router.add_post(path, participant_route)
    return app

if __name__ == "__main__":
    web.run_app(create_app(), host="127.0.0.1", port=PORT, print=None, access_log=None)
