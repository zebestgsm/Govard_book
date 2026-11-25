#!/usr/bin/env python3
"""Собрать выверенный английский текст в документ для машинного перевода.

Зачем: английский в читалке разбит на 891 сегмент, и 25 % переходов между
сегментами разрезают предложение. Если отдавать модели сегменты, она переводит
обрывки. Этот скрипт склеивает сегменты одного разворота в абзацы, вынимает
колонтитулы и табличные блоки в отдельные поля и сохраняет якоря — чтобы
потом можно было понять, откуда взялся каждый абзац.

    PYTHONPATH=.:. python -B tools/build_translation_doc.py

Результат: translation_sources/work-doc.json
    {
      "spreads": {
        "1": {
          "paragraphs": [{"text": "...", "segments": ["s2", "s3"]}, ...],
          "running_heads": ["EDITOR'S INTRODUCTION"],
          "tables": ["..."],
          "footnotes": ["¹ ..."],
          "pages": ["9"]
        }
      },
      "meta": {"paragraphs": ..., "joins": ..., "heads": ..., "source_sha": ...}
    }
"""
from __future__ import annotations
from hashlib import sha256
import json
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'translation_sources' / 'work-doc.json'

# Колонтитулы: короткая строка прописными в начале сегмента.
RUNNING = re.compile(r"^(?:\d{1,3}\s*)?(?:AN ACCOUNT OF FOREIGN PRISONS|PROPOSED IMPROVEMENTS|"
                     r"TABLES|VIEW OF DISTRESS IN PRISONS|INDEX|EDITOR'S INTRODUCTION|"
                     r"INTRODUCTION|CONCLUSION|NOTES|SECTION [IVX]+)$")
TERMINAL = re.compile(r'[.!?:;"”’)\]]$')
FOOTNOTE = re.compile(r'^(?:[¹²³]\s|\[\u0421\u043d\u043e\u0441\u043a\u0430)')


def split_head(text):
    """Отделить колонтитул от первого абзаца сегмента."""
    lines = text.split('\n')
    heads = []
    while len(lines) > 1 and (RUNNING.match(lines[0].strip())
                              or (lines[0].strip().isupper() and 3 < len(lines[0].strip()) < 70
                                  and re.search(r'[A-Z]{3}', lines[0]) and not TERMINAL.search(lines[0]))):
        heads.append(lines.pop(0).strip())
    return heads, '\n'.join(lines).strip()


def main():
    import reader
    doc = {'spreads': {}, 'meta': {}}
    hash_parts, joins, heads_count = [], 0, 0
    for number in range(1, 166):
        _, segments = reader.segment_state(number)
        paras, heads, tables, footnotes = [], [], [], []
        current, current_ids, current_open = [], [], False
        for seg_id in sorted(segments, key=lambda s: int(s[1:])):
            value = segments[seg_id]
            if not isinstance(value, str) or not value.strip():
                continue
            value = value.strip()
            if '\t' in value:
                tables.append({'text': value, 'segments': [seg_id]})
                continue
            seg_heads, body = split_head(value)
            for head in seg_heads:
                heads.append(head)
                heads_count += 1
            if not body:
                continue
            if FOOTNOTE.match(body):
                footnotes.append({'text': body, 'segments': [seg_id]})
                continue
            # Продолжаем текущий абзац, если предыдущий сегмент не завершил фразу.
            if current and current_open and body[:1].islower():
                current.append(body)
                current_ids.append(seg_id)
                joins += 1
            else:
                if current:
                    paras.append({'text': '\n'.join(current), 'segments': current_ids})
                current, current_ids = [body], [seg_id]
            current_open = not TERMINAL.search(body)
        if current:
            paras.append({'text': '\n'.join(current), 'segments': current_ids})
        doc['spreads'][str(number)] = {
            'paragraphs': paras,
            'running_heads': heads,
            'tables': tables,
            'footnotes': footnotes,
            'pages': reader.page_labels(number),
        }
        hash_parts.append('\n'.join(p['text'] for p in paras))

    digest = sha256('\n\0\n'.join(hash_parts).encode('utf-8')).hexdigest()
    words = sum(len(p['text'].split()) for s in doc['spreads'].values() for p in s['paragraphs'])
    doc['meta'] = {
        'paragraphs': sum(len(s['paragraphs']) for s in doc['spreads'].values()),
        'joins': joins,
        'running_heads': heads_count,
        'tables': sum(len(s['tables']) for s in doc['spreads'].values()),
        'footnotes': sum(len(s['footnotes']) for s in doc['spreads'].values()),
        'words': words,
        'source_sha': digest,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding='utf-8')
    print('записано', OUT)
    for key, value in doc['meta'].items():
        print(f'  {key}: {value}')


if __name__ == '__main__':
    main()
