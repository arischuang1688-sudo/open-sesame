"""Offline regression: unknown signals, comparable ranking and request budget."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import update_data as updater
from valuation import rank_stocks, signal_fields, valuation_fields


def stock(code='2330', conditions=None, **extra):
    return dict(code=code, name='測試股', amount=100, volume=100,
                conditions=conditions or dict(ma=True, kd=True, trust=True, market=True),
                **valuation_fields(code, 18, '2026-10-28'), **extra)


def history(count=30):
    return [dict(date=f'2026-09-{i+1:02d}', close=100, high=101, low=99,
                 volume=100) for i in range(count)]


class SignalsTest(unittest.TestCase):
    def test_unknown_is_not_false_or_a_priority_signal(self):
        for key in ('ma', 'kd', 'trust', 'market'):
            for value in (None, 'false', 0, 1):
                with self.subTest(key=key, value=value):
                    item = stock()
                    item['conditions'][key] = value
                    result = signal_fields(item)
                    self.assertIsNone(result['conditions'][key])
                    self.assertEqual(result['cond_known_count'], 3)
                    self.assertEqual(result['cond_count'], 3)
                    self.assertIsNone(result['priority'])
                    self.assertEqual(result['status'], '待評估')
                    self.assertEqual(result['ranking_group'], 'pending')

    def test_false_is_known_and_only_four_true_signals_are_priority(self):
        item = stock()
        self.assertIs(signal_fields(item)['priority'], True)
        item['conditions']['kd'] = False
        result = signal_fields(item)
        self.assertIs(result['conditions']['kd'], False)
        self.assertIs(result['priority'], False)
        self.assertTrue(result['signals_complete'])
        self.assertEqual(result['cond_known_count'], 4)
        self.assertEqual(result['status'], '觀察')

    def test_missing_conditions_and_legacy_uncomputed_labels_are_unknown(self):
        self.assertEqual(signal_fields({})['cond_known_count'], 0)
        for label in ('僅前30名計算', '尚未計算', '資料不足', '資料待補', '待計算'):
            item = stock(month_week=label, kd=label, relative=label,
                         priority=True, cond_count=4)
            ranked = rank_stocks([item])[0]
            self.assertIsNone(ranked['conditions']['ma'])
            self.assertIsNone(ranked['conditions']['kd'])
            self.assertIsNone(ranked['priority'])
            self.assertEqual(ranked['cond_known_count'], 2)
            self.assertNotIn('僅前', ranked['month_week'])

    def test_partial_counts_do_not_compete_and_pe_still_orders_pending(self):
        no_signals = dict(ma=False, kd=False, trust=False, market=False)
        some_signals = dict(ma=None, kd=True, trust=True, market=True)
        none_known = dict(ma=None, kd=None, trust=None, market=None)
        complete = stock('1000', no_signals)
        high_count = stock('1001', some_signals)
        high_count.update(valuation_fields('1001', 21, '2026-10-28'))
        low_pe = stock('1002', none_known)
        same_pe_more_amount = stock('1003', some_signals)
        same_pe_more_amount['amount'] = 1000
        ranked = rank_stocks([high_count, low_pe, same_pe_more_amount, complete])
        self.assertEqual([s['code'] for s in ranked], ['1000', '1003', '1002', '1001'])
        self.assertEqual([s['ranking_group'] for s in ranked],
                         ['complete', 'pending', 'pending', 'pending'])

    def test_normalization_is_idempotent_and_preserves_every_non_signal_field(self):
        items = [stock(month_week='僅前30名計算', kd='僅前30名計算',
                       relative='僅前30名計算', close=234.5, quote_date='2026-10-28',
                       margin_balance=1234, margin='1,234 張', trust_net=0)]
        before = copy.deepcopy(items)
        ranked = rank_stocks(items)
        self.assertEqual(items, before)
        self.assertEqual(rank_stocks(ranked), ranked)
        changed = {'conditions', 'month_week', 'kd', 'relative'}
        for key in before[0].keys() - changed:
            self.assertEqual(ranked[0][key], before[0][key], key)


class GeneratorTest(unittest.TestCase):
    def run_generator(self, stock_history, *, missing_sources=False):
        stocks = [dict(code=str(1000+i), name='測試股', volume=100)
                  for i in range(200)]
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(updater, 'OUT_PATH', str(Path(temp) / 'dashboard.json')), \
                patch.object(updater, 'fetch_base_stocks', return_value=stocks), \
                patch.object(updater, 'fetch_trust_net', return_value=(
                    None if missing_sources else {s['code']: 0 for s in stocks}, '2026-10-28')), \
                patch.object(updater, 'fetch_margin', return_value=(None, None)), \
                patch.object(updater, 'fetch_market_history', return_value=None), \
                patch.object(updater, 'analyze_market', return_value=None if missing_sources
                             else dict(ret20_pct=0, trend_up=False)), \
                patch.object(updater, 'fetch_stock_history', side_effect=stock_history) as fetch, \
                patch.object(updater, 'fetch_json', side_effect=AssertionError('network forbidden')):
            updater.main()
            result = json.loads(Path(updater.OUT_PATH).read_text())
            self.assertEqual(fetch.call_count, 30)
            self.assertEqual([call.args[0] for call in fetch.call_args_list],
                             [str(1000+i) for i in range(30)])
            return {s['code']: s for s in result['stocks']}

    def test_200_stock_pipeline_keeps_request_budget_and_170_unknown(self):
        result = self.run_generator(lambda code: history())
        self.assertEqual(len(result), 200)
        self.assertEqual(sum(s['signals_complete'] for s in result.values()), 30)
        for code, item in result.items():
            self.assertIs(item['conditions']['trust'], False)  # zero is known
            self.assertIs(item['conditions']['market'], False)
            if int(code) < 1030:
                self.assertIs(item['conditions']['ma'], False)
                self.assertIs(item['conditions']['kd'], False)
                self.assertEqual(item['status'], '觀察')
            else:
                self.assertIsNone(item['conditions']['ma'])
                self.assertIsNone(item['conditions']['kd'])
                self.assertIsNone(item['priority'])
                self.assertEqual(item['status'], '待評估')
                self.assertEqual(item['month_week'], '尚未計算')

    def test_failed_and_short_history_and_missing_sources_do_not_become_false(self):
        result = self.run_generator(lambda code: [] if code == '1000' else history(6),
                                    missing_sources=True)
        for item in result.values():
            self.assertEqual(set(item['conditions'].values()), {None})
            self.assertEqual(item['cond_known_count'], 0)
            self.assertEqual(item['cond_count'], 0)
            self.assertIsNone(item['priority'])
        self.assertEqual(result['1000']['kd'], '資料待補')
        self.assertEqual(result['1001']['kd'], '資料不足')

    def test_three_month_fetch_uses_three_logical_requests(self):
        with patch.object(updater, 'fetch_json', return_value=None) as fetch, \
                patch.object(updater.time, 'sleep'):
            self.assertEqual(updater.fetch_stock_history('2330'), [])
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(updater.HISTORY_TOP * fetch.call_count, 90)


if __name__ == '__main__':
    unittest.main()
