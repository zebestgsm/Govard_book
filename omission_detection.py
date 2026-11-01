"""Find *possible* omitted passages by anchoring a second OCR to current text.

The comparison ignores spaces/punctuation so glued words do not dominate.
It is a heuristic, not a transcription or an automated correction.
"""
import re

ANCHOR = 20
MIN_GAP = 15
MAX_GAP = 220


def compact_with_positions(text):
    letters, positions = [], []
    for i, char in enumerate(text):
        if char.isalnum():
            for lowered in char.lower():
                letters.append(lowered)
                positions.append(i)
    return ''.join(letters), positions


def boundary(text, positions, offset):
    if offset <= 0 or offset >= len(positions):
        return True
    return positions[offset] > positions[offset - 1] + 1


def find_omissions(segments, ocr_sides, limit=30):
    """Return bounded, unverified candidates, retaining literal OCR context."""
    found = []
    seen = set()
    sides = [(side, text, *compact_with_positions(text)) for side, text in ocr_sides]
    for segment_id, text in segments.items():
        if not isinstance(text, str) or len(text) < 2 * ANCHOR:
            continue
        source, pos = compact_with_positions(text)
        for side, raw, other, other_pos in sides:
            for i in range(len(source) - 2 * ANCHOR + 1):
                split = i + ANCHOR
                if not boundary(text, pos, split):
                    continue
                anchor = source[i:split]
                at = other.find(anchor)
                if at < 0 or other.find(anchor, at + 1) >= 0:
                    continue
                ocr_start = at + ANCHOR
                if not boundary(raw, other_pos, ocr_start):
                    continue
                for skip in range(1):
                    right_at = split + skip
                    if right_at + ANCHOR > len(source) or not boundary(text, pos, right_at):
                        continue
                    right = source[right_at:right_at + ANCHOR]
                    next_at = other.find(right, ocr_start + MIN_GAP, ocr_start + MAX_GAP + ANCHOR)
                    if next_at < 0 or next_at - ocr_start > MAX_GAP or not boundary(raw, other_pos, next_at):
                        continue
                    # Disallow ambiguous anchors and require several words.
                    if other.find(right, next_at + 1) >= 0:
                        continue
                    candidate = raw[other_pos[ocr_start]:other_pos[next_at]].strip()
                    if len(re.findall(r"\b[\w]+(?:[’'-][\w]+)*\b", candidate)) < 3:
                        continue
                    key = (segment_id, side, other[ocr_start:next_at])
                    if key in seen:
                        continue
                    seen.add(key)
                    left_end = pos[split - 1] + 1
                    right_start = pos[right_at]
                    found.append({'segment': segment_id, 'side': side,
                                  'before': text[max(0, left_end - 85):left_end],
                                  'after': text[right_start:min(len(text), right_start + 85)],
                                  'candidate': re.sub(r'\s+', ' ', candidate)})
                    if len(found) >= limit:
                        return found
                    break
    return found
