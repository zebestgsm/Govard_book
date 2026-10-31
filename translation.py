"""Russian translation layer shown below the English reader text.

Base translations are versioned in ru_translations_*.py. User overrides are
stored separately in translation_edits.json and always take precedence.
"""
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
EDITS_FILE = ROOT / 'translation_edits.json'

try:
    from ru_translations_front import TRANSLATIONS as FRONT
except Exception:
    FRONT = {}
try:
    from ru_translations_core import TRANSLATIONS as CORE
except Exception:
    CORE = {}
try:
    from ru_translations_core2 import TRANSLATIONS as CORE2
except Exception:
    CORE2 = {}
try:
    from ru_translations_tables import TRANSLATIONS as TABLES
except Exception:
    TABLES = {}

BASE_TRANSLATIONS = {}
BASE_TRANSLATIONS.update(FRONT)
BASE_TRANSLATIONS.update(CORE)
BASE_TRANSLATIONS.update(CORE2)
BASE_TRANSLATIONS.update(TABLES)


def load_edits():
    if not EDITS_FILE.exists():
        return {'version': 1, 'spreads': {}}
    data = json.loads(EDITS_FILE.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('spreads'), dict):
        raise ValueError('Неверный формат translation_edits.json')
    return data


def save_edits(data):
    tmp = EDITS_FILE.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, EDITS_FILE)


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def translation_state(number, edits=None):
    saved = edits if edits is not None else load_edits()
    entry = saved['spreads'].get(str(number), {})
    if isinstance(entry.get('text'), str) and entry['text'].strip():
        return entry['text'], 'пользовательский перевод', True
    base = BASE_TRANSLATIONS.get(number)
    if base:
        return base, 'перевод подготовлен', False
    return '', 'перевод готовится', False


def highlight(text, query):
    if not query:
        return escape(text)
    parts = re.split('(' + re.escape(query) + ')', text, flags=re.IGNORECASE)
    return ''.join('<mark>' + escape(p) + '</mark>' if i % 2 else escape(p) for i, p in enumerate(parts))


def render_translation(text, query=''):
    """Render paragraphs, simple notes and tab-separated tables as plain HTML.

    The translation is read-only in the reader; editing uses a separate form.
    """
    out = []
    for chunk in re.split(r'(\n\n)', text):
        if chunk == '\n\n' or not chunk:
            out.append(chunk)
            continue
        if '\t' in chunk:
            rows = []
            for i, line in enumerate(chunk.splitlines()):
                cells = line.split('\t')
                role = 'columnheader' if i == 0 else 'cell'
                rows.append('<span class="restored-row" role="row" style="--numeric-cols:' + str(max(0, len(cells)-1)) + '">')
                for j, cell in enumerate(cells):
                    rows.append('<span class="restored-cell" role="' + role + '">' + highlight(cell, query) + '</span>')
                rows.append('</span>')
            out.append('<span class="restored-table fee-table" role="table" aria-label="Таблица в переводе">' + ''.join(rows) + '</span>')
        elif re.match(r'^[¹²³]\s', chunk):
            out.append('<span class="book-footnote verified-footnote">' + highlight(chunk, query) + '</span>')
        else:
            out.append('<span class="verified-paragraph">' + highlight(chunk, query) + '</span>')
    return ''.join(out)


def panel(number, query=''):
    text, status, user = translation_state(number)
    if text:
        body = '<div class="translation-text book-text" lang="ru">' + render_translation(text, query) + '</div>'
    else:
        body = ('<p class="notice">Русский перевод этого разворота ещё не добавлен. '
                'Он будет появляться здесь по мере последовательной сверки и перевода книги.</p>')
    edit = ''
    if text or True:
        current = escape(text)
        edit = (f'<details class="translation-edit"><summary class="button">Редактировать перевод</summary>'
                f'<form method="post" action="/translation/{number}">'
                f'<label class="label" for="translation-text-{number}">Текст перевода</label>'
                f'<textarea id="translation-text-{number}" class="input translation-textarea" name="text" rows="12">{current}</textarea>'
                f'<button class="button primary" type="submit">Сохранить перевод</button>'
                f'</form></details>')
    return (f'<section class="panel translation-panel" id="translation"><div class="panel-header">'
            f'<span class="panel-title">Русский перевод</span><span class="panel-subtitle">{escape(status)}</span></div>'
            f'{body}{edit}</section>')
