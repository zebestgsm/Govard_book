#!/usr/bin/env python3
"""Build conservative spacing proposals and note/quotation flags from OCR text.

Run from repository root with PyMuPDF installed, after generating OCR files.
Existing proposals for the pilot spreads are retained to keep review IDs stable.
The flags are heuristics and are not verified transcriptions.
"""
import json
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import reader
import inline_editor

REVIEW = ROOT / 'ocr_review'
suggestion_path = REVIEW / 'suggestions.json'
suggestions = json.loads(suggestion_path.read_text(encoding='utf-8'))
annotations = {}

def gather_candidates(segments, ocr_texts, number):
    lookup = {}
    for segid, text in segments.items():
        for match in re.finditer(r'\b[A-Za-z]{10,}\b', text):
            word = match.group()
            if text.count(word) == 1:
                lookup.setdefault(word.lower(), []).append((segid, word, match.start()))
    found = {}
    for text in ocr_texts:
        tokens = list(re.finditer(r'[A-Za-z]+', text))
        for i in range(len(tokens)):
            joined = ''
            for k in range(i, min(i + 8, len(tokens))):
                if k > i and not text[tokens[k-1].end():tokens[k].start()].isspace():
                    break
                joined += tokens[k].group().lower()
                if len(joined) > 60:
                    break
                if k == i or joined not in lookup:
                    continue
                lengths = [len(tokens[z].group()) for z in range(i, k+1)]
                for segment, original, position in lookup[joined]:
                    split, start = [], 0
                    for length in lengths:
                        split.append(original[start:start+length]); start += length
                    if any(len(part) == 1 and part.lower() not in ('a','i') for part in split):
                        continue
                    found.setdefault((segment, original, position), ' '.join(split))
    ordered = sorted(found.items(), key=lambda item: (int(item[0][0][1:]), item[0][2]))
    return [{'id': f'{number}-{i+1}', 'segment': key[0], 'old': key[1], 'new': value}
            for i, (key,value) in enumerate(ordered)]

def collect_flags(texts):
    flags = []
    for side,text in texts:
        lines = text.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            kinds = []
            # Footnote-like lines only, not a guarantee they are notes.
            if re.match(r'^(?:\d{1,2}[.)]?|[†‡*])\s*(?=[A-Za-z])[A-Za-z]', stripped) and len(stripped) >= 18:
                kinds.append('possible_note')
            if any(c in stripped for c in ('“','”','"','‘','’')) and len(stripped) >= 12:
                # Apostrophes inside words are not quotation marks.
                if re.search(r'[“”"]|(?:^|\s)[‘’]|[‘’](?:\s|$)', stripped):
                    kinds.append('possible_quote')
            for kind in kinds:
                context = ' '.join(x.strip() for x in lines[max(0,i-1):min(len(lines),i+2)] if x.strip())
                flags.append({'kind':kind,'side':side,'line':i+1,'text':context[:450]})
    seen=set(); result=[]
    for flag in flags:
        key=(flag['kind'],flag['side'],flag['text'])
        if key not in seen:
            seen.add(key);result.append(flag)
    return result

complete=[]
for number in range(1,166):
    names=[(side, REVIEW / f'{number}_{side}.txt') for side in (0,1) if not (number==1 and side==0)]
    if not all(path.exists() for _,path in names):
        continue
    texts=[(side,path.read_text(encoding='utf-8')) for side,path in names]
    if number > 3:
        # Source OCR + stable segment layout, without any user edits or replacements.
        token=reader._current_corrections.set(())
        try:
            base=reader.render_pages(number,reader.PAGES[number-1][1],'')
            _,segments=inline_editor.render_segments(base)
        finally:
            reader._current_corrections.reset(token)
        suggestions[str(number)] = gather_candidates(segments,[text for _,text in texts],number)
    annotations[str(number)]=collect_flags(texts)
    complete.append(number)

def atomic_json(path, data):
    tmp=path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n',encoding='utf-8')
    tmp.replace(path)

atomic_json(suggestion_path,suggestions)
atomic_json(REVIEW/'annotations.json',annotations)
print('OCR complete:',len(complete),'of 165 spreads')
print('Spacing candidates:',sum(len(v) for v in suggestions.values()))
print('Note/quotation flags:',sum(len(v) for v in annotations.values()))
