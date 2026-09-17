"""Build an elapsed HH:MM:SS.mmm overlay using standard FFmpeg filters."""


def timestamp_filter():
    # Seven-segment digits use drawbox, available without font libraries.
    segments = ('abcdef', 'bc', 'abdeg', 'abcdg', 'bcfg',
                'acdfg', 'acdefg', 'abc', 'abcdefg', 'abcdfg')
    boxes = {
        'a': (0, 0, 3, 1), 'b': (2, 0, 1, 4),
        'c': (2, 3, 1, 4), 'd': (0, 6, 3, 1),
        'e': (0, 3, 1, 4), 'f': (0, 0, 1, 4),
        'g': (0, 3, 3, 1),
    }
    # Each entry is a digit divisor/modulus and its horizontal position.
    digits = [(36000000, 10, 0), (3600000, 10, 4),
              (600000, 6, 10), (60000, 10, 14),
              (10000, 6, 20), (1000, 10, 24),
              (100, 10, 30), (10, 10, 34), (1, 10, 38)]
    filters = ['drawbox=x=8:y=8:w=iw*0.55:h=ih/9+12:color=black@0.8:t=fill']

    def box(x, y, width, height):
        return (f'drawbox=x=16+({x})*ih/63:y=16+({y})*ih/63:'
                f'w=({width})*ih/63:h=({height})*ih/63:color=white:t=fill')

    for divisor, modulus, position in digits:
        value = f'mod(floor(t*1000/{divisor}),{modulus})'
        for segment, (x, y, width, height) in boxes.items():
            enabled = '+'.join(f'eq({value},{digit})' for digit in range(10)
                               if segment in segments[digit])
            filters.append(box(position + x, y, width, height) + f":enable='{enabled}'")
    for x in (8, 18):
        for y in (2, 5):
            filters.append(box(x, y, 1, 1))
    filters.append(box(28, 6, 1, 1))
    return ','.join(filters)


def drawtext_filter():
    return (r"drawtext=text='%{pts\:hms}':fontsize=h/12:fontcolor=white:"
            r"box=1:boxcolor=black@0.75:boxborderw=8:x=16:y=16")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--drawtext', action='store_true')
    args = parser.parse_args()
    print(drawtext_filter() if args.drawtext else timestamp_filter())
