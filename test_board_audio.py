import asyncio
import io
import time
import unittest
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import av
import numpy as np
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from board_audio import LiveAudio, HEADER, MAGIC, FRAME_BYTES, RATE


def packet(user, pcm=b"", channel=123, age=0):
    return HEADER.pack(MAGIC, channel, user, time.monotonic() - age) + pcm


class AudioTests(unittest.IsolatedAsyncioTestCase):
    def test_listen_script_parses_after_repository_text_normalization(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required for the shipped JavaScript parse check')
        result = subprocess.run([node, '--check', str(Path(__file__).with_name('listen.js'))], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def make_audio(self):
        audio = LiveAudio(None)
        audio.channel = 123
        audio.session = "live-room"
        audio.datagram_received(packet(0), ("127.0.0.1", 1))
        return audio

    async def test_room_and_sage_mix_and_other_rooms_stay_out(self):
        audio = self.make_audio()
        audio.listeners.add(asyncio.Queue())
        pcm = np.full(480, 1000, dtype="<i2").tobytes()
        for user in [12, 0]:
            audio.datagram_received(packet(user, pcm), ("127.0.0.1", 1))
        audio.datagram_received(packet(13, pcm, channel=456), ("127.0.0.1", 1))
        audio.datagram_received(packet(14, pcm, age=1), ("127.0.0.1", 1))
        self.assertEqual(set(audio.sources), {12, 0})
        self.assertTrue(np.all(audio.mix(time.monotonic()+.1) == 1400))
        self.assertTrue(np.all(audio.mix(time.monotonic()+.12) == 0))
        self.assertEqual(audio.packet_counts, {"room": 1, "sage": 1})

    async def test_no_listener_retains_no_audio_and_backlog_is_bounded(self):
        audio = self.make_audio()
        pcm = bytes(FRAME_BYTES)
        audio.datagram_received(packet(12, pcm), ("127.0.0.1", 1))
        self.assertEqual(audio.sources, {})
        audio.listeners.add(asyncio.Queue())
        for _ in range(30):
            audio.datagram_received(packet(12, pcm), ("127.0.0.1", 1))
        self.assertLessEqual(len(audio.sources[12]["pcm"]), FRAME_BYTES*10)

    async def test_heartbeat_expiry_and_channel_change_end_listeners(self):
        audio = self.make_audio()
        queue = asyncio.Queue()
        audio.listeners.add(queue)
        audio.heartbeat -= 3
        self.assertFalse(audio.available)
        task = asyncio.create_task(audio.pump())
        try:
            self.assertIsNone(await asyncio.wait_for(queue.get(), .5))
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        async def live(): return "live-room"
        audio.live_state = live
        audio.listeners.add(queue)
        with patch('board_audio.audio_enabled', return_value=False), patch('board_audio.mapped_channel', return_value=456):
            task = asyncio.create_task(audio.monitor())
            try:
                self.assertIsNone(await asyncio.wait_for(queue.get(), .5))
                self.assertEqual(audio.channel, 0)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_worker_follows_sage_from_one_room_to_another(self):
        """Harry, 2026-09-24: "The room she's in goes out. We want that room."
        No fixed channel is configured any more -- the worker accepts whatever
        room the board itself says is active (`mapped_channel`), and only
        packets whose embedded channel (the bot's actual current room) match
        that get through. Moving the board's active room mid-session must
        move playback with it, and a stale packet tagged with the old room
        must stop counting immediately."""
        audio = LiveAudio(None)
        async def live(): return "live-room"
        audio.live_state = live
        mapped = [111]
        with patch('board_audio.audio_enabled', return_value=True), \
                patch('board_audio.mapped_channel', side_effect=lambda: mapped[0]):
            task = asyncio.create_task(audio.monitor())
            try:
                await asyncio.sleep(.1)
                self.assertEqual(audio.channel, 111)
                audio.datagram_received(packet(0, channel=111), ("127.0.0.1", 1))
                self.assertTrue(audio.available)  # room 111's own audio is heard

                # Sage moves rooms; the board's active room follows her there.
                mapped[0] = 222
                await asyncio.sleep(.6)
                self.assertEqual(audio.channel, 222)
                # A packet still tagged with the OLD room no longer counts.
                audio.datagram_received(packet(0, channel=111), ("127.0.0.1", 1))
                self.assertFalse(audio.available)
                # A packet tagged with the NEW room is heard.
                audio.datagram_received(packet(0, channel=222), ("127.0.0.1", 1))
                self.assertTrue(audio.available)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_stream_is_decodable_mp3_and_offline_returns_503(self):
        audio = self.make_audio()
        app = web.Application()
        app.router.add_get('/audio.mp3', audio.stream)
        server = TestServer(app)
        await server.start_server()
        try:
            async with ClientSession() as client:
                async with client.get(server.make_url('/audio.mp3')) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers['Content-Type'], 'audio/mpeg')
                    queue = next(iter(audio.listeners))
                    wave = (np.sin(2*np.pi*440*np.arange(480)/RATE)*8000).astype(np.int16)
                    for _ in range(60):
                        for encoded in audio.encode(wave):
                            queue.put_nowait(encoded)
                    queue.put_nowait(None)
                    data = await response.read()
                    with av.open(io.BytesIO(data), format='mp3') as container:
                        frames = list(container.decode(audio=0))
                    self.assertGreater(sum(f.samples for f in frames), RATE*.8)
                    self.assertGreater(max(float(np.max(np.abs(f.to_ndarray()))) for f in frames), .01)
                self.assertEqual(audio.listeners, set())
                audio.channel = 0
                async with client.get(server.make_url('/audio.mp3')) as response:
                    self.assertEqual(response.status, 503)
        finally:
            await server.close()
