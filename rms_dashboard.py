import io
import pandas as pd
import streamlit as st


def render_rms_dashboard():
    st.header("KSOR RMS Tracker")
    st.caption("Backlog aging, reports, overdue flags, and executive exports.")

    uploaded_file = st.file_uploader(
        "Upload RMS Excel file",
        type=["xlsx"],
        key="rms_upload",
    )

    if uploaded_file is None:
        st.info("Upload your KSOR RMS Excel file to begin.")
        return

    df = pd.read_excel(uploaded_file)

    df.columns = [
        col.strip().lower().replace(" ", "_").replace("/", "_")
        for col in df.columns
    ]

    client_id_col = "client_id"
    name_col = "name"
    dob_col = "birth_date"
    org_col = "organization"
    clinic_col = "clinic_rms_package_was_sent_to"
    requested_date_col = "date_appointment_was_requested"
    scheduled_date_col = "date_of_scheduled_appointment_with_clinic"
    invoice_date_col = "invoice_date"

    required_cols = [
        client_id_col,
        name_col,
        dob_col,
        org_col,
        requested_date_col,
        scheduled_date_col,
        invoice_date_col,
    ]

    missing = [col for col in required_cols if col not in df.columns]

    if missing:
        st.error(f"Missing required columns: {missing}")
        st.write("Detected columns:")
        st.write(list(df.columns))
        return

    for col in [dob_col, requested_date_col, scheduled_date_col, invoice_date_col]:
        df[col] = pd.to_datetime(df[col], errors="coerce")

    if clinic_col in df.columns:
        df["clinic"] = df[clinic_col].fillna(df[org_col])
    else:
        df["clinic"] = df[org_col]

    df["client_key"] = df[client_id_col].astype(str).str.strip()

    missing_id = df["client_key"].isin(["", "nan", "None"])
    df.loc[missing_id, "client_key"] = (
        df.loc[missing_id, name_col].astype(str).str.upper().str.strip()
        + "_"
        + df.loc[missing_id, dob_col].astype(str)
    )

    df["completed"] = df[invoice_date_col].notna()
    df["status"] = df["completed"].apply(
        lambda x: "Completed" if x else "Pending"
    )

    df["reporting_date"] = df[invoice_date_col]
    df["reporting_date"] = df["reporting_date"].fillna(df[scheduled_date_col])
    df["reporting_date"] = df["reporting_date"].fillna(df[requested_date_col])

    df["fiscal_year"] = df["reporting_date"].apply(
        lambda x: x.year + 1
        if pd.notna(x) and x.month >= 10
        else x.year
        if pd.notna(x)
        else None
    )

    df["month"] = df["reporting_date"].dt.to_period("M").astype(str)

    df["week"] = df["reporting_date"].dt.isocalendar().week.astype("Int64")
    df["week_label"] = (
        df["reporting_date"].dt.isocalendar().year.astype(str)
        + "-W"
        + df["week"].astype(str).str.zfill(2)
    )

    def get_trimester(date):
        if pd.isna(date):
            return None
        if date.month in [10, 11, 12]:
            return "T1"
        if date.month in [1, 2, 3]:
            return "T2"
        if date.month in [4, 5, 6]:
            return "T3"
        return "T4"

    df["trimester"] = df["reporting_date"].apply(get_trimester)

    df["days_requested_to_invoice"] = (
        df[invoice_date_col] - df[requested_date_col]
    ).dt.days

    unique_clients = (
        df.sort_values(by=["client_key", "reporting_date"])
        .drop_duplicates(subset=["client_key"], keep="last")
        .copy()
    )

    today = pd.Timestamp.today().normalize()

    unique_clients["days_pending"] = None
    pending_mask = unique_clients["status"] == "Pending"
    unique_clients.loc[pending_mask, "days_pending"] = (
        today - unique_clients.loc[pending_mask, "reporting_date"]
    ).dt.days

    def backlog_bucket(days):
        if pd.isna(days):
            return "Completed"
        if days <= 30:
            return "0-30 days"
        if days <= 60:
            return "31-60 days"
        if days <= 90:
            return "61-90 days"
        return "90+ days"

    unique_clients["backlog_age_bucket"] = unique_clients[
        "days_pending"
    ].apply(backlog_bucket)

    unique_clients["overdue_flag"] = unique_clients["days_pending"].apply(
        lambda x: "Overdue 60+ days" if pd.notna(x) and x > 60 else "OK"
    )

    st.sidebar.header("RMS Filters")

    available_fys = sorted(unique_clients["fiscal_year"].dropna().unique())

    if len(available_fys) == 0:
        st.error("No valid reporting dates found.")
        return

    selected_fy = st.sidebar.selectbox(
        "RMS Fiscal Year",
        available_fys,
        key="rms_fiscal_year",
    )

    clinics = sorted(unique_clients["clinic"].dropna().unique())

    selected_clinics = st.sidebar.multiselect(
        "RMS Clinic",
        clinics,
        default=clinics,
        key="rms_clinic_filter",
    )

    selected_statuses = st.sidebar.multiselect(
        "RMS Status",
        ["Completed", "Pending"],
        default=["Completed", "Pending"],
        key="rms_status_filter",
    )

    selected_trimesters = st.sidebar.multiselect(
        "RMS Trimester",
        ["T1", "T2", "T3", "T4"],
        default=["T1", "T2", "T3", "T4"],
        key="rms_trimester_filter",
    )

    filtered = unique_clients[
        (unique_clients["fiscal_year"] == selected_fy)
        & (unique_clients["clinic"].isin(selected_clinics))
        & (unique_clients["status"].isin(selected_statuses))
        & (unique_clients["trimester"].isin(selected_trimesters))
    ].copy()

    st.subheader(f"FY{int(selected_fy)} Executive Snapshot")

    total_clients = filtered["client_key"].nunique()
    completed_clients = filtered[filtered["completed"]]["client_key"].nunique()
    pending_clients = filtered[~filtered["completed"]]["client_key"].nunique()
    overdue_clients = filtered[
        filtered["overdue_flag"] == "Overdue 60+ days"
    ]["client_key"].nunique()

    completion_rate = completed_clients / total_clients if total_clients else 0
    avg_days_completion = filtered["days_requested_to_invoice"].mean()

    col1, col2, col3, col4, col5, col6 = st.columns(6)

    col1.metric("Unique Clients", total_clients)
    col2.metric("Completed", completed_clients)
    col3.metric("Pending", pending_clients)
    col4.metric("Overdue 60+ Days", overdue_clients)
    col5.metric("Completion Rate", f"{completion_rate:.1%}")
    col6.metric(
        "Avg Days to Completion",
        "N/A" if pd.isna(avg_days_completion) else f"{avg_days_completion:.1f}",
    )

    st.divider()

    st.subheader("Clinic Performance Summary")

    clinic_summary = (
        filtered.groupby("clinic")
        .agg(
            unique_clients=("client_key", "nunique"),
            completed=("completed", "sum"),
            pending=("completed", lambda x: (~x).sum()),
            overdue_60_plus=(
                "overdue_flag",
                lambda x: (x == "Overdue 60+ days").sum(),
            ),
            avg_days_to_completion=("days_requested_to_invoice", "mean"),
        )
        .reset_index()
    )

    clinic_summary["completion_rate"] = (
        clinic_summary["completed"] / clinic_summary["unique_clients"]
    ).fillna(0)

    clinic_summary["risk_flag"] = clinic_summary.apply(
        lambda row: "High Risk"
        if row["completion_rate"] < 0.70 or row["overdue_60_plus"] > 0
        else "OK",
        axis=1,
    )

    st.dataframe(clinic_summary, use_container_width=True)

    st.subheader("Automatic Alerts")

    alert_df = clinic_summary[clinic_summary["risk_flag"] != "OK"]

    if alert_df.empty:
        st.success("No major clinic performance alerts based on current filters.")
    else:
        st.warning("Some clinics may need follow-up.")
        st.dataframe(alert_df, use_container_width=True)

    st.subheader("Backlog Aging Summary")

    backlog_summary = (
        filtered[filtered["status"] == "Pending"]
        .groupby(["clinic", "backlog_age_bucket"])
        .agg(pending_clients=("client_key", "nunique"))
        .reset_index()
    )

    st.dataframe(backlog_summary, use_container_width=True)

    if not backlog_summary.empty:
        backlog_chart = backlog_summary.pivot_table(
            index="clinic",
            columns="backlog_age_bucket",
            values="pending_clients",
            aggfunc="sum",
            fill_value=0,
        )
        st.bar_chart(backlog_chart)

    st.subheader("Pending Backlog Detail")

    pending_backlog = filtered[filtered["status"] == "Pending"].copy()

    pending_display_cols = [
        client_id_col,
        name_col,
        "clinic",
        requested_date_col,
        scheduled_date_col,
        "reporting_date",
        "days_pending",
        "backlog_age_bucket",
        "overdue_flag",
        "status",
    ]

    pending_display_cols = [
        col for col in pending_display_cols if col in pending_backlog.columns
    ]

    st.dataframe(
        pending_backlog[pending_display_cols],
        use_container_width=True,
    )

    st.subheader("Overdue 60+ Days Detail")

    overdue_detail = filtered[
        filtered["overdue_flag"] == "Overdue 60+ days"
    ].copy()

    st.dataframe(
        overdue_detail[pending_display_cols],
        use_container_width=True,
    )

    st.subheader("Monthly Summary")

    monthly = (
        filtered.groupby(["month", "clinic"])
        .agg(
            unique_clients=("client_key", "nunique"),
            completed=("completed", "sum"),
            pending=("completed", lambda x: (~x).sum()),
            overdue_60_plus=(
                "overdue_flag",
                lambda x: (x == "Overdue 60+ days").sum(),
            ),
            avg_days_to_completion=("days_requested_to_invoice", "mean"),
        )
        .reset_index()
    )

    monthly["completion_rate"] = (
        monthly["completed"] / monthly["unique_clients"]
    ).fillna(0)

    st.dataframe(monthly, use_container_width=True)

    st.subheader("Weekly Summary")

    weekly = (
        filtered.groupby(["week_label", "clinic"])
        .agg(
            unique_clients=("client_key", "nunique"),
            completed=("completed", "sum"),
            pending=("completed", lambda x: (~x).sum()),
            overdue_60_plus=(
                "overdue_flag",
                lambda x: (x == "Overdue 60+ days").sum(),
            ),
        )
        .reset_index()
    )

    weekly["completion_rate"] = (
        weekly["completed"] / weekly["unique_clients"]
    ).fillna(0)

    st.dataframe(weekly, use_container_width=True)

    st.subheader("Trimester Summary")

    trimester = (
        filtered.groupby(["trimester", "clinic"])
        .agg(
            unique_clients=("client_key", "nunique"),
            completed=("completed", "sum"),
            pending=("completed", lambda x: (~x).sum()),
            overdue_60_plus=(
                "overdue_flag",
                lambda x: (x == "Overdue 60+ days").sum(),
            ),
        )
        .reset_index()
    )

    trimester["completion_rate"] = (
        trimester["completed"] / trimester["unique_clients"]
    ).fillna(0)

    st.dataframe(trimester, use_container_width=True)

    st.subheader("Executive Report")

    top_clinic = (
        clinic_summary.sort_values("unique_clients", ascending=False)["clinic"].iloc[0]
        if not clinic_summary.empty
        else "N/A"
    )

    lowest_completion_clinic = (
        clinic_summary.sort_values("completion_rate", ascending=True)["clinic"].iloc[0]
        if not clinic_summary.empty
        else "N/A"
    )

    executive_report = pd.DataFrame(
        {
            "metric": [
                "Fiscal Year",
                "Total Unique Clients",
                "Completed",
                "Pending",
                "Overdue 60+ Days",
                "Completion Rate",
                "Average Days to Completion",
                "Highest Volume Clinic",
                "Lowest Completion Rate Clinic",
            ],
            "value": [
                f"FY{int(selected_fy)}",
                total_clients,
                completed_clients,
                pending_clients,
                overdue_clients,
                f"{completion_rate:.1%}",
                "N/A"
                if pd.isna(avg_days_completion)
                else round(avg_days_completion, 1),
                top_clinic,
                lowest_completion_clinic,
            ],
        }
    )

    st.dataframe(executive_report, use_container_width=True)

    st.subheader("Visual Dashboard")

    if not monthly.empty:
        st.markdown("### Monthly Unique Clients by Clinic")
        monthly_chart = monthly.pivot_table(
            index="month",
            columns="clinic",
            values="unique_clients",
            aggfunc="sum",
            fill_value=0,
        )
        st.line_chart(monthly_chart)

    if not clinic_summary.empty:
        st.markdown("### Completed vs Pending by Clinic")
        clinic_chart = clinic_summary.set_index("clinic")[["completed", "pending"]]
        st.bar_chart(clinic_chart)

    if not backlog_summary.empty:
        st.markdown("### Backlog Aging by Clinic")
        st.bar_chart(backlog_chart)

    if not clinic_summary.empty:
        st.markdown("### Completion Rate by Clinic")
        completion_rate_chart = clinic_summary.set_index("clinic")[
            "completion_rate"
        ]
        st.bar_chart(completion_rate_chart)

    output = io.BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Cleaned_Data")
        unique_clients.to_excel(writer, index=False, sheet_name="Unique_Clients")
        filtered.to_excel(writer, index=False, sheet_name="Filtered_View")
        executive_report.to_excel(writer, index=False, sheet_name="Executive_Report")
        clinic_summary.to_excel(writer, index=False, sheet_name="Clinic_Summary")
        backlog_summary.to_excel(writer, index=False, sheet_name="Backlog_Summary")
        pending_backlog.to_excel(writer, index=False, sheet_name="Pending_Backlog")
        overdue_detail.to_excel(writer, index=False, sheet_name="Overdue_60Plus")
        monthly.to_excel(writer, index=False, sheet_name="Monthly")
        weekly.to_excel(writer, index=False, sheet_name="Weekly")
        trimester.to_excel(writer, index=False, sheet_name="Trimester")

    st.download_button(
        label="Download RMS Smart Reports Workbook",
        data=output.getvalue(),
        file_name="ksor_rms_tracker_smart_reports.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def main():
    render_rms_dashboard()


if __name__ == "__main__":
    main()