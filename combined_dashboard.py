import importlib
import streamlit as st
from rhp_dashboard import render_rhp_dashboard

DASHBOARDS = {
    "RMA": ("rma_dashboard", ["render_rma_dashboard", "render_dashboard", "main"]),
    "RMS": ("rms_dashboard", ["render_rms_dashboard", "render_dashboard", "main"]),
    "RHP": ("rhp_dashboard", ["render_rhp_dashboard"]),
}


def _render_external_dashboard(label: str, module_name: str, function_names: list[str]):
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError:
        st.warning(f"{label} dashboard file not found: {module_name}.py")
        return
    for function_name in function_names:
        fn = getattr(module, function_name, None)
        if callable(fn):
            fn()
            return
    st.warning(f"{label} dashboard was found, but no render function was found. Add one of: {', '.join(function_names)}")


def main():
    st.set_page_config(page_title="KSOR Executive Dashboards", layout="wide")
    st.sidebar.title("KSOR Dashboard")
    selected = st.sidebar.radio("Select program", list(DASHBOARDS.keys()), index=2)
    module_name, function_names = DASHBOARDS[selected]
    if selected == "RHP":
        render_rhp_dashboard()
    else:
        _render_external_dashboard(selected, module_name, function_names)


if __name__ == "__main__":
    main()
