"""Offline regressions for official P/E data, ranking and the 200-stock gate."""
import ast
import copy
from pathlib import Path
import sys
import textwrap
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import valuation
import test_quote_validation as quotes


DATE = quotes.DATE


def stock(code='2330', pe=18.5, **overrides):
    item = dict(code=code, priority=False, cond_count=3, amount=100,
                volume=100, change_pct=0, quote_date=DATE,
                **valuation.valuation_fields(code, pe, DATE))
    item.update(overrides)
    # Ranking derives its evidence from the conditions, not cached counts.
    count = 4 if item['priority'] else item['cond_count']
    item.setdefault('conditions', dict(zip(valuation.CONDITION_KEYS,
                                          [i < count for i in range(4)])))
    return item


def snapshot():
    return dict(
        stocks=[stock(str(1000 + i)) for i in range(200)],
        source_dates=dict(pe_ratio=DATE),
        validation=dict(status='passed', trading_date=DATE,
                        stock_quotes_checked=200, pe_ratios_checked=200),
    )


class PEFieldsTest(unittest.TestCase):
    def test_positive_pe_preserves_the_threshold_without_rounding(self):
        for raw, expected in [(18.5, 18.5), ('18.50', 18.5),
                              (' 20.00 ', 20.0), (20, 20.0),
                              ('20.01', 20.01), ('20.001', 20.001),
                              (0.01, 0.01), (1000, 1000.0)]:
            with self.subTest(raw=raw):
                self.assertEqual(valuation.positive_pe(raw), expected)

    def test_missing_zero_negative_boolean_and_nonfinite_are_unavailable(self):
        for raw in (None, '', ' ', '--', '-', 'N/A', 'NaN', 'Infinity',
                    '-Infinity', float('nan'), float('inf'), float('-inf'),
                    0, '0.00', -0.0, -1, '-18.5', True, False, [], {}):
            with self.subTest(raw=raw):
                self.assertIsNone(valuation.positive_pe(raw))
                fields = valuation.valuation_fields('2330', raw, DATE)
                self.assertEqual(fields, dict(pe_ratio=None,
                                              pe_under_20=False, pe_date=DATE))

    def test_twenty_is_inclusive_but_twenty_point_zero_one_is_not(self):
        for raw, qualifies in [(0.01, True), (19.99, True),
                                (20, True), (20.01, False), (20.001, False)]:
            with self.subTest(raw=raw):
                fields = valuation.valuation_fields('2330', raw, DATE)
                self.assertEqual(fields['pe_ratio'], raw)
                self.assertIs(fields['pe_under_20'], qualifies)
                self.assertEqual(fields['pe_date'], DATE)

    def test_etfs_and_non_stock_codes_cannot_get_a_pe_bonus(self):
        for code in ('1000', '2330', '9999'):
            self.assertEqual(valuation.stock_pe(code, '18.50'), 18.5)
        for code in ('0050', '0056', '006208', '2330A', '23300',
                     '0999', 'ABC', '', None, True):
            with self.subTest(code=code):
                self.assertIsNone(valuation.stock_pe(code, '18.50'))
                self.assertEqual(valuation.valuation_fields(code, 18.5, DATE),
                                 dict(pe_ratio=None, pe_under_20=False, pe_date=DATE))

    def test_matches_requires_canonical_values_and_the_official_value(self):
        item = stock()
        self.assertTrue(valuation.valuation_matches(item, '18.50', DATE))
        for pe in (None, '18.5', True, 0, -1, 18.51,
                   float('nan'), float('inf')):
            with self.subTest(pe=pe):
                self.assertFalse(valuation.valuation_matches(
                    dict(item, pe_ratio=pe), '18.50', DATE))
        for flag in (False, 1, 0, 'true', None):
            with self.subTest(flag=flag):
                self.assertFalse(valuation.valuation_matches(
                    dict(item, pe_under_20=flag), '18.50', DATE))
        for raw in ('18.51', None, '--', '0', '-1', 'NaN'):
            with self.subTest(raw=raw):
                self.assertFalse(valuation.valuation_matches(item, raw, DATE))

    def test_matches_requires_all_fields_and_the_same_date(self):
        for field in ('pe_ratio', 'pe_under_20', 'pe_date'):
            item = stock()
            del item[field]
            with self.subTest(missing=field):
                self.assertFalse(valuation.valuation_matches(item, '18.50', DATE))
        for date in (None, '', '2026-09-30', '20261001'):
            with self.subTest(date=date):
                self.assertFalse(valuation.valuation_matches(
                    stock(pe_date=date), '18.50', DATE))

    def test_unavailable_and_etf_pe_must_use_null_with_false_flag(self):
        for raw in (None, '', '--', 0, -1, float('nan'), float('inf')):
            with self.subTest(raw=raw):
                self.assertTrue(valuation.valuation_matches(stock(pe=None), raw, DATE))
                self.assertFalse(valuation.valuation_matches(
                    stock(pe=None, pe_under_20=True), raw, DATE))
                self.assertFalse(valuation.valuation_matches(stock(pe_ratio=0,
                    pe_under_20=False), raw, DATE))
        etf = stock('0050', pe=18.5)
        self.assertTrue(valuation.valuation_matches(etf, '18.50', DATE))
        self.assertFalse(valuation.valuation_matches(
            dict(etf, pe_ratio=18.5, pe_under_20=True), '18.50', DATE))


class GeneratorPEValidationTest(unittest.TestCase):
    def test_truncated_official_pe_aborts_instead_of_replacing_the_stock(self):
        response = copy.deepcopy(quotes.RECORDED)
        rows = response['tables'][0]['data']
        replacement = copy.deepcopy(rows[0])
        replacement[0] = '2330'
        rows[0].pop()  # The official 6834 row is missing its final P/E column.
        rows.append(replacement)
        with patch.object(quotes.generator, '_latest_market_date',
                          return_value='2026-10-02'), \
                patch.object(quotes.generator.u, 'fetch_json',
                             return_value=response) as request:
            with self.assertRaisesRegex(SystemExit, '6834:pe-field-incomplete'):
                quotes.generator.fetch_base_stocks_strict()
        request.assert_called_once_with(quotes.RECORDED['source'], timeout=45, retries=3)


class PERankingTest(unittest.TestCase):
    def codes(self, items):
        return [item['code'] for item in valuation.rank_stocks(items)]

    def test_low_pe_does_not_override_priority_or_stronger_signals(self):
        items = [stock('1003', pe=1, cond_count=2, amount=10000),
                 stock('1002', pe=40, cond_count=3, amount=1),
                 stock('1001', pe=40, priority=True, cond_count=1, amount=1)]
        before = copy.deepcopy(items)
        self.assertEqual(self.codes(items), ['1001', '1002', '1003'])
        self.assertEqual(items, before)
        self.assertIs(items[0]['priority'], False)

    def test_equal_signals_prioritize_eligible_pe_before_amount(self):
        items = [stock('1003', pe=None, amount=10000),
                 stock('1002', pe=20.01, amount=1000),
                 stock('1001', pe=20, amount=1)]
        self.assertEqual(self.codes(items), ['1001', '1003', '1002'])

    def test_forged_true_flags_do_not_qualify_invalid_or_etf_pe(self):
        valid = stock('2330', pe=20, amount=1)
        cases = [stock('1000', pe_ratio=pe, pe_under_20=True, amount=10000)
                 for pe in (None, 0, -1, 20.01, True,
                            float('nan'), float('inf'))]
        cases.append(stock('0050', pe_ratio=18.5, pe_under_20=True, amount=10000))
        cases.extend(stock('1000', pe=18.5, pe_under_20=flag, amount=10000)
                     for flag in (False, 'true', 1, None))
        missing_flag = stock('1000', pe=18.5, amount=10000)
        del missing_flag['pe_under_20']
        cases.append(missing_flag)
        for invalid in cases:
            with self.subTest(invalid=invalid):
                self.assertEqual(self.codes([invalid, valid]),
                                 ['2330', invalid['code']])

    def test_amount_outranks_percentage_change(self):
        items = [stock('3167', amount=85.73e8, change_pct=9.92, volume=99999),
                 stock('2327', amount=436.06e8, change_pct=9.85, volume=1)]
        self.assertEqual(self.codes(items), ['2327', '3167'])

    def test_volume_then_code_break_ties_independent_of_input_order(self):
        items = [stock('1002', amount=100, volume=100),
                 stock('1001', amount=100, volume=100),
                 stock('1003', amount=100, volume=200)]
        self.assertEqual(self.codes(items), ['1003', '1001', '1002'])
        self.assertEqual(self.codes(list(reversed(items))), ['1003', '1001', '1002'])

    def test_sort_returns_a_new_list_without_mutating_input_records(self):
        items = [stock('1002', amount=1), stock('1001', amount=100)]
        before = copy.deepcopy(items)
        ranked = valuation.rank_stocks(items)
        self.assertIsInstance(ranked, list)
        self.assertIsNot(ranked, items)
        self.assertEqual(items, before)
        self.assertEqual([item['code'] for item in ranked], ['1001', '1002'])


class CompleteValuationTest(unittest.TestCase):
    def test_complete_200_stock_snapshot_and_canonical_missing_values_pass(self):
        data = snapshot()
        before = copy.deepcopy(data)
        self.assertTrue(valuation.complete_valuation(data, DATE))
        self.assertEqual(data, before)
        data['stocks'][199] = stock('1199', pe=None)
        self.assertTrue(valuation.complete_valuation(data, DATE))
        data['stocks'][199] = stock('0050', pe=None)
        self.assertTrue(valuation.complete_valuation(data, DATE))

    def test_source_date_and_validation_count_are_required(self):
        for section, field, values in [
            ('source_dates', 'pe_ratio', (None, '', '2026-09-30')),
            ('validation', 'pe_ratios_checked', (None, 0, 199, 201, '200')),
        ]:
            for value in values:
                with self.subTest(section=section, field=field, value=value):
                    data = snapshot()
                    data[section][field] = value
                    self.assertFalse(valuation.complete_valuation(data, DATE))
            data = snapshot()
            del data[section][field]
            self.assertFalse(valuation.complete_valuation(data, DATE))
            del data[section]
            self.assertFalse(valuation.complete_valuation(data, DATE))

    def test_exactly_200_stocks_are_required(self):
        for count in (0, 50, 199, 201):
            with self.subTest(count=count):
                data = snapshot()
                data['stocks'] = (data['stocks'] + [stock('1200')])[:count]
                self.assertFalse(valuation.complete_valuation(data, DATE))

    def test_last_stock_must_have_canonical_fields_and_matching_date(self):
        for field, values in [
            ('pe_ratio', ('18.5', True, 0, -1, float('nan'), float('inf'), None)),
            ('pe_under_20', (False, 'true', 1, None)),
            ('pe_date', ('2026-09-30', '', None)),
        ]:
            for value in values:
                with self.subTest(field=field, value=value):
                    data = snapshot()
                    data['stocks'][199][field] = value
                    self.assertFalse(valuation.complete_valuation(data, DATE))
            data = snapshot()
            del data['stocks'][199][field]
            self.assertFalse(valuation.complete_valuation(data, DATE))


class WorkflowPEValidationTest(unittest.TestCase):
    # Reuse the execution harness, without inheriting and rerunning its quote tests.
    validate = quotes.QuoteValidationTest.validate

    def test_legacy_force_update_uses_the_same_strict_validator_as_main(self):
        validators = []
        for filename, step in [
            ('main.yml', '80% Strict same-trading-day validation'),
            ('validated-now.yml', 'Strict validate latest trading day'),
        ]:
            workflow = ROOT / '.github' / 'workflows' / filename
            source = workflow.read_text(encoding='utf-8').split(
                '- name: ' + step, 1)[1].split("python - <<'PY'\n", 1)[1]
            source = textwrap.dedent(source.split('\n          PY', 1)[0])
            validators.append(ast.dump(ast.parse(source), include_attributes=False))
        self.assertEqual(validators[0], validators[1],
                         'Manual force-update and scheduled updates must validate '
                         'the same 200 quotes, P/E fields and source dates.')

    def test_pe_validation_covers_stock_51_and_stock_200(self):
        for index in (50, 199):
            for field, wrong in [('pe_ratio', 18.51),
                                 ('pe_under_20', False),
                                 ('pe_date', '2026-09-30')]:
                with self.subTest(position=index + 1, field=field):
                    dashboard, response = quotes.fixture()
                    dashboard['stocks'][index][field] = wrong
                    self.validate(dashboard, response, str(1000 + index) + ':pe-')

    def test_missing_and_noncanonical_dashboard_pe_fields_fail_closed(self):
        for field, values in [
            ('pe_ratio', ('18.5', True, None, 0, float('nan'), float('inf'))),
            ('pe_under_20', ('true', 1, None)),
        ]:
            for value in values:
                with self.subTest(field=field, value=value):
                    dashboard, response = quotes.fixture()
                    dashboard['stocks'][199][field] = value
                    self.validate(dashboard, response, '1199:pe-')
        for field in ('pe_ratio', 'pe_under_20', 'pe_date'):
            with self.subTest(missing=field):
                dashboard, response = quotes.fixture()
                del dashboard['stocks'][199][field]
                self.validate(dashboard, response, '1199:pe-')

    def test_official_unavailable_pe_is_accepted_only_as_null(self):
        for raw in ('--', '', None, '0.00', '-1.00', 'NaN', 'Infinity'):
            with self.subTest(raw=raw):
                dashboard, response = quotes.fixture()
                response['tables'][0]['data'][199][-1] = raw
                self.validate(dashboard, response, '1199:pe-')
                dashboard['stocks'][199].update(
                    valuation.valuation_fields('1199', raw, DATE))
                self.validate(dashboard, response)

    def test_official_pe_boundary_is_verified_without_rounding(self):
        for raw, qualifies in [('20.00', True), ('20.01', False)]:
            with self.subTest(raw=raw):
                dashboard, response = quotes.fixture()
                response['tables'][0]['data'][199][-1] = raw
                dashboard['stocks'][199].update(
                    valuation.valuation_fields('1199', raw, DATE))
                self.assertIs(dashboard['stocks'][199]['pe_under_20'], qualifies)
                self.validate(dashboard, response)

    def test_wrong_or_missing_official_response_date_cannot_pass(self):
        for date in ('20260930', '', None):
            with self.subTest(date=date):
                dashboard, response = quotes.fixture()
                response['date'] = date
                self.validate(dashboard, response, 'MI_INDEX date')


if __name__ == '__main__':
    unittest.main()
