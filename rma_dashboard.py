"""
KSOR Refugee Medical Assistance (RMA) Dashboard
Version: 2026-08-17

Purpose
-------
Standalone Streamlit dashboard and reusable module for the KSOR Health Update.
It implements the final KSOR RMA Dashboard Concept Note agreed on 8/17/2026.

Required packages
-----------------
streamlit
pandas
plotly
openpyxl

Recommended repo layout
-----------------------
app.py / ksor_rma_dashboard.py
/data/ct_export.xlsx
/data/ered_active.xlsx
/data/ered_comprehensive.xlsx

The app also accepts files through Streamlit uploaders if the local files are not present.
No personally identifiable client-level fields are displayed; the dashboard is aggregate only.
"""

from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple, Union

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st


# =============================================================================
# 1. CONFIGURATION / PLANNING ASSUMPTIONS
# =============================================================================

DEFAULT_REPORT_DATE = pd.Timestamp("2026-08-17")
DEFAULT_AUGUST_ARRIVALS_MTD = 8  # operational count supplied by KSOR on 8/17/2026
RMA_EIGHT_MONTH_POLICY_START = pd.Timestamp("2026-01-01")
FY27_START = pd.Timestamp("2026-10-01")
FY27_END = pd.Timestamp("2027-09-30")

# Final Concept Note planning benchmarks. The dashboard also recalculates observed
# rates from uploaded CT/eRED files and shows any material difference in Data QA.
CN_ALL_ARRIVAL_CONVERSION = 0.410
CN_PRIMARY_CONVERSION = 0.434
CN_SECONDARY_CONVERSION = 0.344

# U.S. Department of State / Refugee Processing Center historical Kansas share
# of U.S. primary refugee arrivals, as incorporated into the final Concept Note.
KS_SHARE_HISTORY = {
    "FY2020": 0.0106,
    "FY2021": 0.0145,
    "FY2022": 0.0135,
    "FY2023": 0.0131,
}
KS_SHARE_LOW = 0.0100
KS_SHARE_BASE = 0.0129
KS_SHARE_HIGH = 0.0150

# Near-term planning example used in the approved 8/17/2026 dashboard visual.
DEFAULT_NATIONAL_MONTHLY_REFUGEE_ARRIVALS = 2500

# FY22 RMA enrollment is not supported by the current comprehensive eRED file.
# If KSOR later locates an FY22 RMA source, insert the verified unduplicated value here.
FY22_RMA_MANUAL: Optional[int] = None

# Local file candidates. The shorter names are recommended for the GitHub repo.
LOCAL_FILE_CANDIDATES = {
    "ct": [
        "data/ct_export.xlsx",
        "ct_export.xlsx",
        "data/CT_Export Question All Clients from FY 2022 till date 2026-08-17.xlsx",
        "CT_Export Question All Clients from FY 2022 till date 2026-08-17.xlsx",
    ],
    "active": [
        "data/ered_active.xlsx",
        "ered_active.xlsx",
        "data/eRED_Active Report-All-Agency08-17-2026.XLSX",
        "eRED_Active Report-All-Agency08-17-2026.XLSX",
    ],
    "comp": [
        "data/ered_comprehensive.xlsx",
        "ered_comprehensive.xlsx",
        "data/eRED_COMPREHENSIVE Report -All-Agency_10.01.2022_08.17.2026.xlsx",
        "eRED_COMPREHENSIVE Report -All-Agency_10.01.2022_08.17.2026.xlsx",
    ],
}

PARTNER_MAP = {
    "International Rescue Committee - Wichita": "IRC-Wichita",
    "International Rescue Committee - Kansas City": "IRC-Kansas City",
    "Catholic Charities of Southwest Kansas": "CCSWKS",
    "Manhattan Area Resettlement Team (MART)": "MART",
    "Catholic Charities of Northeast Kansas": "CCNEK",
}

# Statuses that should NOT be automatically pushed into RMA solely because of
# the 10/1/2026 Medicaid policy transition. Keep this mapping configurable.
MEDICAID_RETAINED_STATUS_PATTERNS = [
    r"Cuban/Haitian Entrant",
    r"Lawful Permanent Resident",
    r"Conditional Permanent Resident",
    r"COFA",
]


# =============================================================================
# 2. HELPERS
# =============================================================================

ExcelSource = Union[bytes, bytearray, io.BytesIO, str, Path]


def fiscal_year(value: pd.Timestamp) -> Optional[int]:
    if pd.isna(value):
        return None
    value = pd.Timestamp(value)
    return value.year + 1 if value.month >= 10 else value.year


def normalize_alien_number(value) -> Optional[str]:
    """Create a safe matching key without exposing it in dashboard outputs."""
    if pd.isna(value):
        return None
    text = str(value).strip()
    text = re.sub(r"\.0$", "", text)
    digits = re.sub(r"\D", "", text)
    if not digits or set(digits) == {"0"}:
        return None
    return digits


def normalize_partner(value) -> str:
    if pd.isna(value) or not str(value).strip():
        return "Unknown/Unassigned"
    value = str(value).strip()
    return PARTNER_MAP.get(value, value)


def pct(value: Optional[float], decimals: int = 1) -> str:
    if value is None or pd.isna(value):
        return "TBD"
    return f"{value * 100:.{decimals}f}%"


def pretty_int(value) -> str:
    if value is None or pd.isna(value):
        return "TBD"
    return f"{int(round(value)):,}"


def approximate_range(value: float) -> str:
    """For values such as 3.47, return ≈3–4; for integers, return ≈3."""
    low = math.floor(value)
    high = math.ceil(value)
    if low == high:
        return f"≈{low}"
    return f"≈{low}–{high}"


def month_label(period: pd.Period) -> str:
    return pd.Timestamp(period.start_time).strftime("%b %Y")


def _source_to_bytes(source: ExcelSource) -> bytes:
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    if isinstance(source, io.BytesIO):
        return source.getvalue()
    return Path(source).read_bytes()


def _find_header_row(file_bytes: bytes, sheet_name=0, expected="Alien Number", scan_rows=12) -> int:
    raw = pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet_name, header=None, nrows=scan_rows)
    for idx, row in raw.iterrows():
        values = {str(v).strip() for v in row.tolist() if pd.notna(v)}
        if expected in values:
            return int(idx)
    raise ValueError(f"Could not find '{expected}' in the first {scan_rows} rows.")


def read_excel_auto_header(source: ExcelSource, preferred_sheet: Optional[str] = None) -> pd.DataFrame:
    file_bytes = _source_to_bytes(source)
    xl = pd.ExcelFile(io.BytesIO(file_bytes))
    sheet = preferred_sheet if preferred_sheet in xl.sheet_names else xl.sheet_names[0]
    header_row = _find_header_row(file_bytes, sheet_name=sheet)
    df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet, header=header_row)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _get_local_bytes(candidates: Iterable[str]) -> Tuple[Optional[bytes], Optional[str]]:
    for candidate in candidates:
        p = Path(candidate)
        if p.exists() and p.is_file():
            return p.read_bytes(), str(p)
    return None, None


def resolve_source(uploaded_file, candidates: Iterable[str]) -> Tuple[Optional[bytes], Optional[str]]:
    if uploaded_file is not None:
        return uploaded_file.getvalue(), uploaded_file.name
    return _get_local_bytes(candidates)


# =============================================================================
# 3. DATA PREPARATION
# =============================================================================

@dataclass
class PreparedRMAData:
    ct: pd.DataFrame
    active: pd.DataFrame
    comp: pd.DataFrame
    enrolled_episodes: pd.DataFrame
    first_enrollments: pd.DataFrame
    ct_clients: pd.DataFrame


def prepare_data(ct_source: ExcelSource, active_source: ExcelSource, comp_source: ExcelSource) -> PreparedRMAData:
    ct = read_excel_auto_header(ct_source, preferred_sheet="Table1")
    active = read_excel_auto_header(active_source, preferred_sheet="data")
    comp = read_excel_auto_header(comp_source, preferred_sheet="data")

    required_ct = {
        "Alien Number", "Secondary Migrant", "DOA to State", "DOA in the USA",
        "Date of ORR Eligibility", "Immigration Status"
    }
    required_ered = {
        "Status", "Alien Number", "Date of Eligibility (DOE)", "Enrollment Date",
        "Termination Date", "Local Resettlement Provider"
    }
    missing_ct = sorted(required_ct - set(ct.columns))
    missing_active = sorted(required_ered - set(active.columns))
    missing_comp = sorted(required_ered - set(comp.columns))
    if missing_ct or missing_active or missing_comp:
        raise ValueError(
            "Missing required columns. "
            f"CT: {missing_ct or 'OK'}; Active: {missing_active or 'OK'}; "
            f"Comprehensive: {missing_comp or 'OK'}"
        )

    for df in (ct, active, comp):
        df["_alien_key"] = df["Alien Number"].map(normalize_alien_number)

    ct_date_cols = ["DOA to State", "DOA in the USA", "Date of ORR Eligibility"]
    ered_date_cols = [
        "Date of Eligibility (DOE)", "Application Date", "Enrollment Date",
        "Effective Date", "Termination Date", "Early Termination Date",
        "Secondary Migration Date"
    ]
    for col in ct_date_cols:
        if col in ct.columns:
            ct[col] = pd.to_datetime(ct[col], errors="coerce")
    for df in (active, comp):
        for col in ered_date_cols:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")

    active["Partner"] = active["Local Resettlement Provider"].map(normalize_partner)
    comp["Partner"] = comp["Local Resettlement Provider"].map(normalize_partner)

    # Enrolled episodes = all rows containing an Enrollment Date and an enrolled status.
    enrolled_episodes = comp[
        comp["Enrollment Date"].notna()
        & comp["Status"].astype(str).str.contains("Enrolled", case=False, na=False)
    ].copy()
    enrolled_episodes["_source_row"] = enrolled_episodes.index
    enrolled_episodes["Enrollment FY"] = enrolled_episodes["Enrollment Date"].map(fiscal_year)

    # First-time enrollee = first enrollment for each valid Alien Number.
    # For same-day duplicate records, preserve the earliest source row. This reconciles
    # the FY26 duplicate provider record without double-counting the client.
    first_enrollments = (
        enrolled_episodes
        .dropna(subset=["_alien_key"])
        .sort_values(["_alien_key", "Enrollment Date", "_source_row"])
        .drop_duplicates(subset=["_alien_key"], keep="first")
        .copy()
    )
    first_enrollments["Enrollment FY"] = first_enrollments["Enrollment Date"].map(fiscal_year)
    first_enrollments["Partner"] = first_enrollments["Local Resettlement Provider"].map(normalize_partner)

    # CT client-level file. Invalid/missing identifiers remain available for arrival totals
    # but are not used for RMA linkage. For duplicate valid IDs, retain earliest source row.
    ct["_source_row"] = ct.index
    ct["Is Secondary"] = (
        ct["Secondary Migrant"].astype(str).str.strip().str.lower().eq("yes")
    )
    ct["Kansas Arrival Date"] = ct["DOA in the USA"]
    sec_mask = ct["Is Secondary"] & ct["DOA to State"].notna()
    ct.loc[sec_mask, "Kansas Arrival Date"] = ct.loc[sec_mask, "DOA to State"]
    ct["Arrival FY"] = ct["Kansas Arrival Date"].map(fiscal_year)

    valid_ct = ct[ct["_alien_key"].notna()].copy()
    invalid_ct = ct[ct["_alien_key"].isna()].copy()
    valid_ct = valid_ct.sort_values(["_alien_key", "_source_row"]).drop_duplicates("_alien_key", keep="first")
    ct_clients = pd.concat([valid_ct, invalid_ct], ignore_index=True, sort=False)

    return PreparedRMAData(
        ct=ct,
        active=active,
        comp=comp,
        enrolled_episodes=enrolled_episodes,
        first_enrollments=first_enrollments,
        ct_clients=ct_clients,
    )


# =============================================================================
# 4. METRICS
# =============================================================================

@dataclass
class DashboardMetrics:
    report_date: pd.Timestamp
    current_fy: int
    active_clients: pd.DataFrame
    active_count: int
    carry_forward_active: pd.DataFrame
    carry_forward_count: int
    fy26_new: pd.DataFrame
    fy26_new_count: int
    fy26_reenrollment_episodes: int
    monthly_counts: pd.Series
    quarterly_counts: Dict[str, int]
    annual_counts: Dict[str, Optional[int]]
    partner_table: pd.DataFrame
    secondary_fy26_count: int
    secondary_rma_fy26_count: int
    observed_conversion_all: Optional[float]
    observed_conversion_primary: Optional[float]
    observed_conversion_secondary: Optional[float]
    conversion_detail: pd.DataFrame
    last7: int
    prior7: int
    current_month_count: int
    prior_month_count: int
    data_qa: Dict[str, int]


def calculate_metrics(data: PreparedRMAData, report_date: pd.Timestamp) -> DashboardMetrics:
    report_date = pd.Timestamp(report_date).normalize()
    current_fy = fiscal_year(report_date)

    # ---- Current active caseload ----
    active_clients = data.active[
        data.active["Status"].astype(str).str.contains("Enrolled-Active", case=False, na=False)
    ].copy()
    active_clients["Policy Adjusted End Date"] = active_clients["Termination Date"]
    policy_mask = active_clients["Date of Eligibility (DOE)"] >= RMA_EIGHT_MONTH_POLICY_START
    active_clients.loc[policy_mask, "Policy Adjusted End Date"] = (
        active_clients.loc[policy_mask, "Date of Eligibility (DOE)"] + pd.DateOffset(months=8)
    )
    carry_forward_active = active_clients[
        active_clients["Policy Adjusted End Date"] > pd.Timestamp("2026-09-30")
    ].copy()

    # ---- First-time RMA enrollment ----
    fy_new = data.first_enrollments[data.first_enrollments["Enrollment FY"] == current_fy].copy()
    fy_new = fy_new[fy_new["Enrollment Date"] <= report_date]

    # FY26 extension/re-enrollment episodes = enrolled episodes in FY26 minus unduplicated
    # first-time FY26 clients. This is a process metric, not added to active/new counts.
    episodes_fy = data.enrolled_episodes[
        (data.enrolled_episodes["Enrollment FY"] == current_fy)
        & (data.enrolled_episodes["Enrollment Date"] <= report_date)
    ]
    reenrollment_episodes = max(0, len(episodes_fy) - len(fy_new))

    # ---- Monthly FY26 ----
    fy_start = pd.Timestamp(year=current_fy - 1, month=10, day=1)
    month_end = report_date.to_period("M")
    months = pd.period_range(fy_start.to_period("M"), month_end, freq="M")
    month_series = (
        fy_new.assign(Month=fy_new["Enrollment Date"].dt.to_period("M"))
        .groupby("Month")
        .size()
        .reindex(months, fill_value=0)
    )

    # ---- Quarterly FY26 ----
    def quarter_for_month(month: int) -> str:
        if month in (10, 11, 12):
            return "Q1 Oct–Dec"
        if month in (1, 2, 3):
            return "Q2 Jan–Mar"
        if month in (4, 5, 6):
            return "Q3 Apr–Jun"
        return "Q4 Jul–Sep"

    q_counts = {q: 0 for q in ["Q1 Oct–Dec", "Q2 Jan–Mar", "Q3 Apr–Jun", "Q4 Jul–Sep"]}
    if not fy_new.empty:
        q = fy_new["Enrollment Date"].dt.month.map(quarter_for_month)
        for key, value in q.value_counts().items():
            q_counts[key] = int(value)

    # ---- Annual enrollment trend ----
    annual_counts: Dict[str, Optional[int]] = {
        "FY22": FY22_RMA_MANUAL,
        "FY23": int((data.first_enrollments["Enrollment FY"] == 2023).sum()),
        "FY24": int((data.first_enrollments["Enrollment FY"] == 2024).sum()),
        "FY25": int((data.first_enrollments["Enrollment FY"] == 2025).sum()),
        "FY26 YTD": int(len(fy_new)),
    }

    # ---- Partner table ----
    partner_order = ["IRC-Wichita", "IRC-Kansas City", "CCSWKS", "MART"]
    partners = set(active_clients["Partner"].dropna()) | set(fy_new["Partner"].dropna())
    partners = [p for p in partner_order if p in partners] + sorted(p for p in partners if p not in partner_order)

    july_start = pd.Timestamp("2026-07-01")
    july_end = pd.Timestamp("2026-07-31")
    month_start = report_date.replace(day=1)
    last7_start = report_date - pd.Timedelta(days=6)

    rows = []
    for partner in partners:
        p_new = fy_new[fy_new["Partner"] == partner]
        rows.append({
            "Partner Agency": partner,
            "Active RMA": int((active_clients["Partner"] == partner).sum()),
            "FY26 New Enrollees": int(len(p_new)),
            "July": int(p_new["Enrollment Date"].between(july_start, july_end).sum()),
            "Aug MTD": int(p_new["Enrollment Date"].between(month_start, report_date).sum()),
            "Last 7 Days": int(p_new["Enrollment Date"].between(last7_start, report_date).sum()),
        })
    partner_table = pd.DataFrame(rows)
    if not partner_table.empty:
        total = {
            "Partner Agency": "KSOR Total",
            "Active RMA": int(partner_table["Active RMA"].sum()),
            "FY26 New Enrollees": int(partner_table["FY26 New Enrollees"].sum()),
            "July": int(partner_table["July"].sum()),
            "Aug MTD": int(partner_table["Aug MTD"].sum()),
            "Last 7 Days": int(partner_table["Last 7 Days"].sum()),
        }
        partner_table = pd.concat([partner_table, pd.DataFrame([total])], ignore_index=True)

    # ---- Recent trend ----
    last7 = int(fy_new["Enrollment Date"].between(last7_start, report_date).sum())
    prior7_start = report_date - pd.Timedelta(days=13)
    prior7_end = report_date - pd.Timedelta(days=7)
    prior7 = int(fy_new["Enrollment Date"].between(prior7_start, prior7_end).sum())
    current_month_count = int(fy_new["Enrollment Date"].between(month_start, report_date).sum())
    prior_period = (report_date - pd.DateOffset(months=1)).to_period("M")
    prior_month_count = int(month_series.get(prior_period, 0))

    # ---- CT secondary migration ----
    ct_clients = data.ct_clients.copy()
    secondary_fy26 = ct_clients[
        (ct_clients["Arrival FY"] == current_fy) & ct_clients["Is Secondary"]
    ].copy()

    # Link FY26 first-time RMA clients to CT secondary flag.
    ct_link = (
        ct_clients[ct_clients["_alien_key"].notna()]
        [["_alien_key", "Is Secondary", "Immigration Status", "Kansas Arrival Date", "Arrival FY"]]
        .drop_duplicates("_alien_key", keep="first")
    )
    fy_new_linked = fy_new.merge(ct_link, on="_alien_key", how="left", suffixes=("_eRED", "_CT"))
    secondary_rma_fy26_count = int(fy_new_linked["Is Secondary"].eq(True).sum())

    # ---- Historical RMA conversion, completed FY23-FY25 arrival cohorts ----
    first_enroll_map = (
        data.first_enrollments
        .dropna(subset=["_alien_key"])
        .set_index("_alien_key")["Enrollment Date"]
        .to_dict()
    )
    cohorts = ct_clients[ct_clients["Arrival FY"].isin([2023, 2024, 2025])].copy()
    cohorts["RMA First Enrollment"] = cohorts["_alien_key"].map(first_enroll_map)
    cohorts["Converted to RMA"] = cohorts["RMA First Enrollment"].notna()

    conversion_rows = []
    for fy_value in [2023, 2024, 2025]:
        c = cohorts[cohorts["Arrival FY"] == fy_value]
        conversion_rows.append({
            "Arrival FY": f"FY{str(fy_value)[-2:]}",
            "All KS Arrivals": int(len(c)),
            "RMA Enrollees": int(c["Converted to RMA"].sum()),
            "Conversion": c["Converted to RMA"].mean() if len(c) else np.nan,
        })
    conversion_detail = pd.DataFrame(conversion_rows)

    observed_all = cohorts["Converted to RMA"].mean() if len(cohorts) else None
    primary = cohorts[~cohorts["Is Secondary"]]
    secondary = cohorts[cohorts["Is Secondary"]]
    observed_primary = primary["Converted to RMA"].mean() if len(primary) else None
    observed_secondary = secondary["Converted to RMA"].mean() if len(secondary) else None

    # ---- Data QA counts (aggregate only) ----
    duplicate_enrollment_person_date = int(
        data.enrolled_episodes.dropna(subset=["_alien_key"])
        .duplicated(subset=["_alien_key", "Enrollment Date"], keep=False)
        .sum()
    )
    fy_new_unmatched_ct = int(fy_new_linked["Arrival FY"].isna().sum())
    secondary_conflicts = 0
    if "Secondary Migrant" in fy_new_linked.columns and "Is Secondary" in fy_new_linked.columns:
        ered_secondary = fy_new_linked["Secondary Migrant"].astype(str).str.contains("Secondary Migrant", case=False, na=False)
        # eRED value "Not Secondary Migrant" also contains the words, so fix explicitly.
        ered_secondary = fy_new_linked["Secondary Migrant"].astype(str).str.strip().str.lower().eq("secondary migrant")
        secondary_conflicts = int((ered_secondary != fy_new_linked["Is Secondary"].eq(True)).sum())

    data_qa = {
        "FY26 enrolled episodes": int(len(episodes_fy)),
        "FY26 unduplicated first-time enrollees": int(len(fy_new)),
        "FY26 re-enrollment/extension episodes": int(reenrollment_episodes),
        "Duplicate same-person/same-date enrolled rows": duplicate_enrollment_person_date,
        "FY26 first-time RMA clients unmatched to CT": fy_new_unmatched_ct,
        "FY26 CT/eRED secondary-migrant flag conflicts": secondary_conflicts,
    }

    return DashboardMetrics(
        report_date=report_date,
        current_fy=current_fy,
        active_clients=active_clients,
        active_count=int(len(active_clients)),
        carry_forward_active=carry_forward_active,
        carry_forward_count=int(len(carry_forward_active)),
        fy26_new=fy_new,
        fy26_new_count=int(len(fy_new)),
        fy26_reenrollment_episodes=int(reenrollment_episodes),
        monthly_counts=month_series,
        quarterly_counts=q_counts,
        annual_counts=annual_counts,
        partner_table=partner_table,
        secondary_fy26_count=int(len(secondary_fy26)),
        secondary_rma_fy26_count=secondary_rma_fy26_count,
        observed_conversion_all=observed_all,
        observed_conversion_primary=observed_primary,
        observed_conversion_secondary=observed_secondary,
        conversion_detail=conversion_detail,
        last7=last7,
        prior7=prior7,
        current_month_count=current_month_count,
        prior_month_count=prior_month_count,
        data_qa=data_qa,
    )


# =============================================================================
# 5. FORECAST / SENSE-MAKING
# =============================================================================

@dataclass
class PlanningInputs:
    august_arrivals_mtd: int = DEFAULT_AUGUST_ARRIVALS_MTD
    national_monthly_refugee_arrivals: int = DEFAULT_NATIONAL_MONTHLY_REFUGEE_ARRIVALS
    ks_share_low: float = KS_SHARE_LOW
    ks_share_base: float = KS_SHARE_BASE
    ks_share_high: float = KS_SHARE_HIGH
    primary_prepolicy_conversion: float = CN_PRIMARY_CONVERSION
    secondary_conversion: float = CN_SECONDARY_CONVERSION
    fy27_national_refugee_arrivals: int = 0
    fy27_enrollment_uptake: float = 0.0  # 0 = deliberately not yet set
    projected_fy27_secondary_migrants: int = 0


def build_sensemaking_bullets(metrics: DashboardMetrics, plan: PlanningInputs) -> list[str]:
    active_partner = (
        metrics.active_clients.groupby("Partner").size().sort_values(ascending=False).to_dict()
        if not metrics.active_clients.empty else {}
    )
    partner_phrase = ", ".join(f"{v} {k}" for k, v in active_partner.items())

    if metrics.current_month_count == 0:
        recent = (
            f"Recent enrollment activity is subdued: {metrics.prior_month_count} new first-time RMA "
            f"enrollment{'s' if metrics.prior_month_count != 1 else ''} were recorded in July and none "
            f"through {metrics.report_date.strftime('%B %d').replace(' 0', ' ')}; no new first-time enrollments were recorded "
            f"during either of the two most recent seven-day periods."
        )
    else:
        recent = (
            f"RMA recorded {metrics.current_month_count} new first-time enrollment(s) so far this month, "
            f"compared with {metrics.prior_month_count} in the prior month; {metrics.last7} occurred in the "
            f"most recent seven days versus {metrics.prior7} in the preceding seven days."
        )

    sep_ks = plan.national_monthly_refugee_arrivals * plan.ks_share_base
    sep_prepolicy_rma = sep_ks * plan.primary_prepolicy_conversion
    aug_prepolicy_rma = plan.august_arrivals_mtd * plan.primary_prepolicy_conversion

    bullets = [
        f"**{metrics.active_count} clients are actively enrolled in RMA** as of "
        f"{metrics.report_date.strftime('%m/%d/%Y')} ({partner_phrase}). FY26 has recorded "
        f"**{metrics.fy26_new_count} unduplicated new RMA enrollees**; re-enrollment/extension episodes "
        f"are tracked separately and are not added again as new clients.",
        recent + (
            f" Meanwhile, **{plan.august_arrivals_mtd} new Kansas arrivals** have been reported in August MTD, "
            f"equivalent to {approximate_range(aug_prepolicy_rma)} RMA enrollees only if the historical "
            f"pre-policy primary-arrival benchmark were applied and the arrivals were all primary."
        ),
        f"**Prospective RMA demand is expected to rise in FY27.** {metrics.carry_forward_count} of the "
        f"{metrics.active_count} currently active clients are expected to remain within their policy-adjusted "
        f"eligibility period after September 30. Beginning October 1, newly arriving primary clients in "
        f"Medicaid-losing/RMA-eligible status categories are treated as 100% RMA-eligible for forecasting; "
        f"CHEs and other Medicaid-retained categories are excluded from that automatic projection, while "
        f"actual enrollment/uptake remains a separate assumption.",
        f"**Secondary migration remains material to forecasting:** CT identifies "
        f"**{metrics.secondary_fy26_count} secondary migrants into Kansas in FY26 through the report date**, "
        f"and at least **{metrics.secondary_rma_fy26_count} FY26 first-time RMA enrollees** are identified as "
        f"secondary migrants through CT linkage. For near-term primary-arrival planning, a national monthly "
        f"volume of {plan.national_monthly_refugee_arrivals:,} and Kansas' {plan.ks_share_base*100:.2f}% "
        f"historical-share anchor imply about {round(sep_ks):,} Kansas primary refugee arrivals and about "
        f"{round(sep_prepolicy_rma):,} RMA enrollees under the pre-10/1 Medicaid-first benchmark."
    ]
    return bullets


def build_smt_update(metrics: DashboardMetrics, plan: PlanningInputs) -> str:
    sep_ks = round(plan.national_monthly_refugee_arrivals * plan.ks_share_base)
    sep_rma = round(sep_ks * plan.primary_prepolicy_conversion)
    partner_counts = metrics.active_clients.groupby("Partner").size().sort_values(ascending=False).to_dict()
    wichita = partner_counts.get("IRC-Wichita", 0)
    kc = partner_counts.get("IRC-Kansas City", 0)

    return f"""
### Refugee Medical Assistance (RMA) Update — {metrics.report_date.strftime('%B %d, %Y')}

RMA currently has **{metrics.active_count} actively enrolled clients**, including {wichita} through IRC-Wichita and {kc} through IRC-Kansas City. FY26 has recorded **{metrics.fy26_new_count} unduplicated new RMA enrollees** through {metrics.report_date.strftime('%B %d')}. New enrollment activity has been relatively low in recent weeks: {metrics.prior_month_count} new clients enrolled in July, {metrics.current_month_count} new first-time enrollments have been recorded so far in August, and {metrics.last7 + metrics.prior7} were recorded during the two most recent seven-day periods.

Kansas has reported **{plan.august_arrivals_mtd} new arrivals so far in August**, which may generate additional RMA activity as eligibility and coverage are determined. Under the historical pre-October 2026 Medicaid-first environment, approximately **41.0% of all Kansas arrivals** and **43.4% of primary arrivals** ultimately enrolled in RMA during the completed FY23–FY25 benchmark period. These historical rates remain useful for interpreting the remainder of FY26 but should not be carried forward mechanically into FY27.

Secondary migration remains a significant component of the Kansas caseload. ClientTrack identifies **{metrics.secondary_fy26_count} secondary migrants entering Kansas in FY26 through {metrics.report_date.strftime('%B %d')}**. The final planning benchmark for historical RMA conversion among secondary migrants is approximately **34.4%**, compared with 43.4% among primary arrivals, supporting continued separate treatment of the two streams in forecasting.

Looking ahead, **{metrics.carry_forward_count} of the {metrics.active_count} currently active RMA clients** are expected to remain within their policy-adjusted eligibility period after September 30. RMA demand is also expected to increase beginning October 1 as newly arriving primary clients in immigration categories that lose Medicaid/CHIP become RMA-eligible, subject to other program requirements. Cuban/Haitian Entrants and other categories that retain Medicaid/CHIP eligibility should not be automatically shifted into the RMA projection.

For primary-arrival forecasting, Kansas historically received approximately **1.06%–1.45% of U.S. refugee arrivals during FY2020–FY2023**, averaging about **1.29%**. As an interim near-term planning example, {plan.national_monthly_refugee_arrivals:,} national refugee arrivals would imply approximately **{sep_ks} Kansas primary refugee arrivals** and about **{sep_rma} RMA enrollees** under the pre-October Medicaid-first conversion benchmark. A full FY27 Low/Base/High RMA enrollment projection should be finalized once the national admissions assumption, partner pipeline and post-October enrollment/uptake assumption are sufficiently established.
""".strip()


# =============================================================================
# 6. STREAMLIT RENDERING
# =============================================================================


def _inject_css() -> None:
    st.markdown(
        """
        <style>
        .block-container {padding-top: 1.1rem; padding-bottom: 3rem; max-width: 1500px;}
        .small-note {font-size: 0.86rem; color: #5b6573;}
        div[data-testid="stMetric"] {border: 1px solid rgba(120,120,120,.22); padding: 12px; border-radius: 10px;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _display_dataframe(df: pd.DataFrame, hide_index: bool = True) -> None:
    st.dataframe(df, hide_index=hide_index, use_container_width=True)


def render_rma_dashboard(
    ct_source: ExcelSource,
    active_source: ExcelSource,
    comp_source: ExcelSource,
    report_date: pd.Timestamp = DEFAULT_REPORT_DATE,
    august_arrivals_mtd: int = DEFAULT_AUGUST_ARRIVALS_MTD,
    show_smt_update: bool = True,
) -> None:
    """Reusable renderer. Import this function into the combined KSOR Health app."""
    _inject_css()

    data = prepare_data(ct_source, active_source, comp_source)
    metrics = calculate_metrics(data, pd.Timestamp(report_date))

    # -------------------------------------------------------------------------
    # Sidebar planning inputs
    # -------------------------------------------------------------------------
    with st.sidebar:
        st.subheader("RMA Planning Inputs")
        report_date_ui = st.date_input("Report date", value=pd.Timestamp(report_date).date())
        if pd.Timestamp(report_date_ui) != pd.Timestamp(report_date).normalize():
            metrics = calculate_metrics(data, pd.Timestamp(report_date_ui))

        aug_arrivals = st.number_input(
            "August 2026 arrivals MTD",
            min_value=0,
            value=int(august_arrivals_mtd),
            step=1,
            help="Operational KSOR count. This can override CT when CT has a reporting lag.",
        )
        national_monthly = st.number_input(
            "Near-term U.S. refugee arrivals / month",
            min_value=0,
            value=int(DEFAULT_NATIONAL_MONTHLY_REFUGEE_ARRIVALS),
            step=100,
            help="Planning input only; replace when a current official monthly assumption is available.",
        )

        with st.expander("FY27 scenario inputs", expanded=False):
            st.caption("Leave at zero until KSOR adopts a planning assumption.")
            fy27_national = st.number_input(
                "FY27 annual U.S. refugee arrivals", min_value=0, value=0, step=1000
            )
            fy27_uptake_pct = st.number_input(
                "FY27 RMA enrollment/uptake among eligible primary arrivals (%)",
                min_value=0.0,
                max_value=100.0,
                value=0.0,
                step=1.0,
                help="Eligibility is modeled separately at 100% for affected-status new primary arrivals after 10/1/2026. Leave 0 until an enrollment/uptake assumption is approved.",
            )
            sec_fy27 = st.number_input(
                "Projected FY27 incoming secondary migrants",
                min_value=0,
                value=0,
                step=10,
                help="Modeled separately; leave 0 if no approved secondary-migration volume is available.",
            )

    plan = PlanningInputs(
        august_arrivals_mtd=int(aug_arrivals),
        national_monthly_refugee_arrivals=int(national_monthly),
        fy27_national_refugee_arrivals=int(fy27_national),
        fy27_enrollment_uptake=float(fy27_uptake_pct) / 100.0,
        projected_fy27_secondary_migrants=int(sec_fy27),
    )

    # -------------------------------------------------------------------------
    # Header / Executive KPIs
    # -------------------------------------------------------------------------
    st.title("KSOR Refugee Medical Assistance (RMA)")
    st.caption(f"Health Update dashboard • Data through {metrics.report_date.strftime('%B %d, %Y')}")

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("ACTIVE RMA", metrics.active_count)
    k2.metric("FY26 NEW RMA", metrics.fy26_new_count, help="Unduplicated first-time RMA enrollees")
    k3.metric("AUG. ARRIVALS MTD", plan.august_arrivals_mtd, help="Operational KSOR count")
    k4.metric("FY27 ACTIVE CARRY-FORWARD", metrics.carry_forward_count)
    k5.metric("HISTORICAL RMA CONVERSION", "41.0%", help="Final CN FY23–FY25 all-arrival planning benchmark")

    st.markdown("---")

    # -------------------------------------------------------------------------
    # Main partner table + required sense-making bullets
    # -------------------------------------------------------------------------
    st.subheader("Current Caseload & FY26 Enrollment")
    _display_dataframe(metrics.partner_table)

    for bullet in build_sensemaking_bullets(metrics, plan):
        st.markdown(f"- {bullet}")

    # -------------------------------------------------------------------------
    # Monthly trend
    # -------------------------------------------------------------------------
    st.subheader("FY26 New RMA Enrollments — Monthly Trend")
    monthly_df = pd.DataFrame({
        "Month": [month_label(p) for p in metrics.monthly_counts.index],
        "New RMA Enrollees": metrics.monthly_counts.values.astype(int),
    })
    c1, c2 = st.columns([1.5, 1])
    with c1:
        fig_month = px.bar(monthly_df, x="Month", y="New RMA Enrollees", text_auto=True)
        fig_month.update_layout(height=340, margin=dict(l=10, r=10, t=20, b=10), showlegend=False)
        st.plotly_chart(fig_month, use_container_width=True)
    with c2:
        month_row = {p.strftime("%b"): int(v) for p, v in zip(metrics.monthly_counts.index, metrics.monthly_counts.values)}
        month_row["FY YTD"] = metrics.fy26_new_count
        _display_dataframe(pd.DataFrame([month_row]))
        peak_period = metrics.monthly_counts.idxmax() if len(metrics.monthly_counts) else None
        peak_value = int(metrics.monthly_counts.max()) if len(metrics.monthly_counts) else 0
        if peak_period is not None:
            st.caption(
                f"Peak FY26 monthly first-time enrollment: {peak_value} in {month_label(peak_period)}. "
                "Re-enrollment/extension episodes are excluded from this trend."
            )

    # -------------------------------------------------------------------------
    # Quarterly and annual trends
    # -------------------------------------------------------------------------
    st.subheader("Quarterly & Multi-Year RMA Enrollment")
    q_col, y_col = st.columns(2)
    with q_col:
        q_df = pd.DataFrame([metrics.quarterly_counts])
        q_df["FY26 YTD"] = metrics.fy26_new_count
        _display_dataframe(q_df)
        st.caption("Q4 is partial through the report date.")
    with y_col:
        annual_display = pd.DataFrame([
            {k: ("TBD" if v is None else f"{v:,}") for k, v in metrics.annual_counts.items()}
        ])
        _display_dataframe(annual_display)
        st.caption("FY22 remains TBD because the current comprehensive eRED source begins 10/1/2022.")

    # -------------------------------------------------------------------------
    # Secondary migration and conversion
    # -------------------------------------------------------------------------
    st.subheader("Secondary Migration & Historical RMA Conversion")
    s1, s2 = st.columns([1, 1.35])
    with s1:
        sec_table = pd.DataFrame({
            "Measure": [
                "FY26 secondary migrants into KS",
                "FY26 first-time RMA clients identified as secondary migrants",
                "CN all-arrival conversion benchmark (FY23–FY25)",
                "CN primary-arrival conversion benchmark (FY23–FY25)",
                "CN secondary-migrant conversion benchmark (FY23–FY25)",
            ],
            "Current / Historical": [
                f"{metrics.secondary_fy26_count:,}",
                f"≥{metrics.secondary_rma_fy26_count:,}",
                "41.0%",
                "43.4%",
                "34.4%",
            ],
        })
        _display_dataframe(sec_table)
    with s2:
        conv = metrics.conversion_detail.copy()
        conv["Conversion"] = conv["Conversion"].map(lambda x: pct(x))
        _display_dataframe(conv)
        st.caption(
            "Observed rates are recomputed from uploaded CT/eRED files; the Concept Note planning "
            "benchmarks remain 41.0% all-arrival, 43.4% primary and 34.4% secondary."
        )

    # -------------------------------------------------------------------------
    # Near-term and FY27 planning
    # -------------------------------------------------------------------------
    st.subheader("Near-Term / FY27 Planning")
    sep_ks_base = plan.national_monthly_refugee_arrivals * plan.ks_share_base
    sep_rma_pre = sep_ks_base * plan.primary_prepolicy_conversion
    aug_rma_pre = plan.august_arrivals_mtd * plan.primary_prepolicy_conversion

    planning_rows = [
        {
            "Forecast Component": "August 2026 arrivals",
            "Planning Treatment": f"{plan.august_arrivals_mtd} actual MTD (operational KSOR count)",
        },
        {
            "Forecast Component": "Indicative RMA demand from August arrivals",
            "Planning Treatment": f"{approximate_range(aug_rma_pre)} only if all 8 are primary and the pre-policy 43.4% benchmark applies",
        },
        {
            "Forecast Component": "September primary-arrival planning example",
            "Planning Treatment": f"{plan.national_monthly_refugee_arrivals:,} U.S. arrivals × 1.29% KS share ≈ {round(sep_ks_base):,} KS primary refugee arrivals",
        },
        {
            "Forecast Component": "Indicative September RMA under pre-policy environment",
            "Planning Treatment": f"≈{round(sep_rma_pre):,} at 43.4% primary-arrival conversion",
        },
        {
            "Forecast Component": "Known FY27 active carry-forward",
            "Planning Treatment": f"{metrics.carry_forward_count} active clients with policy-adjusted end date after 9/30/2026",
        },
        {
            "Forecast Component": "FY27 new primary arrivals",
            "Planning Treatment": "Affected Medicaid-losing/RMA-eligible statuses = 100% RMA eligibility; enrollment/uptake modeled separately",
        },
        {
            "Forecast Component": "CHE / Medicaid-retained categories",
            "Planning Treatment": "Excluded from automatic post-10/1 RMA projection unless otherwise Medicaid-ineligible",
        },
        {
            "Forecast Component": "Secondary migrants",
            "Planning Treatment": "Forecast separately using volume + selected secondary-migrant RMA conversion/uptake",
        },
    ]
    _display_dataframe(pd.DataFrame(planning_rows))

    # FY27 scenario table is shown only when an annual national assumption is entered.
    if plan.fy27_national_refugee_arrivals > 0:
        st.markdown("#### FY27 Primary Refugee Scenario")
        scenario_rows = []
        for label, share in [("Low", plan.ks_share_low), ("Base", plan.ks_share_base), ("High", plan.ks_share_high)]:
            ks_arrivals = plan.fy27_national_refugee_arrivals * share
            rma_eligible = ks_arrivals  # Refugee arrivals in affected status; 100% eligibility assumption.
            projected_enrolled = (
                rma_eligible * plan.fy27_enrollment_uptake
                if plan.fy27_enrollment_uptake > 0 else np.nan
            )
            scenario_rows.append({
                "Scenario": label,
                "U.S. FY27 Refugee Arrivals": int(plan.fy27_national_refugee_arrivals),
                "KS Share": f"{share*100:.2f}%",
                "Projected KS Primary Refugee Arrivals": round(ks_arrivals),
                "RMA-Eligible Primary Arrivals": round(rma_eligible),
                "Projected RMA Enrollees": (
                    round(projected_enrolled) if pd.notna(projected_enrolled) else "TBD — uptake not set"
                ),
            })
        _display_dataframe(pd.DataFrame(scenario_rows))
        if plan.fy27_enrollment_uptake == 0:
            st.info(
                "FY27 enrollment/uptake has intentionally not been assumed. The table therefore shows "
                "RMA-eligible primary-arrival exposure, not a projected enrollment total."
            )

        if plan.projected_fy27_secondary_migrants > 0:
            sec_expected = plan.projected_fy27_secondary_migrants * plan.secondary_conversion
            st.caption(
                f"Secondary-migration planning component: {plan.projected_fy27_secondary_migrants:,} projected "
                f"incoming secondary migrants × 34.4% historical benchmark ≈ {round(sec_expected):,} RMA clients. "
                "This remains a separate stream and should be replaced when a stronger FY27 secondary-migration assumption is available."
            )
    else:
        st.info(
            "Full FY27 Low/Base/High enrollment totals remain intentionally unpopulated until KSOR enters an "
            "FY27 national admissions assumption and, separately, an approved post-10/1 RMA enrollment/uptake assumption."
        )

    # -------------------------------------------------------------------------
    # Assumptions / data QA
    # -------------------------------------------------------------------------
    with st.expander("Assumptions & Data Quality", expanded=False):
        st.markdown(
            """
            **Counting rules**
            - **Active RMA** comes from the eRED Active Report and is the headline current-caseload measure.
            - **New RMA enrollees** are unduplicated people based on first recorded RMA enrollment; re-enrollment/extension episodes are tracked separately.
            - **Eight-month policy cohort starts 1/1/2026.** FY27 carry-forward is determined client-by-client from the policy-adjusted eligibility end date; February 1 is never hard-coded as the policy start.
            - **Secondary migration** is taken from ClientTrack when linked, because FY26 eRED secondary-migrant flags are not sufficiently reliable on their own.
            - **CHEs and other Medicaid-retained categories** are not automatically moved into the post-10/1 RMA forecast.
            - **Kansas historical share (1.29% base)** refers to primary refugee arrivals only; secondary migration is modeled separately.
            """
        )

        qa_df = pd.DataFrame({"QA Metric": list(metrics.data_qa.keys()), "Count": list(metrics.data_qa.values())})
        _display_dataframe(qa_df)

        obs_df = pd.DataFrame({
            "Rate": ["All arrivals", "Primary arrivals", "Secondary migrants"],
            "Observed from uploaded files": [
                pct(metrics.observed_conversion_all),
                pct(metrics.observed_conversion_primary),
                pct(metrics.observed_conversion_secondary),
            ],
            "Final CN planning benchmark": ["41.0%", "43.4%", "34.4%"],
        })
        _display_dataframe(obs_df)

        # Historical KS share table
        share_df = pd.DataFrame({
            "Fiscal Year": list(KS_SHARE_HISTORY.keys()),
            "Kansas share of U.S. primary refugee arrivals": [f"{v*100:.2f}%" for v in KS_SHARE_HISTORY.values()],
        })
        _display_dataframe(share_df)
        st.caption(f"Simple FY2020–FY2023 average: {np.mean(list(KS_SHARE_HISTORY.values()))*100:.2f}%.")

    # -------------------------------------------------------------------------
    # SMT-ready deterministic narrative
    # -------------------------------------------------------------------------
    if show_smt_update:
        with st.expander("SMT-ready RMA Update", expanded=False):
            st.markdown(build_smt_update(metrics, plan))


def main() -> None:
    st.set_page_config(page_title="KSOR RMA Dashboard", page_icon="🏥", layout="wide")

    st.sidebar.markdown("### Data Sources")
    ct_upload = st.sidebar.file_uploader("ClientTrack export", type=["xlsx", "xls"], key="ct")
    active_upload = st.sidebar.file_uploader("eRED Active Report", type=["xlsx", "xls"], key="active")
    comp_upload = st.sidebar.file_uploader("eRED Comprehensive Report", type=["xlsx", "xls"], key="comp")

    ct_bytes, ct_name = resolve_source(ct_upload, LOCAL_FILE_CANDIDATES["ct"])
    active_bytes, active_name = resolve_source(active_upload, LOCAL_FILE_CANDIDATES["active"])
    comp_bytes, comp_name = resolve_source(comp_upload, LOCAL_FILE_CANDIDATES["comp"])

    missing = []
    if ct_bytes is None:
        missing.append("ClientTrack export")
    if active_bytes is None:
        missing.append("eRED Active Report")
    if comp_bytes is None:
        missing.append("eRED Comprehensive Report")

    if missing:
        st.title("KSOR Refugee Medical Assistance (RMA)")
        st.warning(
            "Missing required data source(s): " + ", ".join(missing) + ". "
            "Upload the file(s) in the sidebar or place them in the repo's data/ folder using the recommended short filenames."
        )
        st.stop()

    with st.sidebar.expander("Loaded files", expanded=False):
        st.write(f"CT: {ct_name}")
        st.write(f"Active: {active_name}")
        st.write(f"Comprehensive: {comp_name}")

    try:
        render_rma_dashboard(
            ct_source=ct_bytes,
            active_source=active_bytes,
            comp_source=comp_bytes,
            report_date=DEFAULT_REPORT_DATE,
            august_arrivals_mtd=DEFAULT_AUGUST_ARRIVALS_MTD,
            show_smt_update=True,
        )
    except Exception as exc:
        st.error("The RMA dashboard could not load the supplied data files.")
        st.exception(exc)


if __name__ == "__main__":
    main()
