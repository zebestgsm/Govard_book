"""Rendering, storage and browser UI for editable OCR text segments."""
from datetime import datetime, timezone
from html import escape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
from verified_spreads import VERIFIED_SEGMENTS
from spacing_repairs import SPACING_REPAIRS
from verified_prose import VERIFIED_PROSE, NOTE_TARGETS, CONTINUED_NOTES
from verified_tables import TABLE151_LEFT, TABLE151_RIGHT, TABLE152_LEFT, TABLE152_RIGHT

ROOT = Path(__file__).resolve().parent
EDITS_FILE = ROOT / 'text_edits.json'

# Labels and layout hints for restored tabular material. `mixed` tables have
# more than one text column and must not use the narrow money-column grid.
TABLE_META = {
    (68, 's3'): ('Diet table for San Fernando', 'diet'),
    (79, 's3'): ('Prisoners of war received at Forton, 1777–1782', 'mixed'),
    (81, 's8'): ('Prisoners in Scotland, September 1782', 'fee'),
    (84, 's5'): ('Dublin prisoners by visit date', 'mixed'),
    (87, 's2'): ('Newgate prisoners by visit date', 'fee'),
    (89, 's1'): ('Newgate fees continued', 'fee'),
    (89, 's4'): ('Newgate prisoners in 1787 and 1788', 'mixed'),
    (89, 's7'): ('Fleet prisoners by visit date', 'fee'),
    (92, 's3'): ('Fleet warden’s fees, 1729', 'fee'),
    (92, 's7'): ('Fleet warden’s fees, continued', 'fee'),
    (88, 's12'): ('Newgate fees, 1729 and keeper’s table', 'fee'),
    (90, 's10'): ('Fleet Prison — Clerk of the Papers fees, 1727', 'fee'),
    (91, 's1'): ('Fleet Prison — Clerk of the Papers fees, continued', 'fee'),
    (91, 's2'): ('Fleet Prison — Clerk of the Inquiries fee', 'fee'),
    (95, 's3'): ('Bridewell commitments and prisoners found', 'mixed'),
    (95, 's6'): ('Whitechapel prison fees, 1776', 'fee'),
    (96, 's8'): ('Tothill-fields prisoners by visit date', 'fee'),
    (97, 's1'): ('King’s Bench prisoners by visit date', 'mixed'),
    (101, 's7'): ('Hertfordshire bridewells, prisoners by visit date', 'mixed'),
    (103, 's7'): ('Horsham prisoners by visit date', 'mixed'),
    (112, 's0'): ('Abingdon prisoners', 'mixed'),
    (112, 's12'): ('Oxford Castle prisoners', 'mixed'),
    (119, 's3'): ('Bodmin fees — debtors, first class', 'fee'),
    (119, 's4'): ('Bodmin fees — debtors, second class', 'fee'),
    (119, 's5'): ('Bodmin fees — debtors in general', 'fee'),
    (119, 's8'): ('Bodmin fees — felons and bridewell', 'fee'),
    (137, 's3'): ('Contractors’ receipts for passage to Maryland', 'mixed'),
    (144, 's3'): ('Prison diet schedule', 'diet'),
    (106, 's6'): ('Cambridge bridewell prisoners', 'mixed'),
    (110, 's6'): ('Stamford town gaol fees', 'fee'),
    (111, 's7'): ('Reading bridewell prisoners by visit date', 'mixed'),
    (112, 's4'): ('Oxford Castle prisoners', 'mixed'),
    (116, 's2'): ('Berkeley bridewell returns and prisoners', 'mixed'),
    (128, 's3'): ('Carnarvon prisoners by visit date', 'mixed'),
    (136, 's1'): ('Convicts in the hulks in 1788', 'mixed'),
    (148, 's1'): ('Table I (continued) and Table II — Ireland, 1787–1788', 'fee'),
    (148, 's3'): ('Table III — England and Wales, 1787–1788 (part)', 'fee'),
    (149, 's1'): ('Table III — England and Wales, continued', 'fee'),
    (149, 's3'): ('Table III — totals and London prisons, 1788', 'fee'),
    (150, 's2'): ('Table IV — convicts ordered for transportation, 1769–1776', 'fee'),
    (150, 's5'): ('Table IV — convicts ordered for transportation, continued', 'fee'),
}

# Publisher imprint on the final scan: verified against the original image.
# Defaults never replace a user's saved segment. Segment IDs remain unchanged.
PUBLISHER_IMPRINT = ('PUBLISHERS:\n\nJ. M. DENT & SONS LTD.\n'
                     'ALDINE HOUSE · BEDFORD STREET\nLONDON W.C.2\n\n'
                     'E. P. DUTTON & COMPANY INC.\n286-302 FOURTH AVENUE\nNEW YORK\n\n'
                     'Made in Great Britain at The Temple Press, Letchworth, Herts (K 11)')

# Spread 153: manually transcribed against the original scan. These defaults
# keep the existing three segment IDs; user overrides always take priority.
SPREAD_153_LEFT = (
    'keeper of Tothill-fields bridewell,¹ should be exposed to the unhappiness of leaving a wife, or children, or both, without any comfortable provision for their support? This object appears the more desirable, as it may now be hoped that the abolition of the tap, and the allowance of salaries in lieu of the profits which were derived from the sale of liquors, will be the means of engaging people of more credit, and who will pay greater attention to their duty, to undertake the office of gaolers or keepers of prisons. Such persons I have observed to be placed in foreign prisons; and among them, some of their wives take a very active part, looking frequently in upon the prisoners of their own sex, and paying a kind attention to them.\n\n'
    '“Should the plan take place, during my life, of establishing a permanent charity, under some such title as that at Philadelphia, viz., “A Society for alleviating the Miseries of Public Prisons,” and annuities be engrafted thereupon for the above-mentioned purpose, I would most readily stand at the bottom of a page as a subscriber of £500; or if such a society shall be constituted within three years after my death, this sum shall be paid out of my estate.”\n\n'
    '[From An Account of Lazarettos, p. 259.]\n\n'
    '1 Mr. George Smith, who died in 1786, was keeper of Tothill-fields bridewell at my first visit in April 1774; and my many subsequent visits fully convinced me of his integrity and humanity. He was a striking example of a constant, unabated zeal in the discharge of the important trust reposed in him.'
)
SPREAD_153_RIGHT = (
    'NOTES\n\nNOTE A.—BRIDEWELLS\n(See page 37)\n\n'
    'THE distinction between gaols and bridewells (or houses of correction) was historical, and, as is evident from Howard’s own work, had already become blurred. Thus, in 1779, an act for establishing a national penitentiary to relieve the hulks (see Note D), constituted both prisons and bridewells penitentiaries, pending the erection of such an institution, although the distinction was not abolished by law until 1865.\n\n'
    'Gaols, properly speaking, were places where individuals could be kept in safe custody pending their trial, their execution, their transportation, or the payment of their debts. Detention in them, theoretically at least, was not as an end in itself, but a temporary expedient.\n\n'
    'Bridewells, on the other hand, were established under the Elizabethan Poor Law Acts as houses of correction for dissolute paupers and idle apprentices. They were thus penal establishments, both in intention and in fact.\n\n'
    'NOTE B.—PROPOSED IMPROVEMENTS\n(See page 19)\n\n'
    'Howard’s recommendations for reform were obviously sane, practicable, and desirable, yet a century after he began his work they were only partially adopted, while even to-day there are particulars in which prisons could be improved on lines suggested by him. Nevertheless, the story of the prisons since 1777 is the story of the gradual approximation to Howard’s ideals.\n\n'
    'Enlightened opinion was in favour of the reforms from the first, and Parliament showed its approval of them even during Howard’s lifetime. But Parliament’s control over the prisons was rudimentary at this stage, and it was only as central control increased that an improvement in their condition became general.'
)


def load_edits():
    if not EDITS_FILE.exists():
        return {'version': 1, 'spreads': {}}
    data = json.loads(EDITS_FILE.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('spreads'), dict):
        raise ValueError('Неверный формат файла text_edits.json; восстановите файл из резервной копии.')
    return data


def save_edits(data):
    tmp = EDITS_FILE.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, EDITS_FILE)


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def highlight(text, query):
    if not query:
        return escape(text)
    return ''.join('<mark>' + escape(part) + '</mark>' if i % 2 else escape(part)
                   for i, part in enumerate(re.split('(' + re.escape(query) + ')', text, flags=re.IGNORECASE)))


# Exact contextual anchors for words crossed by the distorted fold on page 20.
# Only unchanged matches are marked; edited text is never substituted.
SCAN_18_FOLD = {
    's0': [('upon trial,', 'trial'), ('honest employment;', 'employment'),
           ('one will set', 'will'), ('cause of his being', 'his'),
           ('that require but', 'require'), ('go from door', 'from'),
           ('be lamented,', 'lamented'), ('being cherished,', 'cherished'),
           ('penitent should', 'should'), ('again, though reluctant', 'though'),
           ('back to his former', 'his'), ('might have been', 'have')],
    's1': [('to redress these', 'redress'), ('into consideration is', 'consideration'),
           ('other prisons are', 'prisons'), ('so totally unfit', 'totally'),
           ('in their stead', 'their'), ('be improved upon', 'improved'),
           ('by the keeper,', 'keeper'), ('a thorough repair', 'thorough'),
           ('can to those who', 'those'), ('the liberty to', 'liberty'),
           ('hopes that some', 'that'), ('and benevolent task', 'benevolent'),
           ('which I can', 'I'), ('word of the', 'the')],
    's2': [('gaol, and indeed', 'and'), ('airy, and if possible', 'and'),
           ('found prisons situated', 'prisons'), ('have) subterraneous dungeons', 'subterraneous'),
           ('and by their nearness', 'by'), ('almost as noxious,', 'noxious')],
    's3': [('gaol should be near', 'should'), ('be not so near', 'not'),
           ('within the reach of floods', 'reach'), ('first Discharged Prisoners’', 'Discharged'),
           ('next century.', 'century')],
}


# The right edge of page 187 bends into the gutter of the scan.
SCAN_100_FOLD = {'s7': [('a hundred and seventy-five', 'hundred'),
                        ('this incommodious prison', 'incommodious'),
                        ('estate bequeathed to him', 'bequeathed'),
                        ('debts do not exceed £4', 'debts'),
                        ('in Oxfordshire for charitable', 'Oxfordshire')]}


def highlight_scan_fold(text, segment_id, query, contexts=SCAN_18_FOLD):
    matches = []
    for context, word in contexts.get(segment_id, ()):
        start = text.find(context)
        if start >= 0 and text.find(context, start + 1) < 0:
            start += context.index(word)
            matches.append((start, start + len(word)))
    matches.sort()
    result, pos = [], 0
    for start, end in matches:
        if start < pos:
            continue
        result.append(highlight(text[pos:start], query))
        result.append('<strong class="scan-fold-word">' + highlight(text[start:end], query) + '</strong>')
        pos = end
    result.append(highlight(text[pos:], query))
    return ''.join(result)


def render_verified_block(value, spread, segment_id, query):
    """Style verified paragraphs, notes, verse and tables without changing text.

    Every delimiter and tab remains a text node: contentEditable, search and
    saved overrides therefore round-trip the exact same plain text as before.
    """
    continuation = CONTINUED_NOTES.get(spread, {}).get(segment_id)
    if continuation and value:
        target = 'footnote-' + str(spread) + '-' + segment_id + '-' + continuation['number']
        return ('<span class="book-footnote verified-footnote footnote-continuation" id="' + target + '">'
                + highlight(value, query) + '</span>')
    targets = NOTE_TARGETS.get(spread, {}).get(segment_id, {})
    superscripts = {'¹': '1', '²': '2', '³': '3', '⁴': '4', '⁵': '5'}

    def text_html(text, skip_initial_note=False):
        prefix = ''
        if skip_initial_note and text[:1] in superscripts:
            prefix, text = text[:1], text[1:]
        pieces = re.split(r'([¹²³⁴⁵])', text)
        result = [highlight(prefix, query)] if prefix else []
        for piece in pieces:
            number = superscripts.get(piece)
            if number and number in targets:
                target = 'footnote-' + str(spread) + '-' + targets[number] + '-' + number
                result.append('<a class="verified-note-ref" href="#' + target + '">' + highlight(piece, query) + '</a>')
            else:
                if spread == 18:
                    result.append(highlight_scan_fold(piece, segment_id, query))
                elif spread == 100:
                    result.append(highlight_scan_fold(piece, segment_id, query, SCAN_100_FOLD))
                else:
                    result.append(highlight(piece, query))
        rendered = ''.join(result)
        # Recreate the shared-value brace without inserting text into the
        # editable segment or assigning the amount to only one category.
        if spread == 103 and segment_id == 's7':
            rendered = rendered.replace('Fees: Debtors, Felons, £1 4s. 10d.',
                '<span class="shared-brace"><span>Fees: </span><span class="brace-labels"><span>Debtors, </span><span>Felons, </span></span><span class="brace-value">£1 4s. 10d.</span></span>')
        elif spread == 87 and segment_id == 's2':
            rendered = rendered.replace('Allowance: Debtors, Felons, a penny loaf a day',
                '<span class="shared-brace"><span>Allowance: </span><span class="brace-labels"><span>Debtors, </span><span>Felons, </span></span><span class="brace-value">a penny loaf a day</span></span>')
        return rendered

    # Statistical prison profiles are printed as line-by-line entries, not
    # prose. Keep the actual whitespace and text nodes for inline editing.
    def prose_html(text):
        profiles = {(87, 's2'), (96, 's8'), (103, 's7'),
                    (112, 's4'), (128, 's3')}
        if ((spread, segment_id) not in profiles or
                not text.lstrip().startswith(('Gaoler.', 'Keeper.', 'Prisoners.',
                                               'Chaplain.', 'Surgeon.'))):
            return '<span class="verified-paragraph">' + text_html(text) + '</span>'
        parts = ['<span class="prison-profile">']
        for line in text.splitlines(keepends=True):
            row = line.rstrip('\n')
            ending = line[len(row):]
            # Carnarvon's three fee lines have separate category and amount
            # columns. Preserve the printed spaces as text, not as layout.
            if spread == 128 and segment_id == 's3':
                fee = re.match(r'^((?:Fees:[ ]*)?)([ ]*(?:Debtors,|Felons,)?[ ]*)(£.*)$', row)
                if fee:
                    parts.append('<span class="profile-row profile-fees"><span class="profile-label">'
                                 + text_html(fee.group(1)) + '</span><span class="profile-category">'
                                 + text_html(fee.group(2)) + '</span><span class="profile-value">'
                                 + text_html(fee.group(3)) + '</span></span>' + ending)
                    continue
            # Shared braces are a single semantic unit; do not split them.
            if ((spread == 87 and row.startswith('Allowance: Debtors, Felons,')) or
                    (spread == 103 and row.startswith('Fees: Debtors, Felons,'))):
                parts.append('<span class="profile-row profile-whole">' + text_html(row) + '</span>' + ending)
                continue
            match = re.match(r'^((?:Gaoler\.|Keeper\.|Prisoners\.|Chaplain\.|Surgeon\.|Salary:?|Fees:|Transports:|Licence:|Allowance:|Garnish:|Number:|Duty:)[ \t]*)(.*)$', row)
            label, body = (match.group(1), match.group(2)) if match else ('', row)
            parts.append('<span class="profile-row"><span class="profile-label">' + text_html(label)
                         + '</span><span class="profile-value">' + text_html(body) + '</span></span>' + ending)
        return ''.join(parts) + '</span>'

    if spread == 142 and segment_id == 's2' and value.startswith('the proposed dietary'):
        return '<span class="book-footnote verified-footnote previous-spread-note">' + text_html(value) + '</span>'

    if spread == 137 and segment_id == 's3' and value.startswith('Divine Providence,'):
        # The previous spread's note ends here; two new notes follow. The
        # receipts and their headerless table are all part of note 2.
        chunks = value.split('\n\n')
        parts = []
        for i, chunk in enumerate(chunks):
            if i:
                parts.append('\n\n')
            if i == 0:
                parts.append('<span class="book-footnote verified-footnote previous-page-note">' + text_html(chunk) + '</span>')
            elif i in (1, 2):
                parts.append('<span class="book-footnote verified-footnote" id="footnote-137-s3-' + str(i) + '">' + text_html(chunk, True) + '</span>')
            elif '\t' in chunk:
                parts.append('<span class="restored-table fee-table long-note-part" role="table" aria-label="Contractors’ receipt">')
                for line in chunk.splitlines(keepends=True):
                    row = line.rstrip('\n'); ending = line[len(row):]
                    cells = row.split('\t')
                    parts.append('<span class="restored-row" role="row" style="--numeric-cols:' + str(len(cells)-1) + '">')
                    for j, cell in enumerate(cells):
                        parts.append('<span class="restored-cell" role="cell">' + text_html(('\t' if j else '') + cell) + '</span>')
                    parts.append('</span>' + ending)
                parts.append('</span>')
            else:
                parts.append('<span class="long-note-part">' + text_html(chunk) + '</span>')
        return ''.join(parts)

    if spread == 75 and segment_id == 's4' and value.startswith('power of any,'):
        # A note begun on the preceding spread ends here, followed by two
        # separate notes on this page. Keep each independently styled.
        chunks = value.split('\n\n')
        output = ['<span class="book-footnote verified-footnote footnote-continuation">' + text_html(chunks[0]) + '</span>']
        for chunk in chunks[1:]:
            note = re.match(r'^([12])\s', chunk)
            if note:
                output.append('\n\n<span class="book-footnote verified-footnote" id="footnote-75-s4-' + note.group(1) + '">' + text_html(chunk, True) + '</span>')
            else:
                output.append('\n\n' + text_html(chunk))
        return ''.join(output)

    if spread == 33 and segment_id == 's7' and '\n\n¹ ' in value:
        # The printed dietary regulation is a single long footnote containing
        # a small beer table. Preserve every text node for inline editing.
        prose, note = value.split('\n\n', 1)
        parts = [render_verified_block(prose, spread, segment_id, query), '\n\n',
                 '<span class="book-footnote verified-footnote diet-regulation" id="footnote-33-s7-1">']
        in_table = False
        for line in note.splitlines(keepends=True):
            ending = '\n' if line.endswith('\n') else ''
            row = line[:-1] if ending else line
            if '\t' in row:
                if not in_table:
                    parts.append('<span class="restored-table mixed-table beer-allowance" role="table" aria-label="Daily beer allowance in cans">')
                    in_table = True
                left, right = row.split('\t', 1)
                role = 'columnheader' if left == '' and right == 'CANS' else 'cell'
                parts.append('<span class="restored-row" role="row" style="--numeric-cols:1"><span class="restored-cell" role="cell">' + highlight(left, query) + '</span><span class="restored-cell" role="' + role + '">' + highlight('\t' + right, query) + '</span></span>' + ending)
            else:
                if in_table:
                    parts.append('</span>'); in_table = False
                parts.append(highlight(row + ending, query))
        if in_table:
            parts.append('</span>')
        parts.append('</span>')
        return ''.join(parts)

    if spread == 73 and segment_id == 's7' and value.startswith('1 The number of prisoners,'):
        # The original footnote contains a small, headerless table. Keep its
        # tabs and line breaks editable while preserving its footnote styling.
        parts = ['<span class="book-footnote verified-footnote" id="footnote-73-s7-1">']
        in_table = False
        for line in value.splitlines(keepends=True):
            ending = '\n' if line.endswith('\n') else ''
            row = line[:-1] if ending else line
            if '\t' in row:
                if not in_table:
                    parts.append('<span class="restored-table fee-table" role="table" aria-label="Prisoners in the Grand Châtelet">')
                    in_table = True
                left, right = row.split('\t', 1)
                parts.append('<span class="restored-row" role="row" style="--numeric-cols:1"><span class="restored-cell" role="cell">' + highlight(left, query) + '</span><span class="restored-cell" role="cell">' + highlight('\t' + right, query) + '</span></span>' + ending)
            else:
                if in_table:
                    parts.append('</span>')
                    in_table = False
                parts.append(highlight(row + ending, query))
        if in_table:
            parts.append('</span>')
        return ''.join(parts) + '</span>'

    output = []
    for chunk in re.split(r'(\n\n)', value):
        if chunk == '\n\n' or not chunk:
            output.append(chunk)
            continue
        note = re.match(r'^([¹²³⁴⁵1-5])\s', chunk)
        marker = note.group(1) if note else ''
        number = superscripts.get(marker, marker)
        # Arabic numerals are footnotes only at explicitly mapped destinations;
        # otherwise numbered prose must remain ordinary text.
        destinations = NOTE_TARGETS.get(spread, {}).values()
        mapped_note = any(mapping.get(number) == segment_id for mapping in destinations)
        if note and (marker in superscripts or mapped_note):
            target = 'footnote-' + str(spread) + '-' + segment_id + '-' + number
            output.append('<span class="book-footnote verified-footnote" id="' + target + '">' + text_html(chunk, True) + '</span>')
        elif '\t' in chunk:
            # Group only consecutive tab-separated lines. Prose introducing or
            # following a table must not become a one-cell table row.
            has_header = not ((spread == 16 and segment_id in ('s7', 's11')) or (spread == 17 and segment_id == 's1') or (spread == 35 and segment_id == 's1') or (spread, segment_id) in ((79, 's3'), (84, 's5'), (89, 's4'), (91, 's2'), (95, 's3'), (101, 's7'), (106, 's6'), (111, 's7'), (112, 's0'), (136, 's1')))
            if spread == 119 and segment_id in ('s3', 's4', 's5', 's8'):
                has_header = chunk.startswith('\t£\ts.\td.')
            if (spread, segment_id) == (116, 's2'):
                # Only the second of the three lists has printed column headings.
                has_header = chunk.splitlines()[0].startswith('\t')
            if (spread, segment_id) in TABLE_META:
                label, table_kind = TABLE_META[(spread, segment_id)]
            elif spread == 16 and segment_id == 's10':
                label, table_kind = 'Number of prisoners, spring 1776', 'fee'
            elif spread == 16 and segment_id == 's11':
                label, table_kind = 'Thatched House Society, 27 March 1782', 'fee'
            elif spread == 17 and segment_id == 's1':
                label, table_kind = 'Bristol and Dublin small-debt societies', 'fee'
            elif spread == 35 and segment_id == 's1':
                label, table_kind = 'Executions at Amsterdam, 1693–1766', 'fee'
            elif spread == 16 and segment_id == 's7':
                label, table_kind = 'Fees for the copy of a calendar, 1735', 'fee'
            elif spread in (145, 146, 147):
                label, table_kind = 'Table I — prisoners in England and Wales, 1779 and 1782', 'statistical'
            else:
                label, table_kind = 'Fees of the Clerk of the Peace', 'fee'
            table_lines = []
            prose_lines = []
            def flush_prose():
                if prose_lines:
                    output.append(prose_html(''.join(prose_lines)))
                    prose_lines.clear()
            def flush_table():
                if not table_lines:
                    return
                rows = []
                # Every row must use the same grid, even when a trailing cell
                # is absent. Empty visual cells carry no text, so editing and
                # history keep the original tab-separated value intact.
                column_count = max(len(line.rstrip('\n').split('\t')) for line in table_lines)
                for index, line in enumerate(table_lines):
                    ending = '\n' if line.endswith('\n') else ''
                    row = line[:-1] if ending else line
                    cells = row.split('\t')
                    role = 'columnheader' if index == 0 and has_header else 'cell'
                    rows.append('<span class="restored-row" role="row" style="--numeric-cols:' + str(column_count-1) + '">')
                    for cell_index, cell in enumerate(cells):
                        cell_text = ('\t' if cell_index else '') + cell
                        # On these dated lists the printed repetition mark and
                        # the count occupy separate positions within the cell.
                        is_count = (((spread, segment_id) in ((84, 's5'), (89, 's4')) and cell_index > 0)
                                    or ((spread, segment_id) in ((95, 's3'), (101, 's7'), (106, 's6'), (111, 's7'), (112, 's0')) and cell_index in (1, 3)))
                        match = re.match(r'^(.*?)(\s+)(\d+(?:\.|, viz\.| and one lunatic|, drs\. \d+\.)?)(\s*)$', cell_text) if is_count else None
                        if match:
                            content = ('<span class="count-label">' + text_html(match.group(1) + match.group(2))
                                       + '</span><span class="count-number">' + text_html(match.group(3) + match.group(4)) + '</span>')
                        else:
                            content = text_html(cell_text)
                        rows.append('<span class="restored-cell' + (' count-cell' if match else '') + '" role="' + role + '">' + content + '</span>')
                    rows.extend('<span class="restored-cell empty-cell" role="cell" aria-label="Пусто"></span>' for _ in range(column_count - len(cells)))
                    rows.append('</span>' + ending)
                table_class = {'fee': 'fee-table', 'mixed': 'mixed-table', 'statistical': 'statistical-table', 'diet': 'diet-table'}.get(table_kind, 'fee-table')
                output.append('<span class="restored-table ' + table_class + (' has-header' if has_header else '') + '" role="table" aria-label="' + label + '">' + ''.join(rows) + '</span>')
                table_lines.clear()
            for line in chunk.splitlines(keepends=True):
                if '\t' in line:
                    flush_prose()
                    table_lines.append(line)
                else:
                    flush_table()
                    prose_lines.append(line)
            flush_table()
            flush_prose()
        elif (spread == 18 and segment_id == 's2' and chunk == 'SITUATION') or (spread == 20 and segment_id == 's5' and chunk == 'REGULATIONS') or (spread == 26 and segment_id == 's8' and chunk == 'BRIDEWELLS') or (spread == 38 and segment_id == 's11' and chunk == 'GERMANY') or (spread == 43 and segment_id == 's13' and chunk == 'SWEDEN') or (spread == 50 and segment_id == 's1' and chunk == 'GERMANY') or (spread == 59 and segment_id == 's9' and chunk == 'GERMANY') or (spread == 62 and segment_id == 's1' and chunk == 'AUSTRIAN FLANDERS') or (spread == 66 and segment_id == 's1' and chunk == 'PORTUGAL') or (spread == 67 and segment_id == 's1' and chunk == 'SPAIN') or (spread == 69 and segment_id == 's7' and chunk == 'FRENCH FLANDERS AND FRANCE') or (spread == 57 and ((segment_id == 's1' and chunk == 'GENEVA') or (segment_id == 's6' and chunk == 'SWITZERLAND'))) or (spread == 31 and ((segment_id == 's0' and chunk in ('SECTION IV', 'AN ACCOUNT OF FOREIGN PRISONS')) or (segment_id == 's1' and chunk == 'HOLLAND'))) or (spread == 107 and chunk in ('ELY GAOL', 'NORFOLK', 'COUNTY BRIDEWELL', 'SUFFOLK', 'COUNTY GAOL AT IPSWICH')):
            output.append('<span class="restored-heading">' + text_html(chunk) + '</span>')
        elif ((spread == 77 and segment_id == 's1' and chunk in ('MALTA', 'TURKEY'))
              or (spread == 78 and segment_id == 's1' and chunk == 'PRISONERS OF WAR IN ENGLAND, ETC.')
              or (spread == 79 and segment_id == 's9' and chunk == 'AMERICAN PRISONERS')
              or (spread == 80 and segment_id == 's2' and chunk in ('SECTION VI', 'SCOTCH AND IRISH PRISONS'))
              or (spread == 82 and segment_id == 's1' and chunk == 'IRELAND²')
              or (spread == 85 and segment_id == 's5' and chunk in ('SECTION VII', 'A PARTICULAR ACCOUNT OF ENGLISH PRISONS'))
              or (spread == 86 and segment_id == 's10' and chunk == 'THE TOWER')
              or (spread == 87 and segment_id == 's1' and chunk == 'NEWGATE¹')
              or (spread == 89 and segment_id == 's7' and chunk in ('HIS MAJESTY’S PRISON', 'THE FLEET,¹ FOR DEBTORS'))
              or (spread == 91 and segment_id == 's4' and chunk == 'HILARY 3d GEORGE II. 1729')
              or (spread == 92 and segment_id == 's7' and chunk == 'POULTRY COMPTER')
              or (spread == 93 and segment_id == 's11' and chunk == 'WOOD STREET COMPTER')
              or (spread == 94 and segment_id == 's1' and chunk == 'BRIDEWELL')
              or (spread == 95 and segment_id == 's3' and chunk == 'WHITECHAPEL PRISON FOR DEBTORS')
              or (spread == 95 and segment_id == 's6' and chunk == 'THE SAVOY')
              or (spread == 96 and segment_id == 's8' and chunk == 'TOTHILL-FIELDS BRIDEWELL')
              or (spread == 97 and segment_id == 's1' and chunk == 'THE KING’S BENCH PRISON, FOR DEBTORS')
              or (spread == 98 and segment_id == 's1' and chunk.startswith('RULES and ORDERS for the better Government'))
              or (spread == 99 and segment_id == 's1' and chunk.startswith('FURTHER RULES and ORDERS'))
              or (spread == 99 and segment_id == 's3' and chunk.startswith('A further RULE and ORDER'))
              or (spread == 100 and segment_id == 's1' and chunk == 'MARSHALSEA PRISON¹')
              or (spread == 101 and ((segment_id == 's3' and chunk == 'COUNTY BRIDEWELLS') or (segment_id == 's7' and chunk in ('ESSEX', 'COUNTY GAOL AT CHELMSFORD'))))
              or (spread == 102 and ((segment_id == 's1' and chunk == 'COUNTY BRIDEWELL') or (segment_id == 's4' and chunk in ('KENT', 'COUNTY GAOL AT MAIDSTONE'))))
              or (spread == 103 and ((segment_id == 's1' and chunk == '[COUNTY BRIDEWELL') or (segment_id == 's7' and chunk in ('SUSSEX', 'COUNTY GAOL AT HORSHAM'))))
              or (spread == 104 and ((segment_id == 's1' and chunk == 'COUNTY BRIDEWELL') or (segment_id == 's8' and chunk in ('SURREY', 'COUNTY GAOL IN SOUTHWARK'))))
              or (spread == 105 and segment_id == 's8' and chunk == 'COUNTY BRIDEWELLS')
              or (spread == 106 and ((segment_id == 's3' and chunk in ('HUNTINGDONSHIRE', 'COUNTY GAOL AT HUNTINGDON')) or (segment_id == 's6' and chunk in ('CAMBRIDGESHIRE', 'CAMBRIDGE'))))
              or (spread == 108 and segment_id == 's3' and chunk in ('MIDLAND CIRCUIT', 'WARWICKSHIRE', 'COUNTY GAOL AT WARWICK', 'COVENTRY CITY AND COUNTY GAOL'))
              or (spread == 109 and segment_id == 's1' and chunk in ('BIRMINGHAM TOWN GAOL', 'LEICESTERSHIRE', 'COUNTY GAOL AT LEICESTER'))
              or (spread == 110 and ((segment_id == 's3' and chunk == 'CHESTERFIELD GAOL') or (segment_id == 's6' and chunk in ('LINCOLNSHIRE', 'COUNTY BRIDEWELL', 'STAMFORD TOWN GAOL'))))
              or (spread == 111 and segment_id == 's3' and chunk == 'COUNTY BRIDEWELLS')
              or (spread == 112 and segment_id == 's4' and chunk in ('OXFORDSHIRE', 'COUNTY GAOL, OXFORD CASTLE'))
              or (spread == 113 and segment_id == 's5' and chunk in ('“COUNTY GAOL, OXFORD CASTLE', 'WORCESTERSHIRE', 'COUNTY GAOL, WORCESTER CASTLE'))
              or (spread == 114 and segment_id == 's9' and chunk in ('STAFFORDSHIRE', 'COUNTY BRIDEWELL', 'SHROPSHIRE', 'COUNTY GAOL AT SHREWSBURY'))
              or (spread == 115 and segment_id == 's3' and chunk in ('GLOUCESTERSHIRE', 'COUNTY GAOL, GLOUCESTER CASTLE'))
              or (spread == 116 and segment_id == 's11' and chunk in ('WESTERN CIRCUIT', 'HAMPSHIRE', 'COUNTY GAOL AT WINCHESTER', 'WILTSHIRE', 'SALISBURY COUNTY GAOL'))
              or (spread == 117 and ((segment_id == 's1' and chunk == 'COUNTY BRIDEWELL') or (segment_id == 's4' and chunk in ('DORSETSHIRE', 'COUNTY BRIDEWELL', 'DEVONSHIRE', 'HIGH GAOL AT EXETER'))))
              or (spread == 118 and ((segment_id == 's1' and chunk == 'PLYMOUTH TOWN GAOL') or (segment_id == 's5' and chunk in ('CORNWALL', 'COUNTY GAOL AT LAUNCESTON FOR FELONS', 'COUNTY GAOL AT BODMIN'))))
              or (spread == 120 and ((segment_id == 's1' and chunk == 'PENZANCE') or (segment_id == 's5' and chunk in ('SOMERSETSHIRE', 'BRISTOL NEWGATE'))))
              or (spread == 121 and ((segment_id == 's1' and chunk == 'BRIDGEWATER TOWN GAOL') or (segment_id == 's2' and chunk in ('NORTHERN CIRCUIT', 'YORKSHIRE', 'COUNTY GAOL, YORK CASTLE'))))
              or (spread == 122 and ((segment_id == 's1' and chunk == 'KNARESBOROUGH PRISON, FOR TOWN DEBTORS') or (segment_id == 's2' and chunk in ('ROTHWELL PRISON, FOR DEBTORS', 'HALIFAX PRISON¹')) or (segment_id == 's4' and chunk in ('DURHAM', 'COUNTY GAOL AT DURHAM'))))
              or (spread == 123 and ((segment_id == 's1' and chunk in ('NORTHUMBERLAND', 'NEWCASTLE')) or (segment_id == 's5' and chunk in ('COUNTY GAOL AT MORPETH', 'CUMBERLAND', 'COUNTY GAOL AT CARLISLE'))))
              or (spread == 124 and segment_id == 's7' and chunk in ('LANCASHIRE', 'COUNTY GAOL, LANCASTER CASTLE'))
              or (spread == 125 and ((segment_id == 's1' and chunk == 'COUNTY BRIDEWELL') or (segment_id == 's9' and chunk == 'LIVERPOOL BOROUGH GAOL')))
              or (spread == 126 and segment_id == 's1' and chunk == 'LIVERPOOL BRIDEWELL')
              or (spread == 127 and segment_id == 's8' and chunk == 'CHESTER CITY BRIDEWELL')
              or (spread == 130 and segment_id == 's1' and chunk == 'REMARKS ON CHARTER SCHOOLS (IRELAND)')
              or (spread == 132 and ((segment_id == 's1' and chunk == 'INSTRUCTIONS TO SCHOOL-MASTERS') or (segment_id == 's5' and chunk.startswith('GENERAL RULES to be strictly observed'))))
              or (spread == 133 and segment_id == 's1' and chunk == 'HULKS ON THE THAMES')
              or (spread == 134 and ((segment_id == 's1' and chunk == 'THE HULK AT PLYMOUTH') or (segment_id == 's3' and chunk in ('THE HULK AT GOSPORT', 'THE HULKS NEAR PORTSMOUTH'))))
              or (spread == 136 and segment_id == 's2' and chunk == 'REMARKS ON THE GAOL-FEVER¹')
              or (spread == 137 and segment_id == 's2' and chunk == 'REMARKS ON PENITENTIARY HOUSES')
              or (spread == 138 and segment_id == 's5' and chunk == 'GENERAL HEADS OF REGULATIONS. PROPOSED TO BE ESTABLISHED IN PENITENTIARY HOUSES OR HOUSES OF CORRECTION')
              or (spread == 140 and segment_id == 's3' and chunk in ('CONCLUSION', 'TO THE STATE OF THE PRISONS'))
              or (spread == 141 and segment_id == 's1' and chunk in ('CONCLUSION', 'TO THE ACCOUNT OF LAZARETTOS'))
              or (spread == 142 and segment_id == 's1' and chunk == 'DRAUGHT OF A BILL')):
            output.append('<span class="restored-heading">' + text_html(chunk) + '</span>')
        elif ((spread == 102 and segment_id == 's4' and chunk.startswith('that want of care respecting them'))
              or (spread == 110 and segment_id == 's3' and chunk.startswith('Before this rigorous law'))
              or (spread == 112 and segment_id == 's0' and chunk.startswith(('habit of idleness', '“In all manufacturing towns', '“A county magistrate said')))
              or (spread == 113 and segment_id == 's5' and chunk.startswith('skeleton with light chains'))
              or (spread == 122 and ((segment_id == 's2' and chunk.startswith('When any felon was here')) or (segment_id == 's4' and chunk.startswith(('not, then before his face', 'If it was a horse')))))
              or (spread == 124 and ((segment_id == 's1' and chunk.startswith('“By a letter dated 14 October')) or (segment_id == 's7' and chunk.startswith('of spinning, at which employment'))))):
            output.append('<span class="book-footnote verified-footnote footnote-continuation">' + text_html(chunk) + '</span>')
        elif spread == 119 and segment_id in ('s3', 's4', 's5', 's8') and chunk in ('From Debtors, First Class', 'Second Class', 'Debtors in General', 'From Felons', 'From Persons Committed to Bridewell'):
            output.append('<span class="fee-subheading">' + text_html(chunk) + '</span>')
        elif spread == 119 and segment_id == 's9' and chunk.startswith('I. No garnish'):
            output.append('<span class="book-list">' + text_html(chunk) + '</span>')
        elif spread == 92 and segment_id == 's7' and chunk.startswith('Ed. Bellamy.'):
            output.append('<span class="book-signatures">' + text_html(chunk) + '</span>')
        elif ((spread == 116 and segment_id == 's2' and chunk == 'FRANCIS NORMAN.')
              or (spread == 95 and segment_id == 's6' and chunk == 'GEORGE GARRED, keeper, 10th August, 1776.')):
            output.append('<span class="book-signature">' + text_html(chunk) + '</span>')
        elif spread == 111 and segment_id == 's3' and chunk.startswith('Oh ye whose hours'):
            output.append('<span class="book-verse">' + text_html(chunk) + '</span>')
        elif spread == 117 and segment_id == 's4' and chunk.startswith('Sherborn, Dorset.'):
            output.append('<span class="regulation-extract">' + text_html(chunk) + '</span>')
        elif ((spread == 98 and ((segment_id == 's1' and chunk.startswith('1. The stocks')) or (segment_id == 's7' and chunk.startswith('5. Coroner'))))
              or (spread == 99 and segment_id == 's1' and (chunk.startswith('to the court or a judge') or chunk.startswith('1. No person to bring')))):
            output.append('<span class="book-list">' + text_html(chunk) + '</span>')
        elif ((spread == 132 and segment_id in ('s1', 's5')) or (spread == 133 and segment_id == 's1')) and chunk.startswith('“‘That '):
            output.append('<span class="book-list">' + text_html(chunk) + '</span>')
        elif spread == 91 and ((segment_id == 's4' and chunk.startswith('1, 2, 3. Warden')) or (segment_id == 's7' and chunk.startswith('10. Those who blaspheme'))):
            output.append('<span class="book-list">' + text_html(chunk) + '</span>')
        elif spread == 68 and segment_id == 's3' and chunk.startswith('Sundays, Tuesdays and Thursdays.'):
            output.append('<span class="book-footnote verified-footnote diet-note">' + text_html(chunk) + '</span>')
        elif ((spread == 62 and segment_id == 's5' and chunk.startswith('The prisoners must be submissive')) or (spread == 63 and segment_id == 's1' and chunk.startswith('in insulting the keeper'))):
            output.append('<span class="verified-paragraph regulation-extract">' + text_html(chunk) + '</span>')
        elif spread == 64 and segment_id == 's7' and chunk.startswith('1. Contains the names of prisoners.\n'):
            output.append('<span class="book-list">' + text_html(chunk) + '</span>')
        elif spread == 60 and segment_id == 's1' and chunk.startswith('Hic criminum frequentia\n'):
            output.append('<span class="book-verse inscription">' + text_html(chunk) + '</span>')
        elif spread == 54 and ((segment_id == 's1' and chunk.startswith(('CLEMENS XI PONT. MAX.\n', 'Pope Clement XI\n'))) or (segment_id == 's5' and (chunk.startswith(('PARUM EST\n', 'It is of little advantage\n')) or chunk == 'SILENTIUM.'))):
            output.append('<span class="book-verse inscription">' + text_html(chunk) + '</span>')
        elif spread == 52 and segment_id == 's6' and (chunk.startswith('JUSTITIÆ ET CLEMENTIÆ\n') or chunk.startswith('To Justice and Clemency,\n')):
            output.append('<span class="book-verse inscription">' + text_html(chunk) + '</span>')
        elif spread == 35 and segment_id == 's7' and chunk == 'Virtutis est domare\nQuæ cuncti pavent.':
            output.append('<span class="book-verse">' + text_html(chunk) + '</span>')
        elif spread == 14 and (segment_id == 's1' or chunk.startswith(('Hail Patriot Band!', 'Much still untouch’d'))):
            output.append('<span class="book-verse">' + text_html(chunk) + '</span>')
        else:
            output.append(prose_html(chunk))
    return ''.join(output)


class SegmentRenderer(HTMLParser):
    """Identify stable textual blocks in the generated OCR HTML, never in the scan."""
    def __init__(self, overrides=None, query='', spread=None):
        super().__init__(convert_charrefs=True)
        self.output = []
        self.overrides = overrides or {}
        self.query = query
        self.spread = spread
        self.segments = {}
        self.index = 0
        self.active = None
        self.depth = 0
        self.captured = []

    def handle_starttag(self, tag, attrs):
        if self.active is not None:
            if tag == 'br':
                self.captured.append('\n')
            else:
                self.depth += 1
            return
        classes = dict(attrs).get('class', '').split()
        eligible = (tag in ('p', 'h3', 'td', 'th', 'caption', 'pre') and 'table-warning' not in classes
                    or tag == 'div' and any(c in classes for c in ('epitaph-name', 'epitaph-inscription', 'epitaph-year'))
                    or tag == 'span' and 'running-title' in classes)
        if eligible:
            self.active = (tag, self.get_starttag_text())
            self.captured = []
            self.depth = 0
        else:
            self.output.append(self.get_starttag_text())

    def handle_startendtag(self, tag, attrs):
        if self.active is None:
            self.output.append(self.get_starttag_text())
        elif tag == 'br':
            self.captured.append('\n')

    def handle_endtag(self, tag):
        if self.active is None:
            self.output.append('</' + tag + '>')
            return
        if self.depth:
            self.depth -= 1
            return
        active_tag, raw_tag = self.active
        if tag != active_tag:
            return
        original = ''.join(self.captured)
        # Empty headings are layout, not editable text. All other blocks get an
        # id even when their saved replacement is empty, so they can be restored.
        if not original:
            self.output.append(raw_tag + '</' + tag + '>')
        else:
            segment_id = 's' + str(self.index)
            self.index += 1
            default = original
            if self.spread == 165 and segment_id == 's0' and original.startswith('PUBLISHERS:'):
                default = PUBLISHER_IMPRINT
            elif self.spread == 165 and segment_id == 's1' and original == 'EET s(Kr1)':
                # OCR spuriously split letters and show-through onto the blank page.
                default = ''
            elif self.spread == 151 and segment_id == 's1' and original == 'TABLEV':
                default = 'TABLE V'
            elif self.spread == 151 and segment_id == 's2' and original.startswith('Alist offees'):
                default = TABLE151_LEFT
            elif self.spread == 151 and segment_id == 's5' and original.startswith('veralcircuits'):
                default = TABLE151_RIGHT
            elif self.spread == 152 and segment_id == 's1' and original.startswith('Condemned'):
                default = TABLE152_LEFT
            elif self.spread == 152 and segment_id == 's3' and original.startswith('doned,etc.'):
                default = TABLE152_RIGHT
            elif self.spread == 153 and segment_id == 's1' and original.startswith('keeper of Tothill-felds bridewell'):
                default = SPREAD_153_LEFT
            elif self.spread == 153 and segment_id == 's2' and original.startswith('posedtothe'):
                default = SPREAD_153_RIGHT
            elif self.spread == 161 and segment_id == 's0' and original.startswith('Everyman,I will'):
                default = 'Everyman, I will go with thee, and be thy guide,\nIn thy most need to go by thy side.'
            elif self.spread == 163:
                if segment_id == 's0' and original.startswith('JOHNHOWARD,born'):
                    default = ('JOHN HOWARD, born about 1726. High Sheriff of Bedfordshire, 1773. '
                               'Visited prisons in England and all over the Continent, 1774–6, 1781, '
                               '1783, and 1785–6. Died of camp-fever while with the Russian army at Kherson in 1790.')
                elif segment_id == 's1' and original == 'THE STATE OF THEPRISONS':
                    default = 'THE STATE OF THE PRISONS'
                elif segment_id == 's2' and original == 'JOHNHOWARD':
                    default = 'JOHN HOWARD'
                elif segment_id == 's3' and original.startswith('LONDON:J.M.DENT'):
                    default = 'LONDON: J. M. DENT & SONS LTD.\nNEW YORK: E. P. DUTTON & CO. INC.'
            verified = VERIFIED_SEGMENTS.get(self.spread, {}).get(segment_id)
            if verified and original.startswith(verified[0]):
                default = verified[1]
            else:
                spacing = SPACING_REPAIRS.get(self.spread, {}).get(segment_id)
                if spacing and original.startswith(spacing[0]):
                    for old, new in spacing[1]:
                        if default.count(old) == 1:
                            default = default.replace(old, new, 1)
            value = self.overrides.get(segment_id, default)
            if not isinstance(value, str):
                raise ValueError('Неверный формат сохранённого текста')
            self.segments[segment_id] = value
            content = highlight(value, self.query)
            if self.spread == 165 and segment_id == 's0':
                separator = '\n\nMade in Great Britain'
                if separator in value:
                    at = value.index(separator)
                    content = ('<span class="publishers-main">' + highlight(value[:at], self.query) + '</span>'
                               + '<span class="publishers-imprint">' + highlight(value[at:], self.query) + '</span>')
            if (self.spread, segment_id) in ((151, 's2'), (151, 's5'), (152, 's1'), (152, 's3')):
                # Group consecutive tab-separated rows into accessible tables.
                # All literal tabs and newlines remain in textContent, so
                # direct edits, history and existing segment IDs still work.
                chunks = []
                in_table = False
                for line in value.splitlines(keepends=True):
                    ending = '\n' if line.endswith('\n') else ''
                    row = line[:-1] if ending else line
                    cells = row.split('\t')
                    if len(cells) > 1:
                        if not in_table:
                            chunks.append('<span class="restored-table" role="table" aria-label="Таблица из книги">')
                            in_table = True
                        chunks.append('<span class="restored-row" role="row" style="--numeric-cols:' + str(len(cells) - 1) + '">')
                        for index, cell in enumerate(cells):
                            chunks.append('<span class="restored-cell" role="cell">' + highlight(('\t' if index else '') + cell, self.query) + '</span>')
                        chunks.append('</span>' + ending)
                    else:
                        if in_table:
                            chunks.append('</span>')
                            in_table = False
                        if row in ('TABLE VII', 'TABLE VII — continued', 'TABLE VIII', 'THE RESPECTIVE OFFENCES'):
                            chunks.append('<span class="restored-heading">' + highlight(row, self.query) + '</span>' + ending)
                        else:
                            chunks.append(highlight(row + ending, self.query))
                if in_table:
                    chunks.append('</span>')
                content = ''.join(chunks)
            if self.spread == 164 and segment_id in ('s3', 's5'):
                # Each row retains its literal tab and newline for editing.
                rows = []
                for line in value.splitlines(keepends=True):
                    end = '\n' if line.endswith('\n') else ''
                    row = line[:-1] if end else line
                    if '\t' in row:
                        title, page = row.rsplit('\t', 1)
                        rows.append('<span class="s164-row"><span>' + highlight(title, self.query) + '</span><span class="s164-number">' + highlight('\t' + page, self.query) + '</span></span>' + end)
                    else:
                        rows.append('<span class="s164-row s164-subrow">' + highlight(row, self.query) + '</span>' + end)
                content = ''.join(rows)
            if self.spread == 162 and segment_id == 's1':
                # Preserve textContent exactly, while placing the three blocks
                # at the top, centre, and bottom of the original title page.
                marker1, marker2 = '\n\nSCIENCE', '\n\nTHE STATE OF THE PRISONS'
                if marker1 in value and marker2 in value:
                    a, b = value.index(marker1), value.index(marker2)
                    content = ('<span class="s162-top">' + highlight(value[:a], self.query) + '</span>'
                               + '<span class="s162-middle">' + highlight(value[a:b], self.query) + '</span>'
                               + '<span class="s162-bottom">' + highlight(value[b:], self.query) + '</span>')
            if self.spread == 153 and segment_id == 's1':
                # Keep exact textContent (and the stable segment ID) while
                # setting the source and footnote apart as on the scan.
                source_marker = '\n\n[From An Account of Lazarettos, p. 259.]'
                footnote_marker = '\n\n1 Mr. George Smith'
                if source_marker in value and footnote_marker in value:
                    source_at = value.index(source_marker)
                    note_at = value.index(footnote_marker, source_at)
                    content = (highlight(value[:source_at], self.query)
                               + '<span class="s153-source">' + highlight(value[source_at:note_at], self.query) + '</span>'
                               + '<span class="s153-footnote">' + highlight(value[note_at:], self.query) + '</span>')
            if self.spread == 153 and segment_id == 's2':
                pattern = r'(?m)^(NOTES|NOTE A.—BRIDEWELLS|NOTE B.—PROPOSED IMPROVEMENTS|\(See page (?:37|19)\))(?=\n|$)'
                pieces = re.split(pattern, value)
                content = ''
                for i, piece in enumerate(pieces):
                    if i % 2:
                        css = 's153-title' if piece == 'NOTES' else ('s153-subtitle' if piece.startswith('NOTE ') else 's153-reference')
                        content += '<span class="' + css + '">' + highlight(piece, self.query) + '</span>'
                    else:
                        content += highlight(piece, self.query)
            # Verified footnote on scan 002, kept within the same editable
            # segment so existing IDs and history stay stable. CSS-generated
            # reference number does not change the editable plain text.
            if self.spread == 2 and segment_id == 's3' and value.count('gaol-fever') == 1:
                at = value.index('gaol-fever') + len('gaol-fever')
                content = (highlight(value[:at], self.query)
                           + '<a class="footnote-ref" href="#footnote-2-1" aria-label="Перейти к примечанию 1"></a>'
                           + highlight(value[at:], self.query))
            if self.spread == 2 and segment_id == 's6':
                marker = '\n1 The continued practice of placing herbs'
                if marker in value:
                    at = value.index(marker)
                    content = (highlight(value[:at], self.query)
                               + '<span class="book-footnote" id="footnote-2-1">'
                               + highlight(value[at:], self.query) + '</span>')
            if self.spread in VERIFIED_PROSE and value:
                content = render_verified_block(value, self.spread, segment_id, self.query)
            self.output.append(raw_tag[:-1] + ' data-edit-id="' + segment_id + '">' + content + '</' + tag + '>')
        self.active = None
        self.captured = []

    def handle_data(self, data):
        if self.active is not None:
            self.captured.append(data)
        else:
            self.output.append(escape(data))

    def handle_entityref(self, name):
        self.handle_data('&' + name + ';')

    def handle_charref(self, name):
        self.handle_data('&#' + name + ';')


def render_segments(html, overrides=None, query='', spread=None):
    parser = SegmentRenderer(overrides, query, spread)
    parser.feed(html)
    parser.close()
    return ''.join(parser.output), parser.segments


SCRIPT = r'''<script>
(()=>{
 const button=document.getElementById('edit-toggle'), save=document.getElementById('edit-save'), cancel=document.getElementById('edit-cancel'), status=document.getElementById('edit-status');
 if(!button||!save||!cancel||!status)return;
 const area=document.getElementById('editable-book');
 const number=area.dataset.spread;
 const nodes=Array.from(area.querySelectorAll('[data-edit-id]'));
 let editing=false, saving=false, originals={};
 const current=n=>n.textContent;
 const changed=()=>nodes.filter(n=>current(n)!==originals[n.dataset.editId]);
 function update(){const n=changed().length;save.disabled=n===0||saving;status.textContent=n?`Изменено фрагментов: ${n}. Не забудьте сохранить.`:'Нажмите на любой текст и исправьте его. Скан справа можно использовать для сверки.';}
 // Insert plain text nodes for line breaks and pasted text. Browsers can
 // otherwise create block elements whose textContent silently joins lines.
 function insertPlain(node,text){
  const selection=window.getSelection();if(!selection||!selection.rangeCount)return;
  const range=selection.getRangeAt(0);if(!node.contains(range.commonAncestorContainer))return;
  range.deleteContents();const inserted=document.createTextNode(text);range.insertNode(inserted);
  range.setStartAfter(inserted);range.collapse(true);selection.removeAllRanges();selection.addRange(range);
  node.dispatchEvent(new Event('input',{bubbles:true}));
 }
 button.addEventListener('click',()=>{
  if(editing)return;editing=true;originals={};
  nodes.forEach(n=>{originals[n.dataset.editId]=current(n);n.setAttribute('contenteditable','plaintext-only');n.setAttribute('role','textbox');n.setAttribute('aria-label','Редактируемый фрагмент книги');n.setAttribute('spellcheck','false');n.addEventListener('input',update);
   n.addEventListener('beforeinput',e=>{if(e.inputType==='insertParagraph'||e.inputType==='insertLineBreak'){e.preventDefault();insertPlain(n,'\n')}});
   n.addEventListener('paste',e=>{const text=e.clipboardData&&e.clipboardData.getData('text/plain');if(text!==undefined&&text!==null){e.preventDefault();insertPlain(n,text.replace(/\r\n?/g,'\n'))}})
  });
  area.classList.add('is-editing');button.hidden=true;save.hidden=false;cancel.hidden=false;status.hidden=false;update();
  if(nodes.length)nodes[0].focus();
 });
 cancel.addEventListener('click',()=>{if(changed().length&&!confirm('Отменить несохранённые изменения?'))return;window.removeEventListener('beforeunload',warn);location.reload()});
 function warn(e){if(editing&&changed().length){e.preventDefault();e.returnValue='';}}
 window.addEventListener('beforeunload',warn);
 save.addEventListener('click',async()=>{
  if(saving)return;const edits=changed().map(n=>({id:n.dataset.editId,old:originals[n.dataset.editId],new:current(n)}));
  if(!edits.length)return;saving=true;save.disabled=true;cancel.disabled=true;nodes.forEach(n=>n.setAttribute('contenteditable','false'));status.textContent='Сохраняем…';
  try{
   const response=await fetch('/api/edits/'+number,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({edits})});
   const result=await response.json();if(!response.ok)throw new Error(result.error||'Не удалось сохранить изменения.');
   editing=false;status.textContent='Изменения сохранены.';location.reload();
  }catch(e){saving=false;cancel.disabled=false;nodes.forEach(n=>n.setAttribute('contenteditable','plaintext-only'));save.disabled=false;status.textContent=e.message+' Текст не потерян: скопируйте его перед обновлением страницы.';}
 });
})();
</script>'''
