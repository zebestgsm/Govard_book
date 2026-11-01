"""Build a conservative, source-backed collation audit for every spread.

This is an audit aid, not an autopublisher. It compares the reader's current
base segments with the independent scan OCR and the three supplied editions.
Only candidates with explicit evidence are written to collation_audit.json.
User edits are never modified. Run from the repository root.
"""
from pathlib import Path
import json, re, difflib, bisect
from rapidfuzz import fuzz
import reader, reference_sources

ROOT = Path(__file__).resolve().parent

def compact(text):
    out, pos = [], []
    for i, ch in enumerate(text):
        ch = ch.replace('ſ', 's')
        for c in ch.lower():
            if c.isalnum():
                out.append(c); pos.append(i)
    return ''.join(out), pos

def word_spans(text):
    """Return (word, compact_start, compact_end, raw_start, raw_end)."""
    spans = []
    cpos = 0
    for m in re.finditer(r"[A-Za-z]+(?:['’-][A-Za-z]+)*", text.replace('ſ', 's')):
        raw = m.group()
        c = re.sub(r'[^a-z0-9]', '', raw.lower())
        if c:
            spans.append((raw, cpos, cpos + len(c), m.start(), m.end()))
            cpos += len(c)
    return spans

SOURCES = reference_sources.sources()
REF_DATA = []
for label, text, _, _ in SOURCES:
    c, _ = compact(text)
    spans = word_spans(text)
    starts = [s[1] for s in spans]
    REF_DATA.append((label, text, c, spans, starts))

def ref_window_words(label_idx, start, end, margin=60):
    label, text, c, spans, starts = REF_DATA[label_idx]
    lo = bisect.bisect_left(starts, max(0, start - margin))
    hi = bisect.bisect_right(starts, min(len(c), end + margin))
    # include word containing start
    if lo > 0 and spans[lo-1][2] > start - margin: lo -= 1
    return spans[lo:hi], lo

def audit():
    saved = reader.inline_editor.load_edits()
    report = {}
    for number in range(1, len(reader.PAGES) + 1):
        token = reader._current_corrections.set(())
        try:
            _, segments = reader.segment_state(number, edits={'spreads': {}})
        finally:
            reader._current_corrections.reset(token)
        user_segments = saved['spreads'].get(str(number), {}).get('segments', {})
        ocr = []
        for side in (0, 1):
            path = ROOT / 'ocr_review' / f'{number}_{side}.txt'
            if path.exists():
                raw = path.read_text(encoding='utf-8')
                ocr.append((side, raw, compact(raw)[0]))
        candidates = []
        best_score = 0
        for segid, value in segments.items():
            if segid in user_segments or len(value) < 80: continue
            src, spos = compact(value)
            sw = word_spans(value)
            if len(src) < 60: continue
            src_starts = [w[1] for w in sw]
            for idx, (label, text, refc, rw, rstarts) in enumerate(REF_DATA):
                align = fuzz.partial_ratio_alignment(src, refc)
                if not align or align.score < 82: continue
                best_score = max(best_score, align.score)
                window_words, offset = ref_window_words(idx, align.dest_start, align.dest_end)
                if not window_words: continue
                wstart = window_words[0][1]; wend = window_words[-1][2]
                window = refc[wstart:wend]
                sm = difflib.SequenceMatcher(None, src, window, autojunk=False)
                for tag, i1, i2, j1, j2 in sm.get_opcodes():
                    if tag == 'equal': continue
                    old_c = src[i1:i2]; new_c = window[j1:j2]
                    if len(old_c) < 4 and len(new_c) < 12: continue
                    # Map to source words
                    si = bisect.bisect_left(src_starts, i1)
                    sj = bisect.bisect_left(src_starts, i2)
                    if i2 > i1 and sj < len(sw) and sw[sj][1] < i2: sj += 1
                    old_words = sw[si:sj]
                    old = ' '.join(w[0] for w in old_words)
                    # Map to ref words
                    rj = bisect.bisect_left(rstarts, wstart + j1)
                    rk = bisect.bisect_left(rstarts, wstart + j2)
                    if j2 > j1 and rk < len(rw) and rw[rk][1] < wstart + j2: rk += 1
                    new_words = rw[rj:rk]
                    new = ' '.join(w[0] for w in new_words)
                    if not new and not old: continue
                    left = value[max(0, (spos[i1] if i1 < len(spos) else len(value)) - 70):(spos[i1] if i1 < len(spos) else len(value))]
                    right = value[(spos[i2] if i2 < len(spos) else len(value)):(spos[i2] if i2 < len(spos) else len(value)) + 70]
                    # Require context agreement on at least one side.
                    lc, _ = compact(left); rc, _ = compact(right)
                    before = refc[max(0, wstart + j1 - 40):wstart + j1]
                    after = refc[wstart + j2:wstart + j2 + 40]
                    context = (lc and before.endswith(lc[-18:])) or (rc and after.startswith(rc[:18]))
                    if not context: continue
                    # Other reference support
                    support = [label]
                    nc, _ = compact(new)
                    if len(nc) >= 12:
                        for k, (lab2, _, rc2, _, _) in enumerate(REF_DATA):
                            if k == idx: continue
                            if nc in rc2:
                                support.append(lab2)
                    # OCR support: inserted compact text appears in OCR, or
                    # replacement matches OCR better than source.
                    ocr_support = False
                    for side, raw, oc in ocr:
                        if nc and nc in oc: ocr_support = True; break
                        if old_c and new_c and fuzz.ratio(old_c, new_c) > 70:
                            # check local OCR around left context
                            at = oc.find(lc[-25:]) if lc else -1
                            if at >= 0 and fuzz.partial_ratio(new_c, oc[at:at+len(nc)+80]) > 85:
                                ocr_support = True; break
                    if tag == 'insert' and len(new_words) >= 2 and (len(support) >= 2 or ocr_support):
                        kind = 'omission'; confidence = 'high' if len(support) >= 2 and ocr_support else 'medium'
                    elif tag == 'delete' and len(old_words) >= 2 and len(support) >= 2:
                        kind = 'extra'; confidence = 'medium'
                    elif tag == 'replace' and len(new_words) >= 1 and len(old_words) >= 1 and len(support) >= 2 and ocr_support:
                        kind = 'reading'; confidence = 'medium'
                    else:
                        continue
                    candidates.append({'segment': segid, 'kind': kind, 'confidence': confidence,
                                       'old': old, 'new': new, 'left': left[-60:], 'right': right[:60],
                                       'sources': support, 'ocr_support': ocr_support,
                                       'ref_score': round(align.score, 1)})
        # deduplicate
        seen = set(); uniq = []
        for c in candidates:
            key = (c['segment'], c['kind'], c['old'], c['new'])
            if key not in seen:
                seen.add(key); uniq.append(c)
        report[str(number)] = {'score': round(best_score), 'candidates': uniq,
                               'user_segments': list(user_segments)}
    return report

if __name__ == '__main__':
    report = audit()
    path = ROOT / 'collation_audit.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    total = sum(len(v['candidates']) for v in report.values())
    high = sum(c['confidence'] == 'high' for v in report.values() for c in v['candidates'])
    print(f'Audited {len(report)} spreads; {total} candidates ({high} high confidence).')
