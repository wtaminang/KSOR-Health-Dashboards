import io

import pandas as pd
import streamlit as st


# -----------------------------------------------------------------------------
# Fiscal-period helpers
# -----------------------------------------------------------------------------


def _fiscal_year_for_date(date_value):
    if pd.isna(date_value):
        return pd.NA
    return int(date_value.year + 1 if date_value.month >= 10 else date_value.year)


def _fy_start_for_number(fiscal_year: int) -> pd.Timestamp:
    return pd.Timestamp(year=int(fiscal_year) - 1, month=10, day=1)


def _add_fiscal_period_columns(
    dataframe: pd.DataFrame,
    date_column: str,
) -> pd.DataFrame:
    """Add FY, fiscal week, fiscal month, and fiscal quarter to an event table."""
    result = dataframe.copy()

    if result.empty:
        result["fiscal_year"] = pd.Series(dtype="Int64")
        result["fiscal_week"] = pd.Series(dtype="Int64")
        result["week_start"] = pd.Series(dtype="datetime64[ns]")
        result["week_end"] = pd.Series(dtype="datetime64[ns]")
        result["week_label"] = pd.Series(dtype="object")
        result["fiscal_month"] = pd.Series(dtype="Int64")
        result["month_label"] = pd.Series(dtype="object")
        result["fiscal_quarter"] = pd.Series(dtype="object")
        result["quarter_label"] = pd.Series(dtype="object")
        return result

    dates = pd.to_datetime(result[date_column], errors="coerce").dt.normalize()
    result[date_column] = dates
    result["fiscal_year"] = dates.apply(_fiscal_year_for_date).astype("Int64")

    fy_starts = result["fiscal_year"].apply(
        lambda fy: _fy_start_for_number(int(fy)) if pd.notna(fy) else pd.NaT
    )
    days_from_fy_start = (dates - fy_starts).dt.days

    result["fiscal_week"] = (days_from_fy_start // 7 + 1).astype("Int64")
    result["week_start"] = fy_starts + pd.to_timedelta(
        (result["fiscal_week"] - 1) * 7,
        unit="D",
    )
    fy_ends = fy_starts + pd.DateOffset(years=1) - pd.Timedelta(days=1)
    result["week_end"] = result["week_start"] + pd.Timedelta(days=6)
    result["week_end"] = result["week_end"].where(
        result["week_end"] <= fy_ends,
        fy_ends,
    )
    result["week_label"] = (
        "W"
        + result["fiscal_week"].astype(str).str.zfill(2)
        + ": "
        + result["week_start"].dt.strftime("%m/%d/%y")
        + " - "
        + result["week_end"].dt.strftime("%m/%d/%y")
    )

    result["fiscal_month"] = (
        ((dates.dt.month - 10) % 12) + 1
    ).astype("Int64")
    result["month_label"] = (
        "M"
        + result["fiscal_month"].astype(str).str.zfill(2)
        + ": "
        + dates.dt.strftime("%b %Y")
    )

    result["quarter_number"] = ((result["fiscal_month"] - 1) // 3 + 1).astype("Int64")
    result["fiscal_quarter"] = "Q" + result["quarter_number"].astype(str)

    quarter_starts = pd.Series(
        [
            fy_start + pd.DateOffset(months=(int(q) - 1) * 3)
            if pd.notna(fy_start) and pd.notna(q)
            else pd.NaT
            for fy_start, q in zip(fy_starts, result["quarter_number"])
        ],
        index=result.index,
        dtype="datetime64[ns]",
    )
    quarter_ends = quarter_starts + pd.DateOffset(months=3) - pd.Timedelta(days=1)
    result["quarter_label"] = (
        result["fiscal_quarter"]
        + ": "
        + quarter_starts.dt.strftime("%b")
        + "-"
        + quarter_ends.dt.strftime("%b %Y")
    )

    return result


def _append_grand_total(summary: pd.DataFrame, label_column: str) -> pd.DataFrame:
    """Append one Grand Total row, calculating rates/averages rather than summing them."""
    if summary.empty:
        return summary.copy()

    result = summary.copy()
    total_row = {col: "" for col in result.columns}
    total_row[label_column] = "Grand Total"

    count_columns = [
        "unique_clients",
        "requested",
        "scheduled",
        "awaiting_scheduling",
        "invoiced",
        "awaiting_invoice",
        "overdue_60_plus",
    ]
    for col in count_columns:
        if col in result.columns:
            total_row[col] = int(pd.to_numeric(result[col], errors="coerce").fillna(0).sum())

    if "scheduled_rate" in result.columns:
        denominator = total_row.get("requested", total_row.get("unique_clients", 0))
        numerator = total_row.get("scheduled", 0)
        total_row["scheduled_rate"] = numerator / denominator if denominator else 0

    if "invoice_rate" in result.columns:
        denominator = total_row.get("requested", total_row.get("unique_clients", 0))
        numerator = total_row.get("invoiced", 0)
        total_row["invoice_rate"] = numerator / denominator if denominator else 0

    for avg_col in ["avg_days_to_schedule", "avg_days_to_invoice"]:
        if avg_col in result.columns:
            total_row[avg_col] = pd.to_numeric(result[avg_col], errors="coerce").mean()

    if "risk_flag" in result.columns:
        total_row["risk_flag"] = ""

    return pd.concat([result, pd.DataFrame([total_row])], ignore_index=True)


def _build_event_table(
    unique_clients: pd.DataFrame,
    requested_date_col: str,
    scheduled_date_col: str,
    invoice_date_col: str,
    invoice_available: bool,
) -> pd.DataFrame:
    """Create one operational event row per Requested/Scheduled/Invoiced milestone."""
    event_frames = []

    requested = unique_clients.loc[
        unique_clients[requested_date_col].notna(),
        ["client_key", "clinic", requested_date_col],
    ].copy()
    requested = requested.rename(columns={requested_date_col: "event_date"})
    requested["stage"] = "Requested"
    event_frames.append(requested)

    scheduled = unique_clients.loc[
        unique_clients[scheduled_date_col].notna(),
        ["client_key", "clinic", scheduled_date_col],
    ].copy()
    scheduled = scheduled.rename(columns={scheduled_date_col: "event_date"})
    scheduled["stage"] = "Scheduled"
    event_frames.append(scheduled)

    if invoice_available:
        invoiced = unique_clients.loc[
            unique_clients[invoice_date_col].notna(),
            ["client_key", "clinic", invoice_date_col],
        ].copy()
        invoiced = invoiced.rename(columns={invoice_date_col: "event_date"})
        invoiced["stage"] = "Invoiced"
        event_frames.append(invoiced)

    if not event_frames:
        return pd.DataFrame(columns=["client_key", "clinic", "event_date", "stage"])

    events = pd.concat(event_frames, ignore_index=True)
    events = _add_fiscal_period_columns(events, "event_date")
    return events


def _event_summary(
    events: pd.DataFrame,
    period_column: str,
    period_order: list[str],
    clinics: list[str],
    invoice_available: bool,
) -> pd.DataFrame:
    """Summarize Requested/Scheduled/Invoiced event volume by period and clinic."""
    stages = ["Requested", "Scheduled"] + (["Invoiced"] if invoice_available else [])

    if events.empty:
        index = pd.MultiIndex.from_product(
            [period_order, clinics],
            names=[period_column, "clinic"],
        )
        pivot = pd.DataFrame(index=index, columns=stages).fillna(0)
    else:
        pivot = pd.pivot_table(
            events,
            index=[period_column, "clinic"],
            columns="stage",
            values="client_key",
            aggfunc=pd.Series.nunique,
            fill_value=0,
        )
        pivot = pivot.reindex(columns=stages, fill_value=0)
        target_index = pd.MultiIndex.from_product(
            [period_order, clinics],
            names=[period_column, "clinic"],
        )
        pivot = pivot.reindex(target_index, fill_value=0)

    summary = pivot.reset_index()
    for stage in stages:
        summary[stage.lower()] = summary[stage].astype(int)
        summary = summary.drop(columns=[stage])

    # These are stage-event counts, not cohort conversion rates. A Scheduled event
    # in a period can belong to a request from an earlier period, so no period
    # "rate" is calculated here (which avoids misleading values above 100%).

    # Add a total row for each reporting period.
    total_rows = []
    for period in period_order:
        period_rows = summary[summary[period_column] == period]
        row = {period_column: period, "clinic": "Grand Total"}
        row["requested"] = int(period_rows["requested"].sum())
        row["scheduled"] = int(period_rows["scheduled"].sum())
        if invoice_available:
            row["invoiced"] = int(period_rows["invoiced"].sum())
        total_rows.append(row)

    if total_rows:
        summary = pd.concat([summary, pd.DataFrame(total_rows)], ignore_index=True)

    return summary


def _quarter_window(fiscal_year: int, quarter_number: int):
    fy_start = _fy_start_for_number(fiscal_year)
    start = fy_start + pd.DateOffset(months=(quarter_number - 1) * 3)
    end = start + pd.DateOffset(months=3) - pd.Timedelta(days=1)
    return start.normalize(), end.normalize()


def _quarter_for_date(date_value: pd.Timestamp, fiscal_year: int) -> int:
    fy_start = _fy_start_for_number(fiscal_year)
    month_index = (date_value.year - fy_start.year) * 12 + date_value.month - fy_start.month
    return int(month_index // 3 + 1)


def _format_change(current_value: int, prior_value: int) -> str:
    difference = current_value - prior_value
    sign = "+" if difference > 0 else ""
    if prior_value == 0:
        if current_value == 0:
            return "no change (0 vs 0)"
        return f"{sign}{difference} clients; percentage change not shown because the prior period was 0"
    pct_change = difference / prior_value
    return f"{sign}{difference} clients ({pct_change:+.1%})"


def _quarter_commentary(
    events_all: pd.DataFrame,
    selected_fy: int,
    report_end: pd.Timestamp,
    selected_clinics: list[str],
    invoice_available: bool,
) -> str:
    """Create a concise, like-for-like current-quarter workload comment."""
    fy_start = _fy_start_for_number(selected_fy)
    fy_end = fy_start + pd.DateOffset(years=1) - pd.Timedelta(days=1)
    effective_end = min(max(report_end.normalize(), fy_start), fy_end)
    current_q = _quarter_for_date(effective_end, selected_fy)
    current_start, natural_end = _quarter_window(selected_fy, current_q)
    current_end = min(effective_end, natural_end)
    elapsed_days = (current_end - current_start).days

    events = events_all.copy()
    if selected_clinics:
        events = events[events["clinic"].isin(selected_clinics)]

    request_events = events[events["stage"] == "Requested"]
    current = request_events[
        (request_events["event_date"] >= current_start)
        & (request_events["event_date"] <= current_end)
    ]
    current_total = int(current["client_key"].nunique())

    if current_total:
        clinic_counts = current.groupby("clinic")["client_key"].nunique().sort_values(ascending=False)
        top_clinic = clinic_counts.index[0]
        top_count = int(clinic_counts.iloc[0])
        top_share = top_count / current_total
        line1 = (
            f"**Quarter note:** Q{current_q} has {current_total} RMS requests through "
            f"{current_end:%b %d}; {top_clinic} has the largest request volume "
            f"({top_count}, {top_share:.0%} of the total)."
        )
    else:
        line1 = (
            f"**Quarter note:** No RMS requests are recorded for Q{current_q} through "
            f"{current_end:%b %d}."
        )

    comparisons = []
    source_min = request_events["event_date"].min() if not request_events.empty else pd.NaT
    for offset in range(1, 4):
        prior_q_index = current_q - offset
        prior_fy = selected_fy
        while prior_q_index <= 0:
            prior_q_index += 4
            prior_fy -= 1
        prior_start, prior_natural_end = _quarter_window(prior_fy, prior_q_index)
        prior_end = min(prior_start + pd.Timedelta(days=elapsed_days), prior_natural_end)

        if pd.isna(source_min) or source_min.normalize() > prior_start:
            continue

        prior = request_events[
            (request_events["event_date"] >= prior_start)
            & (request_events["event_date"] <= prior_end)
        ]
        prior_total = int(prior["client_key"].nunique())
        comparisons.append(f"Q{prior_q_index}: {_format_change(current_total, prior_total)}")

    if comparisons:
        line2 = "Like-for-like request volume vs prior quarters — " + "; ".join(comparisons) + "."
    else:
        line2 = (
            "Comparable earlier quarters are not sufficiently represented in the uploaded file, "
            "so no quarter-over-quarter percentage is shown."
        )

    if current_end < natural_end:
        line2 += " The current quarter is partial and comparisons use the same elapsed number of days."

    return line1 + "  \n" + line2


# -----------------------------------------------------------------------------
# Dashboard
# -----------------------------------------------------------------------------


def render_rms_dashboard():
    st.header("KSOR RMS Tracker")
    st.caption(
        "Medical Screening request, scheduling, backlog, fiscal-period reporting, and executive exports. "
        "Fiscal weeks are fixed 7-day periods beginning October 1; Q1=Oct-Dec, Q2=Jan-Mar, "
        "Q3=Apr-Jun, and Q4=Jul-Sep."
    )

    uploaded_file = st.file_uploader(
        "Upload RMS Excel file",
        type=["xlsx"],
        key="rms_upload",
    )

    if uploaded_file is None:
        st.info("Upload your KSOR RMS Excel file to begin.")
        return

    try:
        df = pd.read_excel(uploaded_file)
    except Exception as exc:
        st.error(f"Could not read the RMS Excel file: {exc}")
        return

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

    # Invoice Date is intentionally OPTIONAL because the current ClientTrack export
    # may omit it. The dashboard still reports Requested/Scheduled activity safely.
    required_cols = [
        client_id_col,
        name_col,
        dob_col,
        org_col,
        requested_date_col,
        scheduled_date_col,
    ]
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        st.error(f"Missing required columns: {missing}")
        st.write("Detected columns:")
        st.write(list(df.columns))
        return

    invoice_available = invoice_date_col in df.columns
    if not invoice_available:
        df[invoice_date_col] = pd.NaT
        st.info(
            "Invoice Date is not present in this export. Requested and Scheduled reporting remains active; "
            "invoice-dependent metrics are marked unavailable rather than treated as zero."
        )

    for col in [dob_col, requested_date_col, scheduled_date_col, invoice_date_col]:
        df[col] = pd.to_datetime(df[col], errors="coerce")

    if clinic_col in df.columns:
        df["clinic"] = df[clinic_col].fillna(df[org_col])
    else:
        df["clinic"] = df[org_col]
    df["clinic"] = df["clinic"].astype(str).str.strip()

    df["client_key"] = df[client_id_col].astype(str).str.strip()
    missing_id = df["client_key"].isin(["", "nan", "None"])
    df.loc[missing_id, "client_key"] = (
        df.loc[missing_id, name_col].astype(str).str.upper().str.strip()
        + "_"
        + df.loc[missing_id, dob_col].astype(str)
    )

    # Stable cohort/reporting anchor: request date first, then schedule, then invoice.
    # This prevents a client's fiscal year from changing simply because a later stage occurs.
    df["reporting_date"] = df[requested_date_col]
    df["reporting_date"] = df["reporting_date"].fillna(df[scheduled_date_col])
    df["reporting_date"] = df["reporting_date"].fillna(df[invoice_date_col])
    df["fiscal_year"] = df["reporting_date"].apply(_fiscal_year_for_date).astype("Int64")

    df["scheduled"] = df[scheduled_date_col].notna()
    df["invoiced"] = df[invoice_date_col].notna() if invoice_available else False

    if invoice_available:
        df["status"] = "Awaiting Scheduling"
        df.loc[df["scheduled"], "status"] = "Scheduled / Awaiting Invoice"
        df.loc[df["invoiced"], "status"] = "Invoiced"
    else:
        df["status"] = "Awaiting Scheduling"
        df.loc[df["scheduled"], "status"] = "Scheduled"

    df["days_requested_to_schedule"] = (
        df[scheduled_date_col] - df[requested_date_col]
    ).dt.days
    df["days_requested_to_invoice"] = (
        df[invoice_date_col] - df[requested_date_col]
    ).dt.days if invoice_available else pd.NA

    unique_clients = (
        df.sort_values(by=["client_key", "reporting_date"])
        .drop_duplicates(subset=["client_key"], keep="last")
        .copy()
    )

    today = pd.Timestamp.today().normalize()
    unique_clients["backlog_start_date"] = pd.NaT
    awaiting_schedule_mask = ~unique_clients["scheduled"]
    unique_clients.loc[awaiting_schedule_mask, "backlog_start_date"] = unique_clients.loc[
        awaiting_schedule_mask, requested_date_col
    ]

    if invoice_available:
        awaiting_invoice_mask = unique_clients["scheduled"] & ~unique_clients["invoiced"]
        unique_clients.loc[awaiting_invoice_mask, "backlog_start_date"] = unique_clients.loc[
            awaiting_invoice_mask, scheduled_date_col
        ]

    unique_clients["days_pending"] = (
        today - unique_clients["backlog_start_date"]
    ).dt.days

    def backlog_bucket(days):
        if pd.isna(days):
            return "No Open Backlog"
        if days <= 30:
            return "0-30 days"
        if days <= 60:
            return "31-60 days"
        if days <= 90:
            return "61-90 days"
        return "90+ days"

    unique_clients["backlog_age_bucket"] = unique_clients["days_pending"].apply(backlog_bucket)
    unique_clients["overdue_flag"] = unique_clients["days_pending"].apply(
        lambda x: "Overdue 60+ days" if pd.notna(x) and x > 60 else "OK"
    )

    events_all = _build_event_table(
        unique_clients,
        requested_date_col,
        scheduled_date_col,
        invoice_date_col,
        invoice_available,
    )

    st.sidebar.header("RMS Filters")
    available_fys = sorted(unique_clients["fiscal_year"].dropna().astype(int).unique())
    if not available_fys:
        st.error("No valid reporting dates found.")
        return

    selected_fy = int(
        st.sidebar.selectbox(
            "RMS Fiscal Year",
            available_fys,
            index=len(available_fys) - 1,
            key="rms_fiscal_year",
        )
    )

    fy_start = _fy_start_for_number(selected_fy)
    fy_end = fy_start + pd.DateOffset(years=1) - pd.Timedelta(days=1)
    default_report_end = min(today, fy_end)
    report_end_input = st.sidebar.date_input(
        "Report through",
        value=default_report_end,
        min_value=fy_start.date(),
        max_value=fy_end.date(),
        key="rms_report_end",
    )
    report_end = pd.Timestamp(report_end_input).normalize()

    cohort_fy = unique_clients[
        (unique_clients["fiscal_year"] == selected_fy)
        & (unique_clients["reporting_date"] <= report_end)
    ].copy()

    clinics = sorted(cohort_fy["clinic"].dropna().unique())
    selected_clinics = st.sidebar.multiselect(
        "RMS Clinic",
        clinics,
        default=clinics,
        key="rms_clinic_filter",
    )
    display_clinics = selected_clinics if selected_clinics else clinics

    status_options = sorted(cohort_fy["status"].dropna().unique())
    selected_statuses = st.sidebar.multiselect(
        "RMS Status",
        status_options,
        default=status_options,
        key="rms_status_filter",
    )

    quarter_options = ["Q1", "Q2", "Q3", "Q4"]
    selected_quarters = st.sidebar.multiselect(
        "RMS Fiscal Quarter",
        quarter_options,
        default=quarter_options,
        key="rms_quarter_filter",
    )

    # Cohort-quarter derives from request/reporting anchor.
    cohort_fy = _add_fiscal_period_columns(cohort_fy, "reporting_date")
    filtered = cohort_fy.copy()
    if selected_clinics:
        filtered = filtered[filtered["clinic"].isin(selected_clinics)]
    if selected_statuses:
        filtered = filtered[filtered["status"].isin(selected_statuses)]
    if selected_quarters:
        filtered = filtered[filtered["fiscal_quarter"].isin(selected_quarters)]

    # Stage-event reporting respects FY, report-through date, and clinic filter.
    events_fy = events_all[
        (events_all["fiscal_year"] == selected_fy)
        & (events_all["event_date"] <= report_end)
    ].copy()
    if selected_clinics:
        events_fy = events_fy[events_fy["clinic"].isin(selected_clinics)]

    st.subheader(f"FY{selected_fy} Executive Snapshot")

    total_clients = int(filtered["client_key"].nunique())
    requested_clients = total_clients
    scheduled_clients = int(filtered.loc[filtered["scheduled"], "client_key"].nunique())
    awaiting_scheduling = int(filtered.loc[~filtered["scheduled"], "client_key"].nunique())
    scheduled_rate = scheduled_clients / requested_clients if requested_clients else 0
    overdue_clients = int(
        filtered.loc[filtered["overdue_flag"] == "Overdue 60+ days", "client_key"].nunique()
    )
    avg_days_schedule = pd.to_numeric(
        filtered["days_requested_to_schedule"], errors="coerce"
    ).mean()

    if invoice_available:
        invoiced_clients = int(filtered.loc[filtered["invoiced"], "client_key"].nunique())
        awaiting_invoice = int(
            filtered.loc[filtered["scheduled"] & ~filtered["invoiced"], "client_key"].nunique()
        )
        invoice_rate = invoiced_clients / requested_clients if requested_clients else 0
        avg_days_invoice = pd.to_numeric(
            filtered["days_requested_to_invoice"], errors="coerce"
        ).mean()

        cols = st.columns(8)
        cols[0].metric("Requested", requested_clients)
        cols[1].metric("Scheduled", scheduled_clients)
        cols[2].metric("Awaiting Scheduling", awaiting_scheduling)
        cols[3].metric("Scheduling Rate", f"{scheduled_rate:.1%}")
        cols[4].metric("Invoiced", invoiced_clients)
        cols[5].metric("Awaiting Invoice", awaiting_invoice)
        cols[6].metric("Invoice Rate", f"{invoice_rate:.1%}")
        cols[7].metric("Overdue 60+ Days", overdue_clients)
    else:
        cols = st.columns(6)
        cols[0].metric("Requested", requested_clients)
        cols[1].metric("Scheduled", scheduled_clients)
        cols[2].metric("Awaiting Scheduling", awaiting_scheduling)
        cols[3].metric("Scheduling Rate", f"{scheduled_rate:.1%}")
        cols[4].metric("Overdue 60+ Days", overdue_clients)
        cols[5].metric(
            "Avg Days Request→Schedule",
            "N/A" if pd.isna(avg_days_schedule) else f"{avg_days_schedule:.1f}",
        )

    st.divider()
    st.subheader("Clinic Performance Summary")

    clinic_summary = (
        filtered.groupby("clinic")
        .agg(
            unique_clients=("client_key", "nunique"),
            scheduled=("scheduled", "sum"),
            overdue_60_plus=(
                "overdue_flag",
                lambda x: (x == "Overdue 60+ days").sum(),
            ),
            avg_days_to_schedule=("days_requested_to_schedule", "mean"),
        )
        .reset_index()
    )
    clinic_summary["requested"] = clinic_summary["unique_clients"].astype(int)
    clinic_summary["scheduled"] = clinic_summary["scheduled"].astype(int)
    clinic_summary["awaiting_scheduling"] = (
        clinic_summary["requested"] - clinic_summary["scheduled"]
    ).astype(int)
    clinic_summary["scheduled_rate"] = (
        clinic_summary["scheduled"] / clinic_summary["requested"].replace(0, pd.NA)
    ).fillna(0)

    if invoice_available:
        invoice_by_clinic = (
            filtered.groupby("clinic")
            .agg(
                invoiced=("invoiced", "sum"),
                avg_days_to_invoice=("days_requested_to_invoice", "mean"),
            )
            .reset_index()
        )
        clinic_summary = clinic_summary.merge(invoice_by_clinic, on="clinic", how="left")
        clinic_summary["invoiced"] = clinic_summary["invoiced"].fillna(0).astype(int)
        clinic_summary["awaiting_invoice"] = (
            clinic_summary["scheduled"] - clinic_summary["invoiced"]
        ).clip(lower=0).astype(int)
        clinic_summary["invoice_rate"] = (
            clinic_summary["invoiced"] / clinic_summary["requested"].replace(0, pd.NA)
        ).fillna(0)

    # Risk focuses on open backlog. It does not interpret volume as care quality.
    clinic_summary["risk_flag"] = clinic_summary.apply(
        lambda row: "Needs Follow-up" if row["overdue_60_plus"] > 0 else "OK",
        axis=1,
    )

    desired_order = [
        "clinic",
        "requested",
        "scheduled",
        "awaiting_scheduling",
        "scheduled_rate",
        "avg_days_to_schedule",
    ]
    if invoice_available:
        desired_order += [
            "invoiced",
            "awaiting_invoice",
            "invoice_rate",
            "avg_days_to_invoice",
        ]
    desired_order += ["overdue_60_plus", "risk_flag"]
    clinic_summary = clinic_summary[[col for col in desired_order if col in clinic_summary.columns]]
    clinic_summary_display = _append_grand_total(clinic_summary, "clinic")
    st.dataframe(clinic_summary_display, use_container_width=True, hide_index=True)

    if not clinic_summary.empty:
        top_row = clinic_summary.sort_values("requested", ascending=False).iloc[0]
        top_share = top_row["requested"] / requested_clients if requested_clients else 0
        st.markdown(
            f"**FYTD note:** {top_row['clinic']} has the largest request volume "
            f"({int(top_row['requested'])}, {top_share:.0%} of filtered FYTD requests). "
            "This is a workload-volume indicator, not a quality ranking."
        )

    st.subheader("Automatic Alerts")
    alert_df = clinic_summary[clinic_summary["risk_flag"] != "OK"]
    if alert_df.empty:
        st.success("No 60+ day open-backlog alerts based on current filters.")
    else:
        st.warning("Some clinics have open records older than 60 days and may need follow-up.")
        st.dataframe(alert_df, use_container_width=True, hide_index=True)

    st.subheader("Backlog Aging Summary")
    backlog_summary = (
        filtered[filtered["backlog_start_date"].notna()]
        .groupby(["clinic", "backlog_age_bucket"])
        .agg(pending_clients=("client_key", "nunique"))
        .reset_index()
    )
    st.dataframe(backlog_summary, use_container_width=True, hide_index=True)

    if not backlog_summary.empty:
        backlog_chart = backlog_summary.pivot_table(
            index="clinic",
            columns="backlog_age_bucket",
            values="pending_clients",
            aggfunc="sum",
            fill_value=0,
        )
        st.bar_chart(backlog_chart)

    st.subheader("Open Backlog Detail")
    pending_backlog = filtered[filtered["backlog_start_date"].notna()].copy()
    pending_display_cols = [
        client_id_col,
        name_col,
        "clinic",
        requested_date_col,
        scheduled_date_col,
        invoice_date_col if invoice_available else None,
        "status",
        "backlog_start_date",
        "days_pending",
        "backlog_age_bucket",
        "overdue_flag",
    ]
    pending_display_cols = [
        col for col in pending_display_cols if col and col in pending_backlog.columns
    ]
    st.dataframe(pending_backlog[pending_display_cols], use_container_width=True, hide_index=True)

    st.subheader("Overdue 60+ Days Detail")
    overdue_detail = filtered[filtered["overdue_flag"] == "Overdue 60+ days"].copy()
    st.dataframe(overdue_detail[pending_display_cols], use_container_width=True, hide_index=True)

    # ------------------------------------------------------------------
    # Fiscal-period summaries
    # ------------------------------------------------------------------
    current_week_num = int(((report_end - fy_start).days // 7) + 1)
    week_numbers = list(range(1, current_week_num + 1))
    week_order = []
    for week_num in week_numbers:
        start = fy_start + pd.Timedelta(days=(week_num - 1) * 7)
        end = min(start + pd.Timedelta(days=6), fy_end)
        week_order.append(f"W{week_num:02d}: {start:%m/%d/%y} - {end:%m/%d/%y}")

    month_order = []
    for month_num in range(1, 13):
        start = fy_start + pd.DateOffset(months=month_num - 1)
        if start > report_end:
            break
        month_order.append(f"M{month_num:02d}: {start:%b %Y}")

    quarter_order = []
    for q in range(1, 5):
        q_start, _ = _quarter_window(selected_fy, q)
        if q_start <= report_end and (not selected_quarters or f"Q{q}" in selected_quarters):
            q_end = q_start + pd.DateOffset(months=3) - pd.Timedelta(days=1)
            quarter_order.append(f"Q{q}: {q_start:%b}-{q_end:%b %Y}")

    st.divider()
    st.subheader("Weekly Summary")
    weekly = _event_summary(
        events_fy,
        "week_label",
        week_order,
        display_clinics,
        invoice_available,
    )
    st.dataframe(weekly, use_container_width=True, hide_index=True)

    st.subheader("Monthly Summary")
    monthly = _event_summary(
        events_fy,
        "month_label",
        month_order,
        display_clinics,
        invoice_available,
    )
    st.dataframe(monthly, use_container_width=True, hide_index=True)

    st.subheader("Quarterly Summary")
    st.caption(
        "Q1 = Oct-Dec | Q2 = Jan-Mar | Q3 = Apr-Jun | Q4 = Jul-Sep. "
        "Grand Total rows show total event volume for each quarter."
    )
    quarterly = _event_summary(
        events_fy,
        "quarter_label",
        quarter_order,
        display_clinics,
        invoice_available,
    )
    st.dataframe(quarterly, use_container_width=True, hide_index=True)
    st.markdown(
        _quarter_commentary(
            events_all,
            selected_fy,
            report_end,
            display_clinics,
            invoice_available,
        )
    )

    # ------------------------------------------------------------------
    # Executive report
    # ------------------------------------------------------------------
    st.subheader("Executive Report")
    top_clinic = (
        clinic_summary.sort_values("requested", ascending=False)["clinic"].iloc[0]
        if not clinic_summary.empty
        else "N/A"
    )

    metric_names = [
        "Fiscal Year",
        "Report Through",
        "Requested",
        "Scheduled",
        "Awaiting Scheduling",
        "Scheduling Rate",
        "Overdue 60+ Days",
        "Average Days Request to Schedule",
        "Highest Request Volume Clinic",
        "Invoice Data Available",
    ]
    metric_values = [
        f"FY{selected_fy}",
        report_end.strftime("%m/%d/%Y"),
        requested_clients,
        scheduled_clients,
        awaiting_scheduling,
        f"{scheduled_rate:.1%}",
        overdue_clients,
        "N/A" if pd.isna(avg_days_schedule) else round(avg_days_schedule, 1),
        top_clinic,
        "Yes" if invoice_available else "No",
    ]

    if invoice_available:
        metric_names[6:6] = ["Invoiced", "Awaiting Invoice", "Invoice Rate", "Average Days Request to Invoice"]
        metric_values[6:6] = [
            invoiced_clients,
            awaiting_invoice,
            f"{invoice_rate:.1%}",
            "N/A" if pd.isna(avg_days_invoice) else round(avg_days_invoice, 1),
        ]

    executive_report = pd.DataFrame({"metric": metric_names, "value": metric_values})
    st.dataframe(executive_report, use_container_width=True, hide_index=True)

    st.subheader("Visual Dashboard")
    monthly_totals = monthly[monthly["clinic"] == "Grand Total"].copy()
    if not monthly_totals.empty:
        chart_cols = ["requested", "scheduled"] + (["invoiced"] if invoice_available else [])
        chart_data = monthly_totals.set_index("month_label")[chart_cols]
        st.markdown("### Monthly RMS Stage Activity")
        st.line_chart(chart_data)

    if not clinic_summary.empty:
        chart_cols = ["requested", "scheduled"] + (["invoiced"] if invoice_available else [])
        st.markdown("### RMS Stage Activity by Clinic")
        st.bar_chart(clinic_summary.set_index("clinic")[chart_cols])

    if not backlog_summary.empty:
        st.markdown("### Backlog Aging by Clinic")
        st.bar_chart(backlog_chart)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Cleaned_Data")
        unique_clients.to_excel(writer, index=False, sheet_name="Unique_Clients")
        filtered.to_excel(writer, index=False, sheet_name="Filtered_View")
        events_fy.to_excel(writer, index=False, sheet_name="Stage_Events")
        executive_report.to_excel(writer, index=False, sheet_name="Executive_Report")
        clinic_summary_display.to_excel(writer, index=False, sheet_name="Clinic_Summary")
        backlog_summary.to_excel(writer, index=False, sheet_name="Backlog_Summary")
        pending_backlog.to_excel(writer, index=False, sheet_name="Open_Backlog")
        overdue_detail.to_excel(writer, index=False, sheet_name="Overdue_60Plus")
        weekly.to_excel(writer, index=False, sheet_name="Weekly")
        monthly.to_excel(writer, index=False, sheet_name="Monthly")
        quarterly.to_excel(writer, index=False, sheet_name="Quarterly")

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
