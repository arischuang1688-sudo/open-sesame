"""Offline regression tests of the actual inline workflow validator (stdlib only)."""
import contextlib
import io
import json
from pathlib import Path
import sys
import textwrap
import unittest
from unittest.mock import Mock, mock_open, patch


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/main.yml'
sys.path.insert(0, str(WORKFLOW.parents[2] / 'scripts'))
import update_data_strict as generator

RECORDED = json.loads((Path(__file__).parent / 'fixtures/mi_index_20261002_6834.json')
                      .read_text(encoding='utf-8'))
STEP = WORKFLOW.read_text(encoding='utf-8').split(
    '- name: 80% Strict same-trading-day validation', 1)[1]
VALIDATOR = compile(textwrap.dedent(STEP.split("python - <<'PY'\n", 1)[1]
                                  .split('\n          PY', 1)[0]), str(WORKFLOW), 'exec')
DATE = '2026-10-01'
FIELDS = ['證券代號', '證券名稱', '收盤價', '成交股數', '漲跌(+/-)', '漲跌價差']


def fixture():
    stocks, rows = [], []
    for i in range(200):
        change = (-1.5, 0, 1.5)[i % 3]
        sign = '-' if change < 0 else '+' if change > 0 else ' '
        code = str(1000 + i)
        stocks.append(dict(code=code, close=100.25, volume=1234.57, change=change))
        rows.append([code, '測試', '100.25', '1,234,567',
                     f'<p style="color:red">{sign}</p>', str(abs(change))])
    return (dict(stocks=stocks, trust_date=DATE, updated_at=DATE,
                 market=dict(quote=dict(date=DATE), margin=dict(history=[dict(date=DATE)]))),
            dict(stat='OK', date='20261001', tables=[dict(fields=FIELDS[:], data=rows)]))


class QuoteValidationTest(unittest.TestCase):
    def validate(self, dashboard, response, error=None):
        date = dashboard['market']['quote']['date']
        file = mock_open(read_data=json.dumps(dashboard))
        output = io.StringIO()
        with patch('builtins.open', file), contextlib.redirect_stdout(output), \
                patch('urllib.request.urlopen', return_value=Mock(
                    read=Mock(return_value=json.dumps(response).encode()))) as request:
            if error:
                with self.assertRaisesRegex(SystemExit, error) as caught:
                    exec(VALIDATOR, {})
                self.assertNotEqual(caught.exception.code, 0)
                file().write.assert_not_called()
            else:
                exec(VALIDATOR, {})
            request.assert_called_once()
            self.assertEqual(request.call_args.args[0].full_url,
                             'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX'
                             f'?date={date.replace("-", "")}&type=ALLBUT0999&response=json')
        if not error:
            saved = json.loads(''.join(c.args[0] for c in file().write.call_args_list))
            self.assertEqual(saved['validation']['stock_quotes_checked'], 200)
            self.assertEqual(saved['stocks'], dashboard['stocks'])
            self.assertIn(f'[validate] PASSED {date} 200', output.getvalue())

    def test_all_200_with_up_down_flat_and_rounded_volume(self):
        self.validate(*fixture())

    def test_mismatch_after_first_50_and_at_last_stock(self):
        for index in (50, 199):
            for field, value, error in [('close', 100.26, ':close'),
                                        ('volume', 1234.58, ':volume'),
                                        ('change', -1.5, ':change-direction')]:
                with self.subTest(position=index + 1, field=field):
                    d, r = fixture()
                    d['stocks'][index][field] = value
                    self.validate(d, r, str(1000 + index) + error)

    def test_missing_source_row_or_incomplete_row(self):
        for index in (50, 199):
            for incomplete in (False, True):
                with self.subTest(position=index + 1, incomplete=incomplete):
                    d, r = fixture()
                    if incomplete:
                        r['tables'][0]['data'][index].pop()
                    else:
                        r['tables'][0]['data'].pop(index)
                    self.validate(d, r, str(1000 + index) + ':(missing|incomplete)')

    def test_missing_invalid_or_nonfinite_numeric_fields(self):
        for field, column in [('close', 2), ('volume', 3), ('change', 5)]:
            for source in (False, True):
                for value in (None, '', '--', 'NaN', 'Infinity', '-Infinity'):
                    with self.subTest(field=field, source=source, value=value):
                        d, r = fixture()
                        if source:
                            r['tables'][0]['data'][199][column] = value
                        else:
                            d['stocks'][199][field] = value
                        self.validate(d, r, '1199:')
            d, r = fixture()
            del d['stocks'][199][field]
            self.validate(d, r, '1199:')

    def test_missing_or_unknown_direction_is_not_flat(self):
        for sign, magnitude in [(None, '0'), ('X', '1.5'), ('?', '0'), ('', '1.5')]:
            with self.subTest(sign=sign, magnitude=magnitude):
                d, r = fixture()
                r['tables'][0]['data'][199][4:] = [sign, magnitude]
                self.validate(d, r, '1199:change-direction')

    def test_missing_or_duplicate_dashboard_stock_fails(self):
        for count in (0, 30, 50, 199, 201):
            with self.subTest(count=count):
                d, r = fixture()
                d['stocks'] = (d['stocks'] + d['stocks'][:1])[:count]
                self.validate(d, r, 'expected 200 stock quotes')
        d, r = fixture()
        d['stocks'][199] = d['stocks'][0].copy()
        self.validate(d, r, '1000:missing-or-duplicate-code')

    def test_core_dates_still_fail_closed(self):
        for source in ('institutional', 'margin'):
            for date in (None, '2026-09-30'):
                with self.subTest(source=source, date=date):
                    d, r = fixture()
                    if source == 'institutional':
                        d['trust_date'] = date
                    else:
                        d['market']['margin']['history'][-1]['date'] = date
                    self.validate(d, r, 'core sources are not the same latest trading day')

    def test_recorded_6834_non_comparison_and_real_mismatches(self):
        with patch.object(generator, '_latest_market_date', return_value='2026-10-02'), \
                patch.object(generator.u, 'fetch_json', return_value=RECORDED) as request:
            stocks = generator.fetch_base_stocks_strict()
        request.assert_called_once_with(RECORDED['source'], timeout=45, retries=3)
        self.assertEqual(len(stocks), 1)
        self.assertEqual({k: stocks[0][k] for k in ('code', 'close', 'volume', 'change', 'change_pct')},
                         dict(code='6834', close=131.0, volume=12078.27, change=0, change_pct=0))
        for field, wrong in [(None, None), ('close', 130), ('volume', 12078), ('change', 1)]:
            with self.subTest(field=field):
                d, r = fixture()
                d['stocks'][199] = stocks[0].copy()
                d['trust_date'] = d['market']['quote']['date'] = '2026-10-02'
                d['market']['margin']['history'][-1]['date'] = '2026-10-02'
                r['date'] = RECORDED['date']
                recorded = dict(zip(RECORDED['tables'][0]['fields'], RECORDED['tables'][0]['data'][0]))
                r['tables'][0]['data'][199] = [recorded[f] for f in FIELDS]
                if field:
                    d['stocks'][199][field] = wrong
                self.validate(d, r, '6834:' + ('change-direction' if field == 'change' else field)
                              if field else None)

    def test_generator_parser_rejects_unknown_and_invalid_values(self):
        cases = [(None, '0'), ('?', '0'), ('X', '1'), ('', '1'), ('+-', '1')]
        cases += [(sign, value) for sign in ('+', '-', '', 'X')
                  for value in (None, '', '--', 'NaN', 'Infinity', '-Infinity')]
        for sign, value in cases:
            with self.subTest(sign=sign, value=value):
                self.assertIsNone(generator._signed_change(sign, value))

    def test_html_attributes_are_not_direction_symbols(self):
        for sign, expected in [('+', 1.5), ('-', -1.5)]:
            markup = f'<p data-label="+/-" style="font-size:12px">{sign}</p>'
            self.assertEqual(generator._signed_change(markup, '1.50'), expected)

    def test_generator_invalid_direction_aborts_instead_of_dropping_stock(self):
        for sign, value in [('<p>?</p>', '0.00'), ('<p>X</p>', '1.00')]:
            response = json.loads(json.dumps(RECORDED))
            response['tables'][0]['data'][0][9:11] = [sign, value]
            with patch.object(generator, '_latest_market_date', return_value='2026-10-02'), \
                    patch.object(generator.u, 'fetch_json', return_value=response):
                with self.assertRaisesRegex(SystemExit, '6834:change-direction'):
                    generator.fetch_base_stocks_strict()


if __name__ == '__main__':
    unittest.main()
