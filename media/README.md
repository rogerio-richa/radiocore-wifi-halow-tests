# media

Place the source videos for `stream.sh` here. Everything in this folder except
this file is ignored by Git, so large video files never enter the repository.

| File | Used by |
|---|---|
| `kitties.mp4` | default input of `./stream.sh` (the tests used a 1920x1080 H.264 MP4) |
| `kitties-360p.mp4` | the Raspberry Pi service in `deploy/rc32-stream.service` (a 640x360 copy, so the Pi does not have to downscale) |

Any MP4 works; pass a different path as the positional argument to `stream.sh`
or set `RC32_VIDEO_PATH`.
