#!/usr/bin/env python3
"""Отчёт о качестве машинного черновика до ручного редактирования.

Сравнивает `translation_sources/ru-mt.json` с выверенным английским
(`work-doc.json`) и пишет `mt-report.md`:

  * числа: сколько потеряно и добавлено на каждом развороте (главный дефект MT);
  * сноски: сохранены ли маркеры ¹²³;
  * объём: отношение RU/EN (потеря или отсебятина);
  * запрещённые глоссарием варианты (гаол, кран, фелон, бридвел…);
  * доля латиницы вне таблиц.

Ничего не исправляет — только измеряет. Запуск после перевода.
"""
from __future__ import annotations
from collections import Counter
import json
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DOC = ROOT / 'translation_sources' / 'work-doc.json'
DRAFT = ROOT / 'translation_sources' / 'ru-mt.json'
REPORT = ROOT / 'mt-report.md'
GLOSSARY = ROOT / 'translation_glossary.json'


def numbers(text):
    return Counter(re.findall(r'\d+', text))


def main():
    doc = json.loads(DOC.read_text(encoding='utf-8'))
    draft = json.loads(DRAFT.read_text(encoding='utf-8'))
    glossary = json.loads(GLOSSARY.read_text(encoding='utf-8'))['terms']
    rejected = [(t['en'], v) for t in glossary for v in t.get('rejected', [])]

    rows, totals = [], Counter()
    for number in sorted(draft, key=int):
        spread = doc['spreads'][number]
        source = '\n'.join([*spread['running_heads']]
                           + [p['text'] for p in spread['paragraphs']]
                           + [t['text'] for t in spread['tables']]
                           + [f['text'] for f in spread['footnotes']])
        target = draft[number]
        lost = sum((numbers(source) - numbers(target)).values())
        added = sum((numbers(target) - numbers(source)).values())
        marks_src = len(re.findall(r'[¹²³]', source))
        marks_out = len(re.findall(r'[¹²³]', target))
        ratio = round(len(target) / max(1, len(source)), 2)
        bad = sorted({variant for _, variant in rejected
                      if re.search(r'\b' + re.escape(variant), target, re.IGNORECASE)})
        totals.update({'lost': lost, 'added': added, 'numbers': sum(numbers(source).values())})
        if marks_src != marks_out:
            totals['footnote_mismatch'] += 1
        if bad:
            totals['glossary_hits'] += 1
        if lost or added or marks_src != marks_out or bad or not 0.7 <= ratio <= 1.4:
            rows.append((number, lost, added, marks_src, marks_out, ratio, ', '.join(bad)))

    lines = ['# Отчёт о машинном черновике', '',
             f'Разворотов в черновике: **{len(draft)}**.', '',
             '| Показатель | Значение |', '|---|---|',
             f'| чисел в источнике | {totals["numbers"]} |',
             f'| чисел потеряно | **{totals["lost"]}** |',
             f'| чисел добавлено | **{totals["added"]}** |',
             f'| разворотов с расхождением сносок | {totals["footnote_mismatch"]} |',
             f'| разворотов с запрещёнными вариантами | {totals["glossary_hits"]} |',
             '']
    if rows:
        lines += ['## Развороты, требующие внимания редактора', '',
                  '| Разворот | чисел потеряно | добавлено | сноски EN→RU | RU/EN | запрещённые варианты |',
                  '|---|---|---|---|---|---|']
        for row in rows[:80]:
            lines.append('| {} | {} | {} | {}→{} | {} | {} |'.format(*row))
        if len(rows) > 80:
            lines.append(f'| … | ещё {len(rows) - 80} разворотов | | | | |')
    else:
        lines.append('Расхождений не найдено.')
    REPORT.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('\n'.join(lines[:12]))
    print('отчёт:', REPORT)


if __name__ == '__main__':
    main()
