"""Машинный черновик перевода по выверенному английскому тексту.

Вход — `translation_sources/work-doc.json`: абзацы, уже склеенные из сегментов
читалки, с вынутыми колонтитулами (см. tools/build_translation_doc.py). Модель
получает целые фразы, а не обрывки, поэтому черновик выходит связным.

Модель задаётся переменной окружения MT_MODEL (по умолчанию — базовая
Helsinki-NLP/opus-mt-en-ru). Для сравнения: MT_MODEL=facebook/nllb-200-distilled-600M
(прим.: Helsinki-NLP/opus-mt-tc-big-en-ru НЕ существует для en→ru, 404 в прогоне 45471417377).
Если задан HF_TOKEN, он передаётся в модель (снимает лимиты анонимных скачиваний
и даёт доступ к вашим копиям моделей); для публичных моделей токен не нужен.

    MT_MODEL=Helsinki-NLP/opus-mt-en-ru python tools/translate_opus.py
    LIMIT=31,87,141,95 python tools/translate_opus.py     # только контрольные

Выход: translation_sources/ru-mt.json  { "разворот": "текст" }
       translation_sources/ru-mt-stats.json
"""
from __future__ import annotations
import json
import os
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'translation_sources' / 'work-doc.json'
OUT = ROOT / 'translation_sources' / 'ru-mt.json'
STATS = ROOT / 'translation_sources' / 'ru-mt-stats.json'
MODEL = os.environ.get('MT_MODEL', 'Helsinki-NLP/opus-mt-en-ru')
TOKEN = os.environ.get('HF_TOKEN') or None
BATCH = int(os.environ.get('MT_BATCH', '8'))


def sentences(paragraph: str):
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z“"\'(\[])', paragraph)
    return [s.strip() for s in parts if s.strip()]


def wanted():
    limit = os.environ.get('LIMIT', '').strip()
    if not limit:
        return None
    return {int(x) for x in limit.split(',') if x.strip()}


def main():
    import torch
    from transformers import MarianMTModel, MarianTokenizer

    print('модель:', MODEL, flush=True)
    tok = MarianTokenizer.from_pretrained(MODEL, token=TOKEN)
    model = MarianMTModel.from_pretrained(MODEL, token=TOKEN)
    model.eval()
    torch.set_num_threads(max(1, os.cpu_count() or 1))

    def translate(batch):
        with torch.no_grad():
            inputs = tok(batch, return_tensors='pt', padding=True, truncation=True, max_length=512)
            generated = model.generate(**inputs, num_beams=4, max_length=512)
        return tok.batch_decode(generated, skip_special_tokens=True)

    doc = json.loads(SRC.read_text(encoding='utf-8'))
    only = wanted()
    out, stats = {}, {}
    started = time.time()
    for number in sorted(doc['spreads'], key=int):
        if only and int(number) not in only:
            continue
        spread = doc['spreads'][number]
        t0 = time.time()
        paragraphs, count = [], 0
        # Заголовки разворота — отдельными строками перед первым абзацем.
        if spread['running_heads']:
            paragraphs.extend(spread['running_heads'])
        for paragraph in spread['paragraphs']:
            pieces = sentences(paragraph['text'].replace('\n', ' '))
            translated = []
            for i in range(0, len(pieces), BATCH):
                chunk = pieces[i:i + BATCH]
                try:
                    translated.extend(translate(chunk))
                except Exception as error:  # не теряем текст при сбое модели
                    print('error', number, error, flush=True)
                    translated.extend(chunk)
                count += len(chunk)
            paragraphs.append(' '.join(translated))
        for table in spread['tables']:
            # Ячейки не переводятся построчно: сохраняем как есть, разметку
            # восстановит ручное редактирование, числа должны остаться целы.
            paragraphs.append(table['text'])
        for note in spread['footnotes']:
            paragraphs.append(' '.join(translate([note['text'].replace('\n', ' ')])))
        out[number] = '\n\n'.join(p for p in paragraphs if p.strip())
        stats[number] = {'sentences': count, 'seconds': round(time.time() - t0, 1)}
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
        STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding='utf-8')
        print(number, count, stats[number]['seconds'], flush=True)
    print('готово:', len(out), 'разворотов за', round(time.time() - started), 'с', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        raise
