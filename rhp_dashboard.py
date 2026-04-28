from pathlib import Path
from datetime import date
import pandas as pd
import streamlit as st
import plotly.express as px

FY_START = pd.Timestamp("2025-10-01")
DEFAULT_REPORT_DATE = pd.Timestamp("2026-04-27")
REQUIRED_COLUMNS = ["Agency", "Client ID", "Enroll Date", "Service", "Service Date"]


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")
    df["Agency"] = df["Agency"].astype(str).str.strip()
    df["Client ID"] = df["Client ID"].astype(str).str.strip()
    df["Enroll Date"] = pd.to_datetime(df["Enroll Date"], errors="coerce")
    df["Service Date"] = pd.to_datetime(df["Service Date"], errors="coerce")
    df["Service"] = df["Service"].fillna("No service recorded").astype(str).str.strip()
    return df


def _week_label(series: pd.Series) -> pd.Series:
    week_start = series - pd.to_timedelta((series.dt.dayofweek + 1) % 7, unit="D")
    week_end = week_start + pd.Timedelta(days=6)
    return week_start.dt.strftime("%m/%d/%y") + "–" + week_end.dt.strftime("%m/%d/%y")


@st.cache_data(show_spinner=False)
def load_rhp(enrollment_file, services_file, fy_start: date, report_date: date):
    fy_start = pd.Timestamp(fy_start)
    report_date = pd.Timestamp(report_date)
    enroll = _clean(pd.read_excel(enrollment_file))
    services = _clean(pd.read_excel(services_file))

    enroll_fy = enroll.loc[
        enroll["Enroll Date"].between(fy_start, report_date, inclusive="both")
    ].drop_duplicates(["Agency", "Client ID", "Enroll Date"])
    enroll_fy["Week"] = _week_label(enroll_fy["Enroll Date"])

    services_fy = services.loc[
        services["Service Date"].between(fy_start, report_date, inclusive="both")
    ].copy()
    services_fy["Week"] = _week_label(services_fy["Service Date"])

    agencies = sorted(set(enroll_fy["Agency"]).union(set(services_fy["Agency"])))
    exec_table = pd.DataFrame({"Partner Agency": agencies})
    exec_table = exec_table.merge(
        enroll_fy.groupby("Agency").size().rename("FYTD Enrollments").reset_index().rename(columns={"Agency": "Partner Agency"}),
        on="Partner Agency", how="left"
    ).merge(
        services_fy.groupby("Agency").size().rename("FYTD Services").reset_index().rename(columns={"Agency": "Partner Agency"}),
        on="Partner Agency", how="left"
    ).fillna(0)
    exec_table[["FYTD Enrollments", "FYTD Services"]] = exec_table[["FYTD Enrollments", "FYTD Services"]].astype(int)
    exec_table["Services per Enrollment"] = (exec_table["FYTD Services"] / exec_table["FYTD Enrollments"].replace(0, pd.NA)).round(2)

    weekly_enroll = pd.pivot_table(enroll_fy, index="Agency", columns="Week", values="Client ID", aggfunc="count", fill_value=0).reset_index().rename(columns={"Agency": "Partner Agency"})
    weekly_services = pd.pivot_table(services_fy, index="Agency", columns="Week", values="Client ID", aggfunc="count", fill_value=0).reset_index().rename(columns={"Agency": "Partner Agency"})
    service_detail = services_fy.groupby(["Agency", "Week", "Service"]).size().rename("Service Count").reset_index().rename(columns={"Agency": "Partner Agency"})
    return exec_table, weekly_enroll, weekly_services, service_detail, enroll_fy, services_fy


def render_rhp_dashboard(enrollment_file=None, services_file=None):
    st.title("RHP Dashboard")
    st.caption("FYTD enrollment and services by partner agency. Enrollment count is deduplicated by Agency + Client ID + Enroll Date; services count is row-level service activity.")

    with st.sidebar:
        st.subheader("RHP inputs")
        fy_start = st.date_input("FY start", FY_START.date(), key="rhp_fy_start")
        report_date = st.date_input("Report through", DEFAULT_REPORT_DATE.date(), key="rhp_report_date")
        uploaded_enroll = st.file_uploader("RHP enrollment report", type=["xlsx"], key="rhp_enroll")
        uploaded_services = st.file_uploader("RHP services report", type=["xlsx"], key="rhp_services")

    enrollment_file = uploaded_enroll or enrollment_file or Path("1-Weekly_RHP_Enrollment_Report_2026-04-27.xlsx")
    services_file = uploaded_services or services_file or Path("2-Weekly_RHP_Services_Report_2026-04-27.xlsx")

    try:
        exec_table, weekly_enroll, weekly_services, service_detail, enroll_fy, services_fy = load_rhp(enrollment_file, services_file, fy_start, report_date)
    except Exception as exc:
        st.error(f"Could not build RHP dashboard: {exc}")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("FYTD Enrollments", f"{int(exec_table['FYTD Enrollments'].sum()):,}")
    c2.metric("FYTD Services", f"{int(exec_table['FYTD Services'].sum()):,}")
    c3.metric("Partner Agencies", f"{exec_table['Partner Agency'].nunique():,}")
    c4.metric("Services / Enrollment", f"{exec_table['FYTD Services'].sum() / max(exec_table['FYTD Enrollments'].sum(), 1):.2f}")

    st.subheader("Executive table")
    st.dataframe(exec_table.sort_values("FYTD Services", ascending=False), use_container_width=True, hide_index=True)

    chart_df = exec_table.melt(id_vars="Partner Agency", value_vars=["FYTD Enrollments", "FYTD Services"], var_name="Metric", value_name="Count")
    st.plotly_chart(px.bar(chart_df, x="Partner Agency", y="Count", color="Metric", barmode="group", title="RHP FYTD Enrollments and Services"), use_container_width=True)

    tab1, tab2, tab3, tab4 = st.tabs(["Weekly enrollments", "Weekly services", "Service detail", "Raw data"])
    with tab1:
        st.dataframe(weekly_enroll, use_container_width=True, hide_index=True)
    with tab2:
        st.dataframe(weekly_services, use_container_width=True, hide_index=True)
    with tab3:
        st.dataframe(service_detail, use_container_width=True, hide_index=True)
        if not service_detail.empty:
            top_services = service_detail.groupby("Service")["Service Count"].sum().sort_values(ascending=False).reset_index()
            st.plotly_chart(px.bar(top_services, x="Service", y="Service Count", title="RHP service counts by service type"), use_container_width=True)
    with tab4:
        st.write("Enrollment rows after FY filter and deduplication")
        st.dataframe(enroll_fy, use_container_width=True, hide_index=True)
        st.write("Service rows after FY filter")
        st.dataframe(services_fy, use_container_width=True, hide_index=True)


def main():
    render_rhp_dashboard()


if __name__ == "__main__":
    main()
