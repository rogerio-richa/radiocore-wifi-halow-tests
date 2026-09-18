"""Build a wall-clock HH:MM:SS overlay using standard FFmpeg filters.

Every frame is stamped with the encoder host's local time of day at the moment
it passes the filter, so a viewer can compare the burned clock with the same
host's clock shown beside the player and read the one-way delay directly.
"""

import datetime


def local_midnight_epoch_us(now=None):
    """Return local midnight of the given moment as microseconds since the epoch."""
    now = now or datetime.datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(midnight.timestamp() * 1_000_000)


def timestamp_filter(fps):
    """Seven-segment wall clock for FFmpeg builds without the drawtext filter.

    The frame timestamp is temporarily replaced by the wall-clock time since
    local midnight so the digit expressions can read it through ``t``; the
    output timestamps are then regenerated as a constant-rate sequence so the
    encoder never sees the wall-clock jitter.
    """
    segments = ('abcdef', 'bc', 'abdeg', 'abcdg', 'bcfg',
                'acdfg', 'acdefg', 'abc', 'abcdefg', 'abcdfg')
    boxes = {
        'a': (0, 0, 3, 1), 'b': (2, 0, 1, 4),
        'c': (2, 3, 1, 4), 'd': (0, 6, 3, 1),
        'e': (0, 3, 1, 4), 'f': (0, 0, 1, 4),
        'g': (0, 3, 3, 1),
    }
    # Each entry is a digit divisor/modulus (in seconds) and its horizontal position.
    digits = [(36000, 10, 0), (3600, 10, 4),
              (600, 6, 10), (60, 10, 14),
              (10, 6, 20), (1, 10, 24)]
    filters = [
        'settb=1/1000000',
        f'setpts=RTCTIME-{local_midnight_epoch_us()}',
        'drawbox=x=8:y=8:w=ih*0.52:h=ih/9+12:color=black@0.8:t=fill',
    ]

    def box(x, y, width, height):
        return (f'drawbox=x=16+({x})*ih/63:y=16+({y})*ih/63:'
                f'w=({width})*ih/63:h=({height})*ih/63:color=white:t=fill')

    for divisor, modulus, position in digits:
        value = f'mod(floor(t/{divisor}),{modulus})'
        for segment, (x, y, width, height) in boxes.items():
            enabled = '+'.join(f'eq({value},{digit})' for digit in range(10)
                               if segment in segments[digit])
            filters.append(box(position + x, y, width, height) + f":enable='{enabled}'")
    for x in (8, 18):
        for y in (2, 5):
            filters.append(box(x, y, 1, 1))
    filters.append(f'setpts=N/({fps}*TB)')
    return ','.join(filters)


def drawtext_filter():
    return (r"drawtext=text='%{localtime\:%T}':fontsize=h/12:fontcolor=white:"
            r"box=1:boxcolor=black@0.75:boxborderw=8:x=16:y=16")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--drawtext', action='store_true')
    parser.add_argument('--fps', type=int, default=15)
    args = parser.parse_args()
    print(drawtext_filter() if args.drawtext else timestamp_filter(args.fps))
