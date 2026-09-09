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
            async for message in ws:
                self.inbound.append(message.data)
            return ws
        upstream=web.Application()
        upstream.router.add_get("/api/status",state)
        upstream.router.add_get("/phoenix",asset)
        upstream.router.add_get("/tokens.json",asset)
        upstream.router.add_get("/ws",socket)
        self.origin=TestServer(upstream)
        await self.origin.start_server()
        self.old=gateway.UPSTREAM
        gateway.UPSTREAM=str(self.origin.make_url("")).rstrip("/")
        self.server=TestServer(gateway.create_app())
        await self.server.start_server()
        self.client=ClientSession()
    async def asyncTearDown(self):
        await self.client.close()
        await self.server.close()
        await self.origin.close()
        gateway.UPSTREAM=self.old
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
            messages=[await ws.receive_json(timeout=2) for _ in range(4)]
            self.assertEqual([m["type"] for m in messages],["session_info","history_begin","ledger_event","history_end"])
            await asyncio.sleep(.05)
            self.assertEqual(self.inbound,[])
            self.mode="idle"
            ended=await ws.receive(timeout=5)
            self.assertIn(ended.type,[WSMsgType.CLOSE,WSMsgType.CLOSED])

    async def test_long_session_snapshot_does_not_prevent_board_history(self):
        # Reproduce the production ordering: a >8 MiB timing snapshot precedes
        # history_begin. The previous client limit closed with zero ledger rows.
        self.large_snapshot = {"type":"talk_time_update", "history": "x" * (12 * 1024 * 1024)}
        async with self.client.ws_connect(self.server.make_url("/ws"), max_msg_size=32*1024*1024) as ws:
            messages = [await ws.receive_json(timeout=5) for _ in range(5)]
            self.assertEqual([m["type"] for m in messages],
                ["session_info", "talk_time_update", "history_begin", "ledger_event", "history_end"])
            self.assertEqual(messages[1], self.large_snapshot)
            self.assertEqual(messages[3]["event"]["seq"], 1)
            self.assertFalse(ws.closed)

if __name__=="__main__": unittest.main()
