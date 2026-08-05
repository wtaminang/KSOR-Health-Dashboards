import io

import pandas as pd
import plotly.express as px
import streamlit as st


def _build_fiscal_week_calendar(
    fy_start: pd.Timestamp,
    report_end: pd.Timestamp,
    fy_end: pd.Timestamp,
) -> pd.DataFrame:
    """Create consecutive 7-day fiscal weeks anchored to the FY start date."""
    number_of_weeks = ((report_end - fy_start).days // 7) + 1
    week_numbers = pd.Series(range(1, number_of_weeks + 1), dtype="int64")
    week_starts = fy_start + pd.to_timedelta((week_numbers - 1) * 7, unit="D")
    week_ends = week_starts + pd.Timedelta(days=6)
    week_ends = week_ends.where(week_ends <= fy_end, fy_end)

    calendar = pd.DataFrame(
        {
            "Fiscal Week": week_numbers,
            "Week Start": week_starts,
            "Week End": week_ends,
        }
    )
    calendar["Week"] = (
        "W"
        + calendar["Fiscal Week"].astype(str).str.zfill(2)
        + ": "
        + calendar["Week Start"].dt.strftime("%m/%d/%y")
        + " - "
        + calendar["Week End"].dt.strftime("%m/%d/%y")
    )
    return calendar


def _add_fiscal_week_columns(
    dataframe: pd.DataFrame,
    date_column: str,
    fy_start: pd.Timestamp,
    fy_end: pd.Timestamp,
) -> pd.DataFrame:
    """Assign each row to a fiscal week beginning on the FY start date."""
    result = dataframe.copy()

    if result.empty:
        result["Fiscal Week"] = pd.Series(dtype="int64")
        result["Week Start"] = pd.Series(dtype="datetime64[ns]")
        result["Week End"] = pd.Series(dtype="datetime64[ns]")
        result["Week"] = pd.Series(dtype="object")
        return result

    days_from_fy_start = (
        result[date_column].dt.normalize() - fy_start
    ).dt.days
    result["Fiscal Week"] = (days_from_fy_start // 7 + 1).astype(int)
    result["Week Start"] = fy_start + pd.to_timedelta(
        (result["Fiscal Week"] - 1) * 7,
        unit="D",
    )
    result["Week End"] = result["Week Start"] + pd.Timedelta(days=6)
    result["Week End"] = result["Week End"].where(
        result["Week End"] <= fy_end,
        fy_end,
    )
    result["Week"] = (
        "W"
        + result["Fiscal Week"].astype(str).str.zfill(2)
        + ": "
        + result["Week Start"].dt.strftime("%m/%d/%y")
        + " - "
        + result["Week End"].dt.strftime("%m/%d/%y")
    )
    return result


def _weekly_pivot(
    dataframe: pd.DataFrame,
    agencies: list[str],
    week_order: list[str],
) -> pd.DataFrame:
    """Build an agency-by-week table and retain zero-activity fiscal weeks."""
    if dataframe.empty:
        pivot = pd.DataFrame(index=agencies, columns=week_order).fillna(0)
    else:
        pivot = pd.pivot_table(
            dataframe,
            index="Agency",
            columns="Week",
            values="Client ID",
            aggfunc="count",
            fill_value=0,
        )
        pivot = pivot.reindex(index=agencies, columns=week_order, fill_value=0)

    pivot = pivot.astype(int)
    pivot.index.name = "Partner Agency"
    return pivot.reset_index()


def render_rhp_dashboard():
    st.header("RHP Dashboard")
    st.caption(
        "FYTD enrollment and services by partner agency. "
        "Enrollment count is deduplicated by Agency + Client ID + Enroll Date; "
        "services count is row-level service activity. Fiscal weeks are fixed "
        "7-day periods beginning on the selected FY start date."
    )

    st.sidebar.header("RHP inputs")
    fy_start_input = st.sidebar.date_input(
        "FY start",
        value=pd.to_datetime("2025-10-01"),
        key="rhp_fy_start",
    )

    fy_start = pd.Timestamp(fy_start_input).normalize()
    fy_end = fy_start + pd.DateOffset(years=1) - pd.Timedelta(days=1)
    default_report_end = min(pd.Timestamp.today().normalize(), fy_end)

    report_end_input = st.sidebar.date_input(
        "Report through",
        value=default_report_end,
        key="rhp_report_end",
    )

    report_end = pd.Timestamp(report_end_input).normalize()

    if report_end < fy_start:
        st.error("Report through date cannot be earlier than the FY start date.")
        return

    if report_end > fy_end:
        st.sidebar.warning(
            f"Report through was limited to the fiscal-year end: {fy_end:%m/%d/%Y}."
        )
        report_end = fy_end

    enroll_file = st.sidebar.file_uploader(
        "RHP enrollment report",
        type=["xlsx"],
        key="rhp_enrollment_upload",
    )
    service_file = st.sidebar.file_uploader(
        "RHP services report",
        type=["xlsx"],
        key="rhp_services_upload",
    )

    if enroll_file is None or service_file is None:
        st.info("Please upload both RHP enrollment and RHP services files.")
        return

    try:
        enroll = pd.read_excel(enroll_file)
        services = pd.read_excel(service_file)

        enroll.columns = enroll.columns.str.strip()
        services.columns = services.columns.str.strip()

        required_enroll_cols = ["Agency", "Client ID", "Enroll Date"]
        required_service_cols = ["Agency", "Client ID", "Service Date"]

        missing_enroll = [
            col for col in required_enroll_cols if col not in enroll.columns
        ]
        missing_services = [
            col for col in required_service_cols if col not in services.columns
        ]

        if missing_enroll:
            st.error(f"Enrollment file missing columns: {missing_enroll}")
            st.write("Detected enrollment columns:")
            st.write(list(enroll.columns))
            return

        if missing_services:
            st.error(f"Services file missing columns: {missing_services}")
            st.write("Detected service columns:")
            st.write(list(services.columns))
            return

        enroll["Agency"] = enroll["Agency"].astype(str).str.strip()
        services["Agency"] = services["Agency"].astype(str).str.strip()

        enroll["Client ID"] = enroll["Client ID"].astype(str).str.strip()
        services["Client ID"] = services["Client ID"].astype(str).str.strip()

        enroll["Enroll Date"] = pd.to_datetime(
            enroll["Enroll Date"],
            errors="coerce",
        )
        services["Service Date"] = pd.to_datetime(
            services["Service Date"],
            errors="coerce",
        )

        enroll_fy = enroll[
            (enroll["Enroll Date"] >= fy_start)
            & (enroll["Enroll Date"] <= report_end)
        ].copy()
        services_fy = services[
            (services["Service Date"] >= fy_start)
            & (services["Service Date"] <= report_end)
        ].copy()

        enroll_fy = enroll_fy.drop_duplicates(
            subset=["Agency", "Client ID", "Enroll Date"]
        )

        all_agencies = sorted(
            set(enroll_fy["Agency"].dropna().unique())
            | set(services_fy["Agency"].dropna().unique())
        )
        selected_agencies = st.sidebar.multiselect(
            "RHP Partner Agency",
            all_agencies,
            default=all_agencies,
            key="rhp_agency_filter",
        )

        display_agencies = selected_agencies if selected_agencies else all_agencies

        if selected_agencies:
            enroll_fy = enroll_fy[enroll_fy["Agency"].isin(selected_agencies)]
            services_fy = services_fy[
                services_fy["Agency"].isin(selected_agencies)
            ]

        enroll_fy = _add_fiscal_week_columns(
            enroll_fy,
            "Enroll Date",
            fy_start,
            fy_end,
        )
        services_fy = _add_fiscal_week_columns(
            services_fy,
            "Service Date",
            fy_start,
            fy_end,
        )

        week_calendar = _build_fiscal_week_calendar(
            fy_start,
            report_end,
            fy_end,
        )
        week_order = week_calendar["Week"].tolist()

        enrollment_summary = (
            enroll_fy.groupby("Agency")
            .size()
            .rename("FYTD Enrollments")
        )
        services_summary = (
            services_fy.groupby("Agency")
            .size()
            .rename("FYTD Services")
        )

        exec_table = pd.DataFrame({"Partner Agency": display_agencies})
        exec_table["FYTD Services"] = (
            exec_table["Partner Agency"].map(services_summary).fillna(0).astype(int)
        )
        exec_table["FYTD Enrollments"] = (
            exec_table["Partner Agency"].map(enrollment_summary).fillna(0).astype(int)
        )

        total_services = int(exec_table["FYTD Services"].sum())
        total_enrollments = int(exec_table["FYTD Enrollments"].sum())
        total_agencies = int(exec_table["Partner Agency"].nunique())

        col1, col2, col3 = st.columns(3)
        col1.metric("FYTD Services", total_services)
        col2.metric("FYTD Enrollments", total_enrollments)
        col3.metric("Partner Agencies", total_agencies)

        st.divider()
        st.subheader("Executive Summary")
        st.dataframe(exec_table, use_container_width=True, hide_index=True)

        st.divider()
        chart_col1, chart_col2 = st.columns(2)

        with chart_col1:
            st.subheader("FYTD Services by Partner Agency")
            if not exec_table.empty:
                fig_services = px.bar(
                    exec_table,
                    x="Partner Agency",
                    y="FYTD Services",
                    text="FYTD Services",
                )
                fig_services.update_layout(xaxis_title="", yaxis_title="Services")
                st.plotly_chart(fig_services, use_container_width=True)

        with chart_col2:
            st.subheader("FYTD Enrollments by Partner Agency")
            if not exec_table.empty:
                fig_enroll = px.bar(
                    exec_table,
                    x="Partner Agency",
                    y="FYTD Enrollments",
                    text="FYTD Enrollments",
                )
                fig_enroll.update_layout(xaxis_title="", yaxis_title="Enrollments")
                st.plotly_chart(fig_enroll, use_container_width=True)

        st.divider()
        st.subheader("Weekly Enrollments")
        st.caption(
            f"Week 1 = {fy_start:%m/%d/%y} - "
            f"{min(fy_start + pd.Timedelta(days=6), fy_end):%m/%d/%y}."
        )
        weekly_enroll = _weekly_pivot(
            enroll_fy,
            display_agencies,
            week_order,
        )
        st.dataframe(weekly_enroll, use_container_width=True, hide_index=True)

        st.subheader("Weekly Services")
        weekly_services = _weekly_pivot(
            services_fy,
            display_agencies,
            week_order,
        )
        st.dataframe(weekly_services, use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Weekly Trend")

        weekly_trend = week_calendar.copy()
        service_counts = services_fy.groupby("Fiscal Week").size()
        enrollment_counts = enroll_fy.groupby("Fiscal Week").size()

        weekly_trend["Services"] = (
            weekly_trend["Fiscal Week"].map(service_counts).fillna(0).astype(int)
        )
        weekly_trend["Enrollments"] = (
            weekly_trend["Fiscal Week"].map(enrollment_counts).fillna(0).astype(int)
        )

        trend_long = weekly_trend.melt(
            id_vars=["Fiscal Week", "Week"],
            value_vars=["Services", "Enrollments"],
            var_name="Metric",
            value_name="Count",
        )

        fig_trend = px.line(
            trend_long,
            x="Week",
            y="Count",
            color="Metric",
            markers=True,
            category_orders={"Week": week_order},
        )
        fig_trend.update_layout(
            xaxis_title="Fiscal Week",
            yaxis_title="Count",
            xaxis_tickangle=-45,
        )
        st.plotly_chart(fig_trend, use_container_width=True)

        st.divider()
        st.subheader("Enrollment Detail")
        enrollment_detail_cols = [
            col
            for col in [
                "Agency",
                "Client ID",
                "Client Name",
                "First Name",
                "Last Name",
                "Enroll Date",
                "Fiscal Week",
                "Week Start",
                "Week End",
                "Week",
            ]
            if col in enroll_fy.columns
        ]
        st.dataframe(
            enroll_fy[enrollment_detail_cols],
            use_container_width=True,
            hide_index=True,
        )

        st.subheader("Services Detail")
        service_detail_cols = [
            col
            for col in [
                "Agency",
                "Client ID",
                "Client Name",
                "First Name",
                "Last Name",
                "Service Date",
                "Service",
                "Service Type",
                "Fiscal Week",
                "Week Start",
                "Week End",
                "Week",
            ]
            if col in services_fy.columns
        ]
        st.dataframe(
            services_fy[service_detail_cols],
            use_container_width=True,
            hide_index=True,
        )

        st.divider()
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            exec_table.to_excel(
                writer,
                index=False,
                sheet_name="Executive Summary",
            )
            week_calendar.to_excel(
                writer,
                index=False,
                sheet_name="Fiscal Week Calendar",
            )
            weekly_enroll.to_excel(
                writer,
                index=False,
                sheet_name="Weekly Enrollments",
            )
            weekly_services.to_excel(
                writer,
                index=False,
                sheet_name="Weekly Services",
            )
            weekly_trend.to_excel(
                writer,
                index=False,
                sheet_name="Weekly Trend",
            )
            enroll_fy.to_excel(
                writer,
                index=False,
                sheet_name="Enrollment Detail",
            )
            services_fy.to_excel(
                writer,
                index=False,
                sheet_name="Services Detail",
            )

        st.download_button(
            label="Download RHP Executive Workbook",
            data=output.getvalue(),
            file_name="rhp_executive_dashboard.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    except Exception as e:
        st.error(f"Could not build RHP dashboard: {e}")


def main():
    render_rhp_dashboard()


if __name__ == "__main__":
    main()
