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
