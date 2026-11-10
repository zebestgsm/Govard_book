"""Russian translation layer shown below the English reader text.

Base translations are versioned in ru_translations_*.py. Editorial context
notes live separately in ru_context.py and are never merged into the editable
translation string. User overrides and their history live in
translation_edits.json; the English text is never read from or written to that
file. All writes replace the JSON file atomically.
"""
from datetime import datetime, timezone
from hashlib import sha256
from html import escape
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from ru_translations_front import TRANSLATIONS as FRONT
from ru_translations_core import TRANSLATIONS as CORE
from ru_translations_core2 import TRANSLATIONS as CORE2
from ru_translations_tables import TRANSLATIONS as TABLES
from ru_translations_007_009 import TRANSLATIONS as SPREADS_007_009
from ru_translations_tables2 import TRANSLATIONS as TABLES2
import ru_context

ROOT = Path(__file__).resolve().parent
EDITS_FILE = ROOT / 'translation_edits.json'
MAX_TRANSLATION_CHARS = 50000

BASE_TRANSLATIONS = {}
for _part in (FRONT, CORE, CORE2, TABLES, SPREADS_007_009, TABLES2):
    BASE_TRANSLATIONS.update(_part)
MANUAL_SPREADS = set(BASE_TRANSLATIONS)
try:
    from ru_translations_opus import TRANSLATIONS as OPUS
except ImportError:  # the CI draft may not have been generated yet
    OPUS = {}
WORKING_SPREADS = set(OPUS) - MANUAL_SPREADS
BASE_TRANSLATIONS = {**OPUS, **BASE_TRANSLATIONS}


def load_edits():
    if not EDITS_FILE.exists():
        return {'version': 1, 'spreads': {}}
    data = json.loads(EDITS_FILE.read_text(encoding='utf-8'))
    if (not isinstance(data, dict) or data.get('version') != 1
            or not isinstance(data.get('spreads'), dict)):
        raise ValueError('Неверный формат translation_edits.json')
    for number, entry in data['spreads'].items():
        if (not number.isdigit() or not isinstance(entry, dict)
                or not isinstance(entry.get('history', []), list)
                or ('text' in entry and not isinstance(entry['text'], str))):
            raise ValueError('Неверная запись в translation_edits.json')
        for event in entry.get('history', []):
            if (not isinstance(event, dict)
                    or not isinstance(event.get('before', ''), str)
                    or not isinstance(event.get('after', ''), str)):
                raise ValueError('Неверная история в translation_edits.json')
    return data


def save_edits(data):
    """Write beside the destination, then atomically replace it."""
    name = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=EDITS_FILE.parent,
                                prefix='.translation-', suffix='.tmp', delete=False) as temp:
            name = temp.name
            json.dump(data, temp, ensure_ascii=False, indent=2)
            temp.write('\n')
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(name, EDITS_FILE)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def current_text(number, edits=None):
    saved = edits if edits is not None else load_edits()
    entry = saved['spreads'].get(str(number), {})
    text = entry.get('text')
    if isinstance(text, str) and text.strip():
        return text
    return BASE_TRANSLATIONS.get(number, '')


def translation_state(number, edits=None):
    saved = edits if edits is not None else load_edits()
    entry = saved['spreads'].get(str(number), {})
    text = current_text(number, saved)
    user = isinstance(entry.get('text'), str) and bool(entry['text'].strip())
    if user:
        return text, 'ваш перевод', True
    if text:
        status = 'рабочий перевод' if number in WORKING_SPREADS else 'перевод подготовлен'
        return text, status, False
    return '', 'перевод готовится', False


def revision(number, edits=None):
    """Optimistic-concurrency token for the text shown in an open form."""
    saved = edits if edits is not None else load_edits()
    entry = saved['spreads'].get(str(number), {})
    kind = 'override' if entry.get('text', '').strip() else 'base'
    return sha256((kind + '\0' + current_text(number, saved)).encode('utf-8')).hexdigest()


def record_change(data, number, text, kind='edit'):
    """Append history without discarding earlier versions.

    An empty submission removes only the override, never the authored base.
    Restoring the base text likewise leaves the override absent.
    """
    before = current_text(number, data)
    if text == before:
        return False
    entry = data['spreads'].setdefault(str(number), {})
    base = BASE_TRANSLATIONS.get(number, '')
    if text and text != base:
        entry['text'] = text
    else:
        entry.pop('text', None)
    entry.setdefault('history', []).append({
        'time': stamp(), 'before': before, 'after': text, 'kind': kind,
    })
    return True


def highlight(text, query):
    if not query:
        return escape(text)
    parts = re.split('(' + re.escape(query) + ')', text, flags=re.IGNORECASE)
    return ''.join('<mark>' + escape(p) + '</mark>' if i % 2 else escape(p)
                   for i, p in enumerate(parts))


def _table_html(chunk, query, index):
    lines = chunk.splitlines()
    widths = [len(line.split('\t')) for line in lines]
    columns = max(widths)
    statistical = columns >= 8
    # The statistical translation has a year row followed by category names.
    header_rows = 2 if statistical and re.search(r'\b1779\b', lines[0]) else 1
    css = 'statistical-table' if statistical else 'fee-table'
    rows = []
    for i, line in enumerate(lines):
        cells = line.split('\t')
        header = i < header_rows
        role = 'columnheader' if header else 'cell'
        row_class = 'restored-row' + (' table-group-header' if header else '')
        rows.append(f'<span class="{row_class}" role="row" style="--numeric-cols:{columns - 1}">')
        for j in range(columns):
            cell = cells[j] if j < len(cells) else ''
            cell_class = 'restored-cell'
            if statistical and j in (1, 5):
                cell_class += ' group-start'
            rows.append(f'<span class="{cell_class}" role="{role}">{highlight(cell, query)}</span>')
        rows.append('</span>')
    label = f'Таблица в переводе, часть {index}'
    return (f'<span class="restored-table translation-table {css}" role="table" '
            f'aria-label="{label}">' + ''.join(rows) + '</span>')


def render_translation(text, query=''):
    """Render the editable plain text without changing its source string."""
    out = []
    table_index = 0
    for chunk in re.split(r'(\n\n)', text):
        if chunk == '\n\n' or not chunk:
            out.append(chunk)
        elif '\t' in chunk:
            table_index += 1
            out.append(_table_html(chunk, query, table_index))
        elif re.match(r'^(?:[¹²³]\s|\[Сноска \d+, продолжение\])', chunk):
            out.append('<span class="book-footnote verified-footnote">' + highlight(chunk, query) + '</span>')
        elif len(chunk) < 120 and '\n' not in chunk and re.fullmatch(r'[A-ZА-ЯЁ0-9IVX .,—–-]+', chunk) and any(c.isalpha() for c in chunk):
            out.append('<h3 class="chapter-heading">' + highlight(chunk, query) + '</h3>')
        else:
            out.append('<span class="verified-paragraph">' + highlight(chunk, query) + '</span>')
    return ''.join(out)


def context_notes_html(number):
    """Editorial glosses, kept outside the editable translation string."""
    notes = ru_context.notes_for(number)
    if not notes:
        return ''
    items = ''.join('<li><strong>' + escape(title) + '.</strong> ' + escape(text) + '</li>'
                    for title, text in notes)
    return ('<details class="context-notes"><summary>Пояснения к переводу · '
            f'{len(notes)}</summary><p class="small">Редакционные пояснения к терминам и контексту; '
            'это не часть текста Говарда и не часть редактируемого перевода.</p>'
            f'<ul>{items}</ul></details>')


def panel(number, query='', english_verified=False, edits=None):
    saved = edits if edits is not None else load_edits()
    text, status, user = translation_state(number, saved)
    if text and not user and not english_verified:
        status = 'перевод по черновому английскому тексту'
    if text:
        body = '<div class="translation-text book-text" lang="ru">' + render_translation(text, query) + '</div>'
        if not user and not english_verified:
            body += ('<p class="notice translation-source-note">Русский текст подготовлен, но английский источник этого разворота '
                     'ещё не полностью сверен со сканом. Возможные ошибки распознавания могли перейти в перевод.</p>')
    else:
        body = ('<p class="notice">Русский перевод этого разворота ещё не добавлен. '
                'Он будет появляться здесь по мере последовательной сверки и перевода книги. '
                'Вы можете добавить свой вариант в форме ниже.</p>')
    current = escape(text)
    expected = revision(number, saved)
    edit = (f'<details class="translation-edit"><summary class="button">Редактировать перевод</summary>'
            f'<form method="post" action="/translation/{number}">'
            f'<input type="hidden" name="expected" value="{expected}">'
            f'<label class="label" for="translation-text-{number}">Текст перевода</label>'
            f'<textarea id="translation-text-{number}" class="input translation-textarea" name="text" rows="12">{current}</textarea>'
            f'<p class="small">Пустое поле возвращает подготовленный перевод; прежние варианты остаются в истории. '
            f'В таблицах разделяйте ячейки табуляцией, строки — переносом строки.</p>'
            f'<button class="button primary" type="submit">Сохранить перевод</button>'
            f'</form></details>')
    if len(BASE_TRANSLATIONS) >= 165:
        coverage = (f'Перевод есть для всех 165 разворотов. {len(WORKING_SPREADS)} разворотов — '
                    'рабочий машинный перевод, требующий редакторской вычитки; '
                    f'{len(MANUAL_SPREADS)} разворотов подготовлены вручную.')
    else:
        coverage = f'Подготовлено переводов: {len(BASE_TRANSLATIONS)} из 165 разворотов. Это частичный перевод книги.'
    return (f'<section class="panel translation-panel" id="translation"><div class="panel-header">'
            f'<span class="panel-title">Русский перевод</span><span class="panel-subtitle">{escape(status)}</span></div>'
            f'{body}{context_notes_html(number)}<div class="translation-tools">{edit}'
            f'<a class="button" href="/translation-history/{number}">История перевода</a></div>'
            f'<p class="small translation-coverage">{coverage}</p></section>')
