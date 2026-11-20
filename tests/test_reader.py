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
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import inline_editor
import reader
import translation


class ReaderHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), reader.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.log_patch = patch.object(reader.Handler, 'log_message', lambda *args, **kwargs: None)
        cls.log_patch.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.log_patch.stop()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [patch.object(inline_editor, 'EDITS_FILE', root / 'text_edits.json'),
                        patch.object(reader, 'CORRECTIONS_FILE', root / 'corrections.json'),
                        patch.object(reader, 'QUALITY_FILE', root / 'scan_quality.json'),
                        patch.object(reader, 'SCAN_OVERRIDES', root / 'scan_overrides'),
                        patch.object(translation, 'EDITS_FILE', root / 'translation_edits.json')]
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
        suggestion = None
        for number in range(1, 166):
            _, segments = reader.segment_state(number)
            for candidate in reader.review_suggestions(number):
                if segments.get(candidate['segment'], '').count(candidate['old']) == 1:
                    suggestion = candidate
                    break
            if suggestion:
                break
        self.assertIsNotNone(suggestion)
        self.assertEqual(self.form(f'/review/{number}', {'id': suggestion['id'], 'action': 'accept'})[0], 200)
        state = inline_editor.load_edits()['spreads'][str(number)]
        self.assertEqual(state['suggestions'][suggestion['id']]['status'], 'accepted')
        self.assertEqual(self.form(f'/restore/{number}', {'index': len(state['history']) - 1})[0], 200)
        self.assertNotIn(suggestion['id'], inline_editor.load_edits()['spreads'][str(number)]['suggestions'])

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

    def test_front_spreads_spacing_and_footnote_with_saved_edits(self):
        # Previously saved partial OCR corrections take priority over the clean
        # transcription; corrections must be saved to the history as well.
        examples = {1: ('s2', 'express a modern idea, and', 'classes in his day was a more'),
                    2: ('s3', 'Courage, for instance, had', 'gaol-fever¹ was not only'),
                    3: ('s11', 'Minor poets forsook their birthday', 'while in 1780, when Howard')}
        for number, (segment, first, second) in examples.items():
            current = reader.segment_state(number)[1][segment]
            self.assertIn(first, current)
            self.assertIn(second, current)
            self.assertEqual(self.save(number, [{'id': segment, 'old': current, 'new': current + ' '}])[0], 200)
            self.assertEqual(reader.segment_state(number)[1][segment], current + ' ')
            self.assertEqual(inline_editor.load_edits()['spreads'][str(number)]['history'][-1]['before'], current)
        page = self.request('/spread/2')[1]
        self.assertIn('href="#footnote-2-s6-1"', page)
        self.assertIn('id="footnote-2-s6-1"', page)
        self.assertIn('might have thought his work well done', page)

    def test_history_review_decisions_persist_without_changing_text(self):
        original = reader.segment_state(1)[1]['s2']
        revised = original + ' corrected'
        self.assertEqual(self.save(1, [{'id': 's2', 'old': original, 'new': revised}])[0], 200)
        entry = inline_editor.load_edits()['spreads']['1']['history'][0]
        first_token = reader.history_review_token(entry)
        page = self.request('/history/1')[1]
        self.assertIn('Ожидает вашего решения', page)
        self.assertIn('name="review_token"', page)
        def decide(token, decision):
            return self.form('/history-review/1', {'index': '0', 'review_token': token, 'decision': decision})
        self.assertEqual(decide(first_token, 'accepted')[0], 200)
        self.assertEqual(reader.segment_state(1)[1]['s2'], revised)
        self.assertIn('Вы приняли', self.request('/history/1')[1])
        self.assertEqual(decide(first_token, 'rejected')[0], 409)
        entry = inline_editor.load_edits()['spreads']['1']['history'][0]
        self.assertEqual(decide(reader.history_review_token(entry), 'rejected')[0], 200)
        self.assertIn('требуется продолжить исправление', self.request('/history/1')[1])
        self.assertIn('/history/1#history-0', self.request('/followup')[1])
        # Further editing does not silently close the outstanding decision.
        self.assertEqual(self.save(1, [{'id': 's2', 'old': revised, 'new': revised + '!'}])[0], 200)
        self.assertIn('/history/1#history-0', self.request('/followup')[1])
        entry = inline_editor.load_edits()['spreads']['1']['history'][0]
        self.assertEqual(decide(reader.history_review_token(entry), 'pending')[0], 200)
        self.assertNotIn('/history/1#history-0', self.request('/followup')[1])
        self.assertEqual(self.form('/restore/1', {'index': '1'})[0], 200)
        entry = inline_editor.load_edits()['spreads']['1']['history'][0]
        self.assertEqual([e['after'] for e in entry['review_history']], ['accepted', 'rejected', 'pending'])
        self.assertEqual(reader.segment_state(1)[1]['s2'], revised)
        self.assertEqual(self.form('/restore/1', {'index': '0'})[0], 200)
        self.assertEqual(reader.segment_state(1)[1]['s2'], original)

    def test_spread_review_workflow_and_safe_restore(self):
        for n in (1, 2, 3, 107, 165):
            page = self.request(f'/spread/{n}')[1]
            for label in ('Решение по развороту', 'Принять', 'Доработать', 'Вернуть предыдущее'):
                self.assertIn(label, page)
        def state(n=1):
            return inline_editor.load_edits()['spreads'].get(str(n), {}).get('spread_review', {})
        def decision(action, token=None):
            return self.form('/spread-review/1', {'action': action, 'token': token or reader.history_review_token(state())})
        initial = reader.segment_state(1)[1]['s2']
        stale = reader.history_review_token(state())
        self.assertEqual(decision('start')[0], 200)
        self.assertEqual(decision('accept', stale)[0], 409)
        self.assertEqual(self.save(1, [{'id': 's2', 'old': initial, 'new': initial + ' changed'}])[0], 200)
        self.assertEqual(decision('finish')[0], 200)
        self.assertIn('ожидает вашего решения', self.request('/spread/1')[1])
        self.assertEqual(decision('needs_work')[0], 200)
        self.assertIn('/spread/1#spread-review', self.request('/followup')[1])
        self.assertEqual(decision('accept')[0], 200)
        self.assertNotIn('/spread/1#spread-review', self.request('/followup')[1])
        self.assertEqual(decision('restore')[0], 200)
        self.assertEqual(reader.segment_state(1)[1]['s2'], initial)
        self.assertEqual(inline_editor.load_edits()['spreads']['1']['history'][-1]['kind'], 'restore')
        self.assertEqual(state()['state'], 'restored')
        # A later change to a modified segment must not be overwritten.
        self.assertEqual(decision('start')[0], 200)
        self.assertEqual(self.save(1, [{'id': 's2', 'old': initial, 'new': initial + ' one'}])[0], 200)
        self.assertEqual(decision('finish')[0], 200)
        self.assertEqual(self.save(1, [{'id': 's2', 'old': initial + ' one', 'new': initial + ' two'}])[0], 200)
        self.assertEqual(decision('restore')[0], 409)
        self.assertEqual(reader.segment_state(1)[1]['s2'], initial + ' two')
        self.assertEqual(state()['state'], 'ready')
        self.assertEqual(len(state()['cycles']), 1)

    def test_spread_review_without_prior_snapshot(self):
        token = reader.history_review_token({})
        self.assertEqual(self.form('/spread-review/2', {'action': 'needs_work', 'token': token})[0], 200)
        page = self.request('/spread/2')[1]
        self.assertIn('Требует продолжения исправлений', page)
        self.assertIn('value="restore" disabled', page)
        self.assertIn('/spread/2#spread-review', self.request('/followup')[1])

    def test_spread_14_restored_running_heads_preserve_segment_ids(self):
        html, segments = reader.segment_state(14)
        self.assertEqual(html.count('<span class="running-title">BAD CUSTOMS IN PRISONS</span>'), 2)
        self.assertIn('Into the horrors of the gloomy jail?', segments['s1'])
        self.assertTrue(segments['s3'].startswith('prison, 20th March'))
        old = segments['s1']
        self.assertEqual(self.save(14, [{'id': 's1', 'old': old, 'new': old.replace('horrors', 'horrors corrected')}])[0], 200)
        self.assertIn('horrors corrected of the gloomy jail', reader.segment_state(14)[1]['s1'])

    def test_spread_17_tables_have_no_invented_or_bold_headers(self):
        page = self.request('/spread/17')[1]
        self.assertIn('.spread-17 .fee-table .restored-row:first-child{font-weight:normal;border-bottom:1px solid #e7e3d9}', page)
        self.assertNotIn('data-editorial-caption', page)
        self.assertNotIn('editorial-header', page)
        rendered, _ = reader.segment_state(17)
        self.assertEqual(rendered.count('role="table"'), 3)
        self.assertNotIn('role="columnheader"', rendered)

    def test_spread_18_restored_heading_and_running_head(self):
        _, segments = reader.segment_state(18)
        original = segments['s2']
        self.assertEqual(self.save(18, [{'id': 's2', 'old': original, 'new': 'SITUATION\n\n' + original.lstrip()}])[0], 200)
        rendered, segments = reader.segment_state(18)
        self.assertIn('<span class="restored-heading">SITUATION</span>', rendered)
        self.assertEqual(segments['s2'], 'SITUATION\n\n' + original.lstrip())
        self.assertEqual(rendered.count('PROPOSED IMPROVEMENTS'), 2)
        self.assertIn('href="#footnote-18-s3-1"', rendered)

    def test_spread_18_fold_words_are_highlighted_without_changing_text(self):
        from html.parser import HTMLParser
        class Text(HTMLParser):
            def __init__(self): super().__init__(); self.parts = []
            def handle_data(self, text): self.parts.append(text)
        _, segments = reader.segment_state(18)
        for key in ('s0', 's1', 's2', 's3'):
            html = inline_editor.render_verified_block(segments[key], 18, key, '')
            self.assertIn('class="scan-fold-word"', html)
            parser = Text(); parser.feed(html)
            self.assertEqual(''.join(parser.parts), segments[key])
        html = inline_editor.render_verified_block(segments['s0'], 18, 's0', 'lamented')
        self.assertIn('<strong class="scan-fold-word"><mark>lamented</mark></strong>', html)
        edited = segments['s0'].replace('being cherished,', 'being treasured,')
        self.assertNotIn('<strong class="scan-fold-word">treasured</strong>', inline_editor.render_verified_block(edited, 18, 's0', ''))
        self.assertIn('Жирным отмечены слова', self.request('/spread/18')[1])

    def test_spread_20_section_heading_remains_editable_text(self):
        _, segments = reader.segment_state(20)
        original = segments['s5']
        self.assertEqual(self.save(20, [{'id': 's5', 'old': original, 'new': 'REGULATIONS\n\n' + original.lstrip()}])[0], 200)
        rendered, segments = reader.segment_state(20)
        self.assertIn('<span class="restored-heading">REGULATIONS</span>', rendered)
        self.assertTrue(segments['s5'].startswith('REGULATIONS\n\n'))

    def test_spread_21_fourth_footnote_link_and_editing(self):
        html = inline_editor.render_verified_block('Glasgow⁴ said it.\n\n⁴ The note.', 21, 's8', '')
        self.assertIn('href="#footnote-21-s8-4"', html)
        self.assertIn('id="footnote-21-s8-4"', html)
        self.assertEqual(inline_editor.render_verified_block('ordinary ⁴ text', 22, 's8', ''), '<span class="verified-paragraph">ordinary ⁴ text</span>')

    def test_spreads_22_23_restored_and_notes_linked(self):
        for number, refs in ((22, (('s1', 's2', '1'), ('s1', 's2', '2'), ('s1', 's2', '3'), ('s11', 's11', '1'))),
                             (23, (('s1', 's1', '1'), ('s10', 's10', '1'), ('s10', 's10', '2'), ('s10', 's10', '3')))):
            rendered, segments = reader.segment_state(number)
            for source, target, note in refs:
                self.assertIn(f'href="#footnote-{number}-{target}-{note}"', rendered)
                self.assertIn(f'id="footnote-{number}-{target}-{note}"', rendered)
            self.assertTrue(segments['s1'].startswith('the sick.' if number == 22 else 'according to the act,'))
            self.assertNotIn('☑ OCR', reader.quality_label(number))
        self.assertIn('Visitants who are there', reader.segment_state(22)[1]['s11'])
        self.assertIn('taken care of by the same gentlemen', reader.segment_state(23)[1]['s1'])

    def test_spread_24_restores_text_and_page_local_notes(self):
        rendered, segments = reader.segment_state(24)
        self.assertIn('Sir John Pringle observes, p. 51', segments['s1'])
        self.assertIn('prayers. This would divert them', segments['s7'])
        for segment, number in (('s1', '1'), ('s1', '2'), ('s7', '1')):
            self.assertIn(f'href="#footnote-24-{segment}-{number}"', rendered)
            self.assertIn(f'id="footnote-24-{segment}-{number}"', rendered)

    def test_spread_25_footnote_continues_on_right_page(self):
        rendered, segments = reader.segment_state(25)
        self.assertIn('href="#footnote-25-s4-1"', rendered)
        self.assertIn('id="footnote-25-s4-1"', rendered)
        self.assertIn('id="footnote-25-s9-1"', rendered)
        self.assertIn('footnote-continuation', rendered)
        self.assertTrue(segments['s9'].startswith('of each of the two smaller fines'))
        self.assertIn('href="#footnote-25-s10-2"', rendered)
        self.assertIn('id="footnote-25-s10-2"', rendered)

    def test_spreads_26_28_heading_and_cross_page_footnotes(self):
        h26, s26 = reader.segment_state(26)
        self.assertIn('<span class="restored-heading">BRIDEWELLS</span>', h26)
        self.assertIn('id="footnote-26-s5-2"', h26)
        self.assertTrue(s26['s5'].startswith('orders . . . are hung up'))
        self.assertIn('href="#footnote-26-s6-1"', h26)
        h27, s27 = reader.segment_state(27)
        self.assertIn('href="#footnote-27-s9-5"', h27)
        self.assertIn('id="footnote-27-s9-5"', h27)
        self.assertTrue(s27['s9'].endswith('as of'))
        h28, s28 = reader.segment_state(28)
        self.assertIn('id="footnote-28-s5-5"', h28)
        self.assertTrue(s28['s5'].startswith('oak.'))
        self.assertIn('href="#footnote-28-s6-2"', h28)

    def test_spreads_29_31_section_and_footnotes(self):
        h29, s29 = reader.segment_state(29)
        self.assertIn('href="#footnote-29-s10-4"', h29)
        self.assertIn('id="footnote-29-s10-4"', h29)
        self.assertTrue(s29['s9'].endswith('charged;'))
        h30, s30 = reader.segment_state(30)
        self.assertTrue(s30['s1'].startswith('and was preparing'))
        self.assertIn('href="#footnote-30-s3-2"', h30)
        h31, s31 = reader.segment_state(31)
        for title in ('SECTION IV', 'AN ACCOUNT OF FOREIGN PRISONS', 'HOLLAND'):
            self.assertIn('<span class="restored-heading">' + title + '</span>', h31)
        self.assertIn('href="#footnote-31-s4-1"', h31)
        self.assertTrue(s31['s3'].startswith('cleaned.'))

    def test_spread_33_diet_note_and_beer_table_round_trip(self):
        from html.parser import HTMLParser
        class Text(HTMLParser):
            def __init__(self): super().__init__(); self.parts = []
            def handle_data(self, text): self.parts.append(text)
        _, segments = reader.segment_state(33)
        value = segments['s7']
        html = inline_editor.render_verified_block(value, 33, 's7', '')
        self.assertIn('href="#footnote-33-s7-1"', html)
        self.assertIn('id="footnote-33-s7-1"', html)
        self.assertIn('role="table" aria-label="Daily beer allowance in cans"', html)
        self.assertIn('role="columnheader"', html)
        self.assertIn('\t2½', value)
        parser = Text(); parser.feed(html)
        self.assertEqual(''.join(parser.parts), value)
        self.assertIn('href="#footnote-34-s11-2"', reader.segment_state(34)[0])

    def test_spreads_35_37_table_verse_and_diet_notes(self):
        from html.parser import HTMLParser
        class Text(HTMLParser):
            def __init__(self): super().__init__(); self.parts = []
            def handle_data(self, text): self.parts.append(text)
        h35, s35 = reader.segment_state(35)
        self.assertIn('aria-label="Executions at Amsterdam, 1693–1766"', h35)
        self.assertNotIn('role="columnheader"', h35)
        self.assertIn('<span class="book-verse">Virtutis est domare', h35)
        block = inline_editor.render_verified_block(s35['s1'], 35, 's1', '')
        parser = Text(); parser.feed(block)
        self.assertEqual(''.join(parser.parts), s35['s1'])
        self.assertIn('href="#footnote-36-s9-1"', reader.segment_state(36)[0])
        self.assertIn('href="#footnote-37-s1-1"', reader.segment_state(37)[0])
        self.assertIn('href="#footnote-37-s9-3"', reader.segment_state(37)[0])

    def test_spreads_38_40_germany_and_footnotes(self):
        h38, s38 = reader.segment_state(38)
        self.assertIn('<span class="restored-heading">GERMANY</span>', h38)
        self.assertTrue(s38['s1'].endswith('magistrates,'))
        self.assertTrue(s38['s11'].startswith('he may take in as boarders'))
        h39, s39 = reader.segment_state(39)
        self.assertIn('href="#footnote-39-s1-1"', h39)
        self.assertIn('six sous (3¼d.)', s39['s8'])
        self.assertIn('two marien grosche (or 2½d.)', s39['s8'])
        h40, s40 = reader.segment_state(40)
        self.assertIn('href="#footnote-40-s6-1"', h40)
        self.assertTrue(s40['s6'].endswith('Of Torture.'))

    def test_spreads_41_43_prose_and_note(self):
        _, s41 = reader.segment_state(41)
        self.assertTrue(s41['s1'].endswith('keys to the'))
        self.assertTrue(s41['s8'].startswith('turnkey.'))
        h42, s42 = reader.segment_state(42)
        self.assertIn('href="#footnote-42-s8-1"', h42)
        self.assertIn('peeled barley, oats or buck-wheat', s42['s1'])
        h43, s43 = reader.segment_state(43)
        self.assertIn('<span class="restored-heading">SWEDEN</span>', h43)
        self.assertIn('since 1771, whipping, and slavery for life', s43['s1'])
        self.assertTrue(s43['s13'].endswith('When'))

    def test_spreads_44_46_prose_and_footnotes(self):
        h44, s44 = reader.segment_state(44)
        self.assertIn('href="#footnote-44-s1-1"', h44)
        self.assertIn('href="#footnote-44-s7-1"', h44)
        self.assertIn('the cellar still open', s44['s1'])
        h45, s45 = reader.segment_state(45)
        for number in ('1', '2', '3'):
            self.assertIn('href="#footnote-45-s11-' + number + '"', h45)
        self.assertIn('salary of £50 per annum', s45['s1'])
        _, s46 = reader.segment_state(46)
        self.assertTrue(s46['s1'].endswith('deserters, and criminals'))
        self.assertTrue(s46['s10'].startswith('of various sorts'))

    def test_spreads_47_49_prose_and_footnotes(self):
        h47, s47 = reader.segment_state(47)
        self.assertIn('href="#footnote-47-s8-1"', h47)
        self.assertIn('For some months at first they are allowed meat', s47['s1'])
        h48, s48 = reader.segment_state(48)
        self.assertIn('href="#footnote-48-s1-1"', h48)
        self.assertIn('href="#footnote-48-s9-1"', h48)
        self.assertIn('the prisoners subsisting chiefly by charitable contributions', s48['s9'])
        h49, s49 = reader.segment_state(49)
        self.assertIn('href="#footnote-49-s6-1"', h49)
        self.assertIn('twenty copecks (6½d.)', s49['s1'])
        self.assertIn('two grosche (3¼d.)', s49['s6'])

    def test_spreads_50_52_and_missing_scan_notice(self):
        h50, s50 = reader.segment_state(50)
        for number in ('1', '2', '3'):
            self.assertIn('href="#footnote-50-s7-' + number + '"', h50)
        self.assertIn('<span class="restored-heading">GERMANY</span>', h50)
        h51, s51 = reader.segment_state(51)
        self.assertIn('href="#footnote-51-s1-1"', h51)
        self.assertIn('href="#footnote-51-s11-2"', h51)
        self.assertIn('страницы 86–87', reader.reader_page(50))
        self.assertIn('страницы 86–87', reader.reader_page(51))
        h52, s52 = reader.segment_state(52)
        self.assertIn('href="#footnote-52-s6-2"', h52)
        self.assertIn('book-verse inscription', h52)
        self.assertIn('JUSTITIÆ ET CLEMENTIÆ', s52['s6'])
        self.assertTrue(s52['s1'].endswith('other public buildings.'))

    def test_arabic_numbered_footnotes_are_styled_and_links_resolve(self):
        from html.parser import HTMLParser
        class Text(HTMLParser):
            def __init__(self):
                super().__init__()
                self.text = ''
            def handle_data(self, data):
                self.text += data
        for number, targets in {
            42: [('s8', '1')], 44: [('s1', '1'), ('s7', '1')],
            45: [('s11', '1'), ('s11', '2'), ('s11', '3')],
            47: [('s8', '1')], 48: [('s1', '1'), ('s9', '1')],
            49: [('s6', '1')], 50: [('s7', '1'), ('s7', '2'), ('s7', '3')],
            51: [('s1', '1'), ('s11', '1'), ('s11', '2')],
            52: [('s1', '1'), ('s6', '1'), ('s6', '2')],
        }.items():
            html, segments = reader.segment_state(number)
            for segment, note in targets:
                target = f'footnote-{number}-{segment}-{note}'
                self.assertIn(f'href="#{target}"', html)
                self.assertIn(f'class="book-footnote verified-footnote" id="{target}"', html)
                block = inline_editor.render_verified_block(segments[segment], number, segment, '')
                parser = Text()
                parser.feed(block)
                self.assertEqual(parser.text, segments[segment])
        ordinary = inline_editor.render_verified_block('1 This is numbered prose.', 42, 's1', '')
        self.assertIn('verified-paragraph', ordinary)
        self.assertNotIn('verified-footnote', ordinary)
        self.assertIn('font-size:.78em', reader.reader_page(42))

    def test_spreads_53_55_inscriptions_and_footnotes(self):
        h53, s53 = reader.segment_state(53)
        self.assertIn('href="#footnote-53-s7-1"', h53)
        self.assertIn('class="book-footnote verified-footnote" id="footnote-53-s7-1"', h53)
        self.assertIn('Domine, cum veneris judicare', s53['s7'])
        h54, s54 = reader.segment_state(54)
        self.assertEqual(h54.count('book-verse inscription'), 5)
        self.assertIn('INSTRUCTI REIPUBLICÆ SERVIANT.', s54['s1'])
        h55, s55 = reader.segment_state(55)
        for segment in ('s1', 's8'):
            for number in ('1', '2'):
                target = f'footnote-55-{segment}-{number}'
                self.assertIn(f'href="#{target}"', h55)
                self.assertIn(f'class="book-footnote verified-footnote" id="{target}"', h55)
        self.assertIn('From the heat of the climate', s55['s8'])
        self.assertIn('350,061', s55['s1'])

    def test_spreads_56_58_headings_and_footnotes(self):
        for number, destinations in {
            56: [('s1', '1'), ('s1', '2'), ('s1', '3')],
            57: [('s1', '1'), ('s6', '1'), ('s6', '2')],
            58: [('s1', '1'), ('s10', '1'), ('s10', '2')],
        }.items():
            html, segments = reader.segment_state(number)
            for segment, note in destinations:
                target = f'footnote-{number}-{segment}-{note}'
                self.assertIn(f'href="#{target}"', html)
                self.assertIn(f'class="book-footnote verified-footnote" id="{target}"', html)
        h57, s57 = reader.segment_state(57)
        self.assertIn('<span class="restored-heading">GENEVA</span>', h57)
        self.assertIn('<span class="restored-heading">SWITZERLAND</span>', h57)
        self.assertIn('two feet five inches by one foot five', s57['s1'])
        self.assertIn('a hundred to two hundred', reader.segment_state(54)[1]['s5'])

    def test_spreads_59_61_heading_inscription_and_footnotes(self):
        for number, destinations in {59: [('s1', '1'), ('s1', '2'), ('s9', '1')], 60: [('s1', '1')]}.items():
            html, segments = reader.segment_state(number)
            for segment, note in destinations:
                target = f'footnote-{number}-{segment}-{note}'
                self.assertIn(f'href="#{target}"', html)
                self.assertIn(f'class="book-footnote verified-footnote" id="{target}"', html)
        h59, s59 = reader.segment_state(59)
        self.assertIn('<span class="restored-heading">GERMANY</span>', h59)
        h60, s60 = reader.segment_state(60)
        self.assertIn('book-verse inscription', h60)
        self.assertIn('upon bread and water can work hard', s60['s13'])
        _, s61 = reader.segment_state(61)
        self.assertTrue(s61['s8'].startswith('we should not despair'))

    def test_spreads_62_64_notes_regulations_and_list(self):
        h62, s62 = reader.segment_state(62)
        for segment in ('s1', 's5'):
            target = f'footnote-62-{segment}-1'
            self.assertIn(f'href="#{target}"', h62)
            self.assertIn(f'class="book-footnote verified-footnote" id="{target}"', h62)
        self.assertIn('<span class="restored-heading">AUSTRIAN FLANDERS</span>', h62)
        self.assertIn('regulation-extract', h62)
        h63, s63 = reader.segment_state(63)
        self.assertIn('regulation-extract', h63)
        self.assertIn('no prisoners sleep underground', s63['s10'])
        h64, s64 = reader.segment_state(64)
        self.assertIn('class="book-footnote verified-footnote" id="footnote-64-s1-1"', h64)
        self.assertIn('class="book-list"', h64)
        self.assertIn('14. Punishment for the default.', s64['s7'])

    def test_spreads_65_67_headings_and_notes(self):
        for number, destinations in {66: [('s1', '1')], 67: [('s1', '1'), ('s1', '2'), ('s5', '1')]}.items():
            html, segments = reader.segment_state(number)
            for segment, note in destinations:
                target = f'footnote-{number}-{segment}-{note}'
                self.assertIn(f'href="#{target}"', html)
                self.assertIn(f'class="book-footnote verified-footnote" id="{target}"', html)
        h66, s66 = reader.segment_state(66)
        self.assertIn('<span class="restored-heading">PORTUGAL</span>', h66)
        h67, s67 = reader.segment_state(67)
        self.assertIn('<span class="restored-heading">SPAIN</span>', h67)
        self.assertIn('three hundred and nine men and five hundred and forty-seven women', s67['s5'])
        self.assertTrue(reader.segment_state(65)[1]['s5'].endswith('In consequence of this vile policy I'))

    def test_scan_zoom_controls_and_detail_image(self):
        page = self.request('/spread/107')[1]
        for marker in ('scan-zoom-toolbar', 'scan-zoom-in', 'scan-zoom-out', 'scan-zoom-reset', 'data-detail-src', 'detail=1', 'scan-fullscreen', 'scan-fullscreen-close', 'scan-fullscreen-view', 'aria-modal="true"'):
            self.assertIn(marker, page)
        normal = reader.pymupdf.Pixmap(reader.scan_image(107))
        detail = reader.pymupdf.Pixmap(reader.scan_image(107, detail=True))
        self.assertGreater(detail.width, normal.width)
        self.assertGreater(detail.height, normal.height)
        self.assertEqual(self.request('/scan/107.jpg?detail=1')[0], 200)
        original = reader.pymupdf.Pixmap(reader.scan_image(14))
        rotated_detail = reader.pymupdf.Pixmap(reader.scan_image(14, detail=True))
        self.assertGreater(rotated_detail.width, rotated_detail.height)
        self.assertGreater(rotated_detail.width, original.width)
        pdf = reader.pymupdf.open(); pdf.new_page(width=100, height=50)
        payload = pdf.tobytes(); pdf.close()
        self.assertEqual(self.upload_scan(107, payload)[0], 200)
        replaced = reader.pymupdf.Pixmap(reader.scan_image(107, detail=True))
        self.assertEqual((replaced.width, replaced.height), (350, 175))
        self.assertIn('Загруженный вами скан', self.request('/spread/107')[1])

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
        # Automated collation does not replace manual proofreading.
        self.assertIn('☑ OCR', reader.quality_label(68))
        self.assertEqual(self.form('/scan-action/68', {'action':'dismiss-ocr'})[0], 200)
        self.assertNotIn('OCR', reader.quality_label(68))
        self.assertIn('скан повёрнут', reader.quality_label(14))
        self.assertIn('пометка OCR снята', self.request('/spread/68')[1])

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
        self.assertEqual(self.form('/scan-action/68', {'action':'restore-ocr'})[0], 200)
        self.assertIn('☑ OCR', reader.quality_label(68))

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
        for number in (68, 96, 106, 108):
            self.assertIn('☑ OCR', reader.quality_label(number))
        self.assertNotIn('☑ OCR', reader.quality_label(107))
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

    def test_head_matches_get_without_a_body_and_stays_restricted(self):
        paths = ['/', '/spread/10', '/spread/145', '/spread/14', '/search?q=prison',
                 '/quality', '/history/1', '/edit/1', '/review/1', '/omissions/9',
                 '/translation-history/10', '/scan/14.jpg', '/govard001.pdf', '/ocr_review/1_1.txt']
        for path in paths:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, method='HEAD')) as head:
                self.assertEqual(head.status, 200, path)
                self.assertEqual(head.read(), b'', path)
                length = head.headers['Content-Length']
                ctype = head.headers['Content-Type']
            with urllib.request.urlopen(self.base + path) as get:
                self.assertEqual(get.headers['Content-Length'], length, path)
                self.assertEqual(get.headers['Content-Type'], ctype, path)
                self.assertGreater(len(get.read()), 0, path)
        for path in ['/spread/0', '/spread/166', '/translation-history/0', '/translation-history/166',
                     '/text_edits.json', '/translation_edits.json', '/reader.py', '/.git/HEAD']:
            self.assertEqual(self.request(path, method='HEAD')[0], 404, path)

    def test_translation_http_history_conflict_and_english_isolation(self):
        _, english_before = reader.segment_state(10)
        page = self.request('/spread/10')[1]
        import re
        token = re.search(r'name="expected" value="([a-f0-9]+)"', page).group(1)
        self.assertEqual(self.form('/translation/10', {'text': 'Мой проверенный перевод', 'expected': token})[0], 200)
        self.assertEqual(translation.current_text(10), 'Мой проверенный перевод')
        self.assertEqual(reader.segment_state(10)[1], english_before)
        self.assertFalse(inline_editor.EDITS_FILE.exists())
        data = translation.load_edits()
        self.assertEqual(len(data['spreads']['10']['history']), 1)
        current = translation.revision(10, data)
        # An unchanged submission is a no-op, not a new history entry.
        self.assertEqual(self.form('/translation/10', {'text': 'Мой проверенный перевод', 'expected': current})[0], 200)
        self.assertEqual(len(translation.load_edits()['spreads']['10']['history']), 1)
        # A second tab must not silently overwrite the first tab's save.
        status, body = self.form('/translation/10', {'text': 'Устаревшая вкладка', 'expected': token})
        self.assertEqual(status, 409)
        self.assertIn('Устаревшая вкладка', body)
        self.assertEqual(translation.current_text(10), 'Мой проверенный перевод')
        history = self.request('/translation-history/10')[1]
        self.assertIn('Мой проверенный перевод', history)
        # Clearing the override restores the authored translation, not English.
        self.assertEqual(self.form('/translation/10', {'text': '', 'expected': current})[0], 200)
        self.assertIn('ОБЗОР БЕДСТВИЙ', translation.current_text(10))
        self.assertNotIn('text', translation.load_edits()['spreads']['10'])
        reset_token = translation.revision(10)
        self.assertEqual(self.form('/translation-history/10', {'index': '1', 'expected': reset_token})[0], 200)
        self.assertEqual(translation.current_text(10), 'Мой проверенный перевод')
        self.assertEqual(len(translation.load_edits()['spreads']['10']['history']), 3)
        self.assertEqual(reader.segment_state(10)[1], english_before)
        self.assertEqual(self.request('/translation/0', data=b'text=x', content_type='application/x-www-form-urlencoded')[0], 404)
        self.assertEqual(self.request('/translation/10', data=b'{}', content_type='application/json')[0], 415)

    def test_bilingual_search_links_to_the_translation_anchor(self):
        page = reader.search_page('подлинного целебного дара жизни')
        self.assertIn('/spread/10?q=', page)
        self.assertIn('#translation', page)
        self.assertIn('Русский перевод', page)
        self.assertIn('РАЗВОРОТ 010', reader.search_page('salutiferous'))

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
            self.assertIn('verified-prose-page', html)

    def test_spreads_7_to_9_restored_words_and_footnotes(self):
        page7, seg7 = reader.segment_state(7)
        self.assertIn('Newgate and Ludgate', seg7['s2'])
        self.assertIn('myself from infection in the prisons and hospitals', seg7['s17'])
        self.assertIn('href="#footnote-7-s17-1"', page7)
        self.assertIn('id="footnote-7-s17-1"', page7)
        _, seg8 = reader.segment_state(8)
        self.assertIn('sallow meagre countenances', seg8['s5'])
        self.assertIn('in loathsome cells', seg8['s7'])
        page9, seg9 = reader.segment_state(9)
        self.assertIn('In 1782, the weight of a twopenny white loaf', seg9['s16'])
        for target in ('footnote-9-s8-1', 'footnote-9-s9-2', 'footnote-9-s16-1', 'footnote-9-s16-2'):
            self.assertIn('href="#' + target + '"', page9)
            self.assertIn('id="' + target + '"', page9)
        self.assertIn('My own text', reader.segment_state(7, {'spreads': {'7': {'segments': {'s1': 'My own text'}}}})[1]['s1'])

    def test_spread_107_transcription_notes_and_overrides(self):
        page, segments = reader.segment_state(107)
        self.assertIn('3s. 5½d.', segments['s7'])
        self.assertIn('¹ This was by chaining', segments['s7'])
        self.assertIn('² An unhappy debtor', segments['s7'])
        self.assertIn('footnote-107-s7-1', page)
        self.assertIn('footnote-107-s7-2', page)
        self.assertIn('restored-heading', page)
        self.assertEqual(segments['s13'], '')
        self.assertNotIn('☑ OCR', reader.quality_label(107))
        overrides = {'spreads': {'107': {'segments': {'s2': 'My correction'}}}}
        self.assertEqual(reader.segment_state(107, edits=overrides)[1]['s2'], 'My correction')

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
        working = reader.reader_page(68)
        self.assertIn('перевод по черновому английскому тексту', working)
        self.assertIn('Русский перевод', working)

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

    def test_translation_tables_have_nine_columns_and_matching_numbers(self):
        from html.parser import HTMLParser
        import re
        class Rows(HTMLParser):
            def __init__(self):
                super().__init__(); self.tables=[]
            def handle_starttag(self, tag, attrs):
                a=dict(attrs)
                if a.get('role')=='table': self.tables.append({'statistical':'statistical-table' in a.get('class',''),'rows':[]})
                elif a.get('role')=='row' and self.tables: self.tables[-1]['rows'].append(0)
                elif a.get('role') in ('cell','columnheader') and self.tables and self.tables[-1]['rows']: self.tables[-1]['rows'][-1]+=1
        for number in (145,146,147):
            html=translation.render_translation(translation.BASE_TRANSLATIONS[number])
            parser=Rows(); parser.feed(html)
            self.assertTrue(parser.tables)
            for table in parser.tables:
                widths=set(table['rows'])
                self.assertEqual(len(widths),1,(number,table['rows']))
                if table['statistical']:
                    self.assertEqual(widths,{9})
                else:
                    self.assertIn(widths,({3},{5}))
                self.assertNotIn(17,widths)
        def numeric_cells(text):
            values=[]
            for chunk in re.split(r'\n\n',text):
                if '\t' not in chunk: continue
                for line in chunk.splitlines():
                    for cell in line.split('\t'):
                        cell=cell.strip().replace(' ','').replace(',','')
                        if re.fullmatch(r'[0-9]+',cell) and cell not in ('1779','1782','1783'): values.append(cell)
            return values
        for number, ru in ((n,translation.BASE_TRANSLATIONS[n]) for n in translation.MANUAL_SPREADS):
            en='\n\n'.join(reader.segment_state(number)[1].values())
            if number==2:
                en='\n'.join(name+'\t'+miles for name,miles in reader.TABLE_2_ROWS)
            self.assertEqual(numeric_cells(en),numeric_cells(ru),number)

    def test_translation_coverage_footnotes_and_source_status_are_explicit(self):
        self.assertEqual(len(translation.BASE_TRANSLATIONS),165)
        panel=translation.panel(1,english_verified=False)
        self.assertIn('всех 165',panel)
        self.assertIn('черновому английскому тексту',panel)
        self.assertIn('рабочий машинный перевод',panel)
        self.assertIn('footnote-continuation',reader.reader_page(20))
        self.assertIn('[Сноска 1, продолжение]',translation.BASE_TRANSLATIONS[20])
        self.assertIn('тисками для больших пальцев',translation.BASE_TRANSLATIONS[16])
        self.assertNotIn('можно приходится',translation.BASE_TRANSLATIONS[16])
        self.assertIn('бесстыдными и развратными мерзостями',translation.BASE_TRANSLATIONS[13])
        html=reader.reader_page(147)
        self.assertIn('Арифметика оригинала',html)
        self.assertIn('282',html); self.assertIn('262',html)

    def test_context_notes_are_separate_from_editable_translation(self):
        panel = translation.panel(1, english_verified=True)
        self.assertIn('context-notes', panel)
        self.assertIn('Кеннет Рак', panel)
        self.assertIn('не часть текста Говарда', panel)
        self.assertNotIn('Редакционные пояснения', translation.current_text(1))
        saved = translation.load_edits()
        saved['spreads']['1'] = {'text': 'Мой перевод', 'history': []}
        translation.save_edits(saved)
        panel = translation.panel(1, english_verified=True)
        self.assertIn('Мой перевод', panel)
        self.assertIn('context-notes', panel)
        self.assertNotIn('Редакционные пояснения', translation.current_text(1))

    def test_semantic_terminology_and_script_regressions(self):
        import re
        text = '\n'.join(translation.BASE_TRANSLATIONS.values()).lower()
        for bad in ('фелон', 'гарнир', 'коттер', 'окружные тюрьмы', 'тюремь', 'чумаж'):
            self.assertNotIn(bad, text)
        self.assertFalse(re.search(r'[\u3400-\u9fff]', text))
        for good in ('тюрьмы графств', 'вступительный побор', 'уголовных заключённых',
                     'ссыльных', 'кеннет рак', 'эвримен'):
            self.assertIn(good, text)
        self.assertIn('42 033', translation.BASE_TRANSLATIONS[2])
        self.assertIn('1909', translation.BASE_TRANSLATIONS[4])

    def test_statistical_table_labels_are_translated_but_places_remain_cross_checkable(self):
        table = translation.BASE_TRANSLATIONS[145]
        self.assertIn('Испр. дом', table)
        self.assertIn('Должницы, ж.', table)
        self.assertIn('Уголовные и пр.', table)
        self.assertIn('Abingdon', table)
        self.assertNotIn('\nBridewell\t', table)

    def test_every_spread_renders_with_translation_below_english(self):
        for number in range(1,166):
            html=reader.reader_page(number)
            self.assertIn('id="editable-book"',html)
            self.assertIn('id="translation"',html)
            self.assertLess(html.index('id="editable-book"'),html.index('id="translation"'))
            self.assertIn(f'/translation-history/{number}',html)


if __name__ == '__main__':
    unittest.main()
