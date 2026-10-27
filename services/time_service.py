"""Shared laboratory clock and validation of completed detection times."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd


LAB_TIMEZONE = ZoneInfo('Asia/Shanghai')
FUTURE_DETECTION_MESSAGE = '检测时间不能晚于当前时间，请核对日期和时间。'


def local_now():
    return datetime.now(LAB_TIMEZONE).replace(tzinfo=None)


def local_today():
    return local_now().date()


def _local_timestamp(value):
    try:
        stamp = pd.Timestamp(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError('请填写有效的检测时间。') from None
    if pd.isna(stamp):
        raise ValueError('请填写有效的检测时间。')
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert(LAB_TIMEZONE).tz_localize(None)
    return stamp


def validate_detection_time(value, *, now=None):
    stamp = _local_timestamp(value)
    current = _local_timestamp(local_now() if now is None else now)
    if stamp > current:
        raise ValueError(FUTURE_DETECTION_MESSAGE)
    return stamp.isoformat(sep=' ')


def detection_input_max(value=None):
    """Keep a prior invalid draft visible so it can be corrected in place."""
    today_end = pd.Timestamp(local_now().replace(hour=23, minute=59, second=59, microsecond=999999))
    if value is None:
        return today_end.to_pydatetime()
    try:
        prior = _local_timestamp(value)
    except ValueError:
        return today_end.to_pydatetime()
    return max(today_end, prior).to_pydatetime()
