# Gear 360 Attendance Gateway

Windows 11 gateway for Samsung Gear 360 2017 (SM-R210) using the SD-card mod and its Wi-Fi live-preview endpoint.

## Camera already verified by the owner

- Firmware: `R210GLU0ARB2`
- Camera AP: `Gear 360(02F4)`
- Camera IP: `192.168.43.1`
- Live-preview endpoint: `http://192.168.43.1:7679/livestream_high.avi`
- Observed response: `206 Partial Content`, `Content-Type: video/x-avi`
- TCP port 7679: listening
- SD mod HTTP/Telnet/FTP: working

## What this version implements

1. Connects to the camera live-preview HTTP endpoint.
2. Parses the public Gear360App chunk structure (`VID0`, `movi`, `00VD`, etc.).
3. Extracts stream codec/dimensions from the `VID0` header.
4. Sends extracted encoded access units to local FFmpeg (`hevc` when codec=1, otherwise `h264`).
5. Publishes decoded BGR frames through an OpenCV-backed MJPEG endpoint.
6. Provides a status API and a simple monitoring page.

The parser/stream design is based on the public Gear360App extractor, which uses `livestream_high.avi` and a custom extractor rather than a normal AVI demuxer.

## Run on Windows 11

1. Install FFmpeg and make sure `ffmpeg.exe` is available in PATH.
2. Put the PC on the Gear 360 Wi-Fi network.
3. Edit `config.json` only if the camera IP/port differ.
4. Run:

```bat
run_gateway.bat
```

5. Open:

`http://127.0.0.1:8765/`

## Next layer: attendance AI

Once `/mjpeg` is receiving frames, the next module can be added without changing camera transport:

`person detector -> face detector -> face embeddings -> identity matching -> attendance events`

The AI layer is intentionally disabled in `config.json` until the camera frame pipeline is active.
