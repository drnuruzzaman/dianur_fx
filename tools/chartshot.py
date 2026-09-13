"""chartshot.py — the ticket, drawn on the bars it was read from.

WHY A PICTURE. A signal message is five prices and a side. The prices say what
to do and say nothing about what the rule is looking at, so the reader either
takes it on trust or goes and opens the chart -- and a message that requires
opening the chart has not saved anybody anything. The image carries the one
thing the numbers cannot: whether the level being broken is a real edge of
recent structure or a line through noise.

WHAT IT DRAWS, and nothing else. The rule's own inputs: candles, EMA(20) and
EMA(50) whose cross is the trend gate, the 20-bar swing high and low the break
is measured from, and the five levels of the ticket. No extra indicators. A
chart that shows something the rule does not use invites reading a confirmation
into it that the measurement never included.

THE DECISION BAR IS MARKED because it is not the last bar on the chart. The
rule decides on the last CLOSED bar and the forming one is still moving; the
marker is what stops the reader measuring the setup from the wrong candle.

MATPLOTLIB, NOT mplfinance. The candles here are a dozen lines of Rectangle and
vlines, and the alternative is a dependency on the scheduled-task path -- which
runs headless under pythonw with no console to print an ImportError to.

HEADLESS BY CONSTRUCTION. The Agg backend is selected before pyplot is
imported: the scheduled task has no display, and matplotlib picking an
interactive backend there fails at import time, inside a process whose stdout
is already redirected to a log file.
"""
import io as _io
import os
import sys

import matplotlib
matplotlib.use('Agg')                                        # before pyplot
import matplotlib.dates as mdates                            # noqa: E402
import matplotlib.pyplot as plt                              # noqa: E402
import numpy as np                                           # noqa: E402
from matplotlib.patches import Rectangle                     # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

#: Dark, because every destination this reaches is read on a phone, usually in
#: a dark chat. A white 800x500 rectangle in a dark thread is a flashbang.
BG = '#0e1420'
FG = '#c9d4e4'
GRID = '#1e2735'
UP = '#26a17b'
DOWN = '#e0574c'
ENTRY = '#4da3ff'
STOP = '#e0574c'
TP = '#26a17b'
DIM = '#6c7a90'

#: THE BRAND MARK, the same five squares index.html draws as an inline SVG and
#: the same palette css/app.css declares. Copied as values rather than parsed
#: out of the stylesheet: this runs headless from a scheduled task with no DOM,
#: and a logo that silently stops matching the app is worse than one that is
#: obviously hand-kept. If the app's mark changes, change these five.
NAVY, PINK, GREEN, ORANGE, GREY = (
    '#171C8F', '#E31C79', '#93C90F', '#FF9E1B', '#B1B3B3')

#: Shown on every image. Signals go to a public Telegram channel, so the one
#: sentence that has to survive a forward sits on the picture itself -- a
#: caption can be cropped away, a screenshot of the chart cannot.
#: The twin of RISK_TEXT in js/util.js. Two copies is the cost of the
#: language boundary; keep them identical.
RISK_TEXT = 'Trading is risky, Do not over trade, You might lose your funds.'


def _brand(fig, right=0.988, y=0.955, sq=0.017, fs=10):
    """The DiaNurFx mark and wordmark, top-right, in FIGURE coordinates.

    FIGURE coordinates, not axes: the right-hand edge of the axes is where the
    level captions live, and anything anchored there would land on top of SL or
    a TP the moment the ticket's levels moved.

    THE WIDTHS ARE MEASURED, NOT GUESSED. The group reads mark-then-wordmark,
    right-aligned, and laying it out by eye put half of "FX" off the canvas --
    a logo is the one thing on the image that must not look broken. Each text
    is drawn, measured through the renderer, and the next element placed from
    where it actually landed.
    """
    fig.canvas.draw()                       # so extents are real
    r = fig.canvas.get_renderer()
    W, H = fig.get_size_inches() * fig.dpi

    fx = fig.text(right, y, 'FX', color=PINK, fontsize=fs, fontweight='bold',
                  ha='right', va='center', zorder=10)
    x_fx = fx.get_window_extent(renderer=r).x0 / W
    nur = fig.text(x_fx, y, 'DIANUR', color=FG, fontsize=fs, fontweight='bold',
                   ha='right', va='center', zorder=10)
    x_nur = nur.get_window_extent(renderer=r).x0 / W

    ar = (fig.get_size_inches()[0] / fig.get_size_inches()[1])
    sx, sy = sq / ar, sq             # squares must be square, and x/y are not
    gx, gy = sx * 0.28, sy * 0.28
    x0 = x_nur - 0.010 - (2 * sx + gx)
    y0 = y - (2 * sy + gy) / 2
    for dx, dy, col in ((0, 1, NAVY), (1, 1, ORANGE),
                        (0, 0, GREEN), (1, 0, PINK)):
        fig.add_artist(Rectangle((x0 + dx * (sx + gx), y0 + dy * (sy + gy)),
                                 sx, sy, facecolor=col, edgecolor='none',
                                 transform=fig.transFigure, zorder=10))
    fig.add_artist(Rectangle((x0 + (sx + gx) / 2, y0 + (sy + gy) / 2), sx, sy,
                             facecolor=GREY, edgecolor='none',
                             transform=fig.transFigure, zorder=11))


def _ema(v, n):
    k = 2.0 / (n + 1.0)
    out = np.empty(len(v), float)
    out[0] = v[0]
    for i in range(1, len(v)):
        out[i] = v[i] * k + out[i - 1] * (1 - k)
    return out


def render(df, ticket, symbol, tf, digits=2, bars=90, expected=None,
           path=None):
    """PNG bytes for one ticket. `df` is OHLC indexed by time, forming bar
    already dropped -- exactly what tools/scalper.py hands the rule.

    Returns the bytes, and also writes `path` when given.
    """
    d = df.iloc[-bars:] if len(df) > bars else df
    if len(d) < 5:
        raise ValueError('not enough bars to draw')

    x = mdates.date2num(d.index.to_pydatetime())
    o = d['open'].to_numpy(float)
    h = d['high'].to_numpy(float)
    lo_ = d['low'].to_numpy(float)
    c = d['close'].to_numpy(float)
    # the EMAs are the rule's, so they are computed over the WHOLE frame and
    # then sliced -- seeded on 90 bars they would not be the same lines
    ef = _ema(df['close'].to_numpy(float), 20)[-len(d):]
    es = _ema(df['close'].to_numpy(float), 50)[-len(d):]

    step = (x[1] - x[0]) if len(x) > 1 else 1.0
    w = step * 0.62

    fig, ax = plt.subplots(figsize=(9.2, 5.2), dpi=110)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    for i in range(len(d)):
        up = c[i] >= o[i]
        col = UP if up else DOWN
        ax.vlines(x[i], lo_[i], h[i], color=col, linewidth=0.9, zorder=2)
        y0, hgt = min(o[i], c[i]), abs(c[i] - o[i])
        ax.add_patch(Rectangle((x[i] - w / 2, y0), w, hgt or step * 1e-9,
                               facecolor=col, edgecolor=col, linewidth=0.6,
                               zorder=3))

    ax.plot(x, ef, color='#e8b84b', linewidth=1.1, zorder=4, label='EMA20')
    ax.plot(x, es, color='#8a7fd4', linewidth=1.1, zorder=4, label='EMA50')

    fmt = '%%.%df' % digits
    buy = ticket['side'] == 'buy'
    tps = list(ticket.get('tp') or [])

    # ---- THE VERTICAL RANGE, and why it is not "fit everything". ----
    # TP3 sits 2.4R from the entry, which on a 4 ATR stop is nearly ten ATR
    # away. Letting it set the axis squashed every candle into the top third
    # and the picture stopped showing the one thing it is for: the structure
    # being broken. The frame is fitted to the candles, the entry, the stop and
    # TP1 -- TP1 because it is the rung the measurement actually exits on --
    # and any further rung is drawn at the boundary with an arrow instead.
    must = [np.nanmin(lo_), np.nanmax(h), ticket['entry'], ticket['sl']]
    if tps:
        must.append(tps[0])
    ylo, yhi = float(np.nanmin(must)), float(np.nanmax(must))
    pad = (yhi - ylo) * 0.08 or 1.0
    ylo, yhi = ylo - pad, yhi + pad

    # ---- LABELS THAT DO NOT SIT ON EACH OTHER. ----
    # The entry is a tenth of an ATR from the swing it breaks, so their two
    # captions landed on the same pixel row and read as one unparseable
    # string. Collected, sorted and pushed apart by a minimum gap.
    labels = []

    def level(y, colour, label, style='--', lw=1.1, alpha=1.0):
        if y is None or not np.isfinite(y):
            return
        if ylo <= y <= yhi:
            ax.axhline(y, color=colour, linestyle=style, linewidth=lw,
                       alpha=alpha, zorder=5)
            labels.append([float(y), colour, '%s %s' % (label, fmt % y),
                           1.0, float(y)])
        else:
            # off the frame: say which way and how far, at the edge. The TRUE
            # price is carried as the sort key -- clamping both TP2 and TP3 to
            # the same edge made them compare equal, and the stable sort then
            # stacked TP3 above TP2, which is simply the wrong order.
            edge = yhi if y > yhi else ylo
            arrow = '^' if y > yhi else 'v'
            labels.append([float(edge), colour,
                           '%s %s %s' % (arrow, label, fmt % y), 0.55,
                           float(y)])

    lvl = ticket.get('level')
    if lvl is not None:
        level(lvl, DIM, '20-bar %s' % ('high' if buy else 'low'), ':', 1.0, .8)
    for i, tp in enumerate(tps, start=1):
        level(tp, TP, 'TP%d' % i, '-' if i == 1 else '--',
              1.3 if i == 1 else 0.9, 1.0 if i == 1 else 0.55)
    level(ticket['entry'], ENTRY, 'ENTRY', '-', 1.5)
    level(ticket['sl'], STOP, 'SL', '-', 1.3)

    # ---- SEPARATE THEM, BUT KEEP THEM ON THE CANVAS. ----
    # The upward pass alone pushed a stack of off-frame labels past the top of
    # the axes and straight under the brand mark. When several TPs clamp to the
    # same edge they all need room, and the only place to find it is downward,
    # so an overflow shifts the whole stack back and re-separates from the top.
    gap = (yhi - ylo) * 0.045
    y_min, y_max = ylo + gap * 0.4, yhi - gap * 0.4
    labels.sort(key=lambda r: r[4])          # by TRUE price
    for i in range(1, len(labels)):
        if labels[i][0] - labels[i - 1][0] < gap:
            labels[i][0] = labels[i - 1][0] + gap
    if labels and labels[-1][0] > y_max:
        over = labels[-1][0] - y_max
        for row in labels:
            row[0] -= over
        for i in range(len(labels) - 2, -1, -1):
            if labels[i + 1][0] - labels[i][0] < gap:
                labels[i][0] = labels[i + 1][0] - gap
    for row in labels:                       # last resort: never off the plot
        row[0] = min(max(row[0], y_min), y_max)
    for y, colour, text, alpha, _true in labels:
        ax.annotate(' ' + text, xy=(1.0, y),
                    xycoords=('axes fraction', 'data'), color=colour,
                    fontsize=8.5, va='center', ha='left', zorder=6,
                    alpha=alpha)

    ax.set_ylim(ylo, yhi)

    # the decision bar: the last CLOSED one, which is the last bar drawn
    ax.plot([x[-1]], [c[-1]], marker='o', markersize=5, color=FG, zorder=7)
    ax.annotate('decision bar', xy=(x[-1], c[-1]), xytext=(-8, 14),
                textcoords='offset points', color=DIM, fontsize=7.5,
                ha='right', zorder=7)

    # NO EXPECTATION ON THE IMAGE, by request -- the same call already made on
    # the right-rail panel. `expected` is still accepted and still ignored:
    # callers pass it, and recording the omission here is what stops it being
    # re-added by somebody who assumes it was forgotten. The number lives in
    # configs/alerts.json and in the rayo.py docstring, which is where a
    # measurement belongs; a chart is for reading the price action.
    head = '%s  %s   -   %s %s   -   rayo scalper' % (
        symbol.replace('.a', ''), tf, ticket['side'].upper(),
        ticket['order'].upper())
    ax.set_title(head, color=FG, fontsize=11.5, pad=10, loc='left')

    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b %H:%M'))
    ax.tick_params(colors=DIM, labelsize=8)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.grid(True, color=GRID, linewidth=0.6, alpha=0.7)
    ax.margins(x=0.01)
    # room on the right for the level captions
    ax.set_xlim(x[0] - step, x[-1] + step * 9)
    leg = ax.legend(loc='upper left', fontsize=8, framealpha=0.0)
    for t in leg.get_texts():
        t.set_color(DIM)
    fig.autofmt_xdate(rotation=0, ha='center')

    # AFTER tight_layout: it reflows the axes and would otherwise shift the
    # mark and the warning off the corners they were placed in.
    # right=0.86 reserves the strip the level captions are drawn into;
    # tight_layout used to find it and had to go, because it reflowed the
    # axes after the mark was placed and shifted it off the corner.
    fig.subplots_adjust(left=0.075, right=0.86, top=0.90, bottom=0.13)
    _brand(fig)
    fig.text(0.5, 0.028, RISK_TEXT, color=DIM, fontsize=8.5,
             ha='center', va='center', zorder=10)

    buf = _io.BytesIO()
    fig.savefig(buf, format='png', facecolor=BG)
    plt.close(fig)
    png = buf.getvalue()
    if path:
        with open(path, 'wb') as fh:
            fh.write(png)
    return png
