"""
KSOR Health Dashboards - Combined Streamlit Launcher
Version: 2026-08-17

Expected repo files
-------------------
combined_dashboard.py
rms_dashboard.py
rhp_dashboard.py
rma_dashboard.py   # preferred name
    OR
ksor_rma_dashboard.py

requirements.txt

RMA data can either be uploaded in the app or placed in:
data/ct_export.xlsx
data/ered_active.xlsx
data/ered_comprehensive.xlsx
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Iterable, Optional

import streamlit as st


# =============================================================================
# PAGE / APP CONFIGURATION
# =============================================================================

st.set_page_config(
    page_title="KSOR Health Dashboards",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)


PROGRAMS = {
    "RMA": "Refugee Medical Assistance",
    "RMS": "Refugee Medical Screening",
    "RHP": "Refugee Health Promotion",
}


# =============================================================================
# MODULE HELPERS
# =============================================================================

def _import_first(module_names: Iterable[str]) -> tuple[Optional[ModuleType], Optional[str]]:
    """Import the first available module name."""
    errors = []
    for module_name in module_names:
        try:
            return importlib.import_module(module_name), module_name
        except ModuleNotFoundError as exc:
            errors.append(f"{module_name}: {exc}")
    return None, " | ".join(errors)


def _find_callable(module: ModuleType, function_names: Iterable[str]):
    for name in function_names:
        fn = getattr(module, name, None)
        if callable(fn):
            return fn
    return None


def _render_standard_module(
    label: str,
    module_names: Iterable[str],
    function_names: Iterable[str],
) -> None:
    """Render RMS/RHP-style modules that manage their own input controls."""
    module, import_info = _import_first(module_names)

    if module is None:
        st.error(
            f"{label} dashboard module was not found. "
            f"Expected one of: {', '.join(module_names)}"
        )
        with st.expander("Technical detail"):
            st.code(import_info or "No import detail available.")
        return

    render_fn = _find_callable(module, function_names)
    if render_fn is None:
        st.error(
            f"{label} dashboard module loaded, but no compatible render function was found. "
            f"Expected one of: {', '.join(function_names)}"
        )
        return

    try:
        render_fn()
    except Exception as exc:
        st.error(f"{label} dashboard could not be rendered.")
        st.exception(exc)


# =============================================================================
# RMA INTEGRATION
# =============================================================================

def _render_rma() -> None:
    """
    RMA needs special handling because its reusable renderer requires three
    source files as arguments. The module itself contains the analytical logic.
    """
    module, import_info = _import_first(
        ["rma_dashboard", "ksor_rma_dashboard"]
    )

    if module is None:
        st.error(
            "RMA dashboard module was not found. Add either "
            "`rma_dashboard.py` (preferred) or `ksor_rma_dashboard.py` "
            "to the same repository folder as `combined_dashboard.py`."
        )
        with st.expander("Technical detail"):
            st.code(import_info or "No import detail available.")
        return

    render_fn = getattr(module, "render_rma_dashboard", None)
    resolve_source = getattr(module, "resolve_source", None)
    local_candidates = getattr(module, "LOCAL_FILE_CANDIDATES", None)

    required_api_missing = []
    if not callable(render_fn):
        required_api_missing.append("render_rma_dashboard")
    if not callable(resolve_source):
        required_api_missing.append("resolve_source")
    if not isinstance(local_candidates, dict):
        required_api_missing.append("LOCAL_FILE_CANDIDATES")

    if required_api_missing:
        st.error(
            "The RMA module is present but is not the 8/17/2026 integration-ready version. "
            "Missing: " + ", ".join(required_api_missing)
        )
        return

    st.sidebar.markdown("---")
    st.sidebar.subheader("RMA Data Sources")

    ct_upload = st.sidebar.file_uploader(
        "ClientTrack export",
        type=["xlsx", "xls"],
        key="combined_rma_ct",
    )
    active_upload = st.sidebar.file_uploader(
        "eRED Active Report",
        type=["xlsx", "xls"],
        key="combined_rma_active",
    )
    comp_upload = st.sidebar.file_uploader(
        "eRED Comprehensive Report",
        type=["xlsx", "xls"],
        key="combined_rma_comp",
    )

    try:
        ct_bytes, ct_name = resolve_source(
            ct_upload, local_candidates["ct"]
        )
        active_bytes, active_name = resolve_source(
            active_upload, local_candidates["active"]
        )
        comp_bytes, comp_name = resolve_source(
            comp_upload, local_candidates["comp"]
        )
    except Exception as exc:
        st.error("Could not resolve the RMA input files.")
        st.exception(exc)
        return

    missing = []
    if ct_bytes is None:
        missing.append("ClientTrack export")
    if active_bytes is None:
        missing.append("eRED Active Report")
    if comp_bytes is None:
        missing.append("eRED Comprehensive Report")

    if missing:
        st.title("KSOR Refugee Medical Assistance (RMA)")
        st.warning(
            "Missing required RMA data source(s): "
            + ", ".join(missing)
            + ". Upload them in the sidebar, or place the files in the repo "
              "`data/` folder using the short filenames below."
        )
        st.code(
            "data/ct_export.xlsx\n"
            "data/ered_active.xlsx\n"
            "data/ered_comprehensive.xlsx"
        )
        return

    with st.sidebar.expander("Loaded RMA files", expanded=False):
        st.write(f"ClientTrack: {ct_name}")
        st.write(f"Active: {active_name}")
        st.write(f"Comprehensive: {comp_name}")

    report_date = getattr(module, "DEFAULT_REPORT_DATE", None)
    august_arrivals = getattr(module, "DEFAULT_AUGUST_ARRIVALS_MTD", 8)

    kwargs = {
        "ct_source": ct_bytes,
        "active_source": active_bytes,
        "comp_source": comp_bytes,
        "august_arrivals_mtd": int(august_arrivals),
        "show_smt_update": True,
    }
    if report_date is not None:
        kwargs["report_date"] = report_date

    try:
        render_fn(**kwargs)
    except Exception as exc:
        st.error("The RMA dashboard could not load the supplied data.")
        st.exception(exc)


# =============================================================================
# APP ROUTER
# =============================================================================

def main() -> None:
    st.sidebar.title("KSOR Health Dashboard")
    st.sidebar.caption("RMA • RMS • RHP")

    selected = st.sidebar.radio(
        "Select program",
        list(PROGRAMS.keys()),
        format_func=lambda key: f"{key} — {PROGRAMS[key]}",
        index=2,  # preserve RHP as the default landing selection
        key="ksor_program_selector",
    )

    st.sidebar.markdown("---")
    st.sidebar.caption(
        "FY26 health-program reporting and FY27 planning"
    )

    if selected == "RMA":
        _render_rma()

    elif selected == "RMS":
        _render_standard_module(
            label="RMS",
            module_names=["rms_dashboard"],
            function_names=[
                "render_rms_dashboard",
                "render_dashboard",
            ],
        )

    elif selected == "RHP":
        _render_standard_module(
            label="RHP",
            module_names=["rhp_dashboard"],
            function_names=[
                "render_rhp_dashboard",
                "render_dashboard",
            ],
        )


if __name__ == "__main__":
    main()
import importlib
import sys

import streamlit as st


DASHBOARD_VERSION = "KSOR Health Dashboard Router 2026-08-12"

DASHBOARDS = {
    "RMA": ("rma_dashboard", ["render_rma_dashboard", "render_dashboard", "main"]),
    "RMS": ("rms_dashboard", ["render_rms_dashboard", "render_dashboard", "main"]),
    "RHP": ("rhp_dashboard", ["render_rhp_dashboard", "render_dashboard", "main"]),
}


def _load_module(module_name: str):
    """Load the newest module code and avoid a stale cached dashboard after edits."""
    importlib.invalidate_caches()
    if module_name in sys.modules:
        return importlib.reload(sys.modules[module_name])
    return importlib.import_module(module_name)


def _render_dashboard(label: str, module_name: str, function_names: list[str]):
    """Dynamically load and render the selected program dashboard."""
    try:
        module = _load_module(module_name)
    except ModuleNotFoundError as exc:
        st.error(f"{label} dashboard file not found: {module_name}.py")
        st.caption(str(exc))
        return
    except Exception as exc:
        st.error(f"{label} dashboard could not be imported.")
        st.exception(exc)
        return

    for function_name in function_names:
        fn = getattr(module, function_name, None)
        if callable(fn):
            try:
                fn()
            except Exception as exc:
                st.error(f"{label} dashboard encountered an error while rendering.")
                st.exception(exc)
            return

    st.warning(
        f"{label} dashboard was found, but no supported render function was found. "
        f"Expected one of: {', '.join(function_names)}"
    )


def main():
    st.set_page_config(
        page_title="KSOR Health Dashboards",
        page_icon="📊",
        layout="wide",
    )

    st.sidebar.title("KSOR Dashboard")
    st.sidebar.caption(DASHBOARD_VERSION)
    st.sidebar.caption(
        "FY reporting uses an Oct. 1 fiscal-year start. RHP and Medical Screening "
        "support monthly, quarterly, FYTD and fiscal-week reporting."
    )

    selected = st.sidebar.radio(
        "Select program",
        list(DASHBOARDS.keys()),
        index=2,
    )

    module_name, function_names = DASHBOARDS[selected]
    _render_dashboard(selected, module_name, function_names)


if __name__ == "__main__":
    main()
