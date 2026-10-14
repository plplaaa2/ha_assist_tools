"""Build a bounded alarm from the source MP3; related files: alarm_sounds/README.

This developer-only tool is not imported by Home Assistant. FFmpeg repeats the
source audio and trims decoded audio to 60 seconds without changing the original.
"""

import subprocess
from pathlib import Path

from imageio_ffmpeg import get_ffmpeg_exe


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    sounds = root / "custom_components" / "assist_timers" / "alarm_sounds"
    subprocess.run(
        [
            get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            str(sounds / "beep-alarm-366507.mp3"),
            "-t",
            "60",
            "-map_metadata",
            "-1",
            "-ac",
            "1",
            "-ar",
            "44100",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(sounds / "alarm_60s.mp3"),
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
