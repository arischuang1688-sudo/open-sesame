"""Offline regression for actual cron, retries, strict freshness and Pages delivery."""
import copy
import io
import json
import os
from datetime import datetime
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts import trading_day as calendar
from scripts import scheduled_update as schedule
from scripts import publish_pages as pages
from test_trading_day import allowed, run_inline, GENERATOR, FRESHNESS, DOWNSTREAM

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / '.github/workflows/main.yml').read_text(encoding='utf-8')


def snapshot(day='2026-10-08'):
    return {'market': {'quote': {'date': day}, 'margin': {'history': [{'date': day}]}},
            'trust_date': day,
            'source_dates': {k: day for k in ('stock_quote', 'market_index', 'institutional', 'margin', 'pe_ratio')},
            'validation': {'status': 'passed', 'trading_date': day, 'same_day_sources': True,
                           'stock_quotes_checked': 200, 'pe_ratios_checked': 200},
            'stocks': [{'code': str(1000+i), 'margin': '100 張',
                        'quote_date': day, 'pe_ratio': 18.5, 'pe_under_20': True, 'pe_date': day,
                        'close': 100.25, 'volume': 1234.57, 'change': 1.5} for i in range(200)]}


class ScheduledUpdateTest(unittest.TestCase):
    def test_recorded_cross_midnight_failures_resolve_to_completed_session(self):
        records = json.loads((ROOT / 'tests/fixtures/schedule_delay_20261013.json').read_text())
        with patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')) as http:
            for row in records:
                with self.subTest(run=row['run_id']):
                    now = datetime.fromisoformat(row['calendar_at'].replace('Z', '+00:00'))
                    result = calendar.decision(now, 'schedule')
                    self.assertEqual(result['date'], row['old_expected'])
                    self.assertEqual(result['expected_trading_date'], row['generated'])
                    self.assertFalse(result['skip'])
                    schedule.require_scheduled_date(row['generated'], result['expected_trading_date'], 'schedule')
                    self.assertTrue(schedule.complete_snapshot(snapshot(row['generated']), result['expected_trading_date']))
            http.assert_not_called()

    def test_completed_session_cutoff_preopen_intraday_holidays_and_weekends(self):
        cases = [
            ('2026-10-12T21:04:59+08:00', '2026-10-08'),
            ('2026-10-12T21:05:00+08:00', '2026-10-12'),
            ('2026-10-12T21:35:00+08:00', '2026-10-12'),
            ('2026-10-12T22:05:00+08:00', '2026-10-12'),
            ('2026-10-12T22:35:00+08:00', '2026-10-12'),
            ('2026-10-13T00:00:00+08:00', '2026-10-12'),
            ('2026-10-13T08:59:59+08:00', '2026-10-12'),
            ('2026-10-13T12:00:00+08:00', '2026-10-12'),
            ('2026-10-13T13:30:00+08:00', '2026-10-12'),
            ('2026-10-13T21:05:00+08:00', '2026-10-13'),
            ('2026-10-09T06:00:00+08:00', '2026-10-08'),
            ('2026-10-09T21:05:00+08:00', '2026-10-08'),
            ('2026-10-17T06:00:00+08:00', '2026-10-16'),
            ('2026-10-26T21:05:00+08:00', '2026-10-23'),
            ('2026-02-23T06:00:00+08:00', '2026-02-11'),
            ('2026-10-12T21:41:29+00:00', '2026-10-12'),
        ]
        for clock, expected in cases:
            with self.subTest(clock=clock):
                result = calendar.decision(datetime.fromisoformat(clock), 'schedule')
                self.assertEqual(result['expected_trading_date'], expected)
                self.assertFalse(result['skip'])

    def test_workflow_consumes_frozen_expected_date_at_both_guards(self):
        self.assertEqual(WORKFLOW.count('EXPECTED_TRADING_DATE: ${{ steps.calendar.outputs.expected_trading_date }}'), 2)
        self.assertNotIn('EXPECTED_TRADING_DATE: ${{ steps.calendar.outputs.taipei_date }}', WORKFLOW)

    def test_same_day_incomplete_snapshot_must_reach_strict_validation(self):
        day = '2026-10-12'
        cases = []
        d = snapshot(day); d.pop('validation'); cases.append(d)
        d = snapshot(day); d['stocks'].pop(); cases.append(d)
        d = snapshot(day); d['stocks'][-1].pop('close'); cases.append(d)
        d = snapshot(day); d['source_dates']['margin'] = '2026-10-08'; cases.append(d)
        d = snapshot(day); d['stocks'][-1]['margin'] = '資料待補'; cases.append(d)
        with patch.dict(os.environ, {'GITHUB_EVENT_NAME': 'schedule', 'EXPECTED_TRADING_DATE': day}):
            for old in cases:
                with self.subTest(old=old.get('validation')):
                    self.assertFalse(schedule.complete_snapshot(old, day))
                    # Even no improvement in margin count must not bypass validation.
                    result = run_inline(FRESHNESS, old, copy.deepcopy(old))
                    self.assertEqual(result['skip'], 'false')
                    outputs = {('calendar', 'skip'): 'false', ('scheduled', 'skip'): 'false',
                               ('freshness', 'skip'): result['skip']}
                    self.assertTrue(allowed('80% Strict same-trading-day validation', outputs))
            self.assertEqual(run_inline(FRESHNESS, snapshot(day), snapshot(day))['skip'], 'true')

    def test_delayed_run_still_rejects_genuinely_stale_missing_or_future_data(self):
        expected = calendar.decision(datetime.fromisoformat('2026-10-13T06:03:44+08:00'), 'schedule')['expected_trading_date']
        with patch.dict(os.environ, {'GITHUB_EVENT_NAME': 'schedule', 'EXPECTED_TRADING_DATE': expected}):
            for day in ('2026-10-08', '', '2026-10-13'):
                with self.subTest(day=day), self.assertRaisesRegex(SystemExit, 'SCHEDULE FRESHNESS FAILED'):
                    run_inline(FRESHNESS, snapshot('2026-10-08'), snapshot(day))
            self.assertEqual(run_inline(FRESHNESS, {}, snapshot(expected))['skip'], 'false')

    def test_validation_stamp_cannot_hide_missing_or_invalid_quote_fields(self):
        for field in ('close', 'volume', 'change'):
            for value in (None, '', '100', True, float('nan'), float('inf')):
                with self.subTest(field=field, value=value):
                    data = snapshot(); data['stocks'][-1][field] = value
                    self.assertFalse(schedule.complete_snapshot(data, '2026-10-08'))
        for field, value in [('close', 0), ('volume', -1), ('code', None)]:
            data = snapshot(); data['stocks'][-1][field] = value
            self.assertFalse(schedule.complete_snapshot(data, '2026-10-08'))

    def test_delayed_cli_reuses_valid_snapshot_without_fetch_or_generation(self):
        for clock, day in [('2026-10-13T05:41:29+08:00', '2026-10-12'),
                           ('2026-10-17T06:00:00+08:00', '2026-10-16')]:
            with self.subTest(clock=clock), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / 'output'
                env = {'GITHUB_EVENT_NAME': 'schedule', 'BACKFILL': 'false',
                       'GITHUB_OUTPUT': str(output), 'GITHUB_STEP_SUMMARY': ''}
                with patch.dict(os.environ, env), \
                        patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')) as http:
                    calendar.main(datetime.fromisoformat(clock))
                    outputs = dict(line.split('=', 1) for line in output.read_text().splitlines())
                    self.assertEqual(outputs['expected_trading_date'], day)
                    env['EXPECTED_TRADING_DATE'] = outputs['expected_trading_date']
                    output.unlink()
                    with patch.dict(os.environ, env), \
                            patch.object(Path, 'read_text', return_value=json.dumps(snapshot(day))):
                        schedule.main()
                    self.assertEqual(output.read_text(), 'skip=true\n')
                    self.assertFalse(allowed(GENERATOR, {('calendar', 'skip'): outputs['skip'],
                                                         ('scheduled', 'skip'): 'true'}))
                    http.assert_not_called()

    def test_absent_corrupt_stale_and_incomplete_snapshots_are_not_reused(self):
        invalid = snapshot('2026-10-12'); invalid['stocks'].pop()
        cases = [FileNotFoundError(), 'bad json', '{}', json.dumps(invalid),
                 json.dumps(snapshot('2026-10-08'))]
        for content in cases:
            with self.subTest(content=str(content)[:40]), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / 'output'
                read = {'side_effect': content} if isinstance(content, Exception) else {'return_value': content}
                with patch.dict(os.environ, {'GITHUB_EVENT_NAME': 'schedule',
                                             'EXPECTED_TRADING_DATE': '2026-10-12',
                                             'GITHUB_OUTPUT': str(output), 'GITHUB_STEP_SUMMARY': ''}), \
                        patch.object(Path, 'read_text', **read), \
                        patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')):
                    schedule.main()
                self.assertEqual(output.read_text(), 'skip=false\n')

    def test_scheduled_repair_cannot_overwrite_a_newer_snapshot(self):
        with patch.dict(os.environ, {'GITHUB_EVENT_NAME': 'schedule', 'EXPECTED_TRADING_DATE': '2026-10-12'}):
            with self.assertRaisesRegex(SystemExit, 'refusing rollback'):
                run_inline(FRESHNESS, snapshot('2026-10-13'), snapshot('2026-10-12'))

    def test_real_cron_means_taipei_2105_and_recovery_checks(self):
        crons = re.findall(r'^    - cron: "([^"]+)"$', WORKFLOW, re.M)
        self.assertEqual(crons, ['5 13 * * 1-5', '35 13 * * 1-5', '5,35 14 * * 1-5'])
        self.assertNotRegex(WORKFLOW, r'(?m)^\s+timezone:')
        actual = []
        for cron in crons:
            minute, hour, dom, month, weekday = cron.split()
            self.assertEqual((dom, month, weekday), ('*', '*', '1-5'))
            for m in minute.split(','):
                utc = datetime(2026, 10, 12, int(hour), int(m), tzinfo=ZoneInfo('UTC'))
                actual.append(utc.astimezone(ZoneInfo('Asia/Taipei')).strftime('%H:%M'))
        self.assertEqual(actual, ['21:05', '21:35', '22:05', '22:35'])

    def test_complete_snapshot_reused_only_for_matching_date(self):
        self.assertTrue(schedule.complete_snapshot(snapshot(), '2026-10-08'))
        for day in ('2026-10-07', '2026-10-12', '', 'not-a-date'):
            self.assertFalse(schedule.complete_snapshot(snapshot(), day))

    def test_missing_margins_must_still_allow_backfill(self):
        for value in schedule.MISSING_MARGIN:
            d = snapshot(); d['stocks'][-1]['margin'] = value
            self.assertFalse(schedule.complete_snapshot(d, '2026-10-08'))
            self.assertTrue(schedule.complete_snapshot(d, '2026-10-08', require_margin=False))

    def test_200_unique_quotes_and_passed_same_day_sources_required(self):
        cases = []
        d = snapshot(); d['stocks'].pop(); cases.append(d)
        d = snapshot(); d['stocks'][-1]['code'] = d['stocks'][0]['code']; cases.append(d)
        d = snapshot(); d['stocks'][-1]['code'] = ''; cases.append(d)
        for field, value in [('stock_quotes_checked', 50), ('same_day_sources', False),
                             ('trading_date', '2026-10-07'), ('status', 'failed')]:
            d = snapshot(); d['validation'][field] = value; cases.append(d)
        for field in ('stock_quote', 'market_index', 'institutional', 'margin'):
            d = snapshot(); d['source_dates'][field] = '2026-10-07'; cases.append(d)
        d = snapshot(); d['trust_date'] = '2026-10-07'; cases.append(d)
        d = snapshot(); d['market']['margin']['history'] = []; cases.append(d)
        for data in cases + [{}, None]:
            self.assertFalse(schedule.complete_snapshot(data, '2026-10-08'))

    def test_cli_does_not_fetch_and_does_not_skip_manual_backfill(self):
        with tempfile.TemporaryDirectory() as tmp:
            for event, skip in [('schedule', 'true'), ('workflow_dispatch', 'false')]:
                output = Path(tmp) / event
                with patch.dict(os.environ, {'GITHUB_EVENT_NAME': event, 'EXPECTED_TRADING_DATE': '2026-10-08',
                                             'GITHUB_OUTPUT': str(output), 'GITHUB_STEP_SUMMARY': ''}), \
                        patch.object(Path, 'read_text', return_value=json.dumps(snapshot())), \
                        patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')) as http:
                    schedule.main()
                    http.assert_not_called()
                self.assertEqual(output.read_text(), f'skip={skip}\n')

    def test_recovery_skips_all_data_processing_but_retries_pages(self):
        outputs = {('calendar', 'skip'): 'false', ('cooldown', 'skip'): 'false',
                   ('freshness', 'skip'): 'false', ('scheduled', 'skip'): 'true'}
        for step in ['20% Setup Python', GENERATOR, FRESHNESS] + DOWNSTREAM:
            self.assertFalse(allowed(step, outputs))
        self.assertTrue(allowed('98% Verify GitHub Pages publication', outputs))
        self.assertFalse(allowed('98% Verify GitHub Pages publication',
                                 {**outputs, ('calendar', 'skip'): 'true'}))

    def test_actual_scheduled_freshness_rejects_yesterday_and_future(self):
        env = {'GITHUB_EVENT_NAME': 'schedule', 'EXPECTED_TRADING_DATE': '2026-10-08'}
        with patch.dict(os.environ, env):
            for day in ('2026-10-07', '2026-10-09', ''):
                with self.assertRaisesRegex(SystemExit, 'SCHEDULE FRESHNESS FAILED'):
                    run_inline(FRESHNESS, snapshot('2026-10-07'), snapshot(day))
            self.assertEqual(run_inline(FRESHNESS, snapshot('2026-10-07'), snapshot())['skip'], 'false')
        for expected in ('', 'bad-date'):
            with self.assertRaises(SystemExit):
                schedule.require_scheduled_date('2026-10-08', expected, 'schedule')
        schedule.require_scheduled_date('2026-10-08', '', 'workflow_dispatch')

    def test_release_tests_before_publishing_and_shares_lock(self):
        release = (ROOT / '.github/workflows/publish-release.yml').read_text()
        self.assertIn('branches: [main]', release)
        self.assertIn('group: open-sesame-data-update', release)
        self.assertLess(release.index('python -m unittest'), release.index('python scripts/publish_pages.py'))
        self.assertIn('pages: write', release)
        self.assertIn('pages: write', WORKFLOW)


class PublishPagesTest(unittest.TestCase):
    repo = 'arischuang1688-sudo/open-sesame'
    sha = 'a'*40

    def fake_api(self, built=False, advanced=False, mode='legacy'):
        posted = []
        def response(repo, path, method='GET', allow_missing=False):
            self.assertEqual(repo, self.repo)
            if path == 'pages':
                return {'build_type': mode, 'source': {'branch': 'main', 'path': '/'},
                        'html_url': 'https://arischuang1688-sudo.github.io/open-sesame/'}
            if path == 'git/ref/heads/main':
                return {'object': {'sha': 'b'*40 if advanced else self.sha}}
            if path == 'pages/builds' and method == 'POST':
                posted.append(path); return {'status': 'queued'}
            if path == 'pages/builds/latest':
                return {'commit': self.sha if built or posted else 'old', 'status': 'built'}
            raise AssertionError((path, method))
        return response, posted

    def test_missing_build_requested_and_public_json_verified(self):
        api, posted = self.fake_api()
        with patch.object(pages, 'api', side_effect=api), \
                patch.object(pages, 'public_text', return_value=json.dumps(snapshot())):
            pages.publish(self.repo, self.sha, snapshot(), attempts=1)
        self.assertEqual(posted, ['pages/builds'])

    def test_existing_build_reused_without_post(self):
        api, posted = self.fake_api(built=True)
        with patch.object(pages, 'api', side_effect=api), \
                patch.object(pages, 'public_text', return_value=json.dumps(snapshot())):
            pages.publish(self.repo, self.sha, snapshot(), attempts=1)
        self.assertEqual(posted, [])

    def test_stale_public_site_must_not_report_success(self):
        api, _ = self.fake_api(built=True)
        with patch.object(pages, 'api', side_effect=api), \
                patch.object(pages, 'public_text', return_value=json.dumps(snapshot('2026-10-07'))):
            with self.assertRaisesRegex(RuntimeError, 'did not match'):
                pages.publish(self.repo, self.sha, snapshot(), attempts=1)

    def test_unvalidated_commit_rejected_before_any_api_request(self):
        with patch.object(pages, 'api') as api:
            with self.assertRaisesRegex(RuntimeError, 'lacks same-day 200-stock'):
                pages.publish(self.repo, self.sha, {})
            api.assert_not_called()

    def test_does_not_change_site_settings_or_publish_old_checkout(self):
        for kwargs in ({'advanced': True}, {'mode': 'workflow'}):
            api, posted = self.fake_api(**kwargs)
            with patch.object(pages, 'api', side_effect=api):
                with self.assertRaises(RuntimeError):
                    pages.publish(self.repo, self.sha, snapshot(), attempts=1)
            self.assertEqual(posted, [])

    def test_public_request_never_contains_token(self):
        response = io.BytesIO(b'{}')
        with patch.object(pages, 'urlopen', return_value=response) as http, \
                patch.dict(os.environ, {'GH_TOKEN': 'test-secret'}):
            self.assertEqual(pages.public_text('https://example.test/'), '{}')
        self.assertNotIn('Authorization', dict(http.call_args.args[0].header_items()))

    def test_main_reads_committed_head_not_rejected_working_tree(self):
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': self.repo}), \
                patch.object(pages, 'git', side_effect=[self.sha, json.dumps(snapshot())]) as git, \
                patch.object(pages, 'publish') as publish:
            pages.main()
        self.assertEqual(git.call_args_list[-1].args, ('show', 'HEAD:data/dashboard.json'))
        publish.assert_called_once_with(self.repo, self.sha, snapshot())


if __name__ == '__main__':
    unittest.main()

