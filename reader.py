#!/usr/bin/env python3
"""Local, server-rendered reader for the OCR book. Run: python3 reader.py."""
from html import escape
from contextvars import ContextVar
from email.parser import BytesParser
from email.policy import default as email_policy
from tempfile import NamedTemporaryFile
import math
from threading import RLock
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit
import os
import re
from functools import lru_cache
import inline_editor
import omission_detection
import reference_sources
import translation
import restored_tables_145

try:
    import pymupdf
except ImportError as exc:
    raise SystemExit("Install dependencies first: python -m pip install -r requirements.txt") from exc

ROOT = Path(__file__).resolve().parent
BOOK = ROOT / 'John_Howard_The_State_of_the_Prisons_English.txt'
parts = re.split(r'\n={60}\nSOURCE: (govard\d+\.pdf)\n={60}\n', BOOK.read_text(encoding='utf-8'))
PAGES = [(parts[i], parts[i + 1].strip()) for i in range(1, len(parts), 2)]
if not PAGES:
    raise RuntimeError('No scanned spreads found in the OCR text')

# Only the rotated scan has been visually checked as a physical scan issue.
# Other marks indicate an OCR/layout risk, not a claim of image quality.
SCAN_ISSUES = {14: 'Исходный скан в PDF повёрнут; в читалке изображение развёрнуто.'}
OCR_ISSUES = {
    4: 'Низкая уверенность повторного OCR на правой странице (73/100).',
    18: 'Низкая уверенность повторного OCR на левой странице (72/100).',
    93: 'Низкая уверенность повторного OCR на правой странице (79/100).',
    110: 'Низкая уверенность повторного OCR на правой странице (78/100).',
    126: 'Низкая уверенность повторного OCR на правой странице (79/100).',
    144: 'Низкая уверенность повторного OCR на правой странице (78/100).',
    145: 'Сложная таблица: повторный OCR правой страницы 57/100.',
    146: 'Сложная таблица: повторный OCR 65/100 и 48/100.',
    147: 'Сложная таблица: повторный OCR 63/100 на обеих страницах.',
    148: 'Сложная таблица: повторный OCR правой страницы 68/100.',
    149: 'Сложная таблица: повторный OCR 64/100 и 62/100.',
    150: 'Сложная таблица: повторный OCR правой страницы 68/100.',
    151: 'Таблицы V–VII восстановлены по скану; данные и примечания можно дополнительно сверить с оригиналом.',
    152: 'Таблицы VII–VIII восстановлены по скану; в таблице VIII сумма за 1782–1783 напечатана как 49, хотя числа в строке дают 59. Сохранено значение оригинала.',
    153: 'Разворот пересечён границей OCR: исходное автоматическое распознавание перепутало две страницы. Базовый текст сверен со сканом; сохранённые правки имеют приоритет.',
    164: 'Содержание: повторный OCR правой страницы 66/100.',
    165: 'Страница смещена относительно середины скана; автоматическое разделение текста ненадёжно.',
}

# OCR marks mean the displayed text has not been fully checked against the
# scan; they are not judgments about physical scan quality. Existing user
# decisions to dismiss marks remain in force.
VERIFIED_SPREADS = {1, 2, 3, 4, 5, 6, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 145, 146, 147, 151, 153, 161, 162, 163, 164, 165}
for _number in range(1, len(PAGES) + 1):
    if _number in VERIFIED_SPREADS:
        OCR_ISSUES.pop(_number, None)
    else:
        OCR_ISSUES.setdefault(_number, 'Текст и оформление требуют сверки с оригинальным сканом; пока используется OCR.')

QUALITY_FILE = ROOT / 'scan_quality.json'
SCAN_OVERRIDES = ROOT / 'scan_overrides'
MAX_SCAN_BYTES = 20 * 1024 * 1024
_SCAN_LOCK = RLock()

@lru_cache(maxsize=1)
def dismissed_issues():
    """Separate scan and OCR decisions; migrate old combined decisions lazily."""
    if not QUALITY_FILE.exists():
        return {'scan': frozenset(), 'ocr': frozenset()}
    data = json.loads(QUALITY_FILE.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('Некорректный файл пометок сканов.')
    legacy = data.get('dismissed', [])
    scan = data.get('dismissed_scan', legacy)
    ocr = data.get('dismissed_ocr', legacy)
    if not all(isinstance(x, list) for x in (legacy, scan, ocr)):
        raise ValueError('Некорректный файл пометок сканов.')
    def numbers(values):
        return frozenset(n for n in values if isinstance(n, int) and not isinstance(n, bool))
    return {'scan': numbers(scan), 'ocr': numbers(ocr)}

def set_issue_dismissed(number, dismissed, kind):
    if kind not in ('scan', 'ocr'):
        raise ValueError('Неизвестный тип пометки.')
    with _SCAN_LOCK:
        current = dismissed_issues()
        marks = {key: set(current[key]) for key in ('scan', 'ocr')}
        if dismissed:
            marks[kind].add(number)
        else:
            marks[kind].discard(number)
        data = json.loads(QUALITY_FILE.read_text(encoding='utf-8')) if QUALITY_FILE.exists() else {}
        data['dismissed_scan'] = sorted(marks['scan'])
        data['dismissed_ocr'] = sorted(marks['ocr'])
        atomic_write(QUALITY_FILE, (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
        dismissed_issues.cache_clear()

def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix='.scan-', delete=False) as temp:
            name = temp.name
            temp.write(data)
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)

def replacement_path(number):
    return SCAN_OVERRIDES / f'{number:03}.bin'

def scan_kind(data):
    if data.startswith(b'%PDF-'):
        return 'pdf'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'jpeg'
    raise ValueError('Поддерживаются только JPG, PNG и одностраничный PDF.')

def render_replacement(data):
    kind = scan_kind(data)
    with pymupdf.open(stream=data, filetype=kind) as doc:
        if doc.needs_pass or doc.page_count != 1:
            raise ValueError('Нужен PDF без пароля с одним разворотом на одной странице.')
        page = doc[0]
        width, height = page.rect.width, page.rect.height
        if not all(math.isfinite(v) and 0 < v <= 20000 for v in (width, height)) or width * height > 60_000_000:
            raise ValueError('Слишком большие размеры изображения.')
        scale = min(1.8, math.sqrt(20_000_000 / (width * height)))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        return pix.tobytes('jpg', jpg_quality=85)

def scan_url(number):
    path = replacement_path(number)
    try:
        version = str(path.stat().st_mtime_ns)
    except FileNotFoundError:
        version = 'original-v2'
    return f'/scan/{number}.jpg?v={version}'

def scan_dropdown_label(number):
    """Mark only an actually observed defect in the currently shown scan."""
    if (number not in SCAN_ISSUES or number in dismissed_issues()['scan']
            or replacement_path(number).exists()):
        return ''
    return '⚠ скан повёрнут'

def quality_label(number):
    labels = []
    scan_label = scan_dropdown_label(number)
    if scan_label:
        labels.append(scan_label)
    if number in OCR_ISSUES and number not in dismissed_issues()['ocr']:
        labels.append('☑ OCR')
    return ' · '.join(labels)

def quality_description(number):
    notes = []
    if scan_dropdown_label(number):
        notes.append(SCAN_ISSUES[number])
    if number in OCR_ISSUES and number not in dismissed_issues()['ocr']:
        notes.append(OCR_ISSUES[number])
    return ' '.join(notes)


# Pilot suggestions are generated by comparing independent OCR to the current
# source text. They are proposals only, never applied without confirmation.
OCR_REVIEW_FILE = ROOT / 'ocr_review' / 'suggestions.json'
def review_suggestions(number):
    if not OCR_REVIEW_FILE.exists():
        return []
    return json.loads(OCR_REVIEW_FILE.read_text(encoding='utf-8')).get(str(number), [])

OCR_FLAGS_FILE = ROOT / 'ocr_review' / 'annotations.json'
def review_flags(number):
    if not OCR_FLAGS_FILE.exists():
        return None
    return json.loads(OCR_FLAGS_FILE.read_text(encoding='utf-8')).get(str(number))

def has_review(number):
    return bool(review_suggestions(number)) or review_flags(number) is not None


# User corrections are kept separately from the scan and the original OCR.
CORRECTIONS_FILE = ROOT / 'corrections.json'
_CORRECTIONS_LOCK = RLock()
_current_corrections = ContextVar('current_corrections', default=())
_replacement_probe = ContextVar('replacement_probe', default=None)

def load_corrections():
    if not CORRECTIONS_FILE.exists():
        return {}
    try:
        data = json.loads(CORRECTIONS_FILE.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError('Не удалось прочитать corrections.json; исправьте файл или восстановите его из копии.') from exc
    if not isinstance(data, dict):
        raise RuntimeError('Некорректный формат corrections.json')
    return data

def store_corrections(data):
    temp = CORRECTIONS_FILE.with_suffix('.json.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temp, CORRECTIONS_FILE)

def corrected(text):
    for rule in _current_corrections.get():
        if isinstance(rule, dict) and isinstance(rule.get('old'), str) and rule['old'] and isinstance(rule.get('new'), str):
            text = text.replace(rule['old'], rule['new'])
    return text


STYLE = '''
:root{color-scheme:light;--ink:#292923;--muted:#76766c;--line:#deddd5;--green:#52664f;--paper:#fffefa}
*{box-sizing:border-box}body{margin:0;background:#f2f1eb;color:var(--ink);font-family:Arial,sans-serif}a{color:inherit;text-decoration:none}a:hover{color:var(--green)}
.header{height:68px;background:#fffefa;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 32px;gap:20px}.logo{font-family:Georgia,serif;font-size:21px;letter-spacing:-.04em}.logo i{font-size:16px;color:var(--green);margin-right:9px}.header-note{font-size:12px;color:var(--muted);letter-spacing:.06em}
.layout{max-width:1800px;margin:auto;display:grid;grid-template-columns:225px minmax(0,1fr);min-height:calc(100vh - 68px)}.sidebar{padding:36px 22px;background:#f9f8f3;border-right:1px solid var(--line)}.eyebrow{font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--green);font-weight:700}.sidebar h1{font:25px/1.2 Georgia,serif;margin:13px 0 7px}.author{color:var(--muted);font-size:13px}.divider{border:0;border-top:1px solid var(--line);margin:27px 0}.label{display:block;font-size:10px;letter-spacing:.13em;text-transform:uppercase;color:#737368;font-weight:700;margin-bottom:10px}.input,select{width:100%;border:1px solid #d6d5cc;border-radius:5px;background:white;color:var(--ink);font-size:13px;padding:11px}.button{display:inline-flex;justify-content:center;align-items:center;border:1px solid #d6d5cc;border-radius:5px;padding:10px 13px;font:600 12px Arial,sans-serif;background:white;color:#3b3b35;cursor:pointer}.button:hover{border-color:#82927c;color:var(--green)}.button.primary{background:var(--green);border-color:var(--green);color:white}.sidebar button{width:100%;margin-top:8px}.small{font-size:12px;line-height:1.6;color:var(--muted)}.sidebar-links a{display:block;font-size:13px;padding:8px 0;color:#51564d}.sidebar-links a.current{font-weight:bold;color:var(--green)}
.content{padding:32px clamp(20px,3vw,48px) 48px;min-width:0}.heading-row{display:flex;justify-content:space-between;align-items:end;gap:16px;margin-bottom:22px}.heading-row h2{font:normal 30px Georgia,serif;margin:7px 0}.heading-row p{margin:0;color:var(--muted);font-size:13px}.top-actions{display:flex;gap:8px;flex-wrap:wrap}.nav{display:flex;justify-content:space-between;align-items:center;gap:12px;background:#fffefa;border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin-bottom:16px}.nav-group{display:flex;gap:8px;align-items:center}.nav .muted{font-size:12px;color:var(--muted)}.disabled{opacity:.4;pointer-events:none}.reader{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px;align-items:start}.panel{background:var(--paper);border:1px solid var(--line);border-radius:8px;overflow:hidden;min-width:0}.panel-header{min-height:54px;padding:15px 20px;display:flex;justify-content:space-between;align-items:center;gap:10px;border-bottom:1px solid var(--line)}.panel-title{font-size:12px;font-weight:700}.panel-subtitle{font-size:11px;color:var(--muted)}.book-text{font:18px/1.85 Georgia,serif;padding:30px clamp(22px,3.1vw,52px);overflow-wrap:anywhere}.book-text p{margin:0 0 1.2em}.scan-frame{display:block;width:100%;height:auto;background:#eeede8}.scan-wrap{background:#eeede8;overflow:auto}.scan-wrap a{display:block}.scan-wrap img{width:100%;height:auto;display:block}.scan-fallback{padding:0 20px 15px;font-size:12px;color:var(--muted)}.notice{font-size:12px;color:var(--muted);line-height:1.6;margin:15px 0}.page-separator{border:0;border-top:1px solid #cdc9bd;margin:2.3em 0 1.4em}.page-number{font:600 11px Arial,sans-serif;letter-spacing:.12em;text-transform:uppercase;color:var(--green);margin:0 0 1.4em}.table-wrap{overflow-x:auto;margin:1.5em 0}.book-table{border-collapse:collapse;width:100%;font:15px/1.5 Georgia,serif}.book-table caption{text-align:left;font-weight:bold;margin:0 0 .8em}.book-table th,.book-table td{border-bottom:1px solid #deddd5;padding:8px 6px;text-align:left}.book-table th:last-child,.book-table td:last-child{text-align:right;white-space:nowrap}.ocr-table{white-space:pre-wrap;font:14px/1.6 monospace;overflow-wrap:anywhere;background:#f6f5ef;padding:12px}.table-warning{font:12px/1.5 Arial,sans-serif;color:var(--muted)}
.results{max-width:920px}.results h2{font:normal 31px Georgia,serif}.result{display:block;background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:20px 24px;margin-bottom:10px}.result:hover{border-color:#8a9985}.result strong{font-size:13px;color:var(--green)}.result p{font:16px/1.6 Georgia,serif;margin:8px 0 0;color:#4f5049}mark{background:#f4dfaa;color:inherit}.empty{padding:30px;background:white;border:1px solid var(--line);border-radius:8px}
@media(max-width:950px){.reader{grid-template-columns:1fr}.layout{grid-template-columns:190px minmax(0,1fr)}}@media(max-width:650px){.header{padding:0 18px;height:58px}.header-note{display:none}.layout{display:block}.sidebar{padding:20px;border-right:0;border-bottom:1px solid var(--line)}.sidebar h1{font-size:21px}.sidebar .divider{margin:15px 0}.sidebar-links{display:flex;gap:16px}.content{padding:22px 14px}.heading-row{align-items:start;flex-direction:column}.heading-row h2{font-size:27px}.book-text{padding:24px;font-size:17px}.nav{flex-wrap:wrap}}
/* The text is presented as an open book; the facsimile follows below. */
.reader{display:block}.reader>.panel{margin-bottom:22px}.book-text{padding:0;font:18px/1.78 Georgia,serif}.book-spread{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));background:#faf8f1}.book-page{background:#fffefa;padding:34px clamp(24px,3.1vw,52px) 42px;min-height:560px;box-shadow:inset -14px 0 18px -22px #777}.book-page:nth-child(2){box-shadow:inset 14px 0 18px -22px #777}.book-page.blank{background:#f8f6ef;min-height:300px}.running-head{display:flex;gap:12px;justify-content:space-between;align-items:baseline;border-bottom:1px solid #e7e3d9;padding-bottom:14px;margin-bottom:32px;color:#6b6a5e;font:600 10px/1.5 Arial,sans-serif;letter-spacing:.13em;text-transform:uppercase}.running-title{overflow-wrap:anywhere}.running-page{white-space:nowrap;color:#546550}.chapter-heading{font:normal 19px/1.35 Georgia,serif;text-align:center;letter-spacing:.05em;margin:1.5em 0 1.1em}.book-page p{margin:0 0 1.15em}.scan-panel{margin-top:24px}.scan-wrap{max-width:1250px;margin:auto}.scan-frame{object-fit:contain}.scan-panel .panel-header{padding:20px 24px}.scan-fallback{padding:14px 24px}.jump-link{color:#52664f;text-decoration:underline}.bottom-nav{margin:20px 0}.book-text .page-number{margin:0}.book-note{max-width:80ch}.reader-heading{display:flex;justify-content:space-between;align-items:center;gap:10px}.reader-heading .panel-title{font-size:14px}.book-page .ocr-table{font-size:13px}.book-table{font-size:14px}
@media(max-width:760px){.book-spread{grid-template-columns:1fr}.book-page{min-height:0;padding:28px 25px 36px}.book-page.blank{display:none}.book-page:nth-child(2){border-top:1px solid #e7e3d9;box-shadow:none}.running-head{margin-bottom:24px}.book-text{font-size:17px}.scan-panel .panel-header{padding:15px}}
/* Desktop: keep the text at the left and the source scan visible to its right. */
@media(min-width:1051px){.layout{grid-template-columns:200px minmax(0,1fr)}.sidebar{padding-left:18px;padding-right:18px}.content{padding:24px 18px 48px}.reader{display:grid;grid-template-columns:minmax(0,1.65fr) minmax(280px,1fr);gap:14px;align-items:start}.reader>.panel{margin-bottom:0}.scan-panel{position:sticky;top:12px;margin-top:0}.scan-wrap{max-height:calc(100vh - 190px);overflow:auto}.book-page{padding:30px clamp(18px,2vw,32px) 38px}.book-text{font-size:17px}}
.chapter-cover{display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;background:#f7f5ed;padding:42px 28px;min-height:560px}.chapter-cover .cover-kicker{font:600 10px/1.5 Arial,sans-serif;text-transform:uppercase;letter-spacing:.2em;color:#7a826e}.chapter-cover .cover-rule{width:42px;border:0;border-top:1px solid #99a48b;margin:25px auto}.chapter-cover .cover-title{font:normal clamp(23px,2.5vw,38px)/1.25 Georgia,serif;letter-spacing:.06em;text-transform:uppercase;color:#343b31;margin:0;overflow-wrap:anywhere}.chapter-cover .cover-author{font:13px/1.5 Georgia,serif;color:#878679;margin-top:28px}
@media(max-width:760px){.chapter-cover{min-height:280px;padding:38px 22px}.chapter-cover .cover-title{font-size:26px}}
/* Verified printed footnote; marker is CSS-generated so the editable text is unchanged. */
.footnote-ref{color:var(--green);text-decoration:none}.footnote-ref::after{content:"1";font:600 .7em Arial,sans-serif;vertical-align:super;margin-left:1px}.book-footnote{display:block;border-top:1px solid #d8d4c8;margin-top:1.2em;padding-top:.8em;font:14px/1.55 Georgia,serif;white-space:pre-line;scroll-margin-top:20px}
/* In-place editing keeps the book and its scan visible side by side. */
.edit-actions{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.edit-status{font:12px/1.5 Arial,sans-serif;color:#52664f;margin:10px 15px;padding:10px 12px;background:#eff5ea;border-radius:5px}.is-editing [data-edit-id]{outline:1px dashed #a1b49b;outline-offset:3px;cursor:text;white-space:pre-wrap}.is-editing [data-edit-id]:empty{display:block;min-height:1.4em}.is-editing [data-edit-id]:empty:before{content:"Пустой фрагмент — нажмите, чтобы добавить текст";color:#999;font:12px Arial,sans-serif}.is-editing [data-edit-id]:focus{outline:2px solid #52664f;background:#f5faef;border-radius:2px}.button[hidden],.edit-status[hidden]{display:none}.button:disabled{opacity:.5;cursor:not-allowed}.history-item{margin-bottom:14px}.history-values{display:grid;grid-template-columns:1fr 1fr;gap:12px}.history-text{font:15px/1.6 Georgia,serif;white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f5ef;padding:12px;border-radius:5px}.history-text strong{display:block;font:600 10px Arial,sans-serif;letter-spacing:.1em;text-transform:uppercase;color:#777;margin-bottom:8px}@media(max-width:700px){.history-values{grid-template-columns:1fr}}
/* Second-pass OCR review */
.review-layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(300px,.9fr);gap:15px;align-items:start}.review-list{min-width:0}.review-card{padding:18px 20px;margin-bottom:10px;background:var(--paper);border:1px solid var(--line);border-radius:8px}.review-top{display:flex;align-items:center;justify-content:space-between;gap:12px;font:11px Arial,sans-serif;color:var(--muted)}.review-comparison{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:12px 0}.review-word{background:#f5f4ee;padding:11px;overflow-wrap:anywhere;font:16px/1.45 Georgia,serif}.review-word small{display:block;font:10px Arial,sans-serif;text-transform:uppercase;color:var(--muted);margin-bottom:7px}.review-context{font:14px/1.6 Georgia,serif;color:#62635a}.review-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.review-actions form{margin:0}.review-scan{position:sticky;top:8px}.review-summary{display:flex;gap:15px;flex-wrap:wrap;font:12px Arial,sans-serif;color:var(--muted);margin:14px 0}.review-state{font:12px Arial,sans-serif;color:var(--green)}.ocr-flags{padding-left:20px}.ocr-flags li{font:12px/1.5 Arial,sans-serif;margin:0 0 14px}.ocr-flags p{font:14px/1.5 Georgia,serif;margin:5px 0;color:#555}.editor-card summary{cursor:pointer;font-size:13px;color:var(--green)}@media(max-width:1050px){.review-layout{grid-template-columns:1fr}.review-scan{position:static;order:-1}}@media(max-width:600px){.review-comparison{grid-template-columns:1fr}}
/* Accessible, explicit word-replacement editor. */
.editor{max-width:1080px}.editor h2{font:normal 30px Georgia,serif}.editor-card{background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:24px;margin:16px 0}.editor-form{display:grid;grid-template-columns:1fr 1fr auto;gap:12px;align-items:end}.editor-form label{font-size:12px;font-weight:bold}.editor-form input{display:block;margin-top:7px}.editor-form button{height:40px}.editor-message{border-radius:5px;padding:12px 15px;background:#eaf1e7;color:#344b32;font-size:13px}.editor-message.error{background:#f8e9e6;color:#883f35}.correction-row{display:flex;align-items:center;justify-content:space-between;gap:15px;padding:12px 0;border-top:1px solid var(--line)}.correction-words{font:15px/1.5 Georgia,serif;overflow-wrap:anywhere}.correction-words span{color:var(--muted);margin:0 8px}.editor-preview{max-height:600px;overflow:auto;border:1px solid var(--line);border-radius:6px}.editor-help{font:13px/1.6 Arial,sans-serif;color:var(--muted)}
@media(max-width:700px){.editor-form{grid-template-columns:1fr}.editor-card{padding:17px}.correction-row{align-items:flex-start;flex-direction:column}}
/* An inscription and a quoted passage on the third spread. */
.epitaph{text-align:center;margin:1.6em auto 1.8em;line-height:1.5}.epitaph-name{font:normal 1em/1.4 Georgia,serif;letter-spacing:.05em}.epitaph-inscription{white-space:pre-line;font:600 .72em/1.6 Georgia,serif;letter-spacing:.09em;margin:.25em 0}.epitaph-year{font:.8em/1.5 Georgia,serif}.book-quotation{margin:1.4em 1em;padding:0 .5em;font-size:.86em;line-height:1.65}.book-quotation p{margin:0}
/* Verified opening pages and Everyman's Library title spread. */
.chapter-start-page .running-head{display:block;text-align:center;border:0;margin:15px 0 26px;font:normal 20px Georgia,serif;letter-spacing:.12em}.chapter-start-page .running-page{display:none}.chapter-start-page .running-title{display:block}.chapter-start-number{text-align:center;font:14px Georgia,serif;margin-top:18px}.spread-5 [data-edit-id="s3"],.spread-5 [data-edit-id="s4"],.spread-5 [data-edit-id="s5"],.spread-5 [data-edit-id="s6"]{display:inline}.spread-5 [data-edit-id="s5"]::before{content:"";display:block;height:1em}.s162-front .running-head,.s162-title-page .running-head{display:none}.s162-front p{white-space:pre-line;text-align:center;font:15px/1.55 Georgia,serif;max-width:330px;margin:10px auto}.s162-title-page{display:flex;flex-direction:column}.s162-title-page p{display:flex;flex-direction:column;flex:1;min-height:520px;white-space:pre-line;text-align:center;font:17px/1.5 Georgia,serif;letter-spacing:.04em}.s162-top,.s162-middle,.s162-bottom{display:block}.s162-middle{margin:auto 0}.s162-bottom{margin-top:auto}
.s164-publisher .running-head{display:none}.s164-publisher p{text-align:center;white-space:pre-line;font:italic 14px/1.4 Georgia,serif;margin:0}.s164-contents{display:flex;flex-direction:column}.s164-contents .running-head{display:block;border:0;text-align:center;font:normal 23px Georgia,serif;letter-spacing:.1em;margin:25px 0 15px}.s164-contents .running-page{display:none}.s164-contents [data-edit-id="s4"]{order:0;text-align:right;font:12px Georgia,serif;margin:0}.s164-contents [data-edit-id="s3"]{order:1;font:13px/1.35 Georgia,serif;text-align:left;margin:0}.s164-contents [data-edit-id="s5"]{order:2;font:13px/1.35 Georgia,serif;white-space:normal;margin:0}.s164-row{display:flex;gap:12px;justify-content:space-between}.s164-number{white-space:pre}.s164-subrow{padding-left:14px}.s164-page-number{order:3;text-align:center;margin-top:auto;padding-top:30px;font:14px Georgia,serif}
.wide-table-spread{grid-template-columns:minmax(0,1fr)}.wide-table-spread .book-page:nth-child(2){border-top:1px solid #e7e3d9}.table151-page pre.table151-text{font:14px/1.55 Georgia,serif;white-space:pre-wrap;overflow-wrap:anywhere;margin:0}.restored-table{display:block;max-width:100%;font:clamp(8px,.7vw,12px)/1.35 Georgia,serif;white-space:normal}.restored-row{display:grid;grid-template-columns:minmax(55px,2.5fr) repeat(var(--numeric-cols),minmax(0,1fr));min-width:0;border-bottom:1px solid #e7e3d9;white-space:normal}.restored-cell{display:block;min-width:0;overflow-wrap:anywhere;text-align:right;padding:3px 1px}.restored-cell:first-child{text-align:left}.table-wrap{overflow:visible}.book-table{table-layout:fixed;width:100%;overflow-wrap:anywhere}.book-table th:last-child,.book-table td:last-child{width:22%;white-space:normal}.reference-excerpt{margin:12px 0;padding:10px;border-left:3px solid #c7d1c1}.reference-excerpt p{white-space:pre-wrap;font:14px/1.5 Georgia,serif;overflow-wrap:anywhere}.restored-heading{display:block;text-align:center;font:normal 19px Georgia,serif;letter-spacing:.08em}
/* Spread 153: last page of Tables followed by the opening notes page. */
.s153-page pre.s153-text{font:16px/1.55 Georgia,serif;white-space:pre-wrap;overflow-wrap:anywhere;margin:0;text-align:justify}.s153-page .s153-source{display:block;text-align:right}.s153-page .s153-footnote{display:block;font-size:12px;line-height:1.4}.s153-notes-page .running-head{display:none}.s153-title,.s153-subtitle,.s153-reference{display:block;text-align:center;white-space:normal}.s153-title{font-size:22px;letter-spacing:.11em;padding-top:45px}.s153-subtitle{font-size:15px}.s153-reference{font-style:italic;font-size:14px}.s153-page-number{text-align:center;font:14px Georgia,serif;margin-top:16px}
/* Faithful styling for the publisher imprint on spread 165. */
.book-page.publisher-page{display:flex;flex-direction:column}.publisher-page .running-head,.publisher-blank .running-head{display:none}.publisher-page [data-edit-id="s0"]{display:flex;flex:1;min-height:520px;flex-direction:column;text-align:center;white-space:pre-line;line-height:1.55;font-size:16px}.publishers-main{display:block;margin:auto 0;letter-spacing:.025em}.publishers-imprint{display:block;margin-top:auto;font-size:12px;font-style:italic}.publisher-blank{background:#f8f6ef}.epigraph-page .running-head,.biography-page .running-head{display:none}.epigraph-page p{text-align:center;white-space:pre-line;margin-top:12px}.biography-page p{max-width:300px;margin:8px auto;text-align:left;font-size:15px}.title-page{display:flex;flex-direction:column;text-align:center}.title-page .running-head{display:block;text-align:center;border:0;font:normal 23px Georgia,serif;margin-bottom:80px;letter-spacing:.02em}.title-page .running-page{display:none}.title-page .chapter-heading{font-size:19px;margin:0}.title-page p{white-space:pre-line;font-size:13px;line-height:1.6;margin-top:auto}.quality-badge{display:inline-block;border-radius:4px;background:#fff1dc;color:#7c5123;font:600 11px/1.4 Arial,sans-serif;padding:5px 8px;margin:5px 0}.scan-controls{padding:14px 20px;display:grid;gap:10px}.scan-controls details form{display:grid;gap:8px;margin-top:12px}.scan-controls input[type=file]{max-width:100%;font-size:12px}.scan-controls label{font:12px/1.5 Arial,sans-serif}.scan-controls p{margin:0}.scan-controls summary{cursor:pointer;list-style:none}.quality-list{max-width:900px}.quality-item{display:flex;justify-content:space-between;gap:16px;padding:15px 18px;margin-bottom:8px;background:var(--paper);border:1px solid var(--line);border-radius:6px;font:13px/1.5 Arial,sans-serif}.quality-item strong{white-space:nowrap;color:var(--green)}.quality-legend{font:12px/1.6 Arial,sans-serif;color:var(--muted)}
/* On a desktop the controls occupy a compact top bar, not a permanent
   side column: leave the full viewport width to the book and the scan. */
@media(min-width:1051px){
 .header{height:52px;padding:0 14px}
 .layout{display:block;max-width:none;min-height:calc(100vh - 52px)}
 .sidebar{display:flex;align-items:center;gap:14px;padding:9px 14px;border-right:0;border-bottom:1px solid var(--line)}
 .sidebar>.eyebrow,.sidebar>h1,.sidebar>.author,.sidebar>.divider,.sidebar>.small{display:none}
 .sidebar-links{display:flex;align-items:center;gap:12px;flex-shrink:0}
 .sidebar-links a{padding:0;white-space:nowrap;font-size:12px}
 .sidebar form{display:flex;align-items:center;gap:6px;margin:0}
 .sidebar form:first-of-type{flex:1;max-width:500px}
 .sidebar form:last-of-type{flex:0 1 320px;margin-left:auto}
 .sidebar form .label{display:none}
 .sidebar input,.sidebar select{padding:9px 10px;min-width:0}
 .sidebar button{width:auto;white-space:nowrap;margin:0;padding:9px 12px}
 .content{padding:14px 12px 40px}
 .heading-row{margin-bottom:12px;align-items:center}
 .heading-row h2{font-size:24px;margin:3px 0}
 .heading-row p{font-size:12px}
 .nav{margin-bottom:12px;padding:7px 9px}
 .reader{grid-template-columns:minmax(0,1.55fr) minmax(310px,1fr);gap:10px}
 .book-page{padding:26px clamp(16px,1.7vw,30px) 34px}
 .scan-panel{top:8px}
 .scan-wrap{max-height:calc(100vh - 150px)}
 .scan-panel .panel-header{padding:14px 16px}
 .scan-fallback{padding:10px 16px}
}


/* Sequential transcription: paragraph breaks, page-local notes and poetry. */
.translation-panel{margin-top:18px}.translation-text{font:16px/1.65 Georgia,serif}.translation-text .verified-paragraph{display:block;margin:0 0 .85em}.translation-edit{margin-top:12px}.translation-textarea{width:100%;min-height:220px;font:14px/1.5 Georgia,serif;white-space:pre-wrap}.translation-panel .fee-table{font-size:13px}.verified-prose-page p:empty{display:none}.verified-prose-page .verified-paragraph{display:block;margin:0 0 .85em}.verified-prose-page .verified-paragraph:last-child{margin-bottom:0}.verified-prose-page .verified-footnote{font-size:.78em;line-height:1.5;margin-top:1em}.verified-note-ref{vertical-align:super;font-size:.72em;line-height:0;color:var(--green);scroll-margin-top:20px}.verified-footnote{scroll-margin-top:20px}.book-verse{display:block;white-space:pre-line;font-size:.86em;line-height:1.5;margin:1.2em auto;max-width:34em}.fee-table{font:14px/1.5 Georgia,serif;margin:.5em 0 1em}.fee-table .restored-row{grid-template-columns:minmax(0,1fr) repeat(var(--numeric-cols),minmax(0,.18fr));padding:3px 0}.fee-table .restored-cell{padding:3px 2px}.fee-table .restored-row:first-child{font-weight:700;border-bottom:1px solid #aaa797}.is-editing .verified-prose-page p:empty{display:block}.is-editing .verified-paragraph,.is-editing .book-verse{display:inline}.is-editing .verified-footnote{display:inline;border:0;margin:0;padding:0;font:inherit}.is-editing .verified-note-ref{vertical-align:baseline;font:inherit}
@media(max-width:650px){.restored-table{font-size:10px}.restored-row{grid-template-columns:minmax(40px,2fr) repeat(var(--numeric-cols),minmax(0,1fr))}.restored-cell{padding:3px 0}.fee-table{font-size:13px}.fee-table .restored-row{grid-template-columns:minmax(0,1fr) repeat(var(--numeric-cols),minmax(0,.22fr))}}

/* Translation reader: comfortable measure, real grid tables, visible history. */
.translation-panel>.translation-text{padding:26px clamp(20px,3vw,48px);max-width:1100px;margin:0 auto}
.translation-text .verified-paragraph{white-space:pre-line}
.translation-text .chapter-heading{margin:1.1em 0 .8em}
.translation-source-note{padding:0 clamp(20px,3vw,48px) 8px}
.translation-tools{display:flex;flex-wrap:wrap;gap:10px;align-items:start;padding:0 clamp(20px,3vw,48px) 16px}
.translation-tools .translation-edit{margin:0;flex:1 1 100%}
.translation-tools .translation-edit[open]{border:1px solid var(--line);border-radius:6px;padding:12px;background:#faf9f4}
.translation-tools .translation-edit form{display:grid;gap:10px;margin-top:12px}
.translation-tools .translation-edit .button{width:auto}
.translation-coverage{padding:0 clamp(20px,3vw,48px) 18px;margin:0}
.translation-table{margin:.7em 0 1.2em}
.translation-table.statistical-table{font-size:12px;line-height:1.4}
.translation-table.statistical-table .restored-row{grid-template-columns:minmax(58px,2.4fr) repeat(8,minmax(0,1fr))}
.translation-table .table-group-header{background:#f3f1e8;font-weight:700}
.translation-table .group-start{border-left:1px solid #d8d4c8;padding-left:4px}
.translation-table .restored-cell{padding:4px 2px}
.footnote-continuation{border-top:1px solid #d8d4c8}
.footnote-continuation::before{content:"Сноска 1 · продолжение с левой страницы";display:block;font:600 10px/1.4 Arial,sans-serif;letter-spacing:.06em;text-transform:uppercase;color:var(--green);margin-bottom:.45em}
@media(max-width:650px){.translation-panel>.translation-text{padding:20px 16px}.translation-tools{padding:0 16px 14px}.translation-coverage{padding:0 16px 16px}.translation-table.statistical-table{font-size:10px}.translation-table.statistical-table .restored-row{grid-template-columns:minmax(44px,2.2fr) repeat(8,minmax(0,1fr))}.translation-table .restored-cell{padding:3px 1px}}

'''

def highlighted(text, query):
    """Escape original OCR text while safely highlighting literal matches."""
    text = corrected(text)
    probe = _replacement_probe.get()
    if probe is not None:
        probe.append(text)
    if not query:
        return escape(text)
    return ''.join('<mark>' + escape(part) + '</mark>' if i % 2 else escape(part)
                   for i, part in enumerate(re.split('(' + re.escape(query) + ')', text, flags=re.IGNORECASE)))

# Printed pagination, not sequential scan numbers. Front matter uses Roman
# numerals in the original, shown as Arabic here. Scans 050 and 051 jump
# from printed page 85 to 88; pages 86–87 are absent from this collection.
def page_labels(number):
    labels = {1: ['9'], 2: ['10', '11'], 3: ['12', '13'],
              4: ['14', '15'], 5: ['17'], 6: ['19'],
              7: ['20', '21'], 8: ['22', '1']}
    if number in labels:
        return labels[number]
    if 9 <= number <= 50:
        first = number * 2 - 16
        return [str(first), str(first + 1)]
    if 51 <= number < 160:
        first = number * 2 - 14
        return [str(first), str(first + 1)]
    if number == 160:
        return ['306', None]
    return [None, None]

# The OCR retained the two scanned halves as separate blank-line blocks.
# Some scans are misaligned, so these boundaries remain approximate.
def page_blocks(body):
    return [block for block in re.split(r'\n\s*\n', body) if block.strip()]

TABLE_2_ROWS = [
    ('In Great Britain and Ireland, 1773–6', '10,318'),
    ('First Foreign Journey, 1775', '1,400'),
    ('Second Ditto, 1776', '1,700'),
    ('Third Ditto, 1778', '4,636'),
    ('In Great Britain and Ireland, 1779', '6,490'),
    ('Fourth Foreign Journey, 1781', '4,465'),
    ('In Great Britain and Ireland, 1782', '8,165'),
    ('Fifth Foreign Journey, 1783', '3,304'),
    ('To Ireland', '715'), ('To Worcester', '238'),
    ('To Hertford, Chelmsford, and Warrington', '602'),
    ('Total', '42,033'),
]

def render_table_2(query):
    rows = ''.join('<tr><td>' + highlighted(name, query) + '</td><td>' + highlighted(miles, query) + '</td></tr>' for name, miles in TABLE_2_ROWS)
    return ('<div class="table-wrap"><table class="book-table"><caption>An Account of the Number of Miles Travelled on the Reform of Prisons</caption>'
            '<thead><tr><th scope="col">Journeys</th><th scope="col">Miles</th></tr></thead><tbody>' + rows + '</tbody></table></div>')

# Recognise only headings clearly separated on their own OCR lines. Do not
# guess a heading from prose. OCR output remains available in the source TXT.
HEADING_REPAIRS = {
    "EDITOR'SINTRODUCTION": "EDITOR'S INTRODUCTION",
    "EDITOR'SNOTE": "EDITOR'S NOTE",
    "PROPOSEDIMPROVEMENTS": "PROPOSED IMPROVEMENTS",
    "VIEWOFDISTRESS INPRISONS": "VIEW OF DISTRESS IN PRISONS",
    "VIEWOF DISTRESS IN PRISONS": "VIEW OF DISTRESS IN PRISONS",
    "VIEW OF DISTRESSIN PRISONS": "VIEW OF DISTRESS IN PRISONS",
    "VIEW OF DISTRESS INPRISONS": "VIEW OF DISTRESS IN PRISONS",
    "BAD CUSTOMSINPRISONS": "BAD CUSTOMS IN PRISONS",
    "ANACCOUNTOFFOREIGNPRISONS": "AN ACCOUNT OF FOREIGN PRISONS",
    "ANACCOUNT OF FOREIGN PRISONS": "AN ACCOUNT OF FOREIGN PRISONS",
    "BADCUSTOMSINPRISONS": "BAD CUSTOMS IN PRISONS",
    "SECTION1I": "SECTION 2",
    "TABLEI": "TABLE 1",
}

def heading_line(line):
    text = line.strip()
    text = re.sub(r'^\d{1,3}\s*', '', text)
    text = re.sub(r'\s+\d{1,3}$', '', text)
    # A single roman numeral is a page number, never a heading.
    if re.fullmatch(r'[ivxlcdm]+', text, re.I):
        return None
    letters = [c for c in text if c.isalpha()]
    if not letters or len(text) > 70 or len(letters) < 4:
        return None
    if sum(c.isupper() for c in letters) / len(letters) < .9:
        return None
    if text.endswith(('.', ',', ':', ';')):
        return None
    section = re.fullmatch(r'SECTION\s*([IVX]+)', text, re.I)
    if section:
        roman = section.group(1).upper()
        values = {'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5, 'VI': 6}
        if roman in values:
            return 'SECTION ' + str(values[roman])
    return HEADING_REPAIRS.get(text, text)

def extract_page_headings(block):
    lines = block.splitlines()
    headings = []
    # Only inspect the beginning of a page; do not strip numbers from prose.
    while lines and len(headings) < 3:
        line = lines[0].strip()
        if not line:
            lines.pop(0)
            continue
        if re.fullmatch(r'(?:[ivxlcdm]+|\d{1,3}|I[O0-9]{1,2})', line, re.I):
            lines.pop(0)
            continue
        heading = heading_line(line)
        if heading is None:
            break
        headings.append(heading)
        lines.pop(0)
    return headings, lines

def prose_html(lines, query):
    # OCR does not preserve indentation: only infer breaks at safe sentence
    # boundaries rather than presenting a whole page as one long paragraph.
    chunks, current = [], []
    for line in lines:
        line = line.strip()
        if not line:
            if current:
                chunks.append(' '.join(current)); current = []
            continue
        if current and re.search(r'[.!?][”"\']?$', current[-1]) and re.match(r'^[A-Z][a-z]', line):
            chunks.append(' '.join(current)); current = []
        current.append(line)
    if current:
        chunks.append(' '.join(current))
    return ''.join('<p>' + highlighted(chunk, query) + '</p>' for chunk in chunks)

BURKE_QUOTE = ("I cannot name this gentleman without remarking that his labours and writings have done much to open the eyes and hearts of all mankind. He has visited all Europe—not to survey the sumptuousness of palaces, or the stateliness of temples; not to make accurate measurements of the remains of ancient grandeur, nor to form a scale of the curiosity of modern art; not to collect medals or collate manuscripts—but to dive into the depths of dungeons and plunge into the infection of hospitals; to survey the mansions of sorrow and pain; to take the gauge and measure of misery, depression, and contempt; to remember the forgotten, to attend to the neglected, to visit the forsaken, and compare and collate the miseries of all men in all countries. His plan is original; and it is full of genius as it is of humanity.")

def render_block(lines, number, position, query):
    if number == 1 and lines and lines[-1].strip().lower() == 'ix':
        lines = lines[:-1]
    text = '\n'.join(lines)
    if number == 3 and position == 0:
        # The centred epitaph was transcribed against the original scan.
        start = text.find('JOHNHOWARD\nWHOEVER THOU ART,THOU STANDEST AT\nTHE TOMB OF THY FRIEND\n1790')
        if start != -1:
            end = start + len('JOHNHOWARD\nWHOEVER THOU ART,THOU STANDEST AT\nTHE TOMB OF THY FRIEND\n1790')
            epitaph = ('<div class="epitaph" aria-label="Epitaph on John Howard’s tomb">'
                       '<div class="epitaph-name">' + highlighted('JOHN HOWARD', query) + '</div>'
                       '<div class="epitaph-inscription">' + highlighted('WHOEVER THOU ART, THOU STANDEST AT', query) + '<br>' + highlighted('THE TOMB OF THY FRIEND', query) + '</div>'
                       '<div class="epitaph-year">1790</div></div>')
            return prose_html(text[:start].splitlines(), query) + epitaph + prose_html(text[end:].splitlines(), query)
    if number == 3 and position == 1:
        # The indented quotation from Burke is distinguished from the editor's prose.
        start = text.find('Icannotname thisgentlemanwithoutremarking')
        end = text.find('IfthesoleinterestofTheStateofthePrisons', start)
        if start != -1 and end != -1:

            return prose_html(text[:start].splitlines(), query) + '<blockquote class="book-quotation"><p>' + highlighted(BURKE_QUOTE, query) + '</p></blockquote>' + prose_html(text[end:].splitlines(), query)
    if number == 2 and position == 1:
        start = text.find('ANACCOUNT OF THENUMBEROFMILESTRAVELLEDONTHEREFORM')
        end = text.find('To God alonebe allpraisel', start)
        if start != -1 and end != -1:
            before, after = text[:start].strip(), text[end:].strip()
            return prose_html(before.splitlines(), query) + render_table_2(query) + prose_html(after.splitlines(), query)
    if 145 <= number <= 153:
        return ('<p class="table-warning">Табличная разметка OCR утрачена; строки приведены без восстановления ячеек. Сверяйте со сканом.</p>'
                '<pre class="ocr-table">' + highlighted(text, query) + '</pre>')
    return prose_html(lines, query)

def render_pages(number, body, query):
    blocks = page_blocks(body)
    if not blocks:
        return '<div class="book-spread"><div class="book-page"><p>На этом развороте текст не распознан.</p></div></div>'
    labels = page_labels(number)
    out = []
    # These scans have a blank left page and a printed page on the right.
    if number in (1, 5, 6, 161) and len(blocks) == 1:
        # These titles are verified from the opening pages; do not invent
        # chapter titles for unnumbered publisher or end-matter pages.
        opening_titles = {1: "Editor's Introduction", 5: "Editor's Note", 6: "Introduction"}
        if number in opening_titles:
            title = escape(opening_titles[number])
            out.append('<section class="book-page chapter-cover" lang="en" aria-label="' + title + '"><div class="cover-kicker">The State of the Prisons</div><hr class="cover-rule"><h3 class="cover-title">' + title + '</h3><div class="cover-author">John Howard</div></section>')
        else:
            out.append('<div class="book-page blank" aria-hidden="true"></div>')
    for i, block in enumerate(blocks):
        label = labels[i] if i < len(labels) else None
        label_text = label if label else '—'
        headings, lines = extract_page_headings(block)
        running = headings[0] if headings else ''
        # Section openings are displayed as chapter titles, not running heads.
        titles = headings[1:] if not running.startswith('SECTION ') else headings
        if running.startswith('SECTION '):
            running = ''
        title_html = ''.join('<h3 class="chapter-heading">' + highlighted(title, query) + '</h3>' for title in titles)
        page_class = 'book-page'
        if number == 165:
            page_class += ' publisher-page' if i == 0 else ' publisher-blank'
        elif number == 161:
            page_class += ' epigraph-page'
        elif number == 163:
            page_class += ' biography-page' if i == 0 else ' title-page'
        out.append('<article class="' + page_class + '" lang="en"><header class="running-head"><span class="running-title">' + highlighted(running, query) + '</span><span class="running-page" aria-label="Страница ' + escape(label_text) + '">' + escape(label_text) + '</span></header>' + title_html + render_block(lines, number, i, query) + '</article>')
    return '<div class="book-spread">' + ''.join(out) + '</div>'

def shell(content, number=1, query='', active='reader'):
    options = ''.join(f'<option value="{i}"' + (' selected' if i == number else '') + f'>Разворот {i:03}' + (' — ' + escape(scan_dropdown_label(i)) if scan_dropdown_label(i) else '') + '</option>' for i in range(1, len(PAGES) + 1))
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>The State of the Prisons — электронная книга</title><style>{STYLE}</style></head><body id="top">
<header class="header"><a href="/" class="logo"><i>✳</i> Howard Archive</a><span class="header-note">ЦИФРОВАЯ КОЛЛЕКЦИЯ &nbsp; / &nbsp; JOHN HOWARD</span></header>
<div class="layout"><aside class="sidebar"><span class="eyebrow">Электронная книга</span><h1>The State of<br>the Prisons</h1><div class="author">John Howard</div><hr class="divider"><div class="sidebar-links"><a class="{'current' if active == 'reader' else ''}" href="/spread/{number}">◧ &nbsp; Читать книгу</a><a class="{'current' if active == 'search' else ''}" href="/search">⌕ &nbsp; Поиск по книге</a><a class="{'current' if active == 'quality' else ''}" href="/quality">⚠ &nbsp; Проверка сканов</a></div><hr class="divider"><form action="/search" method="get"><label class="label" for="query">Поиск по тексту</label><input class="input" id="query" name="q" type="search" placeholder="Слово в книге или переводе…" value="{escape(query, quote=True)}" maxlength="200"><button class="button primary" type="submit">Найти</button></form><hr class="divider"><form action="/go" method="get"><label class="label" for="spread">Перейти к развороту</label><select id="spread" name="spread">{options}</select><button class="button" type="submit">Открыть</button></form><p class="small">Каждый разворот соответствует одному исходному PDF-файлу.</p></aside><main class="content">{content}</main></div></body></html>'''

def segment_state(number, edits=None, query='', rules=None):
    # Caller holds _CORRECTIONS_LOCK for a consistent snapshot.
    if rules is None:
        rules = load_corrections().get(str(number), [])
    saved = edits if edits is not None else inline_editor.load_edits()
    spread = saved['spreads'].get(str(number), {})
    token = _current_corrections.set(rules)
    try:
        base = render_pages(number, PAGES[number - 1][1], '')
        if 1 <= number <= 4 or 10 <= number <= 20 or number in (145, 146, 147):
            base = base.replace('<article class="book-page"', '<article class="book-page verified-prose-page spread-' + str(number) + '"')
        if number in (145, 146, 147):
            base = base.replace('<div class="book-spread">', '<div class="book-spread wide-table-spread">', 1)
        if number in (5, 6):
            base = base.replace('<article class="book-page"', '<article class="book-page chapter-start-page spread-' + str(number) + '"', 1)
            base = base.replace('</article></div>', '<div class="chapter-start-number">' + ('17' if number == 5 else '19') + '</div></article></div>', 1)
        if number == 162:
            base = base.replace('<article class="book-page"', '<article class="book-page s162-front"', 1)
            base = base.replace('<article class="book-page"', '<article class="book-page s162-title-page"', 1)
        if number == 164:
            base = base.replace('<article class="book-page"', '<article class="book-page s164-publisher"', 1)
            base = base.replace('<article class="book-page"', '<article class="book-page s164-contents"', 1)
            base = base.replace('</p></article></div>', '</p><div class="s164-page-number">7</div></article></div>', 1)
        if number in (151, 152):
            base = base.replace('<div class="book-spread">', '<div class="book-spread wide-table-spread">', 1)
            base = base.replace('<article class="book-page"', '<article class="book-page table151-page"')
            base = base.replace('<p class="table-warning">Табличная разметка OCR утрачена; строки приведены без восстановления ячеек. Сверяйте со сканом.</p>', '')
            base = base.replace('<pre class="ocr-table">', '<pre class="table151-text">')
        if number == 153:
            # The scan contains prose and notes, not a table. Keep the
            # original OCR blocks in place for stable editing segment IDs.
            base = base.replace('<article class="book-page"', '<article class="book-page s153-page"', 1)
            base = base.replace('<article class="book-page"', '<article class="book-page s153-page s153-notes-page"', 1)
            base = base.replace('<p class="table-warning">Табличная разметка OCR утрачена; строки приведены без восстановления ячеек. Сверяйте со сканом.</p>', '')
            base = base.replace('<pre class="ocr-table">', '<pre class="s153-text">')
            # Page number 293 is at the foot of the original notes page.
            base = base.replace('</pre></article></div>', '</pre><div class="s153-page-number" aria-label="Страница 293">293</div></article></div>')

    finally:
        _current_corrections.reset(token)
    return inline_editor.render_segments(base, spread.get('segments', {}), query, number)

def history_page(number, message=''):
    with _CORRECTIONS_LOCK:
        saved = inline_editor.load_edits()
        _, segments = segment_state(number, saved)
        entries = saved['spreads'].get(str(number), {}).get('history', [])
    blocks = []
    for i in reversed(range(len(entries))):
        entry = entries[i]
        seg, before, after = entry['id'], entry['before'], entry['after']
        current = segments.get(seg)
        if current == after:
            action = f'<form method="post" action="/restore/{number}"><input type="hidden" name="index" value="{i}"><button type="submit" class="button">Вернуть предыдущий текст</button></form>'
        else:
            action = '<span class="small">Для этого фрагмента есть более новые изменения.</span>'
        kind = {'restore': 'Восстановление', 'scan-verified': 'Сверено со сканом'}.get(entry.get('kind'), 'Изменение')
        blocks.append(f'<section class="editor-card history-item"><div class="heading-row"><div><span class="eyebrow">{kind}</span><p>{escape(entry["time"])} · {escape(seg)}</p></div>{action}</div><div class="history-values"><div class="history-text"><strong>Было</strong>{escape(before)}</div><div class="history-text"><strong>Стало</strong>{escape(after)}</div></div></section>')
    items = ''.join(blocks) or '<div class="empty">Пока нет изменений, сделанных прямо в тексте.</div>'
    notice = '<p class="editor-message">' + escape(message) + '</p>' if message else ''
    content = f'<div class="editor"><div class="heading-row"><div><span class="eyebrow">История правок</span><h2>Разворот {number:03}</h2><p>Здесь сохранены предыдущие и новые варианты каждого изменённого фрагмента. Время указано в UTC.</p></div><a class="button" href="/spread/{number}">← К чтению</a></div>{notice}{items}<p class="notice">Ранние замены слов хранятся отдельно. <a href="/edit/{number}"><u>Посмотреть прежние замены</u></a>.</p></div>'
    return shell(content, number)


def _reader_page(number, query=''):
    filename, body = PAGES[number-1]
    paragraphs, _ = segment_state(number, query=query)
    suffix = '?q=' + quote(query) if query else ''
    prev = f'<a class="button" href="/spread/{number-1}{suffix}">← Назад</a>' if number > 1 else '<span class="button disabled">← Назад</span>'
    next_ = f'<a class="button" href="/spread/{number+1}{suffix}">Вперёд →</a>' if number < len(PAGES) else '<span class="button disabled">Вперёд →</span>'
    scan = '/' + filename
    image_url = scan_url(number)
    quality_note = quality_description(number) if quality_label(number) else None
    badge = '<div class="quality-badge">' + escape(quality_label(number)) + ': ' + escape(quality_note) + '</div>' if quality_note else ''
    replacement = replacement_path(number).exists()
    controls = []
    for kind, issues, name in (('scan', SCAN_ISSUES, 'скана'), ('ocr', OCR_ISSUES, 'OCR')):
        if number not in issues:
            continue
        is_dismissed = number in dismissed_issues()[kind]
        action = ('restore-scan-mark' if kind == 'scan' else 'restore-ocr') if is_dismissed else 'dismiss-' + kind
        text = ('Вернуть пометку ' if is_dismissed else 'Снять пометку ') + name
        status = ('Пометка снята вами.' if is_dismissed else
                  ('Исходный скан повёрнут; загруженная замена не оценена.' if kind == 'scan' and replacement else ''))
        controls.append(f'<form method="post" action="/scan-action/{number}"><input type="hidden" name="action" value="{action}"><button class="button" type="submit">{text}</button><span class="small"> {status}</span></form>')
    issue_control = ''.join(controls)
    page_notice = table_one_audit_html() if number == 147 else ''
    page_notice += ('<p class="scan-fallback">В таблице VIII за 1782–1783 в оригинале напечатано 49, хотя сумма чисел строки — 59. Оставлено значение из книги.</p>' if number == 152 else '')
    text_status = ('ENGLISH · ТЕКСТ' if number in VERIFIED_SPREADS else ('ENGLISH · пометка OCR снята' if number in dismissed_issues()['ocr'] else 'ENGLISH · ☑ OCR'))
    text_note = ('Базовый текст этого разворота сверен со сканом; ваши сохранённые правки имеют приоритет.' if number in VERIFIED_SPREADS else ('Пометка OCR снята вами; базовый текст получен автоматическим распознаванием.' if number in dismissed_issues()['ocr'] else 'Текст получен автоматическим распознаванием и не вычитан полностью: возможны слитные слова, ошибки и неточности в сносках.'))
    source_label = 'Загруженный вами скан' if replacement else 'Оригинальный скан для сверки'
    source_note = ('Показана замена. Исходный PDF и OCR не изменены; распознавание и его подсказки относятся к исходному скану.' if replacement else 'Загрузите целый разворот одним файлом; исходный PDF сохранится. OCR автоматически не пересчитывается.')
    restore_scan = f'<form method="post" action="/scan-action/{number}" onsubmit="return confirm(&quot;Вернуть исходный скан? Загруженная замена будет удалена.&quot;)"><input type="hidden" name="action" value="restore-scan"><button class="button" type="submit">Вернуть исходный скан</button></form>' if replacement else ''
    upload_confirm = ' onsubmit="return confirm(&quot;Заменить уже загруженный скан? Предыдущая замена будет удалена.&quot;)"' if replacement else ''
    scan_controls = f'<div class="scan-controls"><details><summary class="button">Заменить скан</summary><form action="/scan-upload/{number}" method="post" enctype="multipart/form-data"{upload_confirm}><label for="scan-file-{number}">Файл целого разворота: JPG, PNG или одностраничный PDF, до 20 МБ</label><input id="scan-file-{number}" name="scan" type="file" accept="image/jpeg,image/png,application/pdf,.jpg,.jpeg,.png,.pdf" required><button class="button primary" type="submit">Загрузить скан</button></form></details>{restore_scan}{issue_control}<p class="small">{source_note}</p></div>'
    pilot_link = f'<a class="button" href="/review/{number}">Проверка OCR</a>' if has_review(number) else ''
    omissions_link = f'<a class="button" href="/omissions/{number}">Возможные пропуски</a>'
    translation_panel = translation.panel(number, query, english_verified=number in VERIFIED_SPREADS)
    content = f'''<div class="heading-row"><div><span class="eyebrow">Режим чтения</span><h2>Книжный разворот</h2><p>Читайте разворот слева и сверяйте его со сканом справа.</p>{badge}</div><div class="top-actions edit-actions"><button type="button" id="edit-toggle" class="button primary">✎ Редактировать текст</button><button type="button" id="edit-save" class="button primary" hidden>Сохранить</button><button type="button" id="edit-cancel" class="button" hidden>Отмена</button><a class="button" href="/history/{number}">История правок</a>{pilot_link}{omissions_link}<a class="button" href="#translation">Русский перевод</a><a class="button" href="#original-scan">К скану</a><a class="button" href="{scan}" target="_blank" rel="noopener">Открыть PDF ↗</a></div></div>
<nav class="nav" aria-label="Перелистывание"><div class="nav-group">{prev}{next_}</div><span class="muted">Разворот {number:03} / {len(PAGES)}</span></nav>
<p id="edit-status" class="edit-status" role="status" aria-live="polite" hidden></p><noscript><p class="notice">Для редактирования прямо в тексте включите JavaScript или используйте <a href="/edit/{number}"><u>замену слов</u></a>.</p></noscript><div class="reader"><section class="panel"><div class="panel-header"><span class="panel-title">Распознанный текст</span><span class="panel-subtitle">{text_status}</span></div>{page_notice}<div class="book-text" id="editable-book" data-spread="{number}" lang="en">{paragraphs}</div></section><section class="panel scan-panel" id="original-scan"><div class="panel-header"><span class="panel-title">{source_label}</span><span class="panel-subtitle">{escape(filename) if not replacement else "замена"}</span></div><div class="scan-wrap"><a href="{image_url}" target="_blank" rel="noopener" title="Открыть изображение скана крупнее"><img class="scan-frame" src="{image_url}" alt="Скан разворота {number}. Нажмите, чтобы открыть изображение крупнее." loading="lazy"></a></div>{scan_controls}<p class="scan-fallback">Нажмите на изображение для увеличения · <a href="{scan}" target="_blank" rel="noopener"><u>Открыть оригинальный PDF ↗</u></a></p></section></div>{translation_panel}<nav class="nav bottom-nav" aria-label="Перелистывание в конце"><div class="nav-group">{prev}{next_}</div><a href="#top" class="muted">↑ В начало</a></nav><p class="notice book-note">{text_note} Скан показан рядом для сверки; на узких экранах — ниже. Номера страниц вводной части переведены из римских цифр в арабские; границы по OCR могут быть неточными.</p>'''
    return shell(content, number, query).replace('</body>', inline_editor.SCRIPT + '</body>')

def reader_page(number, query=''):
    # Keep replacement rules and per-segment edits in one consistent snapshot.
    with _CORRECTIONS_LOCK:
        return _reader_page(number, query)

def edit_page(number, message='', error=False):
    with _CORRECTIONS_LOCK:
        rules = load_corrections().get(str(number), [])
    token = _current_corrections.set(rules)
    try:
        preview = render_pages(number, PAGES[number-1][1], '')
    finally:
        _current_corrections.reset(token)
    alert = ('<p class="editor-message' + (' error' if error else '') + '" role="status">' + escape(message) + '</p>') if message else ''
    rows = []
    for index, rule in enumerate(rules):
        old, new = escape(rule['old']), escape(rule['new'])
        rows.append(f'<div class="correction-row"><div class="correction-words"><s>{old}</s><span>→</span>{new or "<em>(удалено)</em>"}</div><form action="/edit/{number}" method="post"><input type="hidden" name="action" value="delete"><input type="hidden" name="index" value="{index}"><button class="button" type="submit">Отменить замену</button></form></div>')
    list_html = ''.join(rows) or '<p class="editor-help">На этом развороте пока нет сохранённых исправлений.</p>'
    content = f'''<div class="editor"><div class="heading-row"><div><span class="eyebrow">Личные правки</span><h2>Исправить разворот {number:03}</h2><p>Редактирование текста без изменения оригинального скана.</p></div><a class="button" href="/spread/{number}">← Вернуться к чтению</a></div>{alert}
    <section class="editor-card"><h3>Новая замена</h3><p class="editor-help">Скопируйте слово или фразу из текста ниже. Замена применяется ко всем точным совпадениям на этом развороте, с учётом заглавных букв. Чтобы удалить текст, оставьте поле замены пустым.</p><form class="editor-form" action="/edit/{number}" method="post"><input type="hidden" name="action" value="replace"><label>Как написано сейчас<input class="input" name="old" maxlength="300" required></label><label>Как должно быть<input class="input" name="new" maxlength="300"></label><button class="button primary" type="submit">Сохранить исправление</button></form><p class="editor-help">Если нужно изменить только одно из нескольких одинаковых слов, укажите более длинную фразу. Правки применяются по порядку и сохраняются в отдельном файле <code>corrections.json</code>.</p></section>
    <section class="editor-card"><h3>Сохранённые исправления</h3>{list_html}</section><section class="editor-card"><h3>Текст для сверки</h3><p class="editor-help">Ниже показан текст с уже применёнными правками. Оригинал можно открыть <a href="/spread/{number}#original-scan"><u>на странице чтения</u></a>.</p><div class="editor-preview book-text">{preview}</div></section></div>'''
    return shell(content, number)

def review_page(number, notice=''):
    suggestions = review_suggestions(number)
    image_url = scan_url(number)
    with _CORRECTIONS_LOCK:
        data = inline_editor.load_edits()
        _, segments = segment_state(number, data)
        states = data['spreads'].get(str(number), {}).get('suggestions', {})
    counts = {'pending': 0, 'accepted': 0, 'rejected': 0, 'unavailable': 0}
    cards = []
    for entry in suggestions:
        key, old, new = entry['id'], entry['old'], entry['new']
        segment = segments.get(entry['segment'], '')
        status = states.get(key, {}).get('status', 'pending')
        if status == 'pending' and segment.count(old) != 1:
            status = 'unavailable'
        counts[status] += 1
        if segment.count(old) == 1:
            at = segment.index(old)
            before = segment[max(0,at-70):at]
            after = segment[at+len(old):at+len(old)+100]
            context = '<span>…' + escape(before) + '</span><mark>' + escape(old) + '</mark><span>' + escape(after) + '…</span>'
        else:
            context = 'Исходный фрагмент уже изменён или отсутствует; проверьте его в тексте разворота.'
        if status == 'pending':
            actions = f'<form action="/review/{number}" method="post"><input type="hidden" name="id" value="{escape(key, quote=True)}"><input type="hidden" name="action" value="accept"><button class="button primary" type="submit">Принять</button></form><form action="/review/{number}" method="post"><input type="hidden" name="id" value="{escape(key, quote=True)}"><input type="hidden" name="action" value="reject"><button class="button" type="submit">Отклонить</button></form>'
            status_text = 'Ожидает проверки'
        elif status == 'rejected':
            actions = f'<form action="/review/{number}" method="post"><input type="hidden" name="id" value="{escape(key, quote=True)}"><input type="hidden" name="action" value="reset"><button class="button" type="submit">Вернуть на проверку</button></form>'
            status_text = 'Отклонено'
        else:
            actions = ''
            status_text = 'Принято · можно отменить в истории правок' if status == 'accepted' else 'Нужна ручная проверка'
        cards.append(f'<article class="review-card" id="suggestion-{escape(key, quote=True)}"><div class="review-top"><span>ПРЕДЛОЖЕНИЕ {escape(key)}</span><span class="review-state">{status_text}</span></div><div class="review-comparison"><div class="review-word"><small>В книге сейчас</small>{escape(old)}</div><div class="review-word"><small>Предлагается</small>{escape(new)}</div></div><div class="review-context">{context}</div><div class="review-actions">{actions}</div></article>')
    next_page = f'<a class="button" href="/review/{number+1}">Следующий разворот →</a>' if number < 165 and has_review(number+1) else ''
    alert = '<p class="editor-message">' + escape(notice) + '</p>' if notice else ''
    summary = f'<span>Ожидают: {counts["pending"]}</span><span>Приняты: {counts["accepted"]}</span><span>Отклонены: {counts["rejected"]}</span><span>Изменены вручную: {counts["unavailable"]}</span>'
    flags = review_flags(number) or []
    note_count = sum(flag.get('kind') == 'possible_note' for flag in flags)
    quote_count = sum(flag.get('kind') == 'possible_quote' for flag in flags)
    flag_cards = []
    for flag in flags:
        label = 'Возможное примечание' if flag.get('kind') == 'possible_note' else 'Возможные кавычки'
        side = 'левая' if flag.get('side') == 0 else 'правая'
        # The OCR line is included to make ambiguous findings easy to locate in
        # the second OCR transcript; never treated as verified source text.
        flag_cards.append('<li><strong>' + label + ' · ' + side + ' сторона, OCR строка '
                          + escape(str(flag.get('line', ''))) + '</strong><p lang="en">'
                          + escape(flag.get('text', '')) + '</p></li>')
    flag_body = ''.join(flag_cards) or '<li>Автоматических признаков не найдено. Это не гарантия отсутствия примечаний или кавычек.</li>'
    flags_html = ('<details class="editor-card"><summary><strong>Кавычки и примечания: возможные места</strong> · '
                  + str(note_count) + ' примечаний, ' + str(quote_count) + ' цитат</summary>'
                  + '<p class="editor-help">Подсказки найдены автоматически во втором OCR и могут быть неточными или пропущенными; проверьте скан. '
                  + 'Их текст показан только для поиска и не заменяет книгу.</p><ul class="ocr-flags">'
                  + flag_body + '</ul></details>')
    content = f'''<div class="heading-row"><div><span class="eyebrow">Повторное распознавание</span><h2>Проверка OCR</h2><p>Разворот {number} из 165. Каждое предложение проверяйте по скану.</p></div><div class="top-actions"><a class="button" href="/spread/{number}">← К чтению</a>{next_page}</div></div>{alert}<p class="notice">Варианты получены сравнением двух автоматических распознаваний. Они могут быть ошибочными и не применяются без вашего согласия. Исправляется только показанное в контексте вхождение; принятые изменения записываются в историю.</p><div class="review-summary">{summary}</div><div class="review-layout"><div class="review-list">{flags_html}{''.join(cards) or '<div class="empty">Предложений нет.</div>'}</div><section class="panel review-scan"><div class="panel-header"><span class="panel-title">Скан для сверки</span><a class="panel-subtitle" href="{image_url}" target="_blank" rel="noopener">Увеличить ↗</a></div><div class="scan-wrap"><a href="{image_url}" target="_blank" rel="noopener"><img class="scan-frame" src="{image_url}" alt="Скан разворота {number}"></a></div><p class="scan-fallback">Нажмите на скан для увеличения. OCR выполнен по исходному PDF, не по загруженной замене. <a href="/ocr_review/{number}_0.txt">Повторное OCR: левая сторона</a> · <a href="/ocr_review/{number}_1.txt">правая сторона</a></p></section></div>'''
    # First scan has no text on its left side.
    if number == 1:
        content = content.replace('<a href="/ocr_review/1_0.txt">Повторное OCR: левая сторона</a> · ', 'Повторное OCR: ')
    return shell(content, number)


def omission_candidates(number):
    with _CORRECTIONS_LOCK:
        _, segments = segment_state(number)
    sides = []
    for side in (0, 1):
        path = ROOT / 'ocr_review' / f'{number}_{side}.txt'
        if path.exists():
            sides.append((side, path.read_text(encoding='utf-8')))
    return omission_detection.find_omissions(segments, sides)


def omissions_page(number):
    candidates = omission_candidates(number)
    cards = []
    for candidate in candidates:
        side = 'левая половина' if candidate['side'] == 0 else 'правая половина'
        before = candidate['before']
        after = candidate['after']
        excerpt = (escape(before) + ' <mark>' + escape(candidate['candidate']) + '</mark> ' + escape(after))
        # Search for a literal substring of the current editable segment.
        search = quote(before[-45:])
        cards.append(f'<div class="editor-card"><span class="eyebrow">{side} · фрагмент {escape(candidate["segment"])}</span><p class="small">Второе OCR предлагает возможный пропуск:</p><p lang="en">{excerpt}</p><a class="button" href="/spread/{number}?q={search}">Открыть текст для проверки</a></div>')
    with _CORRECTIONS_LOCK:
        _, visible_segments = segment_state(number)
    reference_groups = []
    for group in reference_sources.references(visible_segments):
        matches = ''.join('<blockquote class="reference-excerpt"><span class="eyebrow">Фрагмент '
                          + escape(match['segment']) + '</span><p lang="en">' + escape(match['excerpt']) + '</p></blockquote>'
                          for match in group['matches']) or '<p class="small">Уверенного совпадения не найдено.</p>'
        reference_groups.append('<details class="editor-card"><summary>' + escape(group['source']) + '</summary>' + matches + '</details>')
    reference_html = ('<section class="reference-results"><h3>Сравнение с загруженными изданиями</h3>'
                      '<p class="small">Это другие издания книги и их автоматическое распознавание; слова, порядок и таблицы могут отличаться от нашего скана. Совпадения приблизительные, использовать их можно только для ручной сверки.</p>'
                      + ''.join(reference_groups) + '</section>') if reference_groups else ''
    image_url = scan_url(number)
    content = f'''<div class="heading-row"><div><span class="eyebrow">Сравнение распознаваний</span><h2>Возможные пропуски</h2><p>Разворот {number:03}</p></div><a class="button" href="/spread/{number}">← К чтению</a></div>
<p class="notice">Подсказки получены сравнением текущего текста с повторным OCR: они могут быть ошибочными, а часть пропусков не обнаружится. В таблицах, сносках и на смещённых сканах возможны ложные совпадения. Сверьте со сканом; ничего не добавляется автоматически. При необходимости исправьте текст прямо в читалке.</p>
<div class="review-layout"><div>{''.join(cards) or '<p class="empty">По этой эвристике совпадений не найдено. Это не означает, что пропусков нет.</p>'}{reference_html}</div><section class="panel review-scan"><div class="panel-header"><span class="panel-title">Скан для сверки</span></div><a href="{image_url}" target="_blank" rel="noopener"><img class="scan-frame" src="{image_url}" alt="Скан разворота {number}"></a></section></div>'''
    return shell(content, number)


def quality_page():
    items = []
    reviewed = []
    for number in sorted(set(SCAN_ISSUES) | set(OCR_ISSUES)):
        for kind, issues, default_label in (('scan', SCAN_ISSUES, '⚠ скан повёрнут'), ('ocr', OCR_ISSUES, '☑ OCR')):
            if number not in issues:
                continue
            dismissed = number in dismissed_issues()[kind]
            replaced = kind == 'scan' and replacement_path(number).exists()
            label = (default_label + ' — пометка снята') if dismissed else (default_label + ' — исходный скан заменён' if replaced else default_label)
            item = f'<a class="quality-item" href="/spread/{number}#original-scan"><strong>Разворот {number:03} · {escape(label)}</strong><span>{escape(issues[number])}</span><span aria-hidden="true">→</span></a>'
            (reviewed if dismissed or replaced else items).append(item)
    content = ('<div class="quality-list"><div class="heading-row"><div><span class="eyebrow">Проверка источника</span><h2>Развороты для проверки</h2>'
               '<p>Отдельно показываем замечания к скану и развороты, которые ещё требуют сверки текста.</p></div></div>'
               '<p class="quality-legend"><strong>⚠ скан повёрнут</strong> — замечание подтверждено визуально. '
               '<strong>☑ OCR</strong> — текст ещё не полностью сверен или сложно восстановить оформление; это не означает, что скан плохой. '
               'Уверенность OCR не является объективной оценкой качества изображения, а список не гарантирует выявление всех проблем.</p>'
               + (''.join(items) or '<p class="small">Все пометки сняты.</p>')
               + (('<h3>Снятые пометки и заменённые сканы</h3><p class="small">Управлять ими можно на странице разворота.</p>' + ''.join(reviewed)) if reviewed else '') + '</div>')
    return shell(content, active='quality')


def search_page(query):
    if not query:
        content = '<div class="results"><span class="eyebrow">Поиск</span><h2>Поиск по книге и переводу</h2><p class="empty">Введите слово или фразу на английском либо русском языке. Поиск охватывает оригинал и подготовленные переводы.</p></div>'
    else:
        results = []
        matched_spreads = set()
        with _CORRECTIONS_LOCK:
            all_rules = load_corrections()
            all_edits = inline_editor.load_edits()
            translation_edits = translation.load_edits()
        for i in range(1, len(PAGES) + 1):
            # Search what is actually displayed, including tables and user
            # edits. Never join neighbouring paragraphs or unrelated cells.
            _, visible_segments = segment_state(i, all_edits, rules=all_rules.get(str(i), []))
            ru_text, _, _ = translation.translation_state(i, translation_edits)
            sources = [('en', 'Оригинал', visible_segments.values(), ''),
                       ('ru', 'Русский перевод', [ru_text] if ru_text else [], '#translation')]
            for lang, label, texts, anchor in sources:
                for flat in texts:
                    match = re.search(re.escape(query), flat, re.IGNORECASE)
                    if not match:
                        continue
                    excerpt = ('…' if match.start() > 100 else '') + flat[max(0,match.start()-100):min(len(flat),match.end()+150)] + ('…' if match.end()+150 < len(flat) else '')
                    suffix = ('&q=' + quote(query)) if not anchor else ('?q=' + quote(query))
                    results.append(f'<a class="result" href="/spread/{i}{suffix}{anchor}"><strong>{label} · РАЗВОРОТ {i:03} &nbsp; →</strong><p lang="{lang}">{inline_editor.highlight(excerpt, query)}</p></a>')
                    matched_spreads.add(i)
                    break
        count = len(matched_spreads)
        results_html = ''.join(results) if results else '<p class="empty">Совпадений не найдено. Перевод подготовлен не для всех разворотов; в нераспознанном английском тексте возможны слитные слова и ошибки.</p>'
        content = f'<div class="results"><span class="eyebrow">Поиск по книге и переводу</span><h2>Результаты поиска</h2><p class="notice">Запрос: <strong>{escape(query)}</strong> · найдено разворотов: {count}</p>{results_html}</div>'
    return shell(content, 1, query, 'search')

def table_one_audit_html():
    """Show arithmetic discrepancies without silently correcting the book."""
    names = ('145L', '145R', '146L', '146R', '147L', '147R')
    tables = [getattr(restored_tables_145, 'T' + name) for name in names]
    page_totals = [[int(x) for x in table.splitlines()[-1].split('\t')[1:]] for table in tables]
    sums = [sum(row[j] for row in page_totals) for j in range(8)]
    printed = [int(x) for x in restored_tables_145.SUMMARY.splitlines()[-1].split('\t')[1:]]
    columns = ('1779: должники, муж.', '1779: должницы', '1779: фелоны и пр.', '1779: мелкие',
               '1782: должники, муж.', '1782: должницы', '1782: фелоны и пр.', '1782: мелкие')
    rows = ''.join(f'<tr><td>{label}</td><td>{value}</td><td>{total}</td><td>{total-value:+d}</td></tr>'
                   for label, value, total in zip(columns, sums, printed) if value != total)
    return ('<details class="scan-fallback table-audit"><summary>Арифметика оригинала: расхождения в итогах таблицы I</summary>'
            '<p>Все числа оставлены как в книге. В сводке на стр. 281 за 1779 год для мелких правонарушителей напечатано 282, '
            'а в итоге соответствующей страницы — 262; за 1782 год в сводке 284, а в итоге страницы — 283. '
            'Кроме того, напечатанные общие итоги не всегда равны сумме шести страничных итогов:</p>'
            '<table class="book-table"><thead><tr><th>Столбец</th><th>Сумма страниц</th><th>Напечатано</th><th>Разница</th></tr></thead>'
            f'<tbody>{rows}</tbody></table><p>Это расхождения самого издания, а не исправления читалки.</p></details>')


def translation_history_page(number, message=''):
    data = translation.load_edits()
    entry = data['spreads'].get(str(number), {})
    entries = entry.get('history', [])
    current = translation.current_text(number, data)
    expected = translation.revision(number, data)
    blocks = []
    kinds = {'edit': 'Изменение', 'reset': 'Возврат к подготовленному переводу', 'restore': 'Восстановление версии'}
    for i in reversed(range(len(entries))):
        event = entries[i]
        action = (f'<form method="post" action="/translation-history/{number}" '
                  f'onsubmit="return confirm(&quot;Восстановить вариант «Было»? Текущий текст сохранится в истории.&quot;)">'
                  f'<input type="hidden" name="index" value="{i}"><input type="hidden" name="expected" value="{expected}">'
                  f'<button type="submit" class="button">Вернуть вариант «Было»</button></form>')
        blocks.append(f'<section class="editor-card history-item"><div class="heading-row"><div><span class="eyebrow">{kinds.get(event.get("kind"), "Изменение")}</span><p>{escape(event.get("time", ""))}</p></div>{action}</div><div class="history-values"><div class="history-text"><strong>Было</strong>{escape(event.get("before", "")) or "<em>Пусто</em>"}</div><div class="history-text"><strong>Стало</strong>{escape(event.get("after", "")) or "<em>Пусто / подготовленный перевод</em>"}</div></div></section>')
    items = ''.join(blocks) or '<div class="empty">У перевода этого разворота пока нет сохранённых изменений.</div>'
    notice = '<p class="editor-message" role="status">' + escape(message) + '</p>' if message else ''
    content = (f'<div class="editor"><div class="heading-row"><div><span class="eyebrow">История перевода</span><h2>Разворот {number:03}</h2>'
               f'<p>Русский перевод. Время указано в UTC. Английский текст и его история хранятся отдельно.</p></div>'
               f'<a class="button" href="/spread/{number}#translation">← К переводу</a></div>{notice}'
               f'<details class="editor-card"><summary>Текущий текст перевода</summary><div class="history-text">{escape(current) or "<em>Перевод ещё не добавлен</em>"}</div></details>{items}</div>')
    return shell(content, number)


def translation_error_page(number, message, status=400, draft=None):
    data = translation.load_edits()
    expected = translation.revision(number, data)
    body = ''
    if draft is not None:
        body = (f'<form method="post" action="/translation/{number}" class="editor-card">'
                f'<p class="notice">Ниже сохранён ваш несохранённый текст. Перед повторным сохранением сравните его с текущей версией в истории: повторная отправка заменит текущий текст, но не удалит историю.</p>'
                f'<input type="hidden" name="expected" value="{expected}">'
                f'<label class="label" for="translation-draft">Ваш текст</label>'
                f'<textarea id="translation-draft" class="input translation-textarea" name="text" rows="14">{escape(draft)}</textarea>'
                f'<button class="button primary" type="submit">Сохранить мой текст поверх текущей версии</button></form>')
    content = (f'<div class="editor"><h2>Перевод не сохранён</h2><p class="editor-message error" role="alert">{escape(message)}</p>'
               f'<div class="top-actions"><a class="button" href="/spread/{number}#translation">К развороту</a>'
               f'<a class="button" href="/translation-history/{number}">История перевода</a></div>{body}</div>')
    return shell(content, number).encode('utf-8'), status


def scan_image(number):
    replacement = replacement_path(number)
    with _SCAN_LOCK:
        if replacement.exists():
            return render_replacement(replacement.read_bytes())
    return original_scan_image(number)

@lru_cache(maxsize=12)
def original_scan_image(number):
    with pymupdf.open(ROOT / PAGES[number - 1][0]) as doc:
        matrix = pymupdf.Matrix(1.8, 1.8).prerotate(-90 if number == 14 else 0)
        return doc[0].get_pixmap(matrix=matrix, alpha=False).tobytes('jpg', jpg_quality=85)

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def handle_review_post(self, number):
        suggestions = review_suggestions(number) if 1 <= number <= len(PAGES) else []
        if not suggestions:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if self.headers.get_content_type() != 'application/x-www-form-urlencoded' or not 0 < length <= 4096:
                self.send_error(400)
                return
            params = parse_qs(self.rfile.read(length).decode('utf-8'), max_num_fields=4)
            key, action = params.get('id', [''])[0], params.get('action', [''])[0]
            entry = next((item for item in suggestions if item['id'] == key), None)
            if entry is None or action not in ('accept', 'reject', 'reset'):
                self.send_error(400)
                return
            with _CORRECTIONS_LOCK:
                data = inline_editor.load_edits()
                _, current = segment_state(number, data)
                spread = data['spreads'].setdefault(str(number), {'segments': {}, 'history': []})
                states = spread.setdefault('suggestions', {})
                status = states.get(key, {}).get('status', 'pending')
                if action in ('accept', 'reject') and status != 'pending' or action == 'reset' and status != 'rejected':
                    self.send_error(409, 'Suggestion already reviewed')
                    return
                if action == 'accept':
                    before = current.get(entry['segment'], '')
                    if before.count(entry['old']) != 1:
                        self.send_error(409, 'The text has changed; review it manually')
                        return
                    after = before.replace(entry['old'], entry['new'], 1)
                    spread['segments'][entry['segment']] = after
                    spread['history'].append({'id': entry['segment'], 'before': before, 'after': after, 'time': inline_editor.stamp(), 'kind': 'edit', 'suggestion': key})
                    states[key] = {'status': 'accepted', 'time': inline_editor.stamp()}
                elif action == 'reject':
                    states[key] = {'status': 'rejected', 'time': inline_editor.stamp()}
                else:
                    states.pop(key, None)
                inline_editor.save_edits(data)
            self.send_response(303)
            self.send_header('Location', f'/review/{number}#suggestion-{key}')
            self.end_headers()
        except (ValueError, UnicodeDecodeError, OSError, KeyError):
            self.send_error(500, 'Unable to save review')

    def handle_inline_post(self, match, restore):
        number = int(match.group(1))
        if not 1 <= number <= len(PAGES):
            self.send_error(404)
            return
        def respond(code, payload):
            data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > 300000:
                return respond(413, {'error': 'Слишком большой запрос.'})
            expected_type = 'application/x-www-form-urlencoded' if restore else 'application/json'
            if self.headers.get_content_type() != expected_type:
                return respond(415, {'error': 'Неподдерживаемый формат запроса.'})
            raw = self.rfile.read(length).decode('utf-8')
            request = parse_qs(raw, keep_blank_values=True, max_num_fields=4) if restore else json.loads(raw)
            with _CORRECTIONS_LOCK:
                data = inline_editor.load_edits()
                _, current = segment_state(number, data)
                spread = data['spreads'].setdefault(str(number), {'segments': {}, 'history': []})
                if restore:
                    index = request.get('index', [''])[0]
                    if not index.isdigit() or int(index) >= len(spread['history']):
                        return respond(400, {'error': 'Запись истории не найдена.'})
                    event = spread['history'][int(index)]
                    # Compare the server-stored revision against the current
                    # segment below. Do not round-trip paragraph text through a
                    # hidden input: form submission normalises line breaks.
                    changes = [{'id': event['id'], 'old': event['after'], 'new': event['before']}]
                else:
                    changes = request.get('edits') if isinstance(request, dict) else None
                if not isinstance(changes, list) or not 1 <= len(changes) <= 200:
                    return respond(400, {'error': 'Нет изменений для сохранения.'})
                seen = set()
                for change in changes:
                    if not isinstance(change, dict):
                        return respond(400, {'error': 'Некорректные данные.'})
                    key, old, new = change.get('id'), change.get('old'), change.get('new')
                    if not isinstance(key, str) or key not in current or key in seen or not isinstance(old, str) or not isinstance(new, str) or len(new) > 30000 or '\x00' in new:
                        return respond(400, {'error': 'Некорректный или слишком длинный фрагмент.'})
                    if current[key] != old:
                        return respond(409, {'error': 'Этот текст уже изменился в другой вкладке. Скопируйте свои изменения и обновите страницу.'})
                    seen.add(key)
                for change in changes:
                    if change['old'] == change['new']:
                        continue
                    spread['segments'][change['id']] = change['new']
                    spread['history'].append({'id': change['id'], 'before': change['old'], 'after': change['new'], 'time': inline_editor.stamp(), 'kind': 'restore' if restore else 'edit'})
                if restore and event.get('suggestion'):
                    spread.get('suggestions', {}).pop(event['suggestion'], None)
                inline_editor.save_edits(data)
            if restore:
                self.send_response(303)
                self.send_header('Location', f'/history/{number}?restored=1')
                self.end_headers()
            else:
                respond(200, {'ok': True})
        except (ValueError, TypeError, KeyError, UnicodeDecodeError, json.JSONDecodeError):
            respond(400, {'error': 'Не удалось прочитать запрос или файл правок.'})
        except OSError:
            respond(500, {'error': 'Не удалось записать файл правок. Изменения не сохранены.'})

    def scan_error(self, number, message, status=400):
        page = shell(f'<div class="editor"><h2>Не удалось изменить скан</h2><p>{escape(message)}</p><a class="button" href="/spread/{number}#original-scan">← Вернуться к развороту</a></div>', number)
        data = page.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def scan_redirect(self, number):
        self.send_response(303)
        self.send_header('Location', f'/spread/{number}#original-scan')
        self.end_headers()

    def handle_scan_upload(self, number):
        if not 1 <= number <= len(PAGES):
            return self.send_error(404)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if self.headers.get_content_type() != 'multipart/form-data' or not 0 < length <= MAX_SCAN_BYTES + 65536:
                return self.scan_error(number, 'Выберите JPG, PNG или PDF размером до 20 МБ.', 413)
            header = ('Content-Type: ' + self.headers['Content-Type'] + '\r\nMIME-Version: 1.0\r\n\r\n').encode('ascii')
            msg = BytesParser(policy=email_policy).parsebytes(header + self.rfile.read(length))
            if not msg.is_multipart() or msg.defects:
                raise ValueError('Не удалось прочитать загруженный файл.')
            parts = list(msg.iter_parts())
            files = [part for part in parts if part.get_param('name', header='content-disposition') == 'scan' and part.get_filename()]
            if len(files) != 1 or any(part.defects for part in parts):
                raise ValueError('Выберите один файл с целым разворотом.')
            payload = files[0].get_payload(decode=True)
            if not payload or len(payload) > MAX_SCAN_BYTES:
                raise ValueError('Файл пуст или превышает 20 МБ.')
            # Decode fully before replacing any existing scan. This also checks
            # the page count and limits rendering memory for hostile inputs.
            render_replacement(payload)
            with _SCAN_LOCK:
                atomic_write(replacement_path(number), payload)
            return self.scan_redirect(number)
        except (ValueError, TypeError, UnicodeError, RuntimeError) as exc:
            return self.scan_error(number, str(exc) or 'Файл повреждён или не поддерживается.')
        except Exception:
            return self.scan_error(number, 'Не удалось полностью обработать файл. Проверьте отображаемый скан.', 500)

    def handle_scan_action(self, number):
        if not 1 <= number <= len(PAGES):
            return self.send_error(404)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if self.headers.get_content_type() != 'application/x-www-form-urlencoded' or not 0 < length <= 4096:
                raise ValueError('Некорректный запрос.')
            params = parse_qs(self.rfile.read(length).decode('utf-8'), max_num_fields=5)
            action = params.get('action', [''])[0]
            with _SCAN_LOCK:
                if action in ('dismiss-scan', 'restore-scan-mark', 'dismiss-ocr', 'restore-ocr'):
                    kind = 'scan' if action in ('dismiss-scan', 'restore-scan-mark') else 'ocr'
                    issues = SCAN_ISSUES if kind == 'scan' else OCR_ISSUES
                    if number not in issues:
                        raise ValueError('Пометка не найдена.')
                    set_issue_dismissed(number, action.startswith('dismiss-'), kind)
                elif action in ('dismiss', 'restore-mark'):
                    # Accept submissions from tabs opened before the upgrade.
                    available = [('scan', SCAN_ISSUES), ('ocr', OCR_ISSUES)]
                    relevant = [kind for kind, issues in available if number in issues]
                    if not relevant:
                        raise ValueError('Пометка не найдена.')
                    for kind in relevant:
                        set_issue_dismissed(number, action == 'dismiss', kind)
                elif action == 'restore-scan':
                    replacement_path(number).unlink(missing_ok=True)
                else:
                    raise ValueError('Неизвестное действие.')
            return self.scan_redirect(number)
        except (ValueError, UnicodeError):
            return self.scan_error(number, 'Некорректный запрос.')
        except OSError:
            return self.scan_error(number, 'Не удалось сохранить изменение.', 500)

    def _translation_request(self, number, fields=6):
        if self.headers.get_content_type() != 'application/x-www-form-urlencoded':
            return None, ('Поддерживается только отправка формы.', 415)
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            return None, ('Некорректная длина запроса.', 400)
        if not 0 < length <= 200000:
            return None, ('Запрос пуст или слишком велик.', 413)
        try:
            params = parse_qs(self.rfile.read(length).decode('utf-8'), keep_blank_values=True, max_num_fields=fields)
        except (UnicodeDecodeError, ValueError):
            return None, ('Не удалось прочитать форму.', 400)
        return params, None

    def _translation_redirect(self, number, history=False):
        self.send_response(303)
        self.send_header('Location', f'/translation-history/{number}?restored=1' if history else f'/spread/{number}#translation')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def handle_translation_post(self, number):
        if not 1 <= number <= len(PAGES):
            return self.send_error(404)
        params, error = self._translation_request(number)
        if error:
            data, status = translation_error_page(number, error[0], error[1])
            return self._html_response(data, status)
        text = params.get('text', [''])[0].replace('\r\n', '\n').replace('\r', '\n').strip()
        expected = params.get('expected', [''])[0]
        if len(text) > translation.MAX_TRANSLATION_CHARS or '\x00' in text:
            data, status = translation_error_page(number, 'Перевод пуст, содержит недопустимый символ или превышает 50 000 знаков.', 400, text)
            return self._html_response(data, status)
        with _CORRECTIONS_LOCK:
            data = translation.load_edits()
            if expected != translation.revision(number, data):
                page, status = translation_error_page(number, 'Перевод уже изменён в другой вкладке. Ваши изменения не отброшены и не записаны поверх новой версии.', 409, text)
                return self._html_response(page, status)
            before = translation.current_text(number, data)
            if text != before:
                translation.record_change(data, number, text, 'edit')
                try:
                    translation.save_edits(data)
                except OSError:
                    page, status = translation_error_page(number, 'Не удалось записать файл перевода. Изменения не сохранены.', 500, text)
                    return self._html_response(page, status)
        self._translation_redirect(number)

    def handle_translation_restore(self, number):
        if not 1 <= number <= len(PAGES):
            return self.send_error(404)
        params, error = self._translation_request(number, fields=4)
        if error:
            return self.send_error(error[1], error[0])
        index = params.get('index', [''])[0]
        expected = params.get('expected', [''])[0]
        with _CORRECTIONS_LOCK:
            data = translation.load_edits()
            entries = data['spreads'].get(str(number), {}).get('history', [])
            if not index.isdigit() or int(index) >= len(entries):
                return self.send_error(400, 'Запись истории не найдена.')
            if expected != translation.revision(number, data):
                page, status = translation_error_page(number, 'Перевод изменился после открытия истории. Обновите историю перед восстановлением.', 409)
                return self._html_response(page, status)
            before = entries[int(index)].get('before', '')
            translation.record_change(data, number, before, 'restore')
            try:
                translation.save_edits(data)
            except OSError:
                return self.send_error(500, 'Не удалось записать файл перевода.')
        self._translation_redirect(number, history=True)

    def do_POST(self):
        path = urlsplit(self.path).path
        upload = re.fullmatch(r'/scan-upload/(\d+)', path)
        action = re.fullmatch(r'/scan-action/(\d+)', path)
        if upload:
            return self.handle_scan_upload(int(upload.group(1)))
        if action:
            return self.handle_scan_action(int(action.group(1)))
        review = re.fullmatch(r'/review/(\d+)', path)
        if review:
            return self.handle_review_post(int(review.group(1)))
        translation_post = re.fullmatch(r'/translation/(\d+)', path)
        if translation_post:
            return self.handle_translation_post(int(translation_post.group(1)))
        translation_restore = re.fullmatch(r'/translation-history/(\d+)', path)
        if translation_restore:
            return self.handle_translation_restore(int(translation_restore.group(1)))
        api = re.fullmatch(r'/api/edits/(\d+)', path)
        restore = re.fullmatch(r'/restore/(\d+)', path)
        if api or restore:
            return self.handle_inline_post(api or restore, bool(restore))
        match = re.fullmatch(r'/edit/(\d+)', path)
        if not match or not 1 <= int(match.group(1)) <= len(PAGES):
            self.send_error(404)
            return
        number = int(match.group(1))
        if self.headers.get_content_type() != 'application/x-www-form-urlencoded':
            self.send_error(415)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            self.send_error(400)
            return
        if not 0 < length <= 8192:
            self.send_error(413)
            return
        try:
            params = parse_qs(self.rfile.read(length).decode('utf-8'), keep_blank_values=True, max_num_fields=10)
        except (UnicodeDecodeError, ValueError):
            self.send_error(400)
            return
        action = params.get('action', [''])[0]
        message = ''
        status = ''
        with _CORRECTIONS_LOCK:
            all_rules = load_corrections()
            rules = all_rules.get(str(number), [])
            if action == 'replace':
                old, new = params.get('old', [''])[0], params.get('new', [''])[0]
                if not old or len(old) > 300 or len(new) > 300 or any(c in old + new for c in '\r\n'):
                    message = 'Введите непустой текст для поиска и однострочную замену длиной не более 300 символов.'
                elif old == new:
                    message = 'Текст замены совпадает с исходным.'
                else:
                    token = _current_corrections.set(rules)
                    probe = []
                    probe_token = _replacement_probe.set(probe)
                    try:
                        # Match only editable text segments, never layout labels or markup.
                        render_pages(number, PAGES[number-1][1], '')
                    finally:
                        _replacement_probe.reset(probe_token)
                        _current_corrections.reset(token)
                    if not any(old in segment for segment in probe):
                        message = 'Точное совпадение не найдено в тексте разворота. Скопируйте слово или фразу из области ниже.'
                    else:
                        rules.append({'old': old, 'new': new})
                        all_rules[str(number)] = rules
                        store_corrections(all_rules)
                        status = 'saved'
            elif action == 'delete':
                index = params.get('index', [''])[0]
                if not index.isdigit() or int(index) >= len(rules):
                    message = 'Исправление не найдено. Обновите страницу и попробуйте снова.'
                else:
                    rules.pop(int(index))
                    if rules:
                        all_rules[str(number)] = rules
                    else:
                        all_rules.pop(str(number), None)
                    store_corrections(all_rules)
                    status = 'deleted'
            else:
                self.send_error(400)
                return
        if status:
            self.send_response(303)
            self.send_header('Location', f'/edit/{number}?status={status}')
            self.end_headers()
            return
        data = edit_page(number, message, error=True).encode('utf-8')
        self.send_response(400)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _html_response(self, data, status=200, head_only=False):
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def do_HEAD(self):
        return self._handle_get(head_only=True)

    def do_GET(self):
        return self._handle_get(head_only=False)

    def _handle_get(self, head_only=False):
        parsed = urlsplit(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        query = params.get('q', [''])[0].strip()[:200]
        scan_match = re.fullmatch(r'/scan/(\d+)\.jpg', path)
        if scan_match:
            number = int(scan_match.group(1))
            if not 1 <= number <= len(PAGES):
                self.send_error(404, 'Spread not found')
                return
            try:
                image = scan_image(number)
            except Exception:
                self.send_error(500, 'Could not render scan')
                return
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            self.send_header('Content-Length', str(len(image)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            if not head_only:
                self.wfile.write(image)
            return
        if path == '/go':
            number = params.get('spread', ['1'])[0]
            dest = '/spread/' + number if number.isdigit() and 1 <= int(number) <= len(PAGES) else '/'
            self.send_response(303)
            self.send_header('Location', dest)
            self.end_headers()
            return
        omission_match = re.fullmatch(r'/omissions/(\d+)', path)
        if omission_match:
            number = int(omission_match.group(1))
            if not 1 <= number <= len(PAGES):
                self.send_error(404, 'Spread not found')
                return
            return self._html_response(omissions_page(number).encode('utf-8'), head_only=head_only)
        review_match = re.fullmatch(r'/review/(\d+)', path)
        history_match = re.fullmatch(r'/history/(\d+)', path)
        edit_match = re.fullmatch(r'/edit/(\d+)', path)
        translation_history_match = re.fullmatch(r'/translation-history/(\d+)', path)
        if translation_history_match:
            number = int(translation_history_match.group(1))
            if not 1 <= number <= len(PAGES):
                self.send_error(404, 'Spread not found')
                return
            page = translation_history_page(number, 'Предыдущий вариант восстановлен; история сохранена.' if 'restored' in params else '')
        elif review_match:
            number = int(review_match.group(1))
            if not has_review(number):
                self.send_error(404, 'Suggestions not available')
                return
            page = review_page(number)
        elif history_match:
            number = int(history_match.group(1))
            if not 1 <= number <= len(PAGES):
                self.send_error(404, 'Spread not found')
                return
            page = history_page(number, 'Предыдущий текст восстановлен.' if 'restored' in params else '')
        elif edit_match:
            number = int(edit_match.group(1))
            if not 1 <= number <= len(PAGES):
                self.send_error(404, 'Spread not found')
                return
            messages = {'saved': 'Исправление сохранено.', 'deleted': 'Исправление отменено.'}
            page = edit_page(number, messages.get(params.get('status', [''])[0], ''))
        elif path == '/' or re.fullmatch(r'/spread/\d+', path):
            number = 1 if path == '/' else int(path.rsplit('/', 1)[1])
            if not 1 <= number <= len(PAGES):
                self.send_error(404, 'Spread not found')
                return
            page = reader_page(number, query)
        elif path == '/search':
            page = search_page(query)
        elif path == '/quality':
            page = quality_page()
        elif re.fullmatch(r'/govard\d{3}\.pdf', path) or re.fullmatch(r'/ocr_review/\d+_[01]\.txt', path):
            return super().do_HEAD() if head_only else super().do_GET()
        else:
            self.send_error(404)
            return
        return self._html_response(page.encode('utf-8'), head_only=head_only)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8080'))
    print(f'Book reader: http://0.0.0.0:{port}/ ({len(PAGES)} spreads)', flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()
