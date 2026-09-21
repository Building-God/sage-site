import asyncio
import unittest
from aiohttp import web, ClientSession, WSMsgType
from aiohttp.test_utils import TestServer
import board_gateway as gateway

class BoardGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mode = "live"
        self.inbound = []
        self.requests = []
        self.large_snapshot = None
        self.participant_received = []
        async def state(request):
            return web.json_response({"session_mode": self.mode,"session_id":"test-room","replay_running":self.mode=="replay"})
        async def asset(request):
            self.requests.append(str(request.rel_url))
            return web.Response(text="<html><body><div id=ctrl>controls</div></body></html>")
        async def socket(request):
            ws=web.WebSocketResponse()
            await ws.prepare(request)
            await ws.send_json({"type":"session_info","mode":self.mode,"session_id":"test-room"})
            if self.large_snapshot is not None:
                await ws.send_json(self.large_snapshot)
            await ws.send_json({"type":"history_begin","stream":"phoenix_ledger"})
            await ws.send_json({"type":"ledger_event","event":{"type":"test","seq":1}})
            await ws.send_json({"type":"private_debug","secret":"must not escape"})
            await ws.send_json({"type":"history_end","stream":"phoenix_ledger"})
            await ws.send_json({"type":"exhibit_playback","exhibit_id":"video-1","playing":True})
            async for message in ws:
                self.inbound.append(message.data)
            return ws
        async def participant(request):
            self.participant_received.append({
                "path": request.path, "host": request.host,
                "query": request.query_string,
                "origin": request.headers.get("Origin"),
                "public": request.headers.get("Cf-Connecting-Ip"),
                "csrf": request.headers.get("X-Sage-CSRF"),
                "cookies": dict(request.cookies),
                "body": await request.read(),
            })
            if request.path == "/auth/discord/start":
                response = web.HTTPSeeOther("https://discord.com/oauth2/authorize?state=abc")
                response.set_cookie("sage_oauth_state", "abc", httponly=True)
                return response
            if request.path == "/auth/discord/callback":
                response = web.HTTPSeeOther("/")
                response.set_cookie("sage_participant", "signed-in", httponly=True)
                return response
            return web.json_response({"status": "ok"})
        upstream=web.Application()
        upstream.router.add_get("/api/status",state)
        upstream.router.add_get("/phoenix",asset)
        upstream.router.add_get("/tokens.json",asset)
        upstream.router.add_get("/ws",socket)
        for path in gateway.PARTICIPANT_GET:
            upstream.router.add_get(path,participant)
        for path in gateway.PARTICIPANT_POST:
            upstream.router.add_post(path,participant)
        self.origin=TestServer(upstream)
        await self.origin.start_server()
        self.old=gateway.UPSTREAM
        self.old_origin=gateway.BOARD_ORIGIN
        gateway.BOARD_ORIGIN="https://board.example"
        gateway.UPSTREAM=str(self.origin.make_url("")).rstrip("/")
        self.server=TestServer(gateway.create_app(with_audio=False))
        await self.server.start_server()
        self.client=ClientSession()
    async def asyncTearDown(self):
        await self.client.close()
        await self.server.close()
        await self.origin.close()
        gateway.UPSTREAM=self.old
        gateway.BOARD_ORIGIN=self.old_origin
    async def test_control_and_archive_paths_are_denied(self):
        for method,path in [("POST","/api/clear"),("GET","/api/sessions"),("GET","/data/archive.json"),("GET","/api/maestro/status"),("GET","/vendor/eu_decisive_fixture.js"),("GET","/api/status")]:
            async with self.client.request(method,self.server.make_url(path)) as response:
                self.assertEqual(response.status,404)
        self.assertEqual(self.requests,[])
    async def test_offline_replay_and_unreachable_fail_closed(self):
        for mode in ["idle","replay"]:
            self.mode=mode
            async with self.client.get(self.server.make_url("/status")) as r:
                self.assertEqual(await r.json(),{"live":False})
            async with self.client.get(self.server.make_url("/")) as r:
                self.assertEqual(r.status,503)
                self.assertIn("offline",await r.text())
        await self.origin.close()
        async with self.client.get(self.server.make_url("/status")) as r:
            self.assertEqual(await r.json(),{"live":False})
    async def test_live_html_strips_query_and_hides_controls(self):
        async with self.client.get(self.server.make_url("/?standingDemo=eu-decisive")) as r:
            self.assertEqual(r.status,200)
            self.assertIn("display:none!important",await r.text())
            self.assertEqual(r.headers["Access-Control-Allow-Origin"],gateway.SITE_ORIGIN)
        self.assertEqual(self.requests,["/phoenix"])
    async def test_websocket_filters_events_and_discards_viewer_commands(self):
        async with self.client.ws_connect(self.server.make_url("/ws")) as ws:
            await ws.send_json({"type":"note_created","node_id":"should-never-reach-source"})
            messages=[await ws.receive_json(timeout=2) for _ in range(5)]
            self.assertEqual([m["type"] for m in messages],["session_info","history_begin","ledger_event","history_end","exhibit_playback"])
            await asyncio.sleep(.05)
            self.assertEqual(self.inbound,[])
            self.mode="idle"
            ended=await ws.receive(timeout=5)
            self.assertIn(ended.type,[WSMsgType.CLOSE,WSMsgType.CLOSED])

    async def test_long_session_snapshot_does_not_prevent_board_history(self):
        # Reproduce the old production ordering: a >8 MiB timing snapshot
        # precedes history_begin. The public bridge must reveal the board first.
        self.large_snapshot = {"type":"talk_time_update", "history": "x" * (12 * 1024 * 1024)}
        async with self.client.ws_connect(self.server.make_url("/ws"), max_msg_size=32*1024*1024) as ws:
            messages = [await ws.receive_json(timeout=5) for _ in range(5)]
            self.assertEqual([m["type"] for m in messages],
                ["session_info", "history_begin", "ledger_event", "history_end", "talk_time_update"])
            self.assertEqual(messages[4], self.large_snapshot)
            self.assertEqual(messages[2]["event"]["seq"], 1)
            self.assertFalse(ws.closed)

    async def test_exact_participant_routes_keep_host_cookies_csrf_and_public_boundary(self):
        host={"Host":"board.example"}
        async with self.client.get(self.server.make_url("/auth/discord/start"),
                                   headers=host,allow_redirects=False) as response:
            self.assertEqual(response.status,303)
            self.assertTrue(response.headers["Location"].startswith("https://discord.com/"))
            self.assertIn("sage_oauth_state=abc",response.headers["Set-Cookie"])
        async with self.client.get(self.server.make_url("/api/exhibits/me"),
                                   headers={**host,"Cookie":"sage_participant=valid; unrelated=secret"}) as response:
            self.assertEqual(response.status,200)
        self.assertEqual(self.participant_received[-1]["cookies"],{"sage_participant":"valid"})
        async with self.client.get(self.server.make_url("/auth/discord/callback?code=abc&state=xyz"),
                                   headers={**host,"Cookie":"sage_oauth_state=xyz; unrelated=secret"},
                                   allow_redirects=False) as response:
            self.assertEqual(response.status,303)
            self.assertEqual(response.headers["Location"],"/")
            self.assertIn("sage_participant=signed-in",response.headers["Set-Cookie"])
        self.assertEqual(self.participant_received[-1]["query"],"code=abc&state=xyz")
        self.assertEqual(self.participant_received[-1]["cookies"],{"sage_oauth_state":"xyz"})
        payload=b'{"exhibit_id":"video-1","action":"play","position_s":12,"revision":1}'
        headers={**host,"Origin":"https://board.example","Content-Type":"application/json",
                 "X-Sage-CSRF":"csrf-token","Cookie":"sage_participant=valid; unrelated=secret"}
        async with self.client.post(self.server.make_url("/api/exhibits/playback"),
                                    headers=headers,data=payload) as response:
            self.assertEqual(response.status,200)
        received=self.participant_received[-1]
        self.assertEqual(received["host"],"board.example")
        self.assertEqual(received["origin"],"https://board.example")
        self.assertEqual(received["public"],"192.0.2.1")
        self.assertEqual(received["csrf"],"csrf-token")
        self.assertEqual(received["cookies"],{"sage_participant":"valid"})
        self.assertEqual(received["body"],payload)
        for path in ("/api/exhibits/annotate","/api/exhibits/playback"):
            async with self.client.post(self.server.make_url(path),headers={**host,
                    "Origin":"https://evil.example","Content-Type":"application/json"},
                    data=b'{}') as response:
                self.assertEqual(response.status,403)
        async with self.client.get(self.server.make_url("/auth/discord/start")) as response:
            self.assertEqual(response.status,403)
        self.assertEqual(len(self.participant_received),4)

if __name__=="__main__": unittest.main()
