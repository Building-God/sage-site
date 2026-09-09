"""Selected live-room PCM -> one shared MP3 stream, without disk recording."""
import asyncio
import contextlib
import json
import logging
from pathlib import Path
import sys
import time

import av
import numpy as np
from aiohttp import web

GODBOT = Path(__file__).resolve().parent.parent / "GodBot"
sys.path.insert(0, str(GODBOT))
from public_audio import ADDRESS, HEADER, MAGIC, RATE, FRAME_BYTES, configured_channel

MAPPING_STATE = GODBOT / "data" / "phoenix" / "live_state_9300" / "offset.json"


def mapped_channel():
    try:
        state = json.loads(MAPPING_STATE.read_text(encoding="utf-8"))
        return int(state.get("active_voice_channel_id", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        return 0


class LiveAudio(asyncio.DatagramProtocol):
    def __init__(self, live_state):
        self.live_state = live_state
        self.channel = 0
        self.session = None
        self.heartbeat = 0
        self.sources = {}
        self.listeners = set()
        self.codec = None
        self.pts = 0
        self.transport = None
        self.tasks = []
        self.packet_counts = {"room": 0, "sage": 0}

    @property
    def available(self):
        return bool(self.channel and self.session and time.monotonic() - self.heartbeat < 2)

    async def start(self):
        self.transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: self, local_addr=ADDRESS)
        self.tasks = [asyncio.create_task(self.monitor()), asyncio.create_task(self.pump())]

    def end_listeners(self):
        for queue in tuple(self.listeners):
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(None)
        self.listeners.clear()
        self.sources.clear()
        self.codec = None

    async def close(self):
        self.end_listeners()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.transport:
            self.transport.close()

    async def monitor(self):
        while True:
            session = await self.live_state()
            selected = configured_channel()
            channel = selected if selected and selected == mapped_channel() and session else 0
            if (channel, session) != (self.channel, self.session):
                self.end_listeners()
                self.heartbeat = 0
            self.channel, self.session = channel, session
            if not self.available:
                self.end_listeners()
            await asyncio.sleep(.5)

    def datagram_received(self, packet, addr):
        if addr[0] != "127.0.0.1" or len(packet) < HEADER.size:
            return
        magic, channel, user, sent = HEADER.unpack_from(packet)
        now = time.monotonic()
        pcm = packet[HEADER.size:]
        if (magic != MAGIC or channel != self.channel or not self.session
                or not 0 <= now - sent < .5 or len(pcm) % 2 or len(pcm) > FRAME_BYTES * 4):
            return
        if not pcm:
            if user == 0:
                self.heartbeat = now
            return
        self.packet_counts["sage" if user == 0 else "room"] += 1
        if not self.listeners or not self.available:
            return
        # Per-speaker FIFO preserves overlap; bounded backlog rejoins live after stalls.
        if user not in self.sources:
            if len(self.sources) >= 100:
                return
            self.sources[user] = {"pcm": bytearray(), "ready": now + .06, "last": now}
        source = self.sources[user]
        source["pcm"].extend(pcm)
        source["last"] = now
        if len(source["pcm"]) > FRAME_BYTES * 10:
            del source["pcm"][:-FRAME_BYTES * 3]

    def mix(self, now):
        mixed = np.zeros(FRAME_BYTES // 2, dtype=np.int32)
        for user, source in list(self.sources.items()):
            if now - source["last"] > .5:
                del self.sources[user]
                continue
            if now < source["ready"]:
                continue
            pcm = source["pcm"]
            n = min(len(pcm), FRAME_BYTES)
            if n:
                mixed[:n // 2] += np.frombuffer(bytes(pcm[:n]), dtype="<i2")
                del pcm[:n]
        return np.clip(mixed * .7, -32768, 32767).astype(np.int16)

    def encode(self, samples):
        if self.codec is None:
            self.codec = av.CodecContext.create("libmp3lame", "w")
            self.codec.sample_rate = RATE
            self.codec.layout = "mono"
            self.codec.format = "s16p"
            self.codec.bit_rate = 64000
            self.codec.options = {"reservoir": "0"}
            self.pts = 0
            self.codec.open()
        frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="s16p", layout="mono")
        frame.sample_rate = RATE
        frame.pts = self.pts
        self.pts += len(samples)
        return [bytes(packet) for packet in self.codec.encode(frame)]

    async def pump(self):
        deadline = time.monotonic()
        while True:
            now = time.monotonic()
            if self.listeners and self.available:
                try:
                    for packet in self.encode(self.mix(now)):
                        for queue in tuple(self.listeners):
                            if queue.full():
                                self.listeners.discard(queue)
                                while not queue.empty():
                                    queue.get_nowait()
                                queue.put_nowait(None)
                            else:
                                queue.put_nowait(packet)
                except Exception:
                    logging.exception("Live audio encoder failed; closing listeners")
                    self.end_listeners()
            elif not self.available:
                self.end_listeners()
            else:
                self.sources.clear()
                self.codec = None
            deadline = max(deadline + .02, now)
            await asyncio.sleep(max(0, deadline - time.monotonic()))

    async def stream(self, request):
        if not self.available or len(self.listeners) >= 20:
            return web.Response(status=503, text="Live audio unavailable")
        queue = asyncio.Queue(maxsize=100)  # Slow viewers disconnect, never delay the room.
        self.listeners.add(queue)
        response = web.StreamResponse(headers={
            "Content-Type": "audio/mpeg", "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff", "X-Accel-Buffering": "no"})
        try:
            await response.prepare(request)
            while True:
                packet = await asyncio.wait_for(queue.get(), timeout=3)
                if packet is None:
                    break
                await asyncio.wait_for(response.write(packet), timeout=2)
        except (ConnectionError, asyncio.TimeoutError):
            pass
        finally:
            self.listeners.discard(queue)
        with contextlib.suppress(ConnectionError):
            await response.write_eof()
        return response
