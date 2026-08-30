#!/usr/bin/env python
"""
make_countdown.py -- generate the replay recorder's soundtrack.

    python tools/make_countdown.py

WHAT THIS IS. An ORIGINAL piece, composed here and synthesised from scratch:
a thirty-second arena-rock fanfare that builds and loops seamlessly, meant to
sit under a strategy replay. Every note, the arrangement and the synthesis are
in this file, so the provenance of the audio is the provenance of this script.

WHAT IT IS NOT. Not a cover, not an arrangement, and not an approximation of
any particular record. The distinction is the reason the file exists: the
obvious pick for a countdown-shaped replay is a 1980s stadium single that is
still in copyright, and a recording with it embedded is a file you cannot
share. "Similar but altered" is not a route around that -- it is the
definition of a derivative work -- so this does not chase a specific song.

IT DOES CHASE THE GENRE, which is a different thing and belongs to nobody. The
traits below are era conventions, shared across hundreds of records of the
period, and they are what make something sound like an 80s arena fanfare:

    * SYNTH BRASS as the lead voice -- stacked detuned saws with a bright
      attack that dulls as the note sustains, which is what a filter envelope
      on an analogue polysynth does and is the single biggest cue.
    * FIFTHS AND OCTAVES under the melody rather than full triads, so the lead
      reads as a horn section rather than a keyboard part.
    * GATED REVERB on the snare: a big room switched off mid-decay. The most
      dated sound of the decade and the most recognisable.
    * A BUILD. Sixteen bars, not eight: the first half states the tune with a
      thin kit, the second half restates it with the lead stacked and the kit
      full, then a tom fill throws it back to the top.

THE MUSIC, stated plainly so it can be argued with:

    key         A minor
    tempo       124 BPM, 4/4
    form        sixteen bars, looping -- 8 stated, 8 restated bigger
    harmony     | Am | F | C | G |  four times: i, VI, III, VII. The most
                ordinary loop in popular music and specific to nobody.
    melody      a rising arpeggio answered by a falling one, restated an
                octave up with a tighter rhythm -- the rhythmic tightening is
                what gives it the sense of counting toward something

SEAMLESS BY CONSTRUCTION. Notes are rendered past the end of the loop and the
overhang is added back onto the start, so the release tails of the last bar are
already sounding when the loop restarts. A crossfade would dip the level twice
a bar; this does not dip at all.
"""

import argparse
import os
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SR = 44100
BPM = 124.0
BEAT = 60.0 / BPM
BARS = 16
BEATS = BARS * 4
LOOP_S = BEATS * BEAT
TAIL_S = 1.4                     # rendered past the end, then wrapped

A4 = 69


def hz(midi):
    return 440.0 * (2.0 ** ((midi - A4) / 12.0))


def env(n, attack, decay, sustain, release, hold):
    """ADSR over `n` samples, with `hold` seconds between decay and release."""
    a, d = max(1, int(attack * SR)), max(1, int(decay * SR))
    h, r = max(0, int(hold * SR)), max(1, int(release * SR))
    out = np.zeros(n)
    i = 0
    for seg, lo, hi in ((a, 0.0, 1.0), (d, 1.0, sustain),
                        (h, sustain, sustain), (r, sustain, 0.0)):
        take = min(seg, n - i)
        if take > 0:
            out[i:i + take] = np.linspace(lo, hi, take, endpoint=False)
            i += take
    return out


def saw(freq, n, harmonics, detune_cents=0.0, phase=0.0):
    """Additive saw, harmonics capped explicitly.

    The cap is the tone control: many harmonics is a bright, buzzy brass and
    few is a soft one, so crossfading between two renders of the same note
    imitates a filter closing over the note without an actual filter. It is
    not a real lowpass -- it cannot resonate -- but the audible part of a
    brass patch's attack is the brightness sweep, and this has that.
    """
    f = freq * (2.0 ** (detune_cents / 1200.0))
    t = np.arange(n) / SR
    out = np.zeros(n)
    for k in range(1, harmonics + 1):
        if f * k >= SR / 2.2:
            break
        out += np.sin(2 * np.pi * f * k * t + phase * k) / k
    return out * (2.0 / np.pi)


def brass(freq, n, dur):
    """One synth-brass note: three detuned saws, bright on attack, dulling.

    THE DETUNE IS THE SOUND. A single saw is thin and obviously digital; three
    a few cents apart beat against each other and thicken into something with
    a section's width. Seven cents is about the widest that still reads as one
    instrument rather than as tuning drift.
    """
    bright = (saw(freq, n, 22, -7.0) + saw(freq, n, 22, 0.0, 0.7)
              + saw(freq, n, 22, 7.0, 1.9)) / 3.0
    dull = (saw(freq, n, 5, -7.0) + saw(freq, n, 5, 0.0, 0.7)
            + saw(freq, n, 5, 7.0, 1.9)) / 3.0
    t = np.arange(n) / SR
    open_amt = np.exp(-t * 9.0)                      # the filter closing
    sig = dull + (bright - dull) * open_amt[:, None].ravel()
    if dur > 0.55:                                   # vibrato where it is heard
        sig = sig * (1.0 + 0.004 * np.sin(2 * np.pi * 5.4 * np.maximum(0, t - 0.28)))
    return sig * env(n, 0.014, 0.07, 0.74, 0.26, max(0.0, dur - 0.084))


def square(freq, n, harmonics=18):
    t = np.arange(n) / SR
    out = np.zeros(n)
    for k in range(1, harmonics + 1, 2):
        if freq * k >= SR / 2.2:
            break
        out += np.sin(2 * np.pi * freq * k * t) / k
    return out * (4.0 / np.pi) * 0.5


def sine(freq, n):
    return np.sin(2 * np.pi * freq * np.arange(n) / SR)


def add(buf, start_s, sig):
    i = int(start_s * SR)
    j = min(len(buf), i + len(sig))
    if j > i:
        buf[i:j] += sig[:j - i]


# --------------------------------------------------------------------------
# the piece
# --------------------------------------------------------------------------

# (beat, beats long, midi), over eight bars. Rendered twice: bars 1-8 plain,
# bars 9-16 with the lead stacked in fifths and octaves and the kit full.
LEAD = [
    # bar 1 -- Am, rising
    (0.0, 0.5, 69), (0.5, 0.5, 72), (1.0, 1.0, 76), (2.0, 0.5, 81), (2.5, 1.5, 76),
    # bar 2 -- F, falling answer
    (4.0, 0.5, 77), (4.5, 0.5, 76), (5.0, 1.0, 72), (6.0, 2.0, 69),
    # bar 3 -- C, rising from lower
    (8.0, 0.5, 67), (8.5, 0.5, 72), (9.0, 1.0, 76), (10.0, 0.5, 79), (10.5, 1.5, 76),
    # bar 4 -- G, the turn
    (12.0, 0.5, 74), (12.5, 0.5, 71), (13.0, 1.0, 67), (14.0, 2.0, 74),
    # bar 5 -- Am an octave up, tighter
    (16.0, 0.5, 81), (16.5, 0.5, 84), (17.0, 1.0, 88), (18.0, 0.5, 84), (18.5, 1.5, 81),
    # bar 6 -- F
    (20.0, 0.5, 84), (20.5, 0.5, 81), (21.0, 1.0, 77), (22.0, 2.0, 81),
    # bar 7 -- C
    (24.0, 0.5, 79), (24.5, 0.5, 84), (25.0, 1.0, 88), (26.0, 0.5, 84), (26.5, 1.5, 79),
    # bar 8 -- G, landing on A for the turnaround
    (28.0, 0.5, 86), (28.5, 0.5, 83), (29.0, 1.0, 79), (30.0, 2.0, 81),
]

# root, then the FIFTH stack the pad plays. Fifths not triads: a full triad
# under a brass line muddies it, and the open fifth is the rock voicing.
CHORDS = [
    (45, (57, 64, 69)),      # Am : A2 | A3 E4 A4
    (41, (53, 60, 65)),      # F  : F2 | F3 C4 F4
    (48, (55, 62, 67)),      # C  : C3 | G3 D4 G4
    (43, (55, 62, 67)),      # G  : G2 | G3 D4 G4
]


def gated_snare(rng, room_ms=180, gate_ms=115):
    """A big room switched off mid-decay.

    The gate is the point. A real plate would ring on for a second; slamming it
    shut at 115 ms leaves the size of the room but none of the tail, which is
    the sound the decade is remembered for. Done by multiplying the convolved
    signal by a hard window -- which is literally what the studio trick was.
    """
    ln = int(0.22 * SR)
    t = np.arange(ln) / SR
    hit = (rng.standard_normal(ln) * 0.75
           + np.sin(2 * np.pi * 190.0 * t) * 0.35) * np.exp(-t * 26.0)

    rn = int(room_ms / 1000.0 * SR)
    rt = np.arange(rn) / SR
    room = rng.standard_normal(rn) * np.exp(-rt * 20.0)
    wet = np.convolve(hit, room)[:rn + ln] * 0.05

    gate = np.ones(len(wet))
    cut = int(gate_ms / 1000.0 * SR)
    fall = int(0.006 * SR)                       # not a click: 6 ms of taper
    gate[cut:cut + fall] = np.linspace(1, 0, fall)
    gate[cut + fall:] = 0.0

    out = np.zeros(len(wet))
    out[:ln] += hit
    return (out + wet) * gate


def render():
    n = int((LOOP_S + TAIL_S) * SR)
    lead = np.zeros(n)
    bass = np.zeros(n)
    pad = np.zeros(n)
    drums = np.zeros(n)
    rng = np.random.default_rng(7)

    # ---- lead: stated, then restated as a horn stack ----------------------
    for half in (0, 1):
        off = half * 32 * BEAT
        for beat, length, midi in LEAD:
            dur = length * BEAT
            ln = int((dur + 0.34) * SR)
            if half == 0:
                voices, gain = [(midi, 1.0)], 0.30
            else:
                # root, fifth below, octave below: a section, not a keyboard
                voices, gain = [(midi, 1.0), (midi - 5, 0.55), (midi - 12, 0.62)], 0.26
            sig = np.zeros(ln)
            for m, v in voices:
                sig += brass(hz(m), ln, dur) * v
            add(lead, off + beat * BEAT, sig * gain)

    # ---- bass: straight eighths, octave lift late in the bar ---------------
    for bar in range(BARS):
        root = CHORDS[bar % 4][0]
        for e in range(8):
            beat = bar * 4 + e * 0.5
            midi = root + (12 if e in (5, 7) else 0)
            dur = 0.42 * BEAT
            ln = int((dur + 0.12) * SR)
            sig = square(hz(midi), ln)
            sig *= env(ln, 0.004, 0.05, 0.85, 0.09, max(0.0, dur - 0.054))
            add(bass, beat * BEAT, sig * (0.30 if bar < 8 else 0.36))

    # ---- pad: open fifths, held ------------------------------------------
    for bar in range(BARS):
        stack = CHORDS[bar % 4][1]
        dur = 4 * BEAT
        ln = int((dur + 0.5) * SR)
        sig = np.zeros(ln)
        for midi in stack:
            sig += saw(hz(midi), ln, 6, -4.0) + saw(hz(midi), ln, 6, 4.0, 1.1)
        sig /= len(stack) * 2.4
        sig *= env(ln, 0.20, 0.42, 0.60, 0.45, max(0.0, dur - 0.60))
        add(pad, bar * 4 * BEAT, sig * (0.13 if bar < 8 else 0.19))

    # ---- kit: thin for eight bars, full for eight -------------------------
    for bar in range(BARS):
        b0 = bar * 4
        big = bar >= 8
        kicks = (0.0, 2.0) if not big else (0.0, 2.0, 2.75, 3.5)
        for k in kicks:
            ln = int(0.17 * SR)
            t = np.arange(ln) / SR
            f = 122.0 * np.exp(-t * 26.0) + 44.0
            sig = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 16.0)
            add(drums, (b0 + k) * BEAT, sig * (0.50 if not big else 0.62))
        for s in (1.0, 3.0):
            sig = gated_snare(rng) if big else (
                lambda ln=int(0.18 * SR): (rng.standard_normal(ln)
                                           * np.exp(-np.arange(ln) / SR * 26.0)))()
            add(drums, (b0 + s) * BEAT, sig * (0.30 if big else 0.20))
        for e in range(8):
            ln = int(0.055 * SR)
            t = np.arange(ln) / SR
            sig = rng.standard_normal(ln) * np.exp(-t * 90.0)
            loud = (0.11 if e % 2 == 0 else 0.06) * (1.25 if big else 1.0)
            add(drums, (b0 + e * 0.5) * BEAT, sig * loud)

    # ---- the fill that throws it back to the top --------------------------
    # Descending toms across the last two beats of bar 16. It is the reason the
    # loop does not merely repeat: something happens at the seam.
    for k, pitch in enumerate((196.0, 165.0, 147.0, 123.0, 110.0, 98.0)):
        ln = int(0.20 * SR)
        t = np.arange(ln) / SR
        f = pitch * (1.0 + 0.5 * np.exp(-t * 24.0))
        sig = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 13.0)
        sig += rng.standard_normal(ln) * np.exp(-t * 60.0) * 0.09
        add(drums, (BEATS - 2 + k * (2.0 / 6.0)) * BEAT, sig * 0.34)

    mix = lead + bass + pad + drums

    # ---- wrap the overhang back onto the start ----------------------------
    loop_n = int(LOOP_S * SR)
    tail = mix[loop_n:]
    out = mix[:loop_n].copy()
    m = min(len(tail), loop_n)
    out[:m] += tail[:m]

    out = np.tanh(out * 1.18)
    peak = float(np.max(np.abs(out))) or 1.0
    out *= 0.89 / peak
    return out


def write_wav(path, mono):
    """16-bit stereo, very slightly widened. Some players treat a mono file as
    a fault to be corrected, and a hair of width suits the brass stack."""
    delayed = np.concatenate([np.zeros(int(0.007 * SR)), mono])[:len(mono)]
    stereo = np.empty(len(mono) * 2)
    stereo[0::2] = mono * 0.96 + delayed * 0.04
    stereo[1::2] = delayed * 0.96 + mono * 0.04
    data = (np.clip(stereo, -1.0, 1.0) * 32767.0).astype('<i2').tobytes()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, 'w') as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'data', 'audio', 'countdown-fanfare.wav'))
    args = ap.parse_args()
    write_wav(args.out, render())
    print('wrote %s  (%.1f s, %.1f MB)'
          % (args.out, LOOP_S, os.path.getsize(args.out) / 1048576.0))
    print('original composition -- see the module docstring for the provenance')


if __name__ == '__main__':
    main()
