import pandas as pd
import streamlit as st


def render_rma_dashboard():
    st.header("RMA Dashboard")
    st.caption(
        "Active caseload by nationality, Partner Agency, and 30/60/90 termination tracking."
    )

    uploaded_file = st.file_uploader(
        "Upload RMA Excel file",
        type=["xlsx"],
        key="rma_upload",
    )

    if uploaded_file is None:
        st.info("Upload your KSOR RMA Excel report to begin.")
        return

    df = pd.read_excel(uploaded_file, header=2)
    df.columns = df.columns.str.strip()

    required_columns = [
        "Enrollment Date",
        "Termination Date",
        "Local Resettlement Agency",
        "Nationality",
        "Alien Number",
    ]

    missing = [col for col in required_columns if col not in df.columns]

    if missing:
        st.error(f"Missing required columns: {missing}")
        st.write("Detected columns:")
        st.write(list(df.columns))
        return

    df["Enrollment Date"] = pd.to_datetime(
        df["Enrollment Date"], errors="coerce"
    )
    df["Termination Date"] = pd.to_datetime(
        df["Termination Date"], errors="coerce"
    )

    today = pd.Timestamp.today().normalize()

    df["Active"] = (
        df["Enrollment Date"].notna()
        & (
            df["Termination Date"].isna()
            | (df["Termination Date"] >= today)
        )
    )

    active_df = df[df["Active"]].copy()

    active_df["Days to Termination"] = (
        active_df["Termination Date"] - today
    ).dt.days

    def termination_bucket(days):
        if pd.isna(days):
            return "No Termination Date"
        if days < 0:
            return "Already Terminated"
        if days <= 30:
            return "0-30 Days"
        if days <= 60:
            return "31-60 Days"
        if days <= 90:
            return "61-90 Days"
        return "Over 90 Days"

    active_df["Termination Bucket"] = active_df[
        "Days to Termination"
    ].apply(termination_bucket)

    st.sidebar.header("RMA Filters")

    agency_options = ["All"] + sorted(
        active_df["Local Resettlement Agency"]
        .dropna()
        .unique()
        .tolist()
    )

    selected_agency = st.sidebar.selectbox(
        "RMA Partner Agency",
        agency_options,
        key="rma_agency_filter",
    )

    nationality_options = ["All"] + sorted(
        active_df["Nationality"].dropna().unique().tolist()
    )

    selected_nationality = st.sidebar.selectbox(
        "RMA Nationality",
        nationality_options,
        key="rma_nationality_filter",
    )

    filtered_df = active_df.copy()

    if selected_agency != "All":
        filtered_df = filtered_df[
            filtered_df["Local Resettlement Agency"] == selected_agency
        ]

    if selected_nationality != "All":
        filtered_df = filtered_df[
            filtered_df["Nationality"] == selected_nationality
        ]

    term_30 = filtered_df[
        (filtered_df["Days to Termination"].notna())
        & (filtered_df["Days to Termination"] >= 0)
        & (filtered_df["Days to Termination"] <= 30)
    ]

    term_60 = filtered_df[
        (filtered_df["Days to Termination"].notna())
        & (filtered_df["Days to Termination"] >= 31)
        & (filtered_df["Days to Termination"] <= 60)
    ]

    term_90 = filtered_df[
        (filtered_df["Days to Termination"].notna())
        & (filtered_df["Days to Termination"] >= 61)
        & (filtered_df["Days to Termination"] <= 90)
    ]

    term_next_90 = filtered_df[
        (filtered_df["Days to Termination"].notna())
        & (filtered_df["Days to Termination"] >= 0)
        & (filtered_df["Days to Termination"] <= 90)
    ].copy()

    term_next_90 = term_next_90.sort_values("Days to Termination")

    st.subheader("Key Metrics")

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Total Active Cases", len(filtered_df))
    col2.metric("Terminating in 0-30 Days", len(term_30))
    col3.metric("Terminating in 31-60 Days", len(term_60))
    col4.metric("Terminating in 61-90 Days", len(term_90))

    st.divider()

    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        st.subheader("Active Cases by Nationality")

        nationality_chart = (
            filtered_df["Nationality"]
            .fillna("Unknown")
            .value_counts()
            .reset_index()
        )

        nationality_chart.columns = ["Nationality", "Active Cases"]

        st.bar_chart(nationality_chart.set_index("Nationality"))

    with chart_col2:
        st.subheader("Active Cases by Partner Agency")

        agency_chart = (
            filtered_df["Local Resettlement Agency"]
            .fillna("Unknown")
            .value_counts()
            .reset_index()
        )

        agency_chart.columns = ["Partner Agency", "Active Cases"]

        st.bar_chart(agency_chart.set_index("Partner Agency"))

    st.divider()

    st.subheader("30 / 60 / 90 Day Termination Dashboard")

    if not term_next_90.empty:
        termination_by_agency = term_next_90.pivot_table(
            index="Local Resettlement Agency",
            columns="Termination Bucket",
            values="Alien Number",
            aggfunc="count",
            fill_value=0,
        )

        for column in ["0-30 Days", "31-60 Days", "61-90 Days"]:
            if column not in termination_by_agency.columns:
                termination_by_agency[column] = 0

        termination_by_agency = termination_by_agency[
            ["0-30 Days", "31-60 Days", "61-90 Days"]
        ]

        st.bar_chart(termination_by_agency)
    else:
        st.info("No active cases terminating in the next 90 days.")

    st.divider()

    st.subheader("Termination Action Queue: Next 90 Days")

    display_columns = [
        "First Name",
        "Last Name",
        "Alien Number",
        "Nationality",
        "Local Resettlement Agency",
        "Termination Date",
        "Days to Termination",
        "Termination Bucket",
    ]

    available_columns = [
        col for col in display_columns if col in term_next_90.columns
    ]

    if available_columns and not term_next_90.empty:
        st.dataframe(
            term_next_90[available_columns],
            use_container_width=True,
            hide_index=True,
        )

        csv = term_next_90[available_columns].to_csv(index=False).encode("utf-8")

        st.download_button(
            label="Download RMA Termination Queue as CSV",
            data=csv,
            file_name="rma_termination_queue_next_90_days.csv",
            mime="text/csv",
        )
    else:
        st.info("No termination queue records available.")


def main():
    render_rma_dashboard()


if __name__ == "__main__":
    main()