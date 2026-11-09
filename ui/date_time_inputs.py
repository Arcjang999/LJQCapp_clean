"""Minute-precision controls shared by editable laboratory date/time fields."""
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from services.time_service import LAB_TIMEZONE, local_now


def _datetime(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, str) and value == 'now':
        return local_now()
    try:
        stamp = pd.Timestamp(value)
        if pd.isna(stamp):
            return None
        if stamp.tzinfo is not None:
            stamp = stamp.tz_convert(LAB_TIMEZONE).tz_localize(None)
        return stamp.to_pydatetime(warn=False)
    except (ValueError, TypeError, OverflowError):
        return None


def minute_value(value):
    """Prepare a local, minute-only display value; keep empty fields empty."""
    value = _datetime(value)
    return value.replace(second=0, microsecond=0) if value is not None else None


def ceil_minute(value):
    """Suggest the first selectable minute at or after a precise lower bound."""
    value = _datetime(value)
    if value is None:
        return None
    minute = minute_value(value)
    return minute + timedelta(minutes=1) if minute < value else minute


def minute_datetime_input(label, *, value='now', key=None, preserve_original=None, **kwargs):
    """Display minutes, optionally retaining an unchanged record's exact time.

    Pass the record's original timestamp when editing another field must not
    alter its timestamp. Selecting a different minute returns that minute.
    Existing on_change callbacks continue to receive the selected display value.
    """
    if key is not None and key in st.session_state:
        current = st.session_state[key]
        display = minute_value(current)
        if current != display:
            st.session_state[key] = display
        value = None
    else:
        value = minute_value(value)
        display = value
    # With a session key and value=None Streamlit otherwise builds its default
    # range around today, clearing valid historical/future drafts on a rerun.
    anchors = [local_now(), *[item for item in
        (display, _datetime(kwargs.get('min_value')), _datetime(kwargs.get('max_value')))
        if item is not None]]
    if kwargs.get('min_value') is None:
        kwargs['min_value'] = datetime(max(1, min(item.year for item in anchors) - 10), 1, 1)
    if kwargs.get('max_value') is None:
        kwargs['max_value'] = datetime(min(9999, max(item.year for item in anchors) + 10), 12, 31, 23, 59)
    kwargs['step'] = 60
    kwargs['format'] = 'YYYY-MM-DD'
    chosen = st.datetime_input(label, value=value, key=key, **kwargs)
    original = _datetime(preserve_original)
    if original is not None and chosen == minute_value(original):
        return original
    return minute_value(chosen)
