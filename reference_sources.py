"""Read user-provided editions as *references*, never as replacement text."""
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parent


def compact(text):
    chars, positions = [], []
    for i, char in enumerate(text):
        if char.isalnum():
            for part in char.lower():
                chars.append(part)
                positions.append(i)
    return ''.join(chars), positions


class BodyText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.body = False
        self.hidden = 0
        self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag == 'body':
            self.body = True
        if self.body and tag in ('style', 'script'):
            self.hidden += 1
        if self.body and not self.hidden and tag in ('p', 'div', 'br', 'h1', 'h2', 'h3', 'li'):
            self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in ('style', 'script') and self.hidden:
            self.hidden -= 1
        if tag == 'body':
            self.body = False
    def handle_data(self, data):
        if self.body and not self.hidden:
            self.parts.append(data)


@lru_cache(maxsize=1)
def sources():
    result = []
    docx = ROOT / '08_Howard_Prisons_print.docx'
    if docx.exists():
        try:
            with zipfile.ZipFile(docx) as z:
                doc = ET.fromstring(z.read('word/document.xml'))
                ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
                text = '\n'.join(''.join(t.text or '' for t in p.findall('.//w:t', ns)) for p in doc.findall('.//w:p', ns))
                result.append(('DOCX · William Eyres', text, *compact(text)))
        except (OSError, ValueError, ET.ParseError, KeyError, zipfile.BadZipFile):
            pass
    epub = ROOT / 'The_State_of_the_Prisons_in_England_and.epub'
    if epub.exists():
        try:
            with zipfile.ZipFile(epub) as z:
                parts = []
                for name in sorted(z.namelist()):
                    if re.fullmatch(r'OEBPS/content/content-\d+\.xml', name):
                        parser = BodyText()
                        parser.feed(z.read(name).decode('utf-8', 'replace'))
                        parts.append(' '.join(parser.parts))
                text = '\n'.join(parts)
                result.append(('EPUB · William Eyres', text, *compact(text)))
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            pass
    pdf = ROOT / 'stateofprisonsin00howa.pdf'
    if pdf.exists():
        try:
            import pymupdf
            with pymupdf.open(pdf) as doc:
                text = '\n'.join(page.get_text() for page in doc)
            result.append(('PDF · Internet Archive', text, *compact(text)))
        except (OSError, ValueError, RuntimeError):
            pass
    return result


def references(segments, max_per_source=3):
    output = []
    for label, text, normalized, positions in sources():
        found, seen = [], set()
        for segid, segment in segments.items():
            source, _ = compact(segment)
            if len(source) < 80:
                continue
            # Longer exact anchors reduce false matches in other editions.
            for start in range(0, min(len(source) - 40, 240), 20):
                anchor = source[start:start + 40]
                at = normalized.find(anchor)
                if at < 0 or normalized.find(anchor, at + 1) >= 0:
                    continue
                where = positions[at]
                if any(abs(where - previous) < 250 for previous in seen):
                    continue
                seen.add(where)
                excerpt = re.sub(r'\s+', ' ', text[max(0, where - 180):min(len(text), where + 420)]).strip()
                found.append({'segment': segid, 'excerpt': excerpt})
                break
            if len(found) >= max_per_source:
                break
        output.append({'source': label, 'matches': found})
    return output
