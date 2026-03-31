"""Persist audit inputs in the page URL so refresh / bookmark keeps the same query."""

from __future__ import annotations

from datetime import date

import streamlit as st


def _qp_first(val: str | list[str] | None) -> str | None:
    if val is None:
        return None
    if isinstance(val, list):
        return val[0] if val else None
    return val


def init_audit_widget_defaults() -> None:
    """Seed session_state keys used by form widgets (before URL hydrate)."""
    if "audit_service_date" not in st.session_state:
        st.session_state.audit_service_date = date.today()
    if "audit_route" not in st.session_state:
        st.session_state.audit_route = ""
    if "audit_headsign" not in st.session_state:
        st.session_state.audit_headsign = ""
    if "audit_departure_time" not in st.session_state:
        st.session_state.audit_departure_time = ""
    if "audit_direction" not in st.session_state:
        st.session_state.audit_direction = ("Outbound (GTFS 0)", 0)


def hydrate_from_url_once() -> None:
    """
    On first run after navigation, if ?route=&date=&dep= are present, copy into
    session_state and request a one-shot auto-run (no button click).
    """
    if st.session_state.get("_audit_url_hydrated"):
        return
    st.session_state["_audit_url_hydrated"] = True

    qp = st.query_params
    route = _qp_first(qp.get("route"))
    dep = _qp_first(qp.get("dep"))
    dstr = _qp_first(qp.get("date"))
    if not route or not dep or not dstr:
        return
    try:
        st.session_state["audit_service_date"] = date.fromisoformat(str(dstr))
    except ValueError:
        return
    st.session_state["audit_route"] = str(route)
    st.session_state["audit_departure_time"] = str(dep)
    hs = _qp_first(qp.get("hs"))
    st.session_state["audit_headsign"] = str(hs or "")
    st.session_state["audit_direction"] = (
        ("Outbound (GTFS 0)", 0) if str(qp.get("dir", "0")) == "0" else ("Inbound (GTFS 1)", 1)
    )
    st.session_state["_audit_restore"] = True


def sync_audit_to_url(
    route: str,
    service_date: date,
    direction_id: int,
    headsign: str,
    dep_normalized: str,
) -> None:
    """Write current audit to query string so refresh restores fields and can auto-run."""
    st.query_params["route"] = route.strip()
    st.query_params["date"] = service_date.isoformat()
    st.query_params["dir"] = str(int(direction_id))
    st.query_params["dep"] = dep_normalized
    st.query_params["hs"] = headsign.strip()
