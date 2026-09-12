# Sage public board gateway

The static website embeds the live board at `https://grunty.tail197337.ts.net/`.
`board_gateway.py` serves that board from Sage's existing `:9300` renderer on
loopback `:19301`. It exposes only live, read-only events and explicit assets.
It does not expose the local server's archives or control APIs.

## Live audio

Harry authorized public conversation audio, including Sage, on 2026-09-09.
The bot's `GodBot/public_audio.py` taps received Discord PCM and actual outgoing
Sage playback. The local, ignored `GodBot/data/public_audio.json` enables one
explicit `channel_id`; missing/disabled config sends nothing. Set `enabled` to
`false` to stop sharing without a bot restart. The gateway also requires that
room to match the live board's `active_voice_channel_id` and a fresh receiver
heartbeat. Other rooms, replays and historical audio are not streamed.

One gateway-owned `board_audio.py` worker receives loopback UDP on `:19302`,
mixes speakers and serves 24 kHz mono, 64 kbps MP3 on loopback `:19303`.
Audio is never written to disk. No listener means no retained audio. Queues
are bounded; slow listeners disconnect instead of accumulating old speech.
The worker exits when its gateway parent exits. Use Sage's existing Python3.13
venv, which supplies aiohttp, PyAV, numpy, psutil and audioop-lts.

Tailscale Funnel443 has two handlers:

- `/` -> `http://127.0.0.1:19301`
- `/audio.mp3` -> `http://127.0.0.1:19303/audio.mp3`

Keep audio in its own process AND on its direct route. Large board timing
snapshots hold Python's GIL; even a separate audio thread delivered only
3-6 seconds of sound per8-10 seconds. The isolated process delivered7.968
seconds per8.011 seconds through public HTTPS during verification.

The bridge also protects remote reconnects from old Viz sources that emit
large timing/transcript sidecars before the Phoenix ledger: it holds that
finite prefix until `history_end` and negotiates WebSocket compression. This
lets the board reveal before secondary state instead of looking blank while a
multi-MiB talk-time history crosses the public link.

`tailscale serve` switches443 back to tailnet-only even when just adding a
path. Restore `tailscale funnel --bg --https=443 --yes http://127.0.0.1:19301`
after a Serve change, and inspect Funnel status to retain both handlers.

Start/restart only the gateway to pick up website/audio-service changes.
Bot tap changes need Sage's bot lifecycle, with the repository's live-session
restart check. The2026-09-09 approved bot restart took80 seconds to reach the
receiver because existing TurnClock history took63 seconds to reload.

Check `/status` for `live` and `audio`, then decode a live `/audio.mp3` sample
and compare media duration with wall time. HTTP200 alone is insufficient.
Local `:19303/status` is worker health; it is not exposed by the public route.
Test with Sage's venv: `python -m pytest test_board_audio.py test_board_gateway.py test_site.py -q`.
`build_site.py` packages only the static website; gateway assets `listen.js`
and `listen.css` stay on this local gateway, so static Site redeployment is
unnecessary for this audio feature.
