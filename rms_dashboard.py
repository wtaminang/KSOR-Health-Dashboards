import io

import pandas as pd
import plotly.express as px
import streamlit as st


FISCAL_QUARTERS = {
    10: "Q1", 11: "Q1", 12: "Q1",
    1: "Q2", 2: "Q2", 3: "Q2",
    4: "Q3", 5: "Q3", 6: "Q3",
    7: "Q4", 8: "Q4", 9: "Q4",
}


def _normalize_columns(columns) -> list[str]:
    return [
        str(col).strip().lower().replace(" ", "_").replace("/", "_")
        for col in columns
    ]


def _build_fiscal_week_calendar(
    fy_start: pd.Timestamp,
    report_end: pd.Timestamp,
    fy_end: pd.Timestamp,
) -> pd.DataFrame:
    number_of_weeks = ((report_end - fy_start).days // 7) + 1
    week_numbers = pd.Series(range(1, number_of_weeks + 1), dtype="int64")
    week_starts = fy_start + pd.to_timedelta((week_numbers - 1) * 7, unit="D")
    week_ends = week_starts + pd.Timedelta(days=6)
    week_ends = week_ends.where(week_ends <= fy_end, fy_end)
    result = pd.DataFrame(
        {
            "Fiscal Week": week_numbers,
            "Week Start": week_starts,
            "Week End": week_ends,
        }
    )
    result["Week"] = (
        "W"
        + result["Fiscal Week"].astype(str).str.zfill(2)
        + ": "
        + result["Week Start"].dt.strftime("%m/%d/%y")
        + " - "
        + result["Week End"].dt.strftime("%m/%d/%y")
    )
    result["Complete"] = result["Week End"] <= report_end
    return result


def _month_calendar(fy_start: pd.Timestamp, report_end: pd.Timestamp) -> pd.DataFrame:
    starts = pd.date_range(
        start=fy_start.replace(day=1),
        end=report_end.replace(day=1),
        freq="MS",
    )
    result = pd.DataFrame({"Month Start": starts})
    result["Month End"] = result["Month Start"] + pd.offsets.MonthEnd(0)
    result["Month"] = result["Month Start"].dt.strftime("%b %Y")
    result["Complete"] = result["Month End"] <= report_end
    result["Display Month"] = result["Month"]
    if not result.empty and not bool(result.iloc[-1]["Complete"]):
        result.loc[result.index[-1], "Display Month"] += " (MTD)"
    return result


def _add_period_columns(
    frame: pd.DataFrame,
    date_col: str,
    fy_start: pd.Timestamp,
    fy_end: pd.Timestamp,
) -> pd.DataFrame:
    result = frame.copy()
    if result.empty:
        for name in ["Fiscal Week", "Week Start", "Week End", "Week", "Month Start", "Month", "Fiscal Quarter"]:
            result[name] = pd.Series(dtype="object")
        return result

    date = result[date_col].dt.normalize()
    days = (date - fy_start).dt.days
    result["Fiscal Week"] = (days // 7 + 1).astype("Int64")
    result["Week Start"] = fy_start + pd.to_timedelta((result["Fiscal Week"] - 1) * 7, unit="D")
    result["Week End"] = result["Week Start"] + pd.Timedelta(days=6)
    result["Week End"] = result["Week End"].where(result["Week End"] <= fy_end, fy_end)
    result["Week"] = (
        "W"
        + result["Fiscal Week"].astype(str).str.zfill(2)
        + ": "
        + result["Week Start"].dt.strftime("%m/%d/%y")
        + " - "
        + result["Week End"].dt.strftime("%m/%d/%y")
    )
    result["Month Start"] = date.dt.to_period("M").dt.to_timestamp()
    result["Month"] = result["Month Start"].dt.strftime("%b %Y")
    result["Fiscal Quarter"] = date.dt.month.map(FISCAL_QUARTERS)
    return result


def _add_grand_total_row(table: pd.DataFrame, label_col: str) -> pd.DataFrame:
    result = table.copy()
    numeric_cols = [c for c in result.columns if c != label_col]
    grand = {label_col: "Grand Total"}
    for col in numeric_cols:
        grand[col] = int(pd.to_numeric(result[col], errors="coerce").fillna(0).sum())
    return pd.concat([result, pd.DataFrame([grand])], ignore_index=True)


def _metric_records(
    df: pd.DataFrame,
    date_col: str,
    org_col: str,
    client_key_col: str,
    fy_start: pd.Timestamp,
    report_end: pd.Timestamp,
    selected_orgs: list[str],
    fy_end: pd.Timestamp,
) -> pd.DataFrame:
    scoped = df[
        df[date_col].notna()
        & (df[date_col] >= fy_start)
        & (df[date_col] <= report_end)
        & df[org_col].isin(selected_orgs)
    ].copy()
    scoped = scoped.drop_duplicates(subset=[org_col, client_key_col, date_col])
    scoped = scoped.rename(columns={org_col: "Organization"})
    return _add_period_columns(scoped, date_col, fy_start, fy_end)


def _metric_pivot(
    records: pd.DataFrame,
    organizations: list[str],
    period_col: str,
    period_order: list[str],
) -> pd.DataFrame:
    if records.empty:
        pivot = pd.DataFrame(0, index=organizations, columns=period_order)
    else:
        pivot = pd.pivot_table(
            records,
            index="Organization",
            columns=period_col,
            values="client_key",
            aggfunc="count",
            fill_value=0,
        )
        pivot = pivot.reindex(index=organizations, columns=period_order, fill_value=0)
    pivot = pivot.astype(int)
    pivot["FYTD Total"] = pivot.sum(axis=1)
    pivot.index.name = "Organization"
    return _add_grand_total_row(pivot.reset_index(), "Organization")


def _monthly_combined_table(
    requested: pd.DataFrame,
    scheduled: pd.DataFrame,
    organizations: list[str],
    month_calendar: pd.DataFrame,
) -> pd.DataFrame:
    month_order = month_calendar["Month"].tolist()
    rename = dict(zip(month_calendar["Month"], month_calendar["Display Month"]))

    req = _metric_pivot(requested, organizations, "Month", month_order)
    sch = _metric_pivot(scheduled, organizations, "Month", month_order)
    req = req[req["Organization"] != "Grand Total"].set_index("Organization")
    sch = sch[sch["Organization"] != "Grand Total"].set_index("Organization")

    rows = []
    for org in organizations:
        req_row = {"Organization / Metric": f"{org} - Requested"}
        sch_row = {"Organization / Metric": f"{org} - Scheduled"}
        for month in month_order:
            req_row[rename[month]] = int(req.loc[org, month]) if org in req.index else 0
            sch_row[rename[month]] = int(sch.loc[org, month]) if org in sch.index else 0
        req_row["FYTD Total"] = int(req.loc[org, "FYTD Total"]) if org in req.index else 0
        sch_row["FYTD Total"] = int(sch.loc[org, "FYTD Total"]) if org in sch.index else 0
        rows.extend([req_row, sch_row])

    req_total = {"Organization / Metric": "Grand Total - Requested"}
    sch_total = {"Organization / Metric": "Grand Total - Scheduled"}
    for month in month_order:
        display = rename[month]
        req_total[display] = int((requested["Month"] == month).sum())
        sch_total[display] = int((scheduled["Month"] == month).sum())
    req_total["FYTD Total"] = len(requested)
    sch_total["FYTD Total"] = len(scheduled)
    rows.extend([req_total, sch_total])
    return pd.DataFrame(rows)


def _quarter_tables(
    requested: pd.DataFrame,
    scheduled: pd.DataFrame,
    organizations: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    quarters = ["Q1", "Q2", "Q3", "Q4"]
    return (
        _metric_pivot(requested, organizations, "Fiscal Quarter", quarters),
        _metric_pivot(scheduled, organizations, "Fiscal Quarter", quarters),
    )


def _weekly_summary(
    requested: pd.DataFrame,
    scheduled: pd.DataFrame,
    week_calendar: pd.DataFrame,
) -> pd.DataFrame:
    result = week_calendar[["Fiscal Week", "Week", "Week Start", "Week End", "Complete"]].copy()
    req_counts = requested.groupby("Fiscal Week").size()
    sch_counts = scheduled.groupby("Fiscal Week").size()
    result["Requested"] = result["Fiscal Week"].map(req_counts).fillna(0).astype(int)
    result["Scheduled"] = result["Fiscal Week"].map(sch_counts).fillna(0).astype(int)
    return result


def _pct_change(current: int, prior: int) -> str:
    if prior == 0:
        return "percentage change is not meaningful because the prior value was zero"
    return f"{(current - prior) / prior * 100:+.1f}%"


def _rms_comments(
    requested: pd.DataFrame,
    scheduled: pd.DataFrame,
    organizations: list[str],
    month_calendar: pd.DataFrame,
    request_backlog: pd.DataFrame,
) -> list[str]:
    comments = []
    total_req = len(requested)
    total_sch = len(scheduled)
    gap = len(request_backlog)
    if total_req:
        comments.append(
            f"FYTD Medical Screening activity includes {total_req} appointment requests and {total_sch} scheduled appointments; "
            f"{gap} requested client{' remains' if gap == 1 else 's remain'} without a scheduled appointment in the current extract."
        )

    completed = month_calendar[month_calendar["Complete"]]
    if len(completed) >= 2:
        current = completed.iloc[-1]
        prior = completed.iloc[-2]
        cr = int(((requested["date_appointment_was_requested"] >= current["Month Start"]) & (requested["date_appointment_was_requested"] <= current["Month End"])).sum())
        pr = int(((requested["date_appointment_was_requested"] >= prior["Month Start"]) & (requested["date_appointment_was_requested"] <= prior["Month End"])).sum())
        cs = int(((scheduled["date_of_scheduled_appointment_with_clinic"] >= current["Month Start"]) & (scheduled["date_of_scheduled_appointment_with_clinic"] <= current["Month End"])).sum())
        ps = int(((scheduled["date_of_scheduled_appointment_with_clinic"] >= prior["Month Start"]) & (scheduled["date_of_scheduled_appointment_with_clinic"] <= prior["Month End"])).sum())
        comments.append(
            f"From {prior['Month']} to {current['Month']}, requests moved from {pr} to {cr} ({cr-pr:+d}; {_pct_change(cr, pr)}), "
            f"while scheduled appointments moved from {ps} to {cs} ({cs-ps:+d}; {_pct_change(cs, ps)})."
        )

    if gap and organizations:
        backlog_by_org = request_backlog.groupby("Organization").size().sort_values(ascending=False)
        top_org = backlog_by_org.index[0]
        comments.append(
            f"The current unscheduled-request gap is concentrated in {top_org} ({int(backlog_by_org.iloc[0])} client{'s' if int(backlog_by_org.iloc[0]) != 1 else ''}); "
            "small monthly Medical Screening counts can make percentage changes appear large, so the counts should be read as workflow volume rather than partner quality."
        )
    return comments[:3]


def render_rms_dashboard():
    st.header("KSOR Medical Screening Dashboard")
    st.caption(
        "Requested and scheduled Medical Screening activity by organization, fiscal month, quarter and fiscal week. "
        "Invoice/completion measures appear only when an Invoice Date field is present in the source export."
    )

    st.sidebar.header("RMS inputs")
    fy_start_input = st.sidebar.date_input(
        "FY start",
        value=pd.to_datetime("2025-10-01"),
        key="rms_fy_start",
    )
    fy_start = pd.Timestamp(fy_start_input).normalize()
    fy_end = fy_start + pd.DateOffset(years=1) - pd.Timedelta(days=1)
    default_report_end = min(pd.Timestamp.today().normalize(), fy_end)
    report_end_input = st.sidebar.date_input(
        "Report through",
        value=default_report_end,
        key="rms_report_end",
    )
    report_end = pd.Timestamp(report_end_input).normalize()
    if report_end < fy_start:
        st.error("Report through date cannot be earlier than the FY start date.")
        return
    if report_end > fy_end:
        st.sidebar.warning(f"Report through was limited to FY end: {fy_end:%m/%d/%Y}.")
        report_end = fy_end

    uploaded_file = st.sidebar.file_uploader(
        "Upload Medical Screening Excel file",
        type=["xlsx"],
        key="rms_upload",
    )
    if uploaded_file is None:
        st.info("Upload the ClientTrack Medical Screening export to begin.")
        return

    try:
        df = pd.read_excel(uploaded_file)
        original_columns = list(df.columns)
        df.columns = _normalize_columns(df.columns)

        org_col = "organization"
        client_id_col = "client_id"
        requested_date_col = "date_appointment_was_requested"
        scheduled_date_col = "date_of_scheduled_appointment_with_clinic"
        invoice_date_col = "invoice_date"
        name_col = "name"
        dob_col = "birth_date"
        clinic_col = "clinic_rms_package_was_sent_to"

        required = [org_col, client_id_col, requested_date_col, scheduled_date_col]
        missing = [c for c in required if c not in df.columns]
        if missing:
            st.error(f"Missing required columns: {missing}")
            st.write("Detected columns:", list(df.columns))
            return

        for date_col in [requested_date_col, scheduled_date_col, dob_col, invoice_date_col]:
            if date_col in df.columns:
                df[date_col] = pd.to_datetime(df[date_col], errors="coerce")

        df[org_col] = df[org_col].astype(str).str.strip()
        df[client_id_col] = df[client_id_col].astype(str).str.strip()
        df["client_key"] = df[client_id_col]
        missing_id = df["client_key"].isin(["", "nan", "None", "<NA>"])
        if missing_id.any() and name_col in df.columns:
            fallback_name = df[name_col].astype(str).str.upper().str.strip()
            if dob_col in df.columns:
                fallback_name = fallback_name + "_" + df[dob_col].astype(str)
            df.loc[missing_id, "client_key"] = fallback_name[missing_id]

        if clinic_col in df.columns:
            df["clinic"] = df[clinic_col].fillna(df[org_col]).astype(str).str.strip()
        else:
            df["clinic"] = df[org_col]

        organizations = sorted(
            df.loc[
                df[requested_date_col].notna() | df[scheduled_date_col].notna(),
                org_col,
            ].dropna().unique()
        )
        if not organizations:
            st.error("No valid Medical Screening request or scheduled dates were found.")
            return

        selected_orgs = st.sidebar.multiselect(
            "RMS Organization",
            organizations,
            default=organizations,
            key="rms_organization_filter",
        )
        display_orgs = selected_orgs if selected_orgs else organizations

        requested = _metric_records(
            df,
            requested_date_col,
            org_col,
            "client_key",
            fy_start,
            report_end,
            display_orgs,
            fy_end,
        )
        scheduled = _metric_records(
            df,
            scheduled_date_col,
            org_col,
            "client_key",
            fy_start,
            report_end,
            display_orgs,
            fy_end,
        )

        # Requested client backlog is based on requested records with no scheduled date through report_end.
        requested_keys = set(requested["client_key"].astype(str))
        scheduled_keys = set(scheduled["client_key"].astype(str))
        backlog_keys = requested_keys - scheduled_keys
        request_backlog = requested[requested["client_key"].astype(str).isin(backlog_keys)].copy()

        invoice_available = invoice_date_col in df.columns and df[invoice_date_col].notna().any()
        invoiced = pd.DataFrame()
        if invoice_available:
            invoiced = _metric_records(
                df,
                invoice_date_col,
                org_col,
                "client_key",
                fy_start,
                report_end,
                display_orgs,
                fy_end,
            )
        else:
            st.info(
                "Invoice Date is not present in this export. The dashboard therefore reports Requested and Scheduled activity only; "
                "it does not infer Invoiced or Completed status."
            )

        total_requested = len(requested)
        total_scheduled = len(scheduled)
        total_invoiced = len(invoiced) if invoice_available else None
        scheduling_rate = total_scheduled / total_requested if total_requested else 0

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("FYTD Requested", total_requested)
        c2.metric("FYTD Scheduled", total_scheduled)
        c3.metric("Not Yet Scheduled", len(request_backlog))
        c4.metric("Scheduling Rate", f"{scheduling_rate:.1%}" if total_requested else "N/A")
        c5.metric("Organizations", len(display_orgs))

        if invoice_available:
            st.metric("FYTD Invoiced", total_invoiced)

        req_by_org = requested.groupby("Organization").size().reindex(display_orgs, fill_value=0)
        sch_by_org = scheduled.groupby("Organization").size().reindex(display_orgs, fill_value=0)
        summary = pd.DataFrame(
            {
                "Organization": display_orgs,
                "# Appts Requested": req_by_org.values.astype(int),
                "# Appts Scheduled": sch_by_org.values.astype(int),
            }
        )
        summary["Requested - Scheduled"] = summary["# Appts Requested"] - summary["# Appts Scheduled"]
        summary = _add_grand_total_row(summary, "Organization")

        st.divider()
        st.subheader(f"FY Medical Screening {fy_start:%m/%d/%y}-{report_end:%m/%d/%y}")
        st.dataframe(summary, use_container_width=True, hide_index=True)

        month_calendar = _month_calendar(fy_start, report_end)
        week_calendar = _build_fiscal_week_calendar(fy_start, report_end, fy_end)

        requested_quarterly, scheduled_quarterly = _quarter_tables(requested, scheduled, display_orgs)
        st.subheader("Medical Screening - Quarterly Totals")
        st.markdown("**Appointments Requested**")
        st.dataframe(requested_quarterly, use_container_width=True, hide_index=True)
        st.markdown("**Appointments Scheduled**")
        st.dataframe(scheduled_quarterly, use_container_width=True, hide_index=True)

        current_q = FISCAL_QUARTERS[report_end.month]
        quarter_end_month = {"Q1": 12, "Q2": 3, "Q3": 6, "Q4": 9}[current_q]
        quarter_end_year = fy_start.year if current_q == "Q1" else fy_start.year + 1
        quarter_end = pd.Timestamp(quarter_end_year, quarter_end_month, 1) + pd.offsets.MonthEnd(0)
        if report_end < quarter_end:
            st.caption(f"{current_q} is partial through {report_end:%m/%d/%Y}; quarter-over-quarter narrative comparison is deferred until the quarter closes.")

        monthly = _monthly_combined_table(requested, scheduled, display_orgs, month_calendar)
        st.subheader("Medical Screening - Monthly Totals")
        st.dataframe(monthly, use_container_width=True, hide_index=True)

        monthly_trend = month_calendar[["Month", "Display Month", "Complete"]].copy()
        monthly_trend["Requested"] = monthly_trend["Month"].map(requested.groupby("Month").size()).fillna(0).astype(int)
        monthly_trend["Scheduled"] = monthly_trend["Month"].map(scheduled.groupby("Month").size()).fillna(0).astype(int)
        trend_long = monthly_trend.melt(
            id_vars=["Month", "Display Month", "Complete"],
            value_vars=["Requested", "Scheduled"],
            var_name="Metric",
            value_name="Count",
        )
        fig = px.line(
            trend_long,
            x="Display Month",
            y="Count",
            color="Metric",
            markers=True,
            title="Monthly Medical Screening Activity",
        )
        fig.update_layout(xaxis_title="", yaxis_title="Appointments", xaxis_tickangle=-45)
        st.plotly_chart(fig, use_container_width=True)

        comments = _rms_comments(requested, scheduled, display_orgs, month_calendar, request_backlog)
        if comments:
            st.markdown("#### Medical Screening observations")
            for comment in comments:
                st.markdown(f"- {comment}")

        st.divider()
        with st.expander("Fiscal-week summary"):
            weekly = _weekly_summary(requested, scheduled, week_calendar)
            st.dataframe(weekly, use_container_width=True, hide_index=True)
            completed_weekly = weekly[weekly["Complete"]]
            if not completed_weekly.empty:
                weekly_long = completed_weekly.melt(
                    id_vars=["Fiscal Week", "Week"],
                    value_vars=["Requested", "Scheduled"],
                    var_name="Metric",
                    value_name="Count",
                )
                weekly_fig = px.line(weekly_long, x="Week", y="Count", color="Metric", markers=True)
                weekly_fig.update_layout(xaxis_title="Fiscal Week", yaxis_title="Appointments", xaxis_tickangle=-45)
                st.plotly_chart(weekly_fig, use_container_width=True)

        if invoice_available:
            with st.expander("Invoice-based measures (available in this source export)"):
                invoice_scope = df[
                    df[invoice_date_col].notna()
                    & df[org_col].isin(display_orgs)
                    & (df[invoice_date_col] >= fy_start)
                    & (df[invoice_date_col] <= report_end)
                ].copy()
                invoice_scope["days_request_to_invoice"] = (
                    invoice_scope[invoice_date_col] - invoice_scope[requested_date_col]
                ).dt.days
                avg_days = invoice_scope["days_request_to_invoice"].mean()
                st.metric("Average Days Request to Invoice", "N/A" if pd.isna(avg_days) else f"{avg_days:.1f}")
                invoice_by_org = invoice_scope.groupby(org_col).size().rename("Invoiced").reset_index()
                st.dataframe(invoice_by_org, use_container_width=True, hide_index=True)

        with st.expander("Unscheduled requests and source detail"):
            if request_backlog.empty:
                st.success("No FYTD requested clients are currently missing a scheduled appointment in the selected extract.")
            else:
                backlog_cols = [
                    c for c in [
                        "Organization", "client_id", "name", requested_date_col,
                        scheduled_date_col, "clinic", "client_key",
                    ] if c in request_backlog.columns
                ]
                st.markdown("**Requested but not yet scheduled**")
                st.dataframe(request_backlog[backlog_cols], use_container_width=True, hide_index=True)
            st.markdown("**Cleaned Source Data**")
            st.dataframe(df, use_container_width=True, hide_index=True)

        weekly_export = _weekly_summary(requested, scheduled, week_calendar)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            summary.to_excel(writer, index=False, sheet_name="FYTD Summary")
            requested_quarterly.to_excel(writer, index=False, sheet_name="Quarterly Requested")
            scheduled_quarterly.to_excel(writer, index=False, sheet_name="Quarterly Scheduled")
            monthly.to_excel(writer, index=False, sheet_name="Monthly Totals")
            monthly_trend.to_excel(writer, index=False, sheet_name="Monthly Trend")
            weekly_export.to_excel(writer, index=False, sheet_name="Weekly Summary")
            week_calendar.to_excel(writer, index=False, sheet_name="Fiscal Week Calendar")
            month_calendar.to_excel(writer, index=False, sheet_name="Fiscal Month Calendar")
            request_backlog.to_excel(writer, index=False, sheet_name="Unscheduled Requests")
            requested.to_excel(writer, index=False, sheet_name="Requested Detail")
            scheduled.to_excel(writer, index=False, sheet_name="Scheduled Detail")
            if invoice_available:
                invoiced.to_excel(writer, index=False, sheet_name="Invoiced Detail")
            df.to_excel(writer, index=False, sheet_name="Cleaned Source")

        st.download_button(
            label="Download Medical Screening Executive Workbook",
            data=output.getvalue(),
            file_name="ksor_medical_screening_dashboard.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    except Exception as exc:
        st.exception(exc)
        st.error(f"Could not build Medical Screening dashboard: {exc}")


def main():
    render_rms_dashboard()


if __name__ == "__main__":
    main()
