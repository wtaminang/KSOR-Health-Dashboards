import io
import pandas as pd
import streamlit as st
import plotly.express as px


def render_rhp_dashboard():
    st.header("RHP Dashboard")
    st.caption(
        "FYTD enrollment and services by partner agency. "
        "Enrollment count is deduplicated by Agency + Client ID + Enroll Date; "
        "services count is row-level service activity."
    )

    st.sidebar.header("RHP inputs")

    fy_start = st.sidebar.date_input(
        "FY start",
        value=pd.to_datetime("2025-10-01"),
        key="rhp_fy_start",
    )

    report_end = st.sidebar.date_input(
        "Report through",
        value=pd.to_datetime("2026-04-27"),
        key="rhp_report_end",
    )

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

        fy_start = pd.to_datetime(fy_start)
        report_end = pd.to_datetime(report_end)

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

        if selected_agencies:
            enroll_fy = enroll_fy[enroll_fy["Agency"].isin(selected_agencies)]
            services_fy = services_fy[services_fy["Agency"].isin(selected_agencies)]

        def week_label(date_series):
            week_start = date_series - pd.to_timedelta(
                (date_series.dt.dayofweek + 1) % 7,
                unit="D",
            )
            week_end = week_start + pd.Timedelta(days=6)

            return (
                week_start.dt.strftime("%m/%d/%y")
                + " - "
                + week_end.dt.strftime("%m/%d/%y")
            )

        enroll_fy["Week"] = week_label(enroll_fy["Enroll Date"])
        services_fy["Week"] = week_label(services_fy["Service Date"])

        enrollment_summary = (
            enroll_fy.groupby("Agency")
            .size()
            .rename("FYTD Enrollments")
            .to_frame()
        )

        services_summary = (
            services_fy.groupby("Agency")
            .size()
            .rename("FYTD Services")
            .to_frame()
        )

        exec_table = (
            services_summary.join(enrollment_summary, how="outer")
            .fillna(0)
            .astype(int)
            .reset_index()
            .rename(columns={"Agency": "Partner Agency"})
        )

        exec_table = exec_table[
            ["Partner Agency", "FYTD Services", "FYTD Enrollments"]
        ]

        total_services = int(exec_table["FYTD Services"].sum())
        total_enrollments = int(exec_table["FYTD Enrollments"].sum())
        total_agencies = exec_table["Partner Agency"].nunique()

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

        weekly_enroll = pd.pivot_table(
            enroll_fy,
            index="Agency",
            columns="Week",
            values="Client ID",
            aggfunc="count",
            fill_value=0,
        )

        weekly_enroll = weekly_enroll.reset_index().rename(
            columns={"Agency": "Partner Agency"}
        )

        st.dataframe(weekly_enroll, use_container_width=True, hide_index=True)

        st.subheader("Weekly Services")

        weekly_services = pd.pivot_table(
            services_fy,
            index="Agency",
            columns="Week",
            values="Client ID",
            aggfunc="count",
            fill_value=0,
        )

        weekly_services = weekly_services.reset_index().rename(
            columns={"Agency": "Partner Agency"}
        )

        st.dataframe(weekly_services, use_container_width=True, hide_index=True)

        st.divider()

        st.subheader("Weekly Trend")

        weekly_enroll_long = (
            enroll_fy.groupby("Week")
            .size()
            .rename("Enrollments")
            .reset_index()
        )

        weekly_services_long = (
            services_fy.groupby("Week")
            .size()
            .rename("Services")
            .reset_index()
        )

        weekly_trend = pd.merge(
            weekly_services_long,
            weekly_enroll_long,
            on="Week",
            how="outer",
        ).fillna(0)

        if not weekly_trend.empty:
            weekly_trend["Services"] = weekly_trend["Services"].astype(int)
            weekly_trend["Enrollments"] = weekly_trend["Enrollments"].astype(int)

            trend_long = weekly_trend.melt(
                id_vars="Week",
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
            )

            fig_trend.update_layout(xaxis_title="Week", yaxis_title="Count")
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
            exec_table.to_excel(writer, index=False, sheet_name="Executive Summary")
            weekly_enroll.to_excel(writer, index=False, sheet_name="Weekly Enrollments")
            weekly_services.to_excel(writer, index=False, sheet_name="Weekly Services")
            weekly_trend.to_excel(writer, index=False, sheet_name="Weekly Trend")
            enroll_fy.to_excel(writer, index=False, sheet_name="Enrollment Detail")
            services_fy.to_excel(writer, index=False, sheet_name="Services Detail")

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
