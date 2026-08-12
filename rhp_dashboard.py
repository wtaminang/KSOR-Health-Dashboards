import io

import pandas as pd
import plotly.express as px
import streamlit as st


DASHBOARD_VERSION = "KSOR Master Report 2026-08-12"
MASTER_VIEW = "FYTD + Last Month + Last Week | Monthly | Quarterly | Program Insights"


FISCAL_QUARTERS = {
    10: "Q1", 11: "Q1", 12: "Q1",
    1: "Q2", 2: "Q2", 3: "Q2",
    4: "Q3", 5: "Q3", 6: "Q3",
    7: "Q4", 8: "Q4", 9: "Q4",
}


def _build_fiscal_week_calendar(
    fy_start: pd.Timestamp,
    report_end: pd.Timestamp,
    fy_end: pd.Timestamp,
) -> pd.DataFrame:
    """Create consecutive 7-day fiscal weeks anchored to the FY start date."""
    number_of_weeks = ((report_end - fy_start).days // 7) + 1
    week_numbers = pd.Series(range(1, number_of_weeks + 1), dtype="int64")
    week_starts = fy_start + pd.to_timedelta((week_numbers - 1) * 7, unit="D")
    week_ends = (week_starts + pd.Timedelta(days=6)).where(
        week_starts + pd.Timedelta(days=6) <= fy_end,
        fy_end,
    )

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
    calendar["Complete"] = calendar["Week End"] <= report_end
    return calendar


def _month_calendar(
    fy_start: pd.Timestamp,
    report_end: pd.Timestamp,
) -> pd.DataFrame:
    """Return all fiscal-year months from FY start through the report month."""
    starts = pd.date_range(
        start=fy_start.replace(day=1),
        end=report_end.replace(day=1),
        freq="MS",
    )
    calendar = pd.DataFrame({"Month Start": starts})
    calendar["Month End"] = calendar["Month Start"] + pd.offsets.MonthEnd(0)
    calendar["Fiscal Month"] = range(1, len(calendar) + 1)
    calendar["Month"] = calendar["Month Start"].dt.strftime("%b %Y")
    calendar["Complete"] = calendar["Month End"] <= report_end
    calendar["Display Month"] = calendar["Month"]
    if not calendar.empty and not bool(calendar.iloc[-1]["Complete"]):
        calendar.loc[calendar.index[-1], "Display Month"] += " (MTD)"
    return calendar


def _add_period_columns(
    dataframe: pd.DataFrame,
    date_column: str,
    fy_start: pd.Timestamp,
    fy_end: pd.Timestamp,
) -> pd.DataFrame:
    """Add fiscal week, month, and quarter columns to a record-level table."""
    result = dataframe.copy()

    if result.empty:
        result["Fiscal Week"] = pd.Series(dtype="Int64")
        result["Week Start"] = pd.Series(dtype="datetime64[ns]")
        result["Week End"] = pd.Series(dtype="datetime64[ns]")
        result["Week"] = pd.Series(dtype="object")
        result["Month Start"] = pd.Series(dtype="datetime64[ns]")
        result["Month"] = pd.Series(dtype="object")
        result["Fiscal Quarter"] = pd.Series(dtype="object")
        return result

    normalized = result[date_column].dt.normalize()
    days_from_start = (normalized - fy_start).dt.days
    result["Fiscal Week"] = (days_from_start // 7 + 1).astype("Int64")
    result["Week Start"] = fy_start + pd.to_timedelta(
        (result["Fiscal Week"] - 1) * 7,
        unit="D",
    )
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
    result["Month Start"] = normalized.dt.to_period("M").dt.to_timestamp()
    result["Month"] = result["Month Start"].dt.strftime("%b %Y")
    result["Fiscal Quarter"] = normalized.dt.month.map(FISCAL_QUARTERS)
    return result


def _add_grand_total_row(table: pd.DataFrame, label_col: str) -> pd.DataFrame:
    """Append a Grand Total row and preserve integer count columns."""
    result = table.copy()
    numeric_cols = [col for col in result.columns if col != label_col]
    grand = {label_col: "Grand Total"}
    for col in numeric_cols:
        grand[col] = int(pd.to_numeric(result[col], errors="coerce").fillna(0).sum())
    return pd.concat([result, pd.DataFrame([grand])], ignore_index=True)


def _count_pivot(
    dataframe: pd.DataFrame,
    agencies: list[str],
    period_col: str,
    period_order: list[str],
    label_col: str = "Partner Agency",
    add_row_total: bool = True,
) -> pd.DataFrame:
    """Build agency-by-period count table with zero periods, row totals and Grand Total."""
    if dataframe.empty:
        pivot = pd.DataFrame(0, index=agencies, columns=period_order)
    else:
        pivot = pd.pivot_table(
            dataframe,
            index="Agency",
            columns=period_col,
            values="Client ID",
            aggfunc="count",
            fill_value=0,
        )
        pivot = pivot.reindex(index=agencies, columns=period_order, fill_value=0)

    pivot = pivot.astype(int)
    if add_row_total:
        pivot["FYTD Total"] = pivot.sum(axis=1)
    pivot.index.name = label_col
    result = pivot.reset_index()
    return _add_grand_total_row(result, label_col)


def _latest_completed_month(month_calendar: pd.DataFrame):
    completed = month_calendar[month_calendar["Complete"]]
    if completed.empty:
        return None
    return completed.iloc[-1]


def _latest_completed_week(week_calendar: pd.DataFrame):
    completed = week_calendar[week_calendar["Complete"]]
    if completed.empty:
        return None
    return completed.iloc[-1]


def _period_count_by_agency(
    dataframe: pd.DataFrame,
    date_col: str,
    agencies: list[str],
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
) -> pd.Series:
    if start is None or end is None:
        return pd.Series(0, index=agencies, dtype="int64")
    scoped = dataframe[(dataframe[date_col] >= start) & (dataframe[date_col] <= end)]
    return scoped.groupby("Agency").size().reindex(agencies, fill_value=0).astype(int)


def _enrollment_opening_summary(
    enroll_fy: pd.DataFrame,
    agencies: list[str],
    fy_start: pd.Timestamp,
    report_end: pd.Timestamp,
    month_calendar: pd.DataFrame,
    week_calendar: pd.DataFrame,
) -> tuple[pd.DataFrame, str, str]:
    """FYTD + latest completed month + latest completed fiscal week only."""
    last_month = _latest_completed_month(month_calendar)
    last_week = _latest_completed_week(week_calendar)

    last_month_label = "Last Month"
    month_counts = pd.Series(0, index=agencies, dtype="int64")
    if last_month is not None:
        last_month_label = f"Last Month\n{last_month['Month']}"
        month_counts = _period_count_by_agency(
            enroll_fy,
            "Enroll Date",
            agencies,
            last_month["Month Start"],
            last_month["Month End"],
        )

    last_week_label = "Last Week"
    week_counts = pd.Series(0, index=agencies, dtype="int64")
    if last_week is not None:
        last_week_label = (
            "Last Week\n"
            f"{last_week['Week Start']:%m/%d/%y}-{last_week['Week End']:%m/%d/%y}"
        )
        week_counts = _period_count_by_agency(
            enroll_fy,
            "Enroll Date",
            agencies,
            last_week["Week Start"],
            last_week["Week End"],
        )

    fytd_counts = (
        enroll_fy.groupby("Agency").size().reindex(agencies, fill_value=0).astype(int)
    )
    summary = pd.DataFrame(
        {
            "Partner Agency": agencies,
            f"FY YTD\n{fy_start:%m/%d/%y}-{report_end:%m/%d/%y}": fytd_counts.values,
            last_month_label: month_counts.values,
            last_week_label: week_counts.values,
        }
    )
    return _add_grand_total_row(summary, "Partner Agency"), last_month_label, last_week_label


def _service_week_detail(
    services_fy: pd.DataFrame,
    week_row,
    agencies: list[str],
) -> pd.DataFrame:
    """Create current-week service detail with agency subtotals and Grand Total."""
    columns = ["Agency & Service", "Count of Service"]
    if week_row is None:
        return pd.DataFrame(columns=columns)

    scoped = services_fy[
        (services_fy["Service Date"] >= week_row["Week Start"])
        & (services_fy["Service Date"] <= week_row["Week End"])
    ].copy()

    service_col = "Service" if "Service" in scoped.columns else None
    rows = []
    for agency in agencies:
        agency_df = scoped[scoped["Agency"] == agency]
        if agency_df.empty:
            continue
        if service_col:
            counts = agency_df.groupby(service_col).size().sort_values(ascending=False)
            for service, count in counts.items():
                rows.append({"Agency & Service": f"{agency} — {service}", "Count of Service": int(count)})
        else:
            rows.append({"Agency & Service": agency, "Count of Service": int(len(agency_df))})
        rows.append({"Agency & Service": f"{agency} Total", "Count of Service": int(len(agency_df))})

    rows.append({"Agency & Service": "Grand Total", "Count of Service": int(len(scoped))})
    return pd.DataFrame(rows, columns=columns)


def _pct_change(current: int, prior: int) -> str:
    if prior == 0:
        return "percentage change is not meaningful because the prior value was zero"
    pct = (current - prior) / prior * 100
    return f"{pct:+.1f}%"


def _render_insights(title: str, comments: list[str]):
    """Render concise, visible program interpretation directly beneath its table."""
    if not comments:
        return
    st.markdown(f"#### {title}")
    for comment in comments:
        st.markdown(f"- {comment}")


def _monthly_grand_totals(
    dataframe: pd.DataFrame,
    month_calendar: pd.DataFrame,
) -> pd.DataFrame:
    labels = month_calendar["Month"].tolist()
    counts = dataframe.groupby("Month").size().reindex(labels, fill_value=0).astype(int)
    return pd.DataFrame(
        {
            "Month": month_calendar["Month"].values,
            "Display Month": month_calendar["Display Month"].values,
            "Count": counts.values,
            "Complete": month_calendar["Complete"].values,
        }
    )


def _monthly_trend_statement(monthly: pd.DataFrame, metric: str) -> str:
    if monthly.empty:
        return "No monthly activity is available for the selected reporting period."

    completed = monthly[monthly["Complete"]].copy()
    basis = completed if len(completed) >= 2 else monthly.copy()
    if basis.empty:
        return "Insufficient monthly history is available to describe a trend."

    max_row = basis.loc[basis["Count"].idxmax()]
    min_row = basis.loc[basis["Count"].idxmin()]
    last = basis.iloc[-1]
    first = basis.iloc[0]

    direction = "higher" if last["Count"] > first["Count"] else "lower" if last["Count"] < first["Count"] else "unchanged"
    return (
        f"Across completed FY months, {metric.lower()} peaked at {int(max_row['Count'])} in {max_row['Month']} "
        f"and were lowest at {int(min_row['Count'])} in {min_row['Month']}. "
        f"The latest completed month ({last['Month']}) recorded {int(last['Count'])}, {direction} than the "
        f"{int(first['Count'])} reported in the first completed FY month."
    )


def _rhp_comments(
    enroll_fy: pd.DataFrame,
    services_fy: pd.DataFrame,
    agencies: list[str],
    month_calendar: pd.DataFrame,
    week_calendar: pd.DataFrame,
) -> tuple[list[str], list[str]]:
    """Generate concise SRHC-style observations aligned with the master report."""
    enrollment_comments: list[str] = []
    service_comments: list[str] = []

    # --- Enrollment: FYTD pattern + latest completed-month direction.
    total_enroll = int(len(enroll_fy))
    if total_enroll:
        by_agency = enroll_fy.groupby("Agency").size().sort_values(ascending=False)
        top_agency = by_agency.index[0]
        top_count = int(by_agency.iloc[0])
        second_text = ""
        if len(by_agency) > 1:
            second_agency = by_agency.index[1]
            second_count = int(by_agency.iloc[1])
            second_text = (
                f", followed by {second_agency} with {second_count} "
                f"({second_count / total_enroll:.1%})"
            )
        enrollment_comments.append(
            f"FYTD enrollment is concentrated in {top_agency}, which accounts for {top_count} of "
            f"{total_enroll} enrollments ({top_count / total_enroll:.1%}){second_text}."
        )

    completed_months = month_calendar[month_calendar["Complete"]]
    if len(completed_months) >= 2:
        current = completed_months.iloc[-1]
        prior = completed_months.iloc[-2]
        current_scope = enroll_fy[
            (enroll_fy["Enroll Date"] >= current["Month Start"])
            & (enroll_fy["Enroll Date"] <= current["Month End"])
        ]
        prior_scope = enroll_fy[
            (enroll_fy["Enroll Date"] >= prior["Month Start"])
            & (enroll_fy["Enroll Date"] <= prior["Month End"])
        ]
        current_count = int(len(current_scope))
        prior_count = int(len(prior_scope))
        top_month_text = ""
        if current_count:
            current_by_agency = current_scope.groupby("Agency").size().sort_values(ascending=False)
            top_month_agency = current_by_agency.index[0]
            top_month_count = int(current_by_agency.iloc[0])
            top_month_text = (
                f" {top_month_agency} contributed {top_month_count} of {current_count} "
                f"({top_month_count / current_count:.1%}) in {current['Month']}."
            )
        enrollment_comments.append(
            f"The latest completed month recorded {current_count} enrollments versus {prior_count} in "
            f"{prior['Month']} ({current_count - prior_count:+d}; {_pct_change(current_count, prior_count)})."
            + top_month_text
        )

    # --- Services: current week cross-agency/service mix + 3-week trend + monthly/FYTD context.
    completed_weeks = week_calendar[week_calendar["Complete"]]
    if not completed_weeks.empty:
        current_week = completed_weeks.iloc[-1]
        current_scope = services_fy[
            (services_fy["Service Date"] >= current_week["Week Start"])
            & (services_fy["Service Date"] <= current_week["Week End"])
        ]
        current_total = int(len(current_scope))
        if current_total:
            by_agency = current_scope.groupby("Agency").size().sort_values(ascending=False)
            top_agency = by_agency.index[0]
            top_count = int(by_agency.iloc[0])
            agency_parts = [
                f"{agency} {int(count)} ({int(count) / current_total:.1%})"
                for agency, count in by_agency.items()
            ]
            service_mix = ""
            if "Service" in current_scope.columns and not current_scope["Service"].dropna().empty:
                by_service = current_scope.groupby("Service").size().sort_values(ascending=False)
                top_service = str(by_service.index[0])
                top_service_count = int(by_service.iloc[0])
                # Shorten the most common standard RHP prefix for readability.
                top_service_short = top_service.replace("RHP - ", "")
                service_mix = (
                    f" {top_service_short} remained the predominant service type "
                    f"({top_service_count} of {current_total}; {top_service_count / current_total:.1%})."
                )
            service_comments.append(
                f"For {current_week['Week Start']:%m/%d/%y}-{current_week['Week End']:%m/%d/%y}, "
                f"{top_agency} led reported activity with {top_count} of {current_total} services "
                f"({top_count / current_total:.1%}); across partners, "
                + ", ".join(agency_parts)
                + "."
                + service_mix
            )

        if len(completed_weeks) >= 3:
            last3 = completed_weeks.tail(3)
            totals = []
            for _, row in last3.iterrows():
                count = int(
                    (
                        (services_fy["Service Date"] >= row["Week Start"])
                        & (services_fy["Service Date"] <= row["Week End"])
                    ).sum()
                )
                totals.append((row, count))
            older, prior, current = totals
            service_comments.append(
                f"Short-term service activity was {current[1]} this week, compared with {prior[1]} the "
                f"previous week ({current[1] - prior[1]:+d}; {_pct_change(current[1], prior[1])}) and "
                f"{older[1]} two weeks earlier ({current[1] - older[1]:+d}; "
                f"{_pct_change(current[1], older[1])}). This indicates a modest pullback from the prior-week "
                f"peak while remaining above the level two weeks earlier."
            )

    monthly_text = ""
    if len(completed_months) >= 2:
        current = completed_months.iloc[-1]
        prior = completed_months.iloc[-2]
        current_count = int(
            (
                (services_fy["Service Date"] >= current["Month Start"])
                & (services_fy["Service Date"] <= current["Month End"])
            ).sum()
        )
        prior_count = int(
            (
                (services_fy["Service Date"] >= prior["Month Start"])
                & (services_fy["Service Date"] <= prior["Month End"])
            ).sum()
        )
        monthly_text = (
            f"The latest completed month was relatively stable at {current_count} services versus {prior_count} "
            f"in {prior['Month']} ({current_count - prior_count:+d}; {_pct_change(current_count, prior_count)})."
        )

    total_services = int(len(services_fy))
    fytd_text = ""
    if total_services:
        by_agency = services_fy.groupby("Agency").size().sort_values(ascending=False)
        top_agency = by_agency.index[0]
        top_count = int(by_agency.iloc[0])
        second_text = ""
        if len(by_agency) > 1:
            second_agency = by_agency.index[1]
            second_count = int(by_agency.iloc[1])
            second_text = (
                f", closely followed by {second_agency} with {second_count} "
                f"({second_count / total_services:.1%})"
            )
        fytd_text = (
            f"FYTD, {top_agency} has the largest service volume at {top_count} of {total_services} "
            f"({top_count / total_services:.1%}){second_text}."
        )

    combined_context = " ".join(part for part in [monthly_text, fytd_text] if part)
    if combined_context:
        service_comments.append(combined_context)

    return enrollment_comments[:3], service_comments[:3]


def render_rhp_dashboard():
    st.header("RHP Dashboard")
    st.caption(f"Dashboard build: {DASHBOARD_VERSION}")
    st.sidebar.caption(f"RHP build: {DASHBOARD_VERSION}")
    st.sidebar.caption(MASTER_VIEW)
    st.caption(
        "FYTD, monthly, quarterly and fiscal-week reporting for RHP enrollments and services. "
        "Enrollments are deduplicated by Agency + Client ID + Enroll Date; services are row-level activities."
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
        st.sidebar.warning(f"Report through was limited to FY end: {fy_end:%m/%d/%Y}.")
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
        enroll.columns = enroll.columns.astype(str).str.strip()
        services.columns = services.columns.astype(str).str.strip()

        required_enroll = ["Agency", "Client ID", "Enroll Date"]
        required_services = ["Agency", "Client ID", "Service Date"]
        missing_enroll = [c for c in required_enroll if c not in enroll.columns]
        missing_services = [c for c in required_services if c not in services.columns]
        if missing_enroll:
            st.error(f"Enrollment file missing columns: {missing_enroll}")
            st.write("Detected enrollment columns:", list(enroll.columns))
            return
        if missing_services:
            st.error(f"Services file missing columns: {missing_services}")
            st.write("Detected services columns:", list(services.columns))
            return

        for frame in (enroll, services):
            frame["Agency"] = frame["Agency"].astype(str).str.strip()
            frame["Client ID"] = frame["Client ID"].astype(str).str.strip()
        enroll["Enroll Date"] = pd.to_datetime(enroll["Enroll Date"], errors="coerce")
        services["Service Date"] = pd.to_datetime(services["Service Date"], errors="coerce")

        # Keep a source-history copy before FY filtering so future prior-FY comparisons remain possible.
        enroll_source = enroll.dropna(subset=["Enroll Date"]).copy()
        services_source = services.dropna(subset=["Service Date"]).copy()

        enroll_fy = enroll_source[
            (enroll_source["Enroll Date"] >= fy_start)
            & (enroll_source["Enroll Date"] <= report_end)
        ].copy()
        services_fy = services_source[
            (services_source["Service Date"] >= fy_start)
            & (services_source["Service Date"] <= report_end)
        ].copy()

        enroll_fy = enroll_fy.drop_duplicates(subset=["Agency", "Client ID", "Enroll Date"])

        all_agencies = sorted(
            set(enroll_fy["Agency"].dropna().unique())
            | set(services_fy["Agency"].dropna().unique())
        )
        if not all_agencies:
            st.warning("No RHP records fall within the selected fiscal-year/report-through period.")
            return

        selected_agencies = st.sidebar.multiselect(
            "RHP Partner Agency",
            all_agencies,
            default=all_agencies,
            key="rhp_agency_filter",
        )
        display_agencies = selected_agencies if selected_agencies else all_agencies
        enroll_fy = enroll_fy[enroll_fy["Agency"].isin(display_agencies)].copy()
        services_fy = services_fy[services_fy["Agency"].isin(display_agencies)].copy()

        enroll_fy = _add_period_columns(enroll_fy, "Enroll Date", fy_start, fy_end)
        services_fy = _add_period_columns(services_fy, "Service Date", fy_start, fy_end)

        week_calendar = _build_fiscal_week_calendar(fy_start, report_end, fy_end)
        month_calendar = _month_calendar(fy_start, report_end)
        week_order = week_calendar["Week"].tolist()
        month_order = month_calendar["Month"].tolist()
        quarter_order = ["Q1", "Q2", "Q3", "Q4"]

        enroll_summary = enroll_fy.groupby("Agency").size().reindex(display_agencies, fill_value=0)
        service_summary = services_fy.groupby("Agency").size().reindex(display_agencies, fill_value=0)
        exec_table = pd.DataFrame(
            {
                "Partner Agency": display_agencies,
                "FYTD Enrollments": enroll_summary.values.astype(int),
                "FYTD Services": service_summary.values.astype(int),
            }
        )
        exec_table = _add_grand_total_row(exec_table, "Partner Agency")

        total_enrollments = int(len(enroll_fy))
        total_services = int(len(services_fy))
        latest_week = _latest_completed_week(week_calendar)
        latest_week_services = 0
        if latest_week is not None:
            latest_week_services = int(
                ((services_fy["Service Date"] >= latest_week["Week Start"])
                 & (services_fy["Service Date"] <= latest_week["Week End"])).sum()
            )

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("FYTD Enrollments", total_enrollments)
        c2.metric("FYTD Services", total_services)
        c3.metric("Last Complete Week Services", latest_week_services)
        c4.metric("Partner Agencies", len(display_agencies))

        st.divider()
        st.subheader("FYTD Executive Summary")
        st.dataframe(exec_table, use_container_width=True, hide_index=True)

        enrollment_opening, _, _ = _enrollment_opening_summary(
            enroll_fy,
            display_agencies,
            fy_start,
            report_end,
            month_calendar,
            week_calendar,
        )
        st.divider()
        st.subheader("RHP New Enrollments")
        st.caption("FY YTD | Last completed month | Last completed fiscal week")
        st.dataframe(enrollment_opening, use_container_width=True, hide_index=True)

        enrollment_comments, service_comments = _rhp_comments(
            enroll_fy,
            services_fy,
            display_agencies,
            month_calendar,
            week_calendar,
        )
        _render_insights("Program Insights — Enrollments", enrollment_comments)

        enrollment_monthly = _count_pivot(
            enroll_fy,
            display_agencies,
            "Month",
            month_order,
        )
        # Rename current month for display when partial.
        month_rename = dict(zip(month_calendar["Month"], month_calendar["Display Month"]))
        enrollment_monthly = enrollment_monthly.rename(columns=month_rename)

        st.subheader("RHP New Enrollments - Monthly Totals")
        st.dataframe(enrollment_monthly, use_container_width=True, hide_index=True)

        enroll_month_trend = _monthly_grand_totals(enroll_fy, month_calendar)
        fig_enroll = px.line(
            enroll_month_trend,
            x="Display Month",
            y="Count",
            markers=True,
            title="Monthly RHP Enrollment Trend",
        )
        fig_enroll.update_layout(xaxis_title="", yaxis_title="Enrollments", xaxis_tickangle=-45)
        st.plotly_chart(fig_enroll, use_container_width=True)

        enrollment_quarterly = _count_pivot(
            enroll_fy,
            display_agencies,
            "Fiscal Quarter",
            quarter_order,
        )
        st.subheader("RHP New Enrollments - Quarterly Totals")
        st.dataframe(enrollment_quarterly, use_container_width=True, hide_index=True)
        current_q = FISCAL_QUARTERS[report_end.month]
        quarter_end_month = {"Q1": 12, "Q2": 3, "Q3": 6, "Q4": 9}[current_q]
        quarter_end_year = fy_start.year if current_q == "Q1" else fy_start.year + 1
        quarter_end = pd.Timestamp(quarter_end_year, quarter_end_month, 1) + pd.offsets.MonthEnd(0)
        if report_end < quarter_end:
            st.caption(f"{current_q} is partial through {report_end:%m/%d/%Y}; quarter-over-quarter interpretation is deferred until the quarter closes.")

        st.divider()
        latest_week = _latest_completed_week(week_calendar)
        latest_week_label = "Most Recent Completed Week"
        if latest_week is not None:
            latest_week_label = f"{latest_week['Week Start']:%m/%d/%y}-{latest_week['Week End']:%m/%d/%y}"
        st.subheader(f"RHP Services {latest_week_label}")
        current_service_detail = _service_week_detail(services_fy, latest_week, display_agencies)
        st.dataframe(current_service_detail, use_container_width=True, hide_index=True)
        _render_insights("Program Insights — Services", service_comments)

        services_monthly = _count_pivot(
            services_fy,
            display_agencies,
            "Month",
            month_order,
        ).rename(columns=month_rename)
        st.subheader("RHP Services - Monthly Totals")
        st.dataframe(services_monthly, use_container_width=True, hide_index=True)

        services_month_trend = _monthly_grand_totals(services_fy, month_calendar)
        st.markdown(_monthly_trend_statement(services_month_trend, "RHP services"))
        fig_services = px.line(
            services_month_trend,
            x="Display Month",
            y="Count",
            markers=True,
            title="Monthly RHP Services Trend",
        )
        fig_services.update_layout(xaxis_title="", yaxis_title="Services", xaxis_tickangle=-45)
        st.plotly_chart(fig_services, use_container_width=True)

        services_quarterly = _count_pivot(
            services_fy,
            display_agencies,
            "Fiscal Quarter",
            quarter_order,
        )
        st.subheader("RHP Services - Quarterly Totals")
        st.dataframe(services_quarterly, use_container_width=True, hide_index=True)
        if report_end < quarter_end:
            st.caption(f"{current_q} is partial through {report_end:%m/%d/%Y}; quarterly totals remain visible but the current quarter is incomplete.")

        st.divider()
        with st.expander("Fiscal-week tables and trend"):
            weekly_enroll = _count_pivot(enroll_fy, display_agencies, "Week", week_order)
            weekly_services = _count_pivot(services_fy, display_agencies, "Week", week_order)
            st.markdown("**Weekly Enrollments**")
            st.dataframe(weekly_enroll, use_container_width=True, hide_index=True)
            st.markdown("**Weekly Services**")
            st.dataframe(weekly_services, use_container_width=True, hide_index=True)

            weekly_trend = week_calendar[["Fiscal Week", "Week", "Week Start", "Week End", "Complete"]].copy()
            service_counts = services_fy.groupby("Fiscal Week").size()
            enrollment_counts = enroll_fy.groupby("Fiscal Week").size()
            weekly_trend["Services"] = weekly_trend["Fiscal Week"].map(service_counts).fillna(0).astype(int)
            weekly_trend["Enrollments"] = weekly_trend["Fiscal Week"].map(enrollment_counts).fillna(0).astype(int)
            st.dataframe(weekly_trend, use_container_width=True, hide_index=True)

            completed_trend = weekly_trend[weekly_trend["Complete"]]
            if not completed_trend.empty:
                trend_long = completed_trend.melt(
                    id_vars=["Fiscal Week", "Week"],
                    value_vars=["Services", "Enrollments"],
                    var_name="Metric",
                    value_name="Count",
                )
                fig_week = px.line(trend_long, x="Week", y="Count", color="Metric", markers=True)
                fig_week.update_layout(xaxis_title="Fiscal Week", yaxis_title="Count", xaxis_tickangle=-45)
                st.plotly_chart(fig_week, use_container_width=True)

        with st.expander("Record-level detail"):
            enrollment_detail_cols = [
                c for c in [
                    "Agency", "Client ID", "Client Name", "First Name", "Last Name",
                    "Enroll Date", "Fiscal Week", "Week Start", "Week End", "Week",
                    "Month", "Fiscal Quarter",
                ] if c in enroll_fy.columns
            ]
            service_detail_cols = [
                c for c in [
                    "Agency", "Client ID", "Client Name", "First Name", "Last Name",
                    "Service Date", "Service", "Service Type", "Fiscal Week", "Week Start",
                    "Week End", "Week", "Month", "Fiscal Quarter",
                ] if c in services_fy.columns
            ]
            st.markdown("**Enrollment Detail**")
            st.dataframe(enroll_fy[enrollment_detail_cols], use_container_width=True, hide_index=True)
            st.markdown("**Services Detail**")
            st.dataframe(services_fy[service_detail_cols], use_container_width=True, hide_index=True)

        # Excel executive workbook mirrors the master reporting architecture.
        weekly_enroll_export = _count_pivot(enroll_fy, display_agencies, "Week", week_order)
        weekly_services_export = _count_pivot(services_fy, display_agencies, "Week", week_order)
        weekly_trend_export = week_calendar[["Fiscal Week", "Week", "Week Start", "Week End", "Complete"]].copy()
        weekly_trend_export["Services"] = weekly_trend_export["Fiscal Week"].map(services_fy.groupby("Fiscal Week").size()).fillna(0).astype(int)
        weekly_trend_export["Enrollments"] = weekly_trend_export["Fiscal Week"].map(enroll_fy.groupby("Fiscal Week").size()).fillna(0).astype(int)

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            exec_table.to_excel(writer, index=False, sheet_name="Executive Summary")
            enrollment_opening.to_excel(writer, index=False, sheet_name="Enrollment Snapshot")
            enrollment_monthly.to_excel(writer, index=False, sheet_name="Enrollment Monthly")
            enrollment_quarterly.to_excel(writer, index=False, sheet_name="Enrollment Quarterly")
            current_service_detail.to_excel(writer, index=False, sheet_name="Current Week Services")
            services_monthly.to_excel(writer, index=False, sheet_name="Services Monthly")
            services_quarterly.to_excel(writer, index=False, sheet_name="Services Quarterly")
            weekly_enroll_export.to_excel(writer, index=False, sheet_name="Weekly Enrollments")
            weekly_services_export.to_excel(writer, index=False, sheet_name="Weekly Services")
            weekly_trend_export.to_excel(writer, index=False, sheet_name="Weekly Trend")
            week_calendar.to_excel(writer, index=False, sheet_name="Fiscal Week Calendar")
            month_calendar.to_excel(writer, index=False, sheet_name="Fiscal Month Calendar")
            enroll_fy.to_excel(writer, index=False, sheet_name="Enrollment Detail")
            services_fy.to_excel(writer, index=False, sheet_name="Services Detail")

        st.download_button(
            label="Download RHP Executive Workbook",
            data=output.getvalue(),
            file_name="rhp_executive_dashboard.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        st.caption(f"{DASHBOARD_VERSION} • {MASTER_VIEW}")

    except Exception as exc:
        st.exception(exc)
        st.error(f"Could not build RHP dashboard: {exc}")


def main():
    render_rhp_dashboard()


if __name__ == "__main__":
    main()
