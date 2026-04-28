import pandas as pd
import streamlit as st


def render_rhp_dashboard():
    st.header("RHP Dashboard")
    st.caption(
        "FYTD enrollment and services by partner agency. Enrollment count is deduplicated by Agency + Client ID + Enroll Date; services count is row-level service activity."
    )

    # Sidebar inputs
    st.sidebar.header("RHP inputs")

    fy_start = st.sidebar.date_input("FY start", value=pd.to_datetime("2025-10-01"))
    report_end = st.sidebar.date_input("Report through", value=pd.to_datetime("2026-04-27"))

    enroll_file = st.sidebar.file_uploader(
        "RHP enrollment report",
        type=["xlsx"],
        key="rhp_enroll_upload",
    )

    service_file = st.sidebar.file_uploader(
        "RHP services report",
        type=["xlsx"],
        key="rhp_service_upload",
    )

    if enroll_file is None or service_file is None:
        st.info("Please upload both RHP enrollment and services files.")
        return

    try:
        enroll = pd.read_excel(enroll_file)
        services = pd.read_excel(service_file)

        # Clean
        for df in [enroll, services]:
            df.columns = df.columns.str.strip()

        enroll["Enroll Date"] = pd.to_datetime(enroll["Enroll Date"], errors="coerce")
        services["Service Date"] = pd.to_datetime(services["Service Date"], errors="coerce")

        # Filter FY
        enroll_fy = enroll[
            (enroll["Enroll Date"] >= pd.to_datetime(fy_start))
            & (enroll["Enroll Date"] <= pd.to_datetime(report_end))
        ]

        services_fy = services[
            (services["Service Date"] >= pd.to_datetime(fy_start))
            & (services["Service Date"] <= pd.to_datetime(report_end))
        ]

        # Deduplicate enrollments
        enroll_fy = enroll_fy.drop_duplicates(
            subset=["Agency", "Client ID", "Enroll Date"]
        )

        # Executive table
        exec_table = (
            enroll_fy.groupby("Agency")
            .size()
            .rename("FYTD Enrollments")
            .to_frame()
            .join(
                services_fy.groupby("Agency")
                .size()
                .rename("FYTD Services"),
                how="outer",
            )
            .fillna(0)
            .astype(int)
            .reset_index()
            .rename(columns={"Agency": "Partner Agency"})
        )

        st.subheader("Executive Summary")
        st.dataframe(exec_table, use_container_width=True)

    except Exception as e:
        st.error(f"Error building RHP dashboard: {e}")


def main():
    render_rhp_dashboard()


if __name__ == "__main__":
    main()
