"""One inclusive calendar period for QC displays, exports and archives."""
from __future__ import annotations

from calendar import monthrange
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
import re

import pandas as pd


def _date_text(value, label):
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or '').strip()
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
        raise ValueError(f'{label}请填写完整日期。')
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise ValueError(f'{label}不是有效日期。') from None


@dataclass(frozen=True)
class ReportPeriod:
    start_date: str
    end_date: str
    mode: str = 'custom'
    report_month: str = ''

    def __post_init__(self):
        start, end = _date_text(self.start_date, '开始日期'), _date_text(self.end_date, '结束日期')
        if start > end:
            raise ValueError('开始日期不能晚于结束日期。')
        if self.mode not in ('custom', 'month'):
            raise ValueError('请选择按月或自选日期。')
        try:
            date.fromisoformat(end) + timedelta(days=1)
        except OverflowError:
            raise ValueError('结束日期超出可用范围。') from None
        if self.mode == 'month':
            month = str(self.report_month or '')
            if not re.fullmatch(r'\d{4}-\d{2}', month):
                raise ValueError('请选择完整月份。')
            try:
                year, number = map(int, month.split('-'))
                expected_start = date(year, number, 1).isoformat()
                expected_end = date(year, number, monthrange(year, number)[1]).isoformat()
            except (ValueError, OverflowError):
                raise ValueError('所选月份无效。') from None
            if (start, end) != (expected_start, expected_end):
                raise ValueError('月份与所选起止日期不一致，请重新核对。')
        elif self.report_month:
            raise ValueError('自选日期不使用月份标识，请重新核对。')
        object.__setattr__(self, 'start_date', start)
        object.__setattr__(self, 'end_date', end)

    @property
    def start_time(self):
        return datetime.combine(date.fromisoformat(self.start_date), datetime.min.time())

    @property
    def end_exclusive(self):
        return datetime.combine(date.fromisoformat(self.end_date) + timedelta(days=1), datetime.min.time())

    @property
    def key(self):
        return self.report_month if self.mode == 'month' else f'{self.start_date}_{self.end_date}'

    @property
    def label(self):
        return f'{self.start_date} 至 {self.end_date}'

    def to_payload(self):
        return {**asdict(self), 'period_key': self.key, 'period_label': self.label}


def resolve_report_period(report_month=None, *, start_date=None, end_date=None):
    if report_month:
        month = str(report_month).strip()
        if not re.fullmatch(r'\d{4}-\d{2}', month):
            raise ValueError('请选择完整月份。')
        try:
            year, number = map(int, month.split('-'))
            start = date(year, number, 1).isoformat()
            end = date(year, number, monthrange(year, number)[1]).isoformat()
        except (ValueError, OverflowError):
            raise ValueError('所选月份无效。') from None
        if start_date is not None and _date_text(start_date, '开始日期') != start:
            raise ValueError('月份与开始日期不一致。')
        if end_date is not None and _date_text(end_date, '结束日期') != end:
            raise ValueError('月份与结束日期不一致。')
        return ReportPeriod(start, end, mode='month', report_month=month)
    if start_date is None or end_date is None:
        raise ValueError('请同时选择开始日期和结束日期。')
    return ReportPeriod(start_date, end_date)


def filter_frame_to_period(frame, period, time_column='test_time'):
    if not isinstance(period, ReportPeriod):
        raise ValueError('请重新核对所选日期范围。')
    if frame.empty:
        return frame.copy()
    if time_column not in frame:
        raise ValueError('检测资料缺少检测时间，请核对后重试。')
    times = pd.to_datetime(frame[time_column], errors='coerce', format='mixed')
    return frame.loc[(times >= period.start_time) & (times < period.end_exclusive)].copy()
