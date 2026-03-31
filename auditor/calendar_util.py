"""Resolve which GTFS service_ids run on a calendar date."""

from __future__ import annotations

from datetime import date

import pandas as pd


def _weekday_column(d: date) -> str:
    return ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"][
        d.weekday()
    ]


def services_running_on_date(calendar: pd.DataFrame, calendar_dates: pd.DataFrame, d: date) -> set[str]:
    """
    GTFS calendar + calendar_dates rules.
    Monday in Python date.weekday() is 0 (matches GTFS monday column order).
    """
    ymd = d.strftime("%Y%m%d")
    day_col = _weekday_column(d)
    services: set[str] = set()

    if not calendar.empty:
        cal = calendar.copy()
        cal["start_date"] = cal["start_date"].astype(str).str.zfill(8)
        cal["end_date"] = cal["end_date"].astype(str).str.zfill(8)
        mask = (cal["start_date"] <= ymd) & (cal["end_date"] >= ymd) & (cal[day_col].astype(int) == 1)
        for sid in cal.loc[mask, "service_id"].astype(str):
            services.add(sid)

    if not calendar_dates.empty:
        cd = calendar_dates.copy()
        cd["date"] = cd["date"].astype(str).str.zfill(8)
        sub = cd[cd["date"] == ymd]
        for _, row in sub.iterrows():
            sid = str(row["service_id"])
            et = int(row["exception_type"])
            if et == 1:
                services.add(sid)
            elif et == 2:
                services.discard(sid)

    return services
