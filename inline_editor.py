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
    superscripts = {'¹': '1', '²': '2', '³': '3'}

    def text_html(text, skip_initial_note=False):
        prefix = ''
        if skip_initial_note and text[:1] in superscripts:
            prefix, text = text[:1], text[1:]
        pieces = re.split(r'([¹²³])', text)
        result = [highlight(prefix, query)] if prefix else []
        for piece in pieces:
            number = superscripts.get(piece)
            if number and number in targets:
                target = 'footnote-' + str(spread) + '-' + targets[number] + '-' + number
                result.append('<a class="verified-note-ref" href="#' + target + '">' + highlight(piece, query) + '</a>')
            else:
                result.append(highlight(piece, query))
        return ''.join(result)

    output = []
    for chunk in re.split(r'(\n\n)', value):
        if chunk == '\n\n' or not chunk:
            output.append(chunk)
            continue
        note = re.match(r'^([¹²³])\s', chunk)
        if note:
            number = superscripts[note.group(1)]
            target = 'footnote-' + str(spread) + '-' + segment_id + '-' + number
            output.append('<span class="book-footnote verified-footnote" id="' + target + '">' + text_html(chunk, True) + '</span>')
        elif '\t' in chunk:
            # Group only consecutive tab-separated lines. Prose introducing or
            # following a table must not become a one-cell table row.
            has_header = not ((spread == 16 and segment_id in ('s7', 's11')) or (spread == 17 and segment_id == 's1'))
            if spread == 16 and segment_id == 's10':
                label = 'Number of prisoners, spring 1776'
            elif spread == 16 and segment_id == 's11':
                label = 'Thatched House Society, 27 March 1782'
            elif spread == 17 and segment_id == 's1':
                label = 'Bristol and Dublin small-debt societies'
            elif spread == 16 and segment_id == 's7':
                label = 'Fees for the copy of a calendar, 1735'
            elif spread in (145, 146, 147):
                label = 'Table I — prisoners in England and Wales, 1779 and 1782'
            else:
                label = 'Fees of the Clerk of the Peace'
            table_lines = []
            prose_lines = []
            def flush_prose():
                if prose_lines:
                    output.append('<span class="verified-paragraph">' + text_html(''.join(prose_lines), False) + '</span>')
                    prose_lines.clear()
            def flush_table():
                if not table_lines:
                    return
                rows = []
                for index, line in enumerate(table_lines):
                    ending = '\n' if line.endswith('\n') else ''
                    row = line[:-1] if ending else line
                    cells = row.split('\t')
                    role = 'columnheader' if index == 0 and has_header else 'cell'
                    rows.append('<span class="restored-row" role="row" style="--numeric-cols:' + str(len(cells)-1) + '">')
                    for cell_index, cell in enumerate(cells):
                        rows.append('<span class="restored-cell" role="' + role + '">' + highlight(('\t' if cell_index else '') + cell, query) + '</span>')
                    rows.append('</span>' + ending)
                output.append('<span class="restored-table fee-table" role="table" aria-label="' + label + '">' + ''.join(rows) + '</span>')
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
        elif spread == 14 and (segment_id == 's1' or chunk.startswith(('Hail Patriot Band!', 'Much still untouch’d'))):
            output.append('<span class="book-verse">' + text_html(chunk) + '</span>')
        else:
            output.append('<span class="verified-paragraph">' + text_html(chunk) + '</span>')
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
