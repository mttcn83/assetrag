"""Round-3 recipes, part A (Python stdlib, `unittest`): audio/music, bioinformatics, string distance, geospatial."""
from .recipes import RC, UT

R3A = [
RC("py-audio-synth-wav-adsr", "Synthesise audio: MIDI->frequency, sine/square tones, ADSR envelope, 16-bit WAV writer, peak normalise, resample", "python",
   "Generate or process sound without numpy: tones for a game, a metronome, test signals, or a tiny synth.",
   "Equal-temperament frequency from MIDI note, sample generation as floats in [-1, 1], linear ADSR envelope with exact sample counts, mixing with clipping protection, peak normalisation, linear-interpolation resampling, and `wave`-module WAV writing/reading with clipping to int16 and a round-trip test.",
   {"synth.py": '''"""Tiny audio toolkit. Samples are floats in [-1, 1]; files are 16-bit mono PCM WAV."""
import math, struct, wave

RATE = 44100


def midi_to_freq(note, a4=440.0):
    return a4 * 2 ** ((note - 69) / 12)


def tone(freq, seconds, rate=RATE, shape="sine", amp=1.0):
    n = round(seconds * rate)
    out = []
    for i in range(n):
        ph = (i * freq / rate) % 1.0
        out.append(amp * (math.sin(2 * math.pi * ph) if shape == "sine" else (1.0 if ph < 0.5 else -1.0) if shape == "square" else 4 * abs(ph - 0.5) - 1 if shape == "triangle" else 2 * ph - 1))
    return out


def adsr(n, attack, decay, sustain, release, rate=RATE):
    """Envelope of exactly n samples. Times in seconds, sustain is a level 0..1. Shrinks gracefully on very short notes."""
    a, d, r = int(attack * rate), int(decay * rate), int(release * rate)
    if a + d + r > n:
        k = n / max(1, a + d + r); a, d, r = int(a * k), int(d * k), int(r * k)
    env = [i / a for i in range(a)] + [1 - (1 - sustain) * i / d for i in range(d)]
    env += [sustain] * (n - len(env) - r)
    env += [sustain * (1 - i / r) for i in range(r)]
    return (env + [0.0] * n)[:n]


def apply(samples, env):
    return [s * e for s, e in zip(samples, env)]


def mix(*tracks, headroom=1.0):
    n = max(len(t) for t in tracks)
    out = [sum(t[i] for t in tracks if i < len(t)) for i in range(n)]
    peak = max((abs(x) for x in out), default=0)
    return [x * headroom / peak for x in out] if peak > headroom else out


def normalise(samples, peak=0.95):
    m = max((abs(x) for x in samples), default=0)
    return list(samples) if m == 0 else [x * peak / m for x in samples]


def resample(samples, src_rate, dst_rate):
    if src_rate == dst_rate or not samples: return list(samples)
    n = round(len(samples) * dst_rate / src_rate); out = []
    for i in range(n):
        pos = i * src_rate / dst_rate; j = int(pos); frac = pos - j
        nxt = samples[min(j + 1, len(samples) - 1)]
        out.append(samples[min(j, len(samples) - 1)] * (1 - frac) + nxt * frac)
    return out


def write_wav(path, samples, rate=RATE):
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", max(-32768, min(32767, round(s * 32767)))) for s in samples))


def read_wav(path):
    with wave.open(path, "rb") as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2, "only 16-bit mono supported"
        raw = w.readframes(w.getnframes())
        return [v / 32767 for v in struct.unpack(f"<{len(raw) // 2}h", raw)], w.getframerate()
'''
    , "test_synth.py": '''import unittest, os, tempfile, math
import synth as S


class T(unittest.TestCase):
    def test_equal_temperament(self):
        self.assertAlmostEqual(S.midi_to_freq(69), 440.0); self.assertAlmostEqual(S.midi_to_freq(81), 880.0); self.assertAlmostEqual(S.midi_to_freq(60), 261.6256, places=3)

    def test_tone_length_and_range_and_period(self):
        t = S.tone(441, 0.1, 44100); self.assertEqual(len(t), 4410); self.assertTrue(all(-1 <= x <= 1 for x in t))
        self.assertAlmostEqual(t[0], 0.0); self.assertAlmostEqual(t[100], t[0], places=6)               # 441 Hz at 44.1k => period exactly 100 samples
        for shape in ("square", "triangle", "saw"): self.assertTrue(all(-1.0001 <= x <= 1.0001 for x in S.tone(300, 0.05, shape=shape)))

    def test_adsr_exact_length_and_shape(self):
        env = S.adsr(1000, 0.1, 0.1, 0.5, 0.2, rate=1000); self.assertEqual(len(env), 1000)
        self.assertEqual(env[0], 0.0); self.assertAlmostEqual(env[99], 0.99); self.assertAlmostEqual(env[200], 0.5, places=2); self.assertAlmostEqual(env[500], 0.5); self.assertLess(env[-1], 0.01)
        self.assertTrue(all(0 <= e <= 1 for e in env))

    def test_adsr_short_note_does_not_overflow(self):
        for n in (1, 5, 50): self.assertEqual(len(S.adsr(n, 0.1, 0.1, 0.5, 0.2, rate=1000)), n)

    def test_mix_never_clips(self):
        m = S.mix([0.9] * 10, [0.9] * 5); self.assertAlmostEqual(max(abs(x) for x in m), 1.0); self.assertEqual(len(m), 10)
        self.assertEqual(S.mix([0.2, 0.2], [0.1]), [0.30000000000000004, 0.2])

    def test_normalise(self):
        n = S.normalise([0.1, -0.2, 0.05]); self.assertAlmostEqual(max(abs(x) for x in n), 0.95); self.assertEqual(S.normalise([0, 0]), [0, 0])

    def test_resample_length_and_pitch_preserving_time(self):
        t = S.tone(100, 1.0, 8000); r = S.resample(t, 8000, 16000); self.assertEqual(len(r), 16000); self.assertAlmostEqual(r[2000], math.sin(2 * math.pi * 100 * 2000 / 16000), places=2)
        self.assertEqual(S.resample([1, 2, 3], 8000, 8000), [1, 2, 3])

    def test_wav_round_trip_and_clipping(self):
        p = os.path.join(tempfile.mkdtemp(), "a.wav"); src = S.tone(440, 0.05, 22050) + [3.0, -3.0]; S.write_wav(p, src, 22050); back, rate = S.read_wav(p)
        self.assertEqual((rate, len(back)), (22050, len(src))); self.assertAlmostEqual(back[10], src[10], places=3); self.assertEqual((back[-2], back[-1]), (1.0, -32768 / 32767))


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Mono 16-bit PCM only; use numpy/soundfile for stereo, float formats, long files", "Linear-interpolation resampling aliases when downsampling: low-pass first for quality"], compat={"python": ">=3.9"},
   tags=["audio", "music", "wav", "synth", "midi", "adsr", "envelope", "sine-wave", "sound", "tone", "normalize", "resample", "sample-rate"],
   avoid="Writing floats straight into WAV frames; adding tracks without checking for clipping; envelopes that click because they do not start and end at zero."),

RC("py-audio-fft-bpm-chords", "Audio analysis: radix-2 FFT spectrum peak, onset-based BPM estimate, chord names from notes", "python",
   "Find the dominant pitch of a signal, estimate tempo from a click/drum track, and name the chord a set of notes forms.",
   "Iterative-safe recursive radix-2 FFT with zero padding, magnitude spectrum with parabolic peak interpolation for sub-bin frequency, energy-envelope onset detection with adaptive threshold and refractory period, BPM from median inter-onset interval (octave-corrected into a sensible range), and chord identification by interval patterns over all roots.",
   {"analyze.py": '''"""Signal analysis in pure Python. Fine for seconds of audio; use numpy.fft / librosa for real workloads."""
import cmath, math, statistics

NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
CHORDS = {(0, 4, 7): "", (0, 3, 7): "m", (0, 3, 6): "dim", (0, 4, 8): "aug", (0, 4, 7, 10): "7", (0, 4, 7, 11): "maj7", (0, 3, 7, 10): "m7", (0, 2, 7): "sus2", (0, 5, 7): "sus4"}


def fft(x):
    n = len(x)
    if n & (n - 1): raise ValueError("length must be a power of two")
    if n == 1: return list(x)
    even, odd = fft(x[0::2]), fft(x[1::2])
    t = [cmath.exp(-2j * math.pi * k / n) * odd[k] for k in range(n // 2)]
    return [even[k] + t[k] for k in range(n // 2)] + [even[k] - t[k] for k in range(n // 2)]


def dominant_frequency(samples, rate):
    n = 1 << (len(samples) - 1).bit_length()
    win = [s * (0.5 - 0.5 * math.cos(2 * math.pi * i / (len(samples) - 1))) for i, s in enumerate(samples)]          # Hann window reduces leakage
    mag = [abs(v) for v in fft(win + [0.0] * (n - len(samples)))[: n // 2]]
    k = max(range(1, len(mag) - 1), key=mag.__getitem__)
    a, b, c = mag[k - 1], mag[k], mag[k + 1]
    delta = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) else 0.0                                              # parabolic interpolation between bins
    return (k + delta) * rate / n


def onsets(samples, rate, frame=128, k=1.5, min_gap=0.15):
    """Times (s) where short-time energy jumps above k * local average; refractory period suppresses double triggers.
    Timing resolution is one frame (128 samples = 16 ms at 8 kHz), which caps tempo accuracy at roughly +-1%."""
    e = [sum(s * s for s in samples[i:i + frame]) for i in range(0, len(samples) - frame, frame)]
    out, last = [], -1e9
    for i, v in enumerate(e):
        local = sum(e[max(0, i - 8):i]) / max(1, len(e[max(0, i - 8):i]))
        t = i * frame / rate
        if v > 1e-9 and v > k * local and t - last >= min_gap and (i == 0 or e[i - 1] < v): out.append(t); last = t
    return out


def bpm(onset_times, lo=70, hi=160):
    if len(onset_times) < 3: return None
    gaps = [b - a for a, b in zip(onset_times, onset_times[1:])]
    v = 60.0 / statistics.median(gaps)
    while v < lo: v *= 2                                  # octave errors: a half-time or double-time estimate is the same groove
    while v > hi: v /= 2
    return v


def chord_name(midi_notes):
    pcs = sorted({n % 12 for n in midi_notes})
    for root in pcs:
        pattern = tuple(sorted((p - root) % 12 for p in pcs))
        if pattern in CHORDS: return NOTES[root] + CHORDS[pattern]
    return None
'''
    , "test_analyze.py": '''import unittest, math, random
import analyze as A


def sine(f, n, rate): return [math.sin(2 * math.pi * f * i / rate) for i in range(n)]


class T(unittest.TestCase):
    def test_fft_matches_definition_on_small_input(self):
        x = [1, 2, 3, 4, 0, 0, 0, 0]
        slow = [sum(x[n] * complex(math.cos(2 * math.pi * k * n / 8), -math.sin(2 * math.pi * k * n / 8)) for n in range(8)) for k in range(8)]
        for a, b in zip(A.fft(x), slow): self.assertAlmostEqual(abs(a - b), 0, places=9)
        with self.assertRaises(ValueError): A.fft([1, 2, 3])

    def test_dominant_frequency_between_bins(self):
        for f in (440.0, 1000.0, 123.4): self.assertAlmostEqual(A.dominant_frequency(sine(f, 4000, 8000), 8000), f, delta=f * 0.01 + 1)

    def test_dominant_frequency_with_noise(self):
        r = random.Random(1); x = [s + r.gauss(0, 0.3) for s in sine(600, 4096, 8000)]; self.assertAlmostEqual(A.dominant_frequency(x, 8000), 600, delta=5)

    def click_track(self, bpm, seconds=8, rate=8000):
        x = [0.0] * (seconds * rate); step = 60 / bpm
        for b in range(int(seconds / step)):
            i = int(b * step * rate)
            for j in range(120):
                if i + j < len(x): x[i + j] = math.exp(-j / 30) * math.sin(j)
        return x

    def test_bpm_from_click_track(self):
        for target in (90, 120, 140):
            x = self.click_track(target); est = A.bpm(A.onsets(x, 8000)); self.assertAlmostEqual(est, target, delta=target * 0.03, msg=target)

    def test_bpm_octave_folding_and_too_few_onsets(self):
        self.assertAlmostEqual(A.bpm([0, 1, 2, 3, 4]), 60 * 2); self.assertIsNone(A.bpm([0.0, 1.0]))

    def test_silence_has_no_onsets(self):
        self.assertEqual(A.onsets([0.0] * 8000, 8000), [])

    def test_chord_names_in_any_voicing(self):
        cases = {(60, 64, 67): "C", (64, 67, 72): "C", (57, 60, 64): "Am", (62, 65, 69, 72): "Dm7", (67, 71, 74, 77): "G7", (60, 63, 66): "Cdim", (60, 65, 67): "Csus4", (61, 65, 68): "C#"}
        for notes, name in cases.items(): self.assertEqual(A.chord_name(notes), name, notes)
        self.assertIsNone(A.chord_name([60, 61, 62]))


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Monophonic, clean-ish signals; polyphonic pitch tracking and robust tempo need dedicated libraries (librosa, aubio)"], compat={"python": ">=3.9"},
   tags=["audio", "fft", "spectrum", "frequency", "pitch", "bpm", "tempo", "beat", "onset", "chord", "music-theory", "dsp", "signal-processing"],
   avoid="FFT on non-power-of-two lengths without padding; peak picking without a window (spectral leakage); reporting half/double tempo as the answer."),

RC("py-fasta-fastq-kmers-codons", "Bioinformatics basics: FASTA/FASTQ parsing, reverse complement, GC content, k-mers, codon translation, quality filtering", "python",
   "Sequence files and the handful of operations every pipeline starts with, written so large files stream instead of loading into memory.",
   "Generator-based FASTA and FASTQ readers (multi-line sequences, blank lines, Windows newlines, validation), IUPAC-aware reverse complement, GC fraction ignoring N, k-mer counting with Counter, codon table translation with stop codons and frame offsets, Phred+33 quality decoding and mean-quality / trimming filters.",
   {"seqio.py": '''"""Sequence I/O and basics. Readers are generators: constant memory for multi-GB files."""
from collections import Counter
from itertools import product

COMP = str.maketrans("ACGTUNRYKMSWBDHVacgtunrykmswbdhv", "TGCAANYRMKSWVHDBtgcaanyrmkswvhdb")
_BASES = "TCAG"
CODONS = {a + b + c: aa for (a, b, c), aa in zip(product(_BASES, repeat=3), "FFLLSSSSYY**CC*WLLLLPPPPHHQQRRRRIIIMTTTTNNKKSSRRVVVVAAAADDEEGGGG")}


def read_fasta(lines):
    """Yield (header, sequence) from an iterable of lines (a file object works)."""
    header, parts = None, []
    for raw in lines:
        line = raw.strip()
        if not line: continue
        if line.startswith(">"):
            if header is not None: yield header, "".join(parts)
            header, parts = line[1:].strip(), []
        elif header is None: raise ValueError("sequence data before first '>' header")
        else: parts.append(line.upper())
    if header is not None: yield header, "".join(parts)


def read_fastq(lines):
    """Yield (name, seq, quality_string). Strict 4-line records."""
    it = (l.rstrip("\\r\\n") for l in lines)
    for head in it:
        if not head: continue
        try: seq, plus, qual = next(it), next(it), next(it)
        except StopIteration: raise ValueError("truncated FASTQ record") from None
        if not head.startswith("@") or not plus.startswith("+"): raise ValueError(f"malformed FASTQ record near {head[:30]!r}")
        if len(seq) != len(qual): raise ValueError(f"sequence/quality length mismatch in {head[1:30]!r}")
        yield head[1:].split()[0], seq.upper(), qual


def revcomp(seq):
    return seq.translate(COMP)[::-1]


def gc_content(seq):
    s = [c for c in seq.upper() if c != "N"]
    return sum(c in "GC" for c in s) / len(s) if s else 0.0


def kmers(seq, k):
    if k < 1: raise ValueError("k must be >= 1")
    return Counter(seq[i:i + k] for i in range(len(seq) - k + 1) if "N" not in seq[i:i + k])


def translate(seq, frame=0, to_stop=False):
    aa = []
    for i in range(frame, len(seq) - 2, 3):
        a = CODONS.get(seq[i:i + 3].upper().replace("U", "T"), "X")
        if a == "*" and to_stop: break
        aa.append(a)
    return "".join(aa)


def phred(qual, offset=33):
    return [ord(c) - offset for c in qual]


def mean_quality(qual):
    q = phred(qual); return sum(q) / len(q) if q else 0.0


def trim_low_quality_tail(seq, qual, min_q=20):
    q = phred(qual); end = len(q)
    while end > 0 and q[end - 1] < min_q: end -= 1
    return seq[:end], qual[:end]


def filter_reads(records, min_mean_q=25, min_len=30):
    for name, seq, qual in records:
        seq, qual = trim_low_quality_tail(seq, qual)
        if len(seq) >= min_len and mean_quality(qual) >= min_mean_q: yield name, seq, qual
'''
    , "test_seqio.py": '''import unittest, io
import seqio as S

FA = """>seq1 first
ACGTACGT
ACGT

>seq2
ggccNN
"""


class T(unittest.TestCase):
    def test_fasta_multiline_blank_lines_case(self):
        self.assertEqual(list(S.read_fasta(io.StringIO(FA))), [("seq1 first", "ACGTACGTACGT"), ("seq2", "GGCCNN")])
        self.assertEqual(list(S.read_fasta(io.StringIO(FA.replace("\\n", "\\r\\n")))), list(S.read_fasta(io.StringIO(FA))))
        with self.assertRaises(ValueError): list(S.read_fasta(["ACGT\\n"]))

    def test_revcomp_and_iupac(self):
        self.assertEqual(S.revcomp("AACGT"), "ACGTT"); self.assertEqual(S.revcomp(S.revcomp("ACGTRYKMN")), "ACGTRYKMN"); self.assertEqual(S.revcomp("acgu"), "acgt")

    def test_gc_content_ignores_n(self):
        self.assertEqual(S.gc_content("GGCC"), 1.0); self.assertEqual(S.gc_content("ATGC"), 0.5); self.assertEqual(S.gc_content("GCNN"), 1.0); self.assertEqual(S.gc_content(""), 0.0)

    def test_kmers(self):
        self.assertEqual(S.kmers("ACGACG", 3), {"ACG": 2, "CGA": 1, "GAC": 1}); self.assertEqual(S.kmers("ACNGT", 2), {"AC": 1, "GT": 1}); self.assertEqual(S.kmers("AC", 5), {})
        with self.assertRaises(ValueError): S.kmers("AC", 0)

    def test_translation_frames_and_stops(self):
        self.assertEqual(S.translate("ATGGCCTAA"), "MA*"); self.assertEqual(S.translate("ATGGCCTAAGGG", to_stop=True), "MA"); self.assertEqual(S.translate("AATGGCC", frame=1), "MA")
        self.assertEqual(S.translate("AUGGCC"), "MA"); self.assertEqual(S.translate("ATGNNN"), "MX"); self.assertEqual(len(S.CODONS), 64); self.assertEqual(sum(1 for a in S.CODONS.values() if a == "*"), 3)

    def test_fastq_and_quality(self):
        fq = "@r1 desc\\nACGT\\n+\\nIIII\\n@r2\\nACGT\\n+\\n!!!!\\n"; recs = list(S.read_fastq(io.StringIO(fq))); self.assertEqual(recs[0], ("r1", "ACGT", "IIII"))
        self.assertEqual(S.phred("I!"), [40, 0]); self.assertAlmostEqual(S.mean_quality("II!!"), 20.0)
        for bad in ("@r\\nACGT\\n+\\nIII\\n", "@r\\nACGT\\n+\\n", "r\\nACGT\\n+\\nIIII\\n"):
            with self.assertRaises(ValueError): list(S.read_fastq(io.StringIO(bad)))

    def test_trim_and_filter(self):
        self.assertEqual(S.trim_low_quality_tail("ACGTAC", "IIII##"), ("ACGT", "IIII"))
        good = ("good", "A" * 40, "I" * 40); bad = ("bad", "A" * 40, "#" * 40); short = ("short", "A" * 10, "I" * 10)
        self.assertEqual([r[0] for r in S.filter_reads([good, bad, short])], ["good"])


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Phred+33 (Sanger/Illumina 1.8+) only", "Biopython is the full-featured choice (formats, alignment, GenBank); this keeps small scripts dependency-free"], compat={"python": ">=3.9"},
   tags=["bioinformatics", "fasta", "fastq", "dna", "sequence", "kmer", "gc-content", "reverse-complement", "codon", "translate", "protein", "genomics", "phred", "quality"],
   avoid="Reading a whole genome file into one string; counting k-mers across N bases; hard-coding Phred offsets without saying which."),

RC("py-edit-distance-fuzzy-match", "Edit distance (Levenshtein, Damerau), similarity ratio and fuzzy best-match search", "python",
   "Typo tolerance, 'did you mean', deduplicating names, comparing DNA/text sequences - without pulling in a dependency.",
   "Two-row dynamic programming Levenshtein (O(min(n,m)) memory), optimal-string-alignment Damerau variant (transposition counts 1), early exit with a max-distance bound, normalised similarity ratio, and `best_matches()` returning the closest candidates with deterministic tie-breaking and a threshold.",
   {"fuzzy.py": '''"""String distances. For big candidate sets use rapidfuzz; this is the algorithm explained and tested."""


def levenshtein(a, b, max_dist=None):
    if a == b: return 0
    if len(a) < len(b): a, b = b, a                      # keep the shorter one as the row
    if max_dist is not None and len(a) - len(b) > max_dist: return max_dist + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if max_dist is not None and min(cur) > max_dist: return max_dist + 1
        prev = cur
    return prev[-1]


def damerau(a, b):
    """Optimal string alignment: adjacent transposition is one edit ('teh' -> 'the' = 1)."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1): d[i][0] = i
    for j in range(len(b) + 1): d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = a[i - 1] != b[j - 1]
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]: d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


def ratio(a, b):
    m = max(len(a), len(b))
    return 1.0 if m == 0 else 1 - levenshtein(a, b) / m


def best_matches(query, candidates, limit=3, min_ratio=0.6, casefold=True):
    q = query.casefold() if casefold else query
    scored = []
    for c in candidates:
        r = ratio(q, c.casefold() if casefold else c)
        if r >= min_ratio: scored.append((r, c))
    scored.sort(key=lambda t: (-t[0], len(t[1]), t[1]))
    return [c for _, c in scored[:limit]]
'''
    , "test_fuzzy.py": '''import unittest, random
import fuzzy as F


class T(unittest.TestCase):
    def test_known_distances(self):
        for a, b, d in (("kitten", "sitting", 3), ("flaw", "lawn", 2), ("", "abc", 3), ("abc", "", 3), ("same", "same", 0), ("a", "b", 1), ("saturday", "sunday", 3)):
            self.assertEqual(F.levenshtein(a, b), d, (a, b))

    def test_metric_properties(self):
        r = random.Random(1); w = lambda: "".join(r.choice("abc") for _ in range(r.randint(0, 7)))
        for _ in range(300):
            a, b, c = w(), w(), w()
            self.assertEqual(F.levenshtein(a, b), F.levenshtein(b, a)); self.assertLessEqual(F.levenshtein(a, c), F.levenshtein(a, b) + F.levenshtein(b, c))
            self.assertLessEqual(F.damerau(a, b), F.levenshtein(a, b)); self.assertEqual(F.levenshtein(a, b) == 0, a == b)

    def test_bounded_search_stops_early_but_agrees_when_close(self):
        self.assertEqual(F.levenshtein("kitten", "sitting", max_dist=5), 3); self.assertGreater(F.levenshtein("abcdefgh", "zzzzzzzz", max_dist=2), 2); self.assertGreater(F.levenshtein("a", "abcdefg", max_dist=2), 2)

    def test_transposition_is_one_edit_in_damerau_only(self):
        self.assertEqual((F.damerau("teh", "the"), F.levenshtein("teh", "the")), (1, 2)); self.assertEqual(F.damerau("ca", "abc"), 3)

    def test_unicode(self):
        self.assertEqual(F.levenshtein("café", "cafe"), 1); self.assertEqual(F.levenshtein("日本語", "日本"), 1)

    def test_ratio(self):
        self.assertEqual((F.ratio("", ""), F.ratio("abc", "abc"), F.ratio("abc", "xyz")), (1.0, 1.0, 0.0)); self.assertAlmostEqual(F.ratio("kitten", "sitting"), 1 - 3 / 7)

    def test_did_you_mean(self):
        cmds = ["install", "uninstall", "update", "upgrade", "init", "list"]
        self.assertEqual(F.best_matches("instal", cmds, 2), ["install", "uninstall"]); self.assertEqual(F.best_matches("UPDTE", cmds, 1), ["update"])
        self.assertEqual(F.best_matches("zzzzz", cmds), []); self.assertEqual(F.best_matches("lst", ["list", "last"], 5, 0.5), ["last", "list"])


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Character-level distance; word-order-insensitive matching (token_set_ratio) needs a tokenising layer or rapidfuzz"], compat={"python": ">=3.9"},
   tags=["edit-distance", "levenshtein", "fuzzy", "fuzzy-matching", "string-similarity", "did-you-mean", "typo", "damerau", "text", "nlp", "dedupe", "sequence-alignment"],
   avoid="Full n*m matrices for long strings when two rows suffice; comparing case-sensitively for user-facing suggestions; O(n) scans with no early-exit bound on big lists."),

RC("py-geo-haversine-polygon-geohash", "Geospatial basics: haversine, bounding box, point-in-polygon, geohash, DMS conversion, polyline simplification, GPX", "python",
   "Distances between coordinates, 'is this point inside the zone', cheap spatial indexing keys and reading GPS tracks.",
   "Haversine with Earth radius constant and antimeridian-safe bounding box from a centre+radius, ray-casting point-in-polygon (inside, outside, on-edge), geohash encode/decode with neighbour-prefix property, DMS/hemisphere to decimal degrees, Douglas-Peucker simplification, and GPX track parsing with ElementTree (namespace-safe).",
   {"geo.py": '''"""Geo helpers. Coordinates are (lat, lon) in degrees (WGS84); distances in metres."""
import math
import xml.etree.ElementTree as ET

R = 6371008.8


def haversine(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(h)))


def bbox(center, radius_m):
    """-> (min_lat, min_lon, max_lat, max_lon); longitudes may wrap past +-180 (min > max means it crosses the antimeridian)."""
    lat, lon = center
    dlat = math.degrees(radius_m / R)
    dlon = 180.0 if abs(lat) + dlat >= 90 else math.degrees(radius_m / (R * math.cos(math.radians(lat))))
    wrap = lambda x: (x + 180) % 360 - 180
    return max(-90.0, lat - dlat), wrap(lon - dlon), min(90.0, lat + dlat), wrap(lon + dlon)


def in_polygon(pt, poly):
    """Ray casting. poly: [(lat, lon), ...] (closed implicitly). Points exactly on an edge count as inside."""
    y, x = pt; inside = False
    for (y1, x1), (y2, x2) in zip(poly, poly[1:] + poly[:1]):
        cross = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
        if cross == 0 and min(x1, x2) <= x <= max(x1, x2) and min(y1, y2) <= y <= max(y1, y2): return True
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1: inside = not inside
    return inside


_B32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def geohash_encode(lat, lon, precision=9):
    lo = [-90.0, -180.0]; hi = [90.0, 180.0]; bits, ch, even, out = 0, 0, True, []
    while len(out) < precision:
        i = 1 if even else 0; v = lon if even else lat; mid = (lo[i] + hi[i]) / 2
        if v >= mid: ch = ch * 2 + 1; lo[i] = mid
        else: ch = ch * 2; hi[i] = mid
        even = not even; bits += 1
        if bits == 5: out.append(_B32[ch]); bits, ch = 0, 0
    return "".join(out)


def geohash_decode(h):
    lo = [-90.0, -180.0]; hi = [90.0, 180.0]; even = True
    for c in h:
        v = _B32.index(c)
        for bit in (16, 8, 4, 2, 1):
            i = 1 if even else 0; mid = (lo[i] + hi[i]) / 2
            if v & bit: lo[i] = mid
            else: hi[i] = mid
            even = not even
    return (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2


def dms_to_decimal(deg, minutes=0.0, seconds=0.0, hemisphere="N"):
    v = abs(deg) + minutes / 60 + seconds / 3600
    return -v if hemisphere.upper() in ("S", "W") or deg < 0 else v


def simplify(points, epsilon_m):
    """Douglas-Peucker on a lat/lon polyline using a local flat approximation (fine at track scale)."""
    if len(points) < 3: return list(points)
    lat0 = math.radians(points[0][0]); kx = math.cos(lat0) * math.pi / 180 * R; ky = math.pi / 180 * R
    xy = [((p[1]) * kx, p[0] * ky) for p in points]
    def dist(i, a, b):
        (px, py), (ax, ay), (bx, by) = xy[i], xy[a], xy[b]; dx, dy = bx - ax, by - ay
        if dx == dy == 0: return math.hypot(px - ax, py - ay)
        return abs(dy * px - dx * py + bx * ay - by * ax) / math.hypot(dx, dy)
    keep = {0, len(points) - 1}; stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1: continue
        i, d = max(((i, dist(i, a, b)) for i in range(a + 1, b)), key=lambda t: t[1])
        if d > epsilon_m: keep.add(i); stack += [(a, i), (i, b)]
    return [points[i] for i in sorted(keep)]


def read_gpx(text):
    root = ET.fromstring(text)
    return [(float(p.get("lat")), float(p.get("lon"))) for p in root.iter() if p.tag.rsplit("}", 1)[-1] == "trkpt"]
'''
    , "test_geo.py": '''import unittest, math
import geo as G

PARIS, LONDON, NYC = (48.8566, 2.3522), (51.5074, -0.1278), (40.7128, -74.0060)


class T(unittest.TestCase):
    def test_haversine_known_distances(self):
        self.assertAlmostEqual(G.haversine(PARIS, LONDON) / 1000, 343.5, delta=2); self.assertAlmostEqual(G.haversine(LONDON, NYC) / 1000, 5570, delta=15)
        self.assertEqual(G.haversine(PARIS, PARIS), 0); self.assertAlmostEqual(G.haversine((0, 0), (0, 180)), math.pi * G.R, delta=1)
        self.assertAlmostEqual(G.haversine(PARIS, LONDON), G.haversine(LONDON, PARIS))

    def test_bbox_contains_circle_and_handles_poles_and_antimeridian(self):
        a, b, c, d = G.bbox(PARIS, 10000); self.assertTrue(a < PARIS[0] < c and b < PARIS[1] < d); self.assertAlmostEqual(G.haversine((a, PARIS[1]), (c, PARIS[1])) / 2, 10000, delta=20)
        _, lo, _, hi = G.bbox((0.0, 179.9), 50000); self.assertGreater(lo, hi)                                     # wraps
        self.assertEqual(G.bbox((89.99, 10.0), 50000)[2], 90.0)

    SQUARE = [(0, 0), (0, 10), (10, 10), (10, 0)]
    CONCAVE = [(0, 0), (0, 10), (10, 10), (10, 6), (4, 6), (4, 4), (10, 4), (10, 0)]

    def test_point_in_polygon(self):
        self.assertTrue(G.in_polygon((5, 5), self.SQUARE)); self.assertFalse(G.in_polygon((11, 5), self.SQUARE)); self.assertFalse(G.in_polygon((-0.1, 5), self.SQUARE))
        self.assertTrue(G.in_polygon((0, 5), self.SQUARE)); self.assertTrue(G.in_polygon((10, 10), self.SQUARE))                     # edge and vertex count as inside
        self.assertTrue(G.in_polygon((2, 5), self.CONCAVE)); self.assertFalse(G.in_polygon((7, 5), self.CONCAVE))                    # inside the notch

    def test_geohash_known_values_and_round_trip(self):
        self.assertEqual(G.geohash_encode(57.64911, 10.40744, 11), "u4pruydqqvj"); self.assertEqual(G.geohash_encode(42.6, -5.6, 5), "ezs42")
        lat, lon = G.geohash_decode("ezs42"); self.assertAlmostEqual(lat, 42.6, delta=0.03); self.assertAlmostEqual(lon, -5.6, delta=0.03)
        self.assertTrue(G.geohash_encode(*PARIS, 9).startswith(G.geohash_encode(48.857, 2.353, 5)))                                 # nearby points share a prefix

    def test_dms(self):
        self.assertAlmostEqual(G.dms_to_decimal(40, 26, 46, "N"), 40.446111, places=5); self.assertAlmostEqual(G.dms_to_decimal(79, 58, 56, "W"), -79.982222, places=5); self.assertLess(G.dms_to_decimal(-3, 30), 0)

    def test_simplify_keeps_corners_drops_collinear_noise(self):
        line = [(0.0, 0.0), (0.00001, 0.01), (0.0, 0.02), (0.00001, 0.03), (0.0, 0.04), (0.04, 0.04)]
        out = G.simplify(line, 5); self.assertEqual(out, [(0.0, 0.0), (0.0, 0.04), (0.04, 0.04)]); self.assertEqual(G.simplify(line, 0.0001)[0], line[0])
        self.assertEqual(G.simplify([(0, 0), (1, 1)], 10), [(0, 0), (1, 1)])

    def test_gpx_with_namespace(self):
        gpx = '<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1"><trk><trkseg><trkpt lat="48.1" lon="11.5"><ele>500</ele></trkpt><trkpt lat="48.2" lon="11.6"/></trkseg></trk></gpx>'
        self.assertEqual(G.read_gpx(gpx), [(48.1, 11.5), (48.2, 11.6)])


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Spherical Earth model (error up to ~0.5% vs the WGS84 ellipsoid); use geographiclib/pyproj for survey-grade work", "Flat-plane simplification is accurate at track scale, not across continents"], compat={"python": ">=3.9"},
   tags=["geo", "gps", "haversine", "distance", "latitude", "longitude", "polygon", "geofence", "geohash", "gpx", "bounding-box", "maps", "location", "coordinates"],
   avoid="Euclidean distance on lat/lon degrees; bounding boxes that ignore the antimeridian; storing places as strings with no spatial key."),
]
