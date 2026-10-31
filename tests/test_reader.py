"""Regression tests for the reader; tests never modify the user's edits."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import inline_editor
import reader
import translation


class ReaderHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), reader.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [patch.object(inline_editor, 'EDITS_FILE', root / 'text_edits.json'),
                        patch.object(reader, 'CORRECTIONS_FILE', root / 'corrections.json'),
                        patch.object(reader, 'QUALITY_FILE', root / 'scan_quality.json'),
                        patch.object(reader, 'SCAN_OVERRIDES', root / 'scan_overrides')]
        for item in self.patches:
            item.start()
        reader.dismissed_issues.cache_clear()

    def tearDown(self):
        reader.dismissed_issues.cache_clear()
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def request(self, path, data=None, content_type=None, method=None):
        headers = {'Content-Type': content_type} if content_type else {}
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as response:
                return response.status, response.read().decode('utf-8', errors='replace')
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode('utf-8', errors='replace')

    def save(self, number, changes):
        payload = json.dumps({'edits': changes}).encode('utf-8')
        return self.request(f'/api/edits/{number}', payload, 'application/json')

    def form(self, path, data):
        return self.request(path, urllib.parse.urlencode(data).encode(), 'application/x-www-form-urlencoded')

    def test_search_uses_visible_cells_and_never_joins_unrelated_segments(self):
        self.assertIn('РАЗВОРОТ 002', reader.search_page('First Foreign Journey, 1775'))
        _, segments = reader.segment_state(2)
        self.assertEqual(segments['s13'], 'Journeys')
        self.assertEqual(segments['s14'], 'Miles')
        self.assertNotIn('РАЗВОРОТ 002', reader.search_page('Journeys Miles'))

    def test_edit_conflict_search_and_scope(self):
        _, segments = reader.segment_state(1)
        key, old = next((k, v) for k, v in segments.items() if 'coined' in v)
        new = old.replace('coined', 'unique-corrected-phrase', 1)
        self.assertEqual(self.save(1, [{'id': key, 'old': old, 'new': new}])[0], 200)
        self.assertIn('unique-corrected-phrase', self.request('/spread/1')[1])
        self.assertNotIn('unique-corrected-phrase', self.request('/spread/2')[1])
        self.assertIn('РАЗВОРОТ 001', reader.search_page('unique-corrected-phrase'))
        self.assertEqual(self.save(1, [{'id': key, 'old': old, 'new': 'stale'}])[0], 409)
        self.assertIn('unique-corrected-phrase', self.request('/spread/1')[1])

    def test_multiline_history_restores_without_roundtrip_of_text(self):
        _, segments = reader.segment_state(1)
        key, old = next((k, v) for k, v in segments.items() if 'coined' in v)
        new = old.replace('coined', 'first line\nsecond line', 1)
        self.assertEqual(self.save(1, [{'id': key, 'old': old, 'new': new}])[0], 200)
        history = self.request('/history/1')[1]
        self.assertNotIn('name="expected"', history)
        self.assertEqual(self.form('/restore/1', {'index': 0})[0], 200)
        self.assertEqual(reader.segment_state(1)[1][key], old)
        self.assertEqual(len(inline_editor.load_edits()['spreads']['1']['history']), 2)

    def test_stale_restore_is_rejected(self):
        _, segments = reader.segment_state(1)
        key, original = next((k, v) for k, v in segments.items() if 'coined' in v)
        first = original.replace('coined', 'first', 1)
        second = first.replace('first', 'second', 1)
        self.assertEqual(self.save(1, [{'id': key, 'old': original, 'new': first}])[0], 200)
        self.assertEqual(self.save(1, [{'id': key, 'old': first, 'new': second}])[0], 200)
        self.assertEqual(self.form('/restore/1', {'index': 0})[0], 409)
        self.assertEqual(reader.segment_state(1)[1][key], second)

    def test_accept_and_restore_suggestion(self):
        suggestion = reader.review_suggestions(1)[0]
        self.assertEqual(self.form('/review/1', {'id': suggestion['id'], 'action': 'accept'})[0], 200)
        state = inline_editor.load_edits()['spreads']['1']
        self.assertEqual(state['suggestions'][suggestion['id']]['status'], 'accepted')
        self.assertEqual(self.form('/restore/1', {'index': len(state['history']) - 1})[0], 200)
        self.assertNotIn(suggestion['id'], inline_editor.load_edits()['spreads']['1']['suggestions'])

    def test_static_access_is_restricted(self):
        for method in ('GET', 'HEAD'):
            for path in ('/text_edits.json', '/corrections.json', '/reader.py', '/.git/HEAD', '/ocr_review/suggestions.json'):
                self.assertEqual(self.request(path, method=method)[0], 404, (method, path))
        self.assertEqual(self.request('/govard001.pdf')[0], 200)
        self.assertEqual(self.request('/ocr_review/1_1.txt')[0], 200)

    def upload_scan(self, number, payload, name='scan.pdf'):
        boundary = 'test-boundary-123'
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="scan"; filename="{name}"\r\n'
                'Content-Type: application/octet-stream\r\n\r\n').encode() + payload + f'\r\n--{boundary}--\r\n'.encode()
        return self.request(f'/scan-upload/{number}', body, f'multipart/form-data; boundary={boundary}')

    def test_scan_replacement_and_quality_decision(self):
        self.assertIn('скан повёрнут', self.request('/spread/14')[1])
        self.assertEqual(self.form('/scan-action/14', {'action':'dismiss-scan'})[0], 200)
        self.assertFalse(reader.scan_dropdown_label(14))
        self.assertNotIn('OCR', reader.quality_label(14))
        self.assertIn('Пометка снята вами', self.request('/spread/14')[1])
        self.assertIn('пометка снята', self.request('/quality')[1])
        reader.dismissed_issues.cache_clear()
        self.assertFalse(reader.scan_dropdown_label(14))  # persisted
        self.assertEqual(self.form('/scan-action/14', {'action':'restore-scan-mark'})[0], 200)
        self.assertTrue(reader.scan_dropdown_label(14))
        self.assertEqual(self.form('/scan-action/21', {'action':'dismiss-ocr'})[0], 200)
        self.assertNotIn('OCR', reader.quality_label(21))
        self.assertIn('скан повёрнут', reader.quality_label(14))
        self.assertIn('пометка OCR снята', self.request('/spread/21')[1])

        pdf = reader.pymupdf.open()
        pdf.new_page(width=100, height=50)
        payload = pdf.tobytes()
        pdf.close()
        self.assertEqual(self.upload_scan(14, payload)[0], 200)
        self.assertFalse(reader.scan_dropdown_label(14))  # replacement not inspected
        self.assertNotIn('OCR', reader.quality_label(14))  # decision is preserved
        self.assertEqual(reader.replacement_path(14).read_bytes(), payload)
        self.assertIn('Загруженный вами скан', self.request('/spread/14')[1])
        self.assertIn('не по загруженной замене', self.request('/review/14')[1])
        image = reader.pymupdf.Pixmap(reader.scan_image(14))
        self.assertEqual((image.width, image.height), (180, 90))
        self.assertEqual(self.upload_scan(14, b'not a pdf')[0], 400)
        self.assertEqual(reader.replacement_path(14).read_bytes(), payload)
        self.assertEqual(self.request('/scan_overrides/014.bin')[0], 404)
        self.assertEqual(self.form('/scan-action/14', {'action':'restore-scan'})[0], 200)
        self.assertFalse(reader.replacement_path(14).exists())
        self.assertTrue(reader.scan_dropdown_label(14))
        self.assertNotIn('OCR', reader.quality_label(14))
        self.assertEqual(self.form('/scan-action/21', {'action':'restore-ocr'})[0], 200)
        self.assertIn('☑ OCR', reader.quality_label(21))

    def test_legacy_quality_decisions_are_preserved_when_migrated(self):
        reader.QUALITY_FILE.write_text(json.dumps({'dismissed': [2, 14, 153], 'other': 'keep'}))
        reader.dismissed_issues.cache_clear()
        self.assertFalse(reader.quality_label(14))
        self.assertFalse(reader.quality_label(2))
        self.assertEqual(self.form('/scan-action/14', {'action':'restore-scan-mark'})[0], 200)
        self.assertIn('скан повёрнут', reader.quality_label(14))
        self.assertNotIn('OCR', reader.quality_label(14))
        self.assertFalse(reader.quality_label(2))
        data = json.loads(reader.QUALITY_FILE.read_text())
        self.assertEqual(data['other'], 'keep')
        self.assertEqual(data['dismissed'], [2, 14, 153])
        self.assertIn(14, data['dismissed_ocr'])
        self.assertNotIn(14, data['dismissed_scan'])

    def test_reject_multipage_pdf_and_accept_png(self):
        pdf = reader.pymupdf.open()
        pdf.new_page(width=100, height=50)
        png = pdf[0].get_pixmap().tobytes('png')
        pdf.new_page(width=100, height=50)
        bad = pdf.tobytes()
        pdf.close()
        self.assertEqual(self.upload_scan(1, bad)[0], 400)
        self.assertFalse(reader.replacement_path(1).exists())
        self.assertEqual(self.upload_scan(1, png, 'scan.png')[0], 200)
        self.assertEqual(reader.replacement_path(1).read_bytes(), png)

    def test_omission_detection_and_verified_repairs(self):
        import omission_detection
        missing = 'I have often seen these prisoners eating their "We are locked up and almost starved to death."'
        reference = 'I have often seen these prisoners eating their water-soup (bread boiled in mere water) and heard them say, “We are locked up and almost starved to death.”'
        result = omission_detection.find_omissions({'s4': missing}, [(0, reference)])
        self.assertTrue(result)
        self.assertIn('water-soup', result[0]['candidate'])
        self.assertEqual(omission_detection.find_omissions({'s4': reference}, [(0, reference)]), [])
        self.assertIn('water-soup (bread boiled in mere water)', reader.segment_state(9)[1]['s4'])
        self.assertIn('and so sane that they are likely to remain', reader.segment_state(4)[1]['s1'])
        self.assertIn('the curiosity of some to inquire', reader.segment_state(7)[1]['s5'])
        self.assertIn('one may now look into many a prison', reader.segment_state(7)[1]['s15'])
        self.assertEqual(self.request('/omissions/0')[0], 404)
        page = self.request('/omissions/9')[1]
        self.assertIn('ничего не добавляется автоматически', page)
        self.assertIn('другие издания книги', page)
        for source in ('DOCX · William Eyres', 'EPUB · William Eyres', 'PDF · Internet Archive'):
            self.assertIn(source, page)
        self.assertIn('Возможные пропуски', self.request('/spread/9')[1])

    def test_large_tables_fit_the_available_width(self):
        page, segments = reader.segment_state(152)
        self.assertIn('wide-table-spread', page)
        self.assertIn('--numeric-cols:9', page)
        self.assertIn('grid-template-columns:minmax(55px,2.5fr)', reader.STYLE)
        self.assertNotIn('min-width:max-content', reader.STYLE)
        self.assertIn('1782–1783', segments['s3'])

    def test_restored_tables_preserve_text_edits_search_and_discrepancy(self):
        from html.parser import HTMLParser
        class TextCapture(HTMLParser):
            def __init__(self):
                super().__init__(); self.key = None; self.depth = 0; self.data = {}
            def handle_starttag(self, tag, attrs):
                if self.key is not None:
                    self.depth += 1
                elif 'data-edit-id' in dict(attrs):
                    self.key = dict(attrs)['data-edit-id']; self.data[self.key] = ''; self.depth = 0
            def handle_data(self, value):
                if self.key is not None:
                    self.data[self.key] += value
            def handle_endtag(self, tag):
                if self.key is not None:
                    if self.depth:
                        self.depth -= 1
                    else:
                        self.key = None
        for number in (151, 152):
            html, segments = reader.segment_state(number)
            self.assertIn('role="table"', html)
            self.assertIn('role="row"', html)
            self.assertNotIn('table-warning', html)
            capture = TextCapture(); capture.feed(html)
            self.assertEqual(capture.data, segments)
        _, segments = reader.segment_state(151)
        self.assertIn('1774 February\t16\t33\t7\t3\t1\t—\t60', segments['s5'])
        self.assertIn('РАЗВОРОТ 151', reader.search_page('Felons delivered from Newgate'))
        before = segments['s5']
        after = before.replace('Felons delivered', 'Edited felons delivered')
        self.assertEqual(self.save(151, [{'id':'s5', 'old':before, 'new':after}])[0], 200)
        self.assertEqual(reader.segment_state(151)[1]['s5'], after)
        self.assertEqual(self.form('/restore/151', {'index':0})[0], 200)
        self.assertEqual(reader.segment_state(151)[1]['s5'], before)
        _, s152 = reader.segment_state(152)
        self.assertIn('1782–1783\t—\t—\t6\t—\t52\t1\t—\t—\t49', s152['s3'])
        self.assertIn('сумма чисел строки — 59', self.request('/spread/152')[1])

    def test_verified_spreads_and_pending_ocr_labels(self):
        for number in (5, 6, 151, 153, 161, 162, 163, 164, 165):
            self.assertNotIn('OCR', reader.quality_label(number))
        for number in (1, 4, 7, 21, 148, 160):
            self.assertIn('☑ OCR', reader.quality_label(number))
        self.assertIn('скан повёрнут', reader.quality_label(14))
        _, s5 = reader.segment_state(5)
        self.assertIn('descriptions of individual prisons have been omitted', s5['s3'])
        self.assertNotIn('xvii', s5['s6'])
        _, s6 = reader.segment_state(6)
        self.assertIn('“they were lately brought from the bridewells.”', s6['s4'])
        html, s162 = reader.segment_state(162)
        self.assertIn('s162-middle', html)
        self.assertIn('EDITION', reader.segment_state(164)[1]['s1'].upper())
        html, s164 = reader.segment_state(164)
        self.assertIn('s164-row', html)
        self.assertIn('HULKS ON THE THAMES\t252', s164['s5'])
        self.assertEqual(list(s164), ['s0', 's1', 's2', 's3', 's4', 's5'])
        before = s164['s5']
        updated = before.replace('HULKS ON THE THAMES', 'HULKS ON THE THAMES EDITED')
        self.assertEqual(self.save(164, [{'id':'s5', 'old':before, 'new':updated}])[0], 200)
        self.assertEqual(reader.segment_state(164)[1]['s5'], updated)
        self.assertEqual(self.form('/restore/164', {'index':0})[0], 200)
        self.assertEqual(reader.segment_state(164)[1]['s5'], before)

    def test_spread_153_verified_layout_search_and_edits(self):
        page, segments = reader.segment_state(153)
        self.assertEqual(list(segments), ['s0', 's1', 's2'])
        self.assertEqual(segments['s0'], 'TABLES')
        self.assertIn('keeper of Tothill-fields bridewell,¹ should be exposed', segments['s1'])
        self.assertIn('1 Mr. George Smith, who died in 1786', segments['s1'])
        self.assertTrue(segments['s2'].startswith('NOTES\n\nNOTE A.—BRIDEWELLS'))
        self.assertIn('NOTE B.—PROPOSED IMPROVEMENTS', segments['s2'])
        self.assertIn('s153-footnote', page)
        self.assertIn('s153-notes-page', page)
        self.assertIn('s153-page-number', page)
        self.assertNotIn('table-warning', page)
        self.assertIn('РАЗВОРОТ 153', reader.search_page('national penitentiary'))
        before = segments['s2']
        edited = before.replace('Bridewells, on the other hand', 'Bridewells, edited')
        self.assertEqual(self.save(153, [{'id':'s2', 'old':before, 'new':edited}])[0], 200)
        self.assertEqual(reader.segment_state(153)[1]['s2'], edited)
        self.assertEqual(self.form('/restore/153', {'index':0})[0], 200)
        self.assertEqual(reader.segment_state(153)[1]['s2'], before)

    def test_publisher_layout_remains_editable_and_preserves_user_overrides(self):
        _, segments = reader.segment_state(165)
        self.assertEqual(segments['s0'], inline_editor.PUBLISHER_IMPRINT)
        self.assertEqual(segments['s1'], '')
        page = self.request('/spread/165')[1]
        self.assertIn('publisher-page', page)
        self.assertIn('publishers-main', page)
        old = segments['s0']
        new = old.replace('PUBLISHERS:', 'PUBLISHERS (edited):')
        self.assertEqual(self.save(165, [{'id':'s0', 'old':old, 'new':new}])[0], 200)
        self.assertEqual(reader.segment_state(165)[1]['s0'], new)
        self.assertEqual(self.form('/restore/165', {'index':0})[0], 200)
        self.assertEqual(reader.segment_state(165)[1]['s0'], old)

    def test_navigation_dropdown_only_marks_confirmed_scan_problems(self):
        from html.parser import HTMLParser
        class Options(HTMLParser):
            def __init__(self):
                super().__init__(); self.active = None; self.options = {}
            def handle_starttag(self, tag, attrs):
                if tag == 'option':
                    self.active = dict(attrs).get('value')
                    self.options[self.active] = ''
            def handle_data(self, data):
                if self.active is not None:
                    self.options[self.active] += data
            def handle_endtag(self, tag):
                if tag == 'option':
                    self.active = None
        parser = Options(); parser.feed(self.request('/spread/14')[1])
        self.assertEqual(len(parser.options), 165)
        self.assertIn('скан повёрнут', parser.options['14'])
        self.assertEqual(parser.options['145'], 'Разворот 145')
        self.assertTrue(all('OCR' not in text for text in parser.options.values()))
        self.assertEqual(self.form('/scan-action/14', {'action':'dismiss-scan'})[0], 200)
        parser = Options(); parser.feed(self.request('/spread/14')[1])
        self.assertEqual(parser.options['14'], 'Разворот 014')

    def test_quality_labels_and_rotated_preview(self):
        selection = self.request('/spread/14')[1]
        self.assertIn('скан повёрнут', selection)
        self.assertIn('/scan/14.jpg?v=original-v2', selection)
        self.assertNotIn('☑ OCR', selection)
        quality = self.request('/quality')[1]
        self.assertIn('не означает, что скан плохой', quality)
        self.assertIn('Разворот 014', quality)
        # Original 014 is portrait but its visible scan was sideways.
        # The corrected preview must be landscape.
        image = reader.pymupdf.Pixmap(reader.scan_image(14))
        self.assertGreater(image.width, image.height)

    def test_all_spreads_have_editable_text_and_review(self):
        for n in range(1, 166):
            _, segments = reader.segment_state(n)
            self.assertTrue(segments, n)
            self.assertTrue(reader.has_review(n), n)


class SequentialProofreadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(inline_editor, 'EDITS_FILE', Path(self.tmp.name) / 'text_edits.json')
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_every_guarded_transcription_applies_without_changing_segment_ids(self):
        from verified_prose import VERIFIED_PROSE
        for number, replacements in VERIFIED_PROSE.items():
            html, segments = reader.segment_state(number)
            for key, (prefix, expected) in replacements.items():
                self.assertEqual(segments[key], expected, (number, key))
            self.assertNotIn('☑ OCR', reader.quality_label(number))
            self.assertIn('verified-prose-page', html)

    def test_restored_omissions_follow_the_scan_not_the_other_editions(self):
        cases = {
            (10, 's9'): 'of bedding or straw for prisoners to sleep on; and if by any means',
            (11, 's8'): 'for which they suffered. I will give an abridgment of it in a table',
            (12, 's1'): 'than by all other diseases or means of death put together.',
            (12, 's2'): 'as they have been very properly called) of idleness and every vice.',
            (12, 's13'): 'when a Lisbon packet (the “Hanover”) in which I went passenger',
            (13, 's9'): '(as it is called in some London gaols) chummage. “Pay or strip,”',
            (13, 's12'): 'John Berrisford of two half-guineas',
            (14, 's3'): 'The number of turnkeys should be increased in proportion',
            (15, 's7'): 'the judge ordering the clerk of assize to explain to him in London',
        }
        for (number, key), phrase in cases.items():
            self.assertIn(phrase, reader.segment_state(number)[1][key])
        # This edition retains Dinnan, unlike the other editions' Dinan.
        self.assertIn('prisoner of war in Dinnan', reader.segment_state(12)[1]['s13'])
        self.assertIn('1773, 1774, and 1775', reader.segment_state(11)[1]['s5'])

    def test_notes_poetry_and_fee_table_round_trip_and_remain_editable(self):
        from html.parser import HTMLParser
        class Capture(HTMLParser):
            def __init__(self):
                super().__init__(); self.key = None; self.depth = 0; self.data = {}; self.ids = []; self.hrefs = []
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if 'id' in attrs: self.ids.append(attrs['id'])
                if tag == 'a' and attrs.get('href', '').startswith('#'): self.hrefs.append(attrs['href'][1:])
                if self.key is not None: self.depth += 1
                elif 'data-edit-id' in attrs:
                    self.key = attrs['data-edit-id']; self.data[self.key] = ''; self.depth = 0
            def handle_data(self, data):
                if self.key is not None: self.data[self.key] += data
            def handle_endtag(self, tag):
                if self.key is not None:
                    if self.depth: self.depth -= 1
                    else: self.key = None
        for number in range(10, 21):
            html, segments = reader.segment_state(number, query='prisoners')
            capture = Capture(); capture.feed(html)
            self.assertEqual(capture.data, segments)
            self.assertTrue(set(capture.hrefs) <= set(capture.ids))
            self.assertEqual(len(capture.ids), len(set(capture.ids)))
        html, segments = reader.segment_state(15)
        self.assertIn('role="table"', html)
        self.assertIn('role="columnheader"', html)
        self.assertIn('For larceny and acquitted\t1\t7\t0', segments['s5'])
        self.assertIn('Bastardy\t0\t17\t4', segments['s5'])
        self.assertIn('book-verse', reader.segment_state(14)[0])
        self.assertIn('РАЗВОРОТ 012', reader.search_page('Lisbon packet'))
        before = segments['s5']
        saved = inline_editor.load_edits()
        saved['spreads']['15'] = {'segments': {'s5': 'User-owned table text'}, 'history': []}
        self.assertEqual(reader.segment_state(15, edits=saved)[1]['s5'], 'User-owned table text')

    def test_rotated_scan_is_upright_not_upside_down(self):
        with reader.pymupdf.open(reader.ROOT / 'govard014.pdf') as doc:
            expected = doc[0].get_pixmap(matrix=reader.pymupdf.Matrix(1.8, 1.8).prerotate(-90), alpha=False)
            actual = reader.pymupdf.Pixmap(reader.original_scan_image(14))
            decoded_expected = reader.pymupdf.Pixmap(expected.tobytes('jpg', jpg_quality=85))
            self.assertEqual((actual.width, actual.height), (decoded_expected.width, decoded_expected.height))
            self.assertEqual(actual.samples, decoded_expected.samples)
        self.assertIn('original-v2', reader.scan_url(14))

if __name__ == '__main__':
    unittest.main()


class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(translation, 'EDITS_FILE', Path(self.tmp.name) / 'translation_edits.json')
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_translation_panel_below_original_and_editable(self):
        html = reader.reader_page(10)
        self.assertIn('id="translation"', html)
        self.assertIn('Русский перевод', html)
        self.assertIn('ОБЗОР БЕДСТВИЙ В ТЮРЬМАХ', html)
        self.assertLess(html.index('id="editable-book"'), html.index('id="translation"'))
        missing = reader.reader_page(21)
        self.assertIn('перевод готовится', missing)

    def test_translation_override_and_restore(self):
        saved = translation.load_edits()
        saved['spreads']['10'] = {'text': 'Мой перевод', 'history': []}
        translation.save_edits(saved)
        text, status, user = translation.translation_state(10)
        self.assertEqual(text, 'Мой перевод')
        self.assertTrue(user)
        saved['spreads']['10']['text'] = ''
        translation.save_edits(saved)
        text, status, user = translation.translation_state(10)
        self.assertIn('ОБЗОР БЕДСТВИЙ', text)
        self.assertFalse(user)
