#!/usr/bin/env python3
"""Check the glossary against its own layer and report glossary coverage.

Run after every translation batch:

    PYTHONPATH=.:. python -B tools/glossary_check.py

It never edits text.  It reports:
  * glossary entries whose Russian form is missing from the layer (unused),
  * rejected variants found in the layer (must be zero),
  * the most frequent English terms that are still not in the glossary,
    so the glossary grows from the book itself rather than from guesses.
"""
from __future__ import annotations
import json
import re
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GLOSSARY = ROOT / 'translation_glossary.json'


def load():
    return json.loads(GLOSSARY.read_text(encoding='utf-8'))


def en_text():
    import reader
    for number in range(1, 166):
        _, segments = reader.segment_state(number)
        yield number, '\n'.join(v for v in segments.values() if isinstance(v, str))


def main():
    data = load()
    terms = data['terms']
    try:
        import translation
        layer = '\n'.join(translation.V2[n] for n in sorted(translation.V2))
        layer_name = 'ru_translations_v2.py (%d разворотов)' % len(translation.V2)
    except Exception:
        layer, layer_name = '', 'нет слоя v2'

    bad = []
    for term in terms:
        for variant in term['rejected']:
            if re.search(r'\b' + re.escape(variant), layer, re.IGNORECASE):
                bad.append((term['en'], variant))
    print('Запрещённые варианты в слое:', bad or 'нет')
    print('Слой:', layer_name)

    unused = [t['en'] for t in terms
              if layer and not re.search(r'\b' + re.escape(t['ru'][:6]), layer, re.IGNORECASE)]
    print('Терминов пока не встречается в переведённом:', len(unused), '/', len(terms))

    corpus = ' '.join(text for _, text in en_text())
    words = Counter(re.findall(r"[A-Za-z][A-Za-z-]{3,}", corpus.lower()))
    known = {t['en'].lower() for t in terms}
    STOP = set('''that were their they with which this have from there them some
        these those been being been shall should would could must also more most
        such than then when where while what whose whom here into upon about
        other another every each many much very only both same own said does
        prisoners prison rooms room persons person number still well made make'''.split())
    missing = [(w, c) for w, c in words.most_common(600)
               if w not in known and w not in STOP and c >= 30 and not w.endswith('ing')]
    print('\nЧастые английские слова без глоссарной статьи (кандидаты):')
    for w, c in missing[:40]:
        print(f'  {w:16} {c}')


if __name__ == '__main__':
    main()
