"""Conservative post-processing for Fish S2 speech.

No pitch/F0 changes.  Loudness uses gated K-weighted LUFS when scipy is
available, with an RMS proxy fallback.  A smoothed peak envelope prevents
new clipping but cannot reconstruct samples already clipped by the model.
"""
import math
import numpy as np
import torch
import torch.nn.functional as F


def _loudness_db(audio, sample_rate):
    """Return (level, metric); ignore near-silent regions."""
    x = audio.detach().float().cpu().numpy()
    if x.size == 0:
        return -90.0, "RMS"
    try:
        from scipy.signal import lfilter
        # BS.1770 high shelf and high pass (bilinear transform, arbitrary sample rate).
        def biquad(kind, freq, q, gain_db=0):
            a = 10 ** (gain_db / 40)
            w = 2 * np.pi * freq / sample_rate
            c, s = np.cos(w), np.sin(w)
            alpha = s / (2 * q)
            if kind == 'shelf':
                beta = 2 * np.sqrt(a) * alpha
                b = np.array([a*((a+1)+(a-1)*c+beta), 2*a*((a-1)+(a+1)*c), a*((a+1)+(a-1)*c-beta)])
                d = np.array([(a+1)-(a-1)*c+beta, -2*((a-1)-(a+1)*c), (a+1)-(a-1)*c-beta])
            else:
                b = np.array([(1+c)/2, -(1+c), (1+c)/2])
                d = np.array([1+alpha, -2*c, 1-alpha])
            return b/d[0], d/d[0]
        # ITU-R BS.1770 K-weighting constants
        b1, a1 = biquad('shelf', 1681.974450955533, 0.7071752369554196, 3.999843853973347)
        b2, a2 = biquad('highpass', 38.13547087602444, 0.5003270373238773)
        filtered = lfilter(b2, a2, lfilter(b1, a1, x.astype(np.float64), axis=-1), axis=-1)
        # 400ms blocks, 100ms hop; relative gate -10 LU after absolute -70 LUFS.
        block = max(1, round(sample_rate * 0.4))
        hop = max(1, round(sample_rate * 0.1))
        if filtered.shape[-1] < block:
            powers = np.array([np.mean(filtered ** 2)])
        else:
            powers = np.array([np.mean(filtered[:, i:i+block] ** 2) for i in range(0, filtered.shape[-1]-block+1, hop)])
        levels = -0.691 + 10*np.log10(np.maximum(powers, 1e-15))
        absolute = powers[levels > -70]
        if absolute.size == 0:
            return -90.0, 'LUFS'
        preliminary = -0.691 + 10*np.log10(np.mean(absolute))
        selected = powers[(levels > -70) & (levels > preliminary - 10)]
        return float(-0.691 + 10*np.log10(np.mean(selected))) if selected.size else preliminary, 'LUFS'
    except ImportError:
        # Conservative fallback, not represented as LUFS.
        rms = float(np.sqrt(np.mean(x ** 2)))
        return 20 * math.log10(max(rms, 1e-9)), 'RMS dBFS'


def _soft_peak_limit(audio, ceiling_db=-1.0, sample_rate=44100):
    """Smoothed feed-forward gain reduction. No hard sample clipping."""
    ceiling = 10 ** (ceiling_db / 20)
    peak = float(audio.abs().max())
    if peak <= ceiling:
        return audio, 0.0
    # Noncausal lookahead peak envelope, sampled every ~5ms.
    stride = max(1, round(sample_rate * .005))
    window = max(3, round(sample_rate * .02) | 1)
    mono = audio.abs().amax(dim=0).view(1, 1, -1)
    envelope = F.max_pool1d(mono, kernel_size=window, stride=stride, padding=window//2)
    desired = (ceiling / envelope.clamp(min=ceiling)).clamp(max=1)
    # Smooth attack/release with short convolution; guarantee ceiling via final scalar safeguard.
    smooth_size = max(3, round(.03 / .005) | 1)
    smoothed = F.avg_pool1d(desired, smooth_size, stride=1, padding=smooth_size//2)
    gains = F.interpolate(smoothed, size=audio.shape[-1], mode='linear', align_corners=False).view(-1)
    out = audio * gains.unsqueeze(0)
    residual_peak = float(out.abs().max())
    if residual_peak > ceiling:
        out = out * (ceiling / residual_peak)
    return out, max(0.0, 20*math.log10(max(peak, 1e-9) / max(float(out.abs().max()), 1e-9)))


def repair_segments(segments, sample_rate, normalize_loudness=True, target_lufs=-18.0,
                    peak_limiter=True, peak_ceiling_db=-1.0):
    """segments: [(role, tensor[channels,samples]), ...]. Returns processed, logs.

    Uses partial loudness matching (45% deviation correction) and at most 4 dB
    global gain / 3 dB local gain to retain intended emotion dynamics.
    """
    if not segments:
        return segments, []
    metrics = [_loudness_db(a, sample_rate) for _, a in segments]
    levels = [v for v, _ in metrics]
    metric = metrics[0][1]
    # Loudness for extremely short segments is less reliable; keep local gains restrained.
    valid = [v for v in levels if v > -65]
    median = float(np.median(valid)) if valid else target_lufs
    global_gain = max(-4.0, min(4.0, target_lufs - median)) if normalize_loudness else 0.0
    result, logs = [], []
    for i, ((role, audio), level) in enumerate(zip(segments, levels), 1):
        x = audio.clone()
        if normalize_loudness and level > -65:
            # Keep 55% of natural segment-to-segment loudness difference.
            local = max(-3.0, min(3.0, (median - level) * 0.45))
            db = global_gain + local
            x = x * (10 ** (db / 20))
        else:
            db = 0.0
        original_peak = float(x.abs().max())
        clipped = float((audio.abs() >= .999).float().mean())
        limited_db = 0.0
        if peak_limiter:
            x, limited_db = _soft_peak_limit(x, peak_ceiling_db, sample_rate)
        notes = [f'{i}. {role}: {metric} {level:.1f}', f'增益 {db:+.1f}dB']
        if limited_db > .05:
            notes.append(f'峰值保护约 {limited_db:.1f}dB')
        if clipped > .001:
            notes.append('警告：原始音频疑似削波，无法保证修复已有失真')
        if not peak_limiter and original_peak > 1:
            notes.append('警告：输出超过 0dBFS')
        logs.append(' / '.join(notes))
        result.append((role, x))
    return result, logs
