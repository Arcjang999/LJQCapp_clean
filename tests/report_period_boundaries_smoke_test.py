"""Shared calendar scope includes complete days without changing source rows."""
from datetime import date, datetime
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
from services.report_period_service import ReportPeriod, resolve_report_period, filter_frame_to_period


class ReportPeriodBoundaryTests(unittest.TestCase):
    def test_month_shortcut_and_custom_period_have_identical_boundaries(self):
        for month, last in [('2024-02', '2024-02-29'), ('2025-02', '2025-02-28'), ('2026-12', '2026-12-31')]:
            with self.subTest(month=month):
                period = resolve_report_period(month)
                custom = resolve_report_period(start_date=month + '-01', end_date=last)
                self.assertEqual((period.start_time, period.end_exclusive), (custom.start_time, custom.end_exclusive))
                self.assertEqual(month, period.key)
                self.assertEqual(last, period.end_date)
                self.assertEqual(period.label, custom.label)

    def test_fractional_last_second_is_included_and_next_midnight_is_excluded(self):
        frame = pd.DataFrame({'test_time': ['2026-10-11 23:59:59.999999', '2026-10-12 00:00:00',
            '2026-10-12 23:59:59', '2026-10-12 23:59:59.999999', '2026-10-13 00:00:00'], 'id': [1, 2, 3, 4, 5]})
        period = resolve_report_period(start_date=date(2026, 10, 12), end_date=date(2026, 10, 12))
        self.assertEqual([2, 3, 4], filter_frame_to_period(frame, period)['id'].tolist())
        self.assertEqual(datetime(2026, 10, 13), period.end_exclusive)

    def test_thirty_one_days_and_sparse_year_do_not_truncate_selected_records(self):
        frame = pd.DataFrame({'test_time': pd.date_range('2025-12-31', '2027-01-01', freq='D')})
        month = resolve_report_period(start_date='2026-01-01', end_date='2026-01-31')
        year = resolve_report_period(start_date='2026-01-01', end_date='2026-12-31')
        self.assertEqual(31, len(filter_frame_to_period(frame, month)))
        self.assertEqual(365, len(filter_frame_to_period(frame, year)))
        self.assertEqual([1, 70], filter_frame_to_period(frame.iloc[[0, 1, 70, 366]], year).index.tolist())

    def test_cross_year_preserves_source_order_index_and_values(self):
        frame = pd.DataFrame({'detected_at': ['2027-01-01 12:00', '2026-12-31 12:00', '2026-12-30 12:00'],
                              'id': [10, 11, 12]}, index=[70, 71, 72])
        original = frame.copy(deep=True)
        period = resolve_report_period(start_date='2026-12-31', end_date='2027-01-01')
        result = filter_frame_to_period(frame, period, time_column='detected_at')
        self.assertEqual([10, 11], result['id'].tolist())
        self.assertEqual([70, 71], result.index.tolist())
        pd.testing.assert_frame_equal(frame, original)
        self.assertEqual('2026-12-31_2027-01-01', period.to_payload()['period_key'])

    def test_invalid_or_contradictory_period_is_rejected(self):
        cases = [dict(start_date='2026-10-12', end_date='2026-10-11'),
                 dict(start_date='2026-02-30', end_date='2026-03-01'), dict(start_date='2026-10-12'),
                 dict(report_month='2026-13'),
                 dict(report_month='2026-10', start_date='2026-10-02', end_date='2026-10-31'),
                 dict(start_date='2026/10/01', end_date='2026-10-31')]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                resolve_report_period(**values)
        with self.assertRaises(ValueError):
            ReportPeriod('2026-10-01', '2026-10-31', report_month='2026-10')


if __name__ == '__main__':
    unittest.main(verbosity=2)
