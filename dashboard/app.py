"""Urban Pulse - Streamlit dashboard.

Run from the project root:
    streamlit run dashboard/app.py

Database credentials are read from the project's .env file.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import mysql.connector
import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv


# ---------------------------------------------------------------------
# 1. APPLICATION CONFIGURATION
# ---------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = PROJECT_ROOT / "reports"
load_dotenv(PROJECT_ROOT / ".env")

st.set_page_config(
    page_title="Urban Pulse | NYC 311 Analytics",
    page_icon="🏙️",
    layout="wide",
    initial_sidebar_state="expanded",
)

PAGE_OPTIONS = [
    "Overview",
    "Complaint Analysis",
    "Agency & Borough",
    "Time Trends",
    "Resolution Analytics",
    "Geographic Analysis",
    "Data Explorer",
    "Data Quality & Sampling",
]

MISSING_LABEL = "[Missing]"


# ---------------------------------------------------------------------
# 2. DATABASE HELPERS
# ---------------------------------------------------------------------

def get_db_connection():
    """Create a new MySQL connection using environment variables."""
    required = ["MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            "Missing MySQL environment settings in .env: " + ", ".join(missing)
        )

    try:
        port = int(os.getenv("MYSQL_PORT", "3306"))
    except ValueError as exc:
        raise RuntimeError("MYSQL_PORT must be a valid integer.") from exc

    return mysql.connector.connect(
        host=os.environ["MYSQL_HOST"],
        port=port,
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"],
        connection_timeout=15,
        charset="utf8mb4",
        use_unicode=True,
    )


@st.cache_data(ttl=300, show_spinner=False)
def read_query(sql: str, params: tuple[Any, ...] = ()) -> pd.DataFrame:
    """Run a parameterized read-only query and return a DataFrame."""
    connection = get_db_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            return pd.DataFrame(rows)
        finally:
            cursor.close()
    finally:
        connection.close()


def build_filters(
    start_date: date,
    end_date: date,
    boroughs: list[str],
    complaint_types: list[str],
) -> tuple[str, tuple[Any, ...]]:
    """Build safe SQL predicates; selected filter values remain parameters."""
    clauses = ["created_date >= %s", "created_date < %s"]
    params: list[Any] = [
        datetime.combine(start_date, datetime.min.time()),
        datetime.combine(end_date + timedelta(days=1), datetime.min.time()),
    ]

    for column, selected in (
        ("borough", boroughs),
        ("complaint_type", complaint_types),
    ):
        if not selected:
            continue

        include_missing = MISSING_LABEL in selected
        concrete = [value for value in selected if value != MISSING_LABEL]
        parts: list[str] = []

        if concrete:
            placeholders = ", ".join(["%s"] * len(concrete))
            parts.append(f"{column} IN ({placeholders})")
            params.extend(concrete)

        if include_missing:
            parts.append(f"{column} IS NULL")

        clauses.append("(" + " OR ".join(parts) + ")")

    return " WHERE " + " AND ".join(clauses), tuple(params)


def moneyless_number(value: Any, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):,.{digits}f}"


def format_hours(value: Any) -> str:
    if value is None or pd.isna(value):
        return "—"
    hours = float(value)
    if hours >= 24:
        return f"{hours:,.2f} h ({hours / 24:,.2f} days)"
    return f"{hours:,.2f} h"


def load_filter_options() -> tuple[list[str], list[str], date, date]:
    date_df = read_query(
        "SELECT MIN(DATE(created_date)) AS min_date, "
        "MAX(DATE(created_date)) AS max_date "
        "FROM service_requests WHERE created_date IS NOT NULL"
    )
    if date_df.empty or pd.isna(date_df.loc[0, "min_date"]):
        raise RuntimeError("The service_requests table has no valid created_date values.")

    min_date = pd.to_datetime(date_df.loc[0, "min_date"]).date()
    max_date = pd.to_datetime(date_df.loc[0, "max_date"]).date()

    borough_df = read_query(
        "SELECT DISTINCT borough FROM service_requests ORDER BY borough"
    )
    complaint_df = read_query(
        "SELECT DISTINCT complaint_type FROM service_requests "
        "ORDER BY complaint_type"
    )

    boroughs = [
        MISSING_LABEL if pd.isna(value) else str(value)
        for value in borough_df["borough"].tolist()
    ]
    complaints = [
        MISSING_LABEL if pd.isna(value) else str(value)
        for value in complaint_df["complaint_type"].tolist()
    ]
    return boroughs, complaints, min_date, max_date


@st.cache_data(ttl=300, show_spinner=False)
def load_monthly_source_report() -> tuple[pd.DataFrame, str | None]:
    """Load the extraction's month-level source totals, when available."""
    candidates = list(REPORT_DIR.glob("nyc311_monthly_source_counts_*.csv"))
    if not candidates:
        return pd.DataFrame(), None

    latest = max(candidates, key=lambda path: path.stat().st_mtime)
    report = pd.read_csv(latest)
    required = {"month", "source_count", "sample_target", "records_extracted"}
    if not required.issubset(report.columns):
        raise ValueError(
            "The monthly source-count report is missing one or more required columns: "
            + ", ".join(sorted(required - set(report.columns)))
        )

    report["month"] = pd.to_datetime(report["month"], errors="coerce").dt.strftime("%Y-%m")
    for column in ("source_count", "sample_target", "records_extracted"):
        report[column] = pd.to_numeric(report[column], errors="coerce")
    report = report.dropna(subset=["month", "source_count"])
    return report.sort_values("month"), latest.name


# ---------------------------------------------------------------------
# 3. PAGE HEADER AND SIDEBAR FILTERS
# ---------------------------------------------------------------------

st.title("🏙️ Urban Pulse")
st.caption("Civic Operations Evidence Desk | NYC 311 | 2020–2026")
st.markdown(
    "An evidence-led view of service demand, recorded closures, geographic patterns "
    "and the quality limits of the project sample."
)

try:
    all_boroughs, all_complaints, data_min_date, data_max_date = load_filter_options()
except Exception as exc:
    st.error(
        "Unable to load database filter options. Check that MySQL is running, "
        "the .env settings are correct, and the service_requests table is populated."
    )
    st.exception(exc)
    st.stop()

with st.sidebar:
    st.header("Dashboard navigation")
    selected_page = st.radio("Go to", PAGE_OPTIONS, label_visibility="collapsed")
    st.divider()
    st.header("Filters")
    selected_dates = st.date_input(
        "Created date range",
        value=(data_min_date, data_max_date),
        min_value=data_min_date,
        max_value=data_max_date,
        help="Filters records by their created_date.",
    )

    if isinstance(selected_dates, (tuple, list)) and len(selected_dates) == 2:
        start_date, end_date = selected_dates
    elif isinstance(selected_dates, (tuple, list)) and len(selected_dates) == 1:
        start_date = end_date = selected_dates[0]
    else:
        start_date = end_date = selected_dates

    selected_boroughs = st.multiselect(
        "Borough",
        options=all_boroughs,
        default=[],
        help="Leave empty to include all borough values, including missing values.",
    )
    selected_complaints = st.multiselect(
        "Complaint type",
        options=all_complaints,
        default=[],
        help="Leave empty to include all complaint types.",
    )
    st.caption("Filters apply to all dashboard pages.")
    if st.button("Clear cached data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

where_sql, where_params = build_filters(
    start_date, end_date, selected_boroughs, selected_complaints
)


# ---------------------------------------------------------------------
# 4. COMMON FILTERED DATA AND KPI CALCULATIONS
# ---------------------------------------------------------------------

try:
    kpi_df = read_query(
        "SELECT COUNT(DISTINCT unique_key) AS total_requests, "
        "COALESCE(SUM(is_closed = TRUE), 0) AS recorded_closed, "
        "COUNT(*) - COALESCE(SUM(is_closed = TRUE), 0) AS open_requests, "
        "ROUND(100.0 * COALESCE(SUM(is_closed = TRUE), 0) / NULLIF(COUNT(*), 0), 2) AS closure_rate "
        "FROM service_requests" + where_sql,
        where_params,
    )

    duration_df = read_query(
        "SELECT resolution_hours FROM service_requests"
        + where_sql
        + " AND resolution_hours IS NOT NULL AND resolution_hours >= 0",
        where_params,
    )
except Exception as exc:
    st.error("Unable to retrieve dashboard data from MySQL.")
    st.exception(exc)
    st.stop()

if kpi_df.empty or int(kpi_df.loc[0, "total_requests"] or 0) == 0:
    st.warning("No service requests match the selected filters. Adjust the filters to see results.")
    st.stop()

kpi = kpi_df.iloc[0]
durations = pd.to_numeric(
    duration_df.get("resolution_hours", pd.Series(dtype="float64")),
    errors="coerce",
).dropna()

median_hours = float(durations.median()) if not durations.empty else None
valid_duration_count = int(durations.count())
over_72_count = int((durations > 72).sum()) if valid_duration_count else 0
over_72_pct = (100.0 * over_72_count / valid_duration_count) if valid_duration_count else None


def get_grouped(sql_select: str, group_order: str = "") -> pd.DataFrame:
    sql = "SELECT " + sql_select + " FROM service_requests" + where_sql
    if group_order:
        sql += " " + group_order
    return read_query(sql, where_params)


# ---------------------------------------------------------------------
# 5. OVERVIEW PAGE - FIVE REQUIRED KPIs
# ---------------------------------------------------------------------

if selected_page == "Overview":
    st.subheader("Dashboard overview")
    st.caption(
        f"Showing requests created from {start_date:%d %b %Y} "
        f"through {end_date:%d %b %Y}."
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total requests", f"{int(kpi['total_requests']):,}")
    c2.metric("Recorded closure", f"{float(kpi['closure_rate'] or 0):.2f}%")
    c3.metric("Open requests", f"{int(kpi['open_requests']):,}")
    c4.metric("Median resolution", format_hours(median_hours))
    c5.metric("Over 72 hours", "—" if over_72_pct is None else f"{over_72_pct:.2f}%")

    st.caption(
        f"Resolution KPIs use {valid_duration_count:,} valid, non-negative durations. "
        "Recorded closure is based on the project's is_closed field (a non-null closed_date), "
        "not the text in the status column. The 72-hour threshold is an illustrative project measure."
    )

    left, right = st.columns(2)
    with left:
        complaint_df = get_grouped(
            "complaint_type, COUNT(*) AS request_count",
            "GROUP BY complaint_type ORDER BY request_count DESC LIMIT 10",
        )
        if not complaint_df.empty:
            complaint_df["complaint_type"] = complaint_df["complaint_type"].fillna("Missing")
            fig = px.bar(
                complaint_df.sort_values("request_count"),
                x="request_count", y="complaint_type", orientation="h",
                title="Top 10 complaint types",
                labels={"request_count": "Requests", "complaint_type": "Complaint type"},
            )
            st.plotly_chart(fig, use_container_width=True)
    with right:
        status_df = get_grouped(
            "COALESCE(status, 'Missing') AS request_status, COUNT(*) AS request_count",
            "GROUP BY COALESCE(status, 'Missing') ORDER BY request_count DESC",
        )
        if not status_df.empty:
            fig = px.pie(
                status_df, names="request_status", values="request_count",
                title="Recorded status distribution", hole=0.4,
            )
            st.plotly_chart(fig, use_container_width=True)

    monthly_df = get_grouped(
        "DATE_FORMAT(created_date, '%Y-%m') AS created_month, COUNT(*) AS sample_requests",
        "AND created_date IS NOT NULL GROUP BY DATE_FORMAT(created_date, '%Y-%m') ORDER BY created_month",
    )
    if not monthly_df.empty:
        fig = px.line(
            monthly_df, x="created_month", y="sample_requests", markers=True,
            title="Monthly requests in the 100,000-record project sample",
            labels={"created_month": "Month", "sample_requests": "Sample requests"},
        )
        st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------
# 6. COMPLAINT ANALYSIS PAGE
# ---------------------------------------------------------------------

elif selected_page == "Complaint Analysis":
    st.subheader("Complaint analysis")
    complaint_df = get_grouped(
        "COALESCE(complaint_type, 'Missing') AS complaint_type, COUNT(*) AS request_count",
        "GROUP BY COALESCE(complaint_type, 'Missing') ORDER BY request_count DESC",
    )
    if not complaint_df.empty:
        fig = px.bar(
            complaint_df.head(20).sort_values("request_count"),
            x="request_count", y="complaint_type", orientation="h",
            title="Top 20 complaint categories",
            labels={"request_count": "Requests", "complaint_type": "Complaint type"},
        )
        st.plotly_chart(fig, use_container_width=True)
        complaint_df["sample_percentage"] = (
            100 * complaint_df["request_count"] / complaint_df["request_count"].sum()
        ).round(2)
        st.dataframe(complaint_df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------
# 7. AGENCY & BOROUGH PAGE
# ---------------------------------------------------------------------

elif selected_page == "Agency & Borough":
    st.subheader("Agency workload and borough comparison")
    agency_df = get_grouped(
        "COALESCE(agency, 'Missing') AS agency, "
        "COALESCE(agency_name, 'Missing') AS agency_name, "
        "COUNT(*) AS total_requests, "
        "SUM(is_closed = TRUE) AS recorded_closed, "
        "SUM(is_closed = FALSE) AS requests_without_recorded_closure, "
        "ROUND(100.0 * SUM(is_closed = TRUE) / NULLIF(COUNT(*), 0), 2) AS closure_rate",
        "GROUP BY agency, agency_name ORDER BY total_requests DESC",
    )
    borough_df = get_grouped(
        "COALESCE(borough, 'Missing') AS borough, COUNT(*) AS total_requests, "
        "SUM(is_closed = TRUE) AS recorded_closed, "
        "ROUND(100.0 * SUM(is_closed = TRUE) / NULLIF(COUNT(*), 0), 2) AS closure_rate",
        "GROUP BY COALESCE(borough, 'Missing') ORDER BY total_requests DESC",
    )

    a, b = st.columns(2)
    with a:
        if not agency_df.empty:
            fig = px.bar(
                agency_df.head(15).sort_values("total_requests"),
                x="total_requests", y="agency", orientation="h",
                title="Top 15 agencies by workload",
                labels={"total_requests": "Requests", "agency": "Agency"},
            )
            st.plotly_chart(fig, use_container_width=True)
    with b:
        if not borough_df.empty:
            fig = px.bar(
                borough_df.sort_values("total_requests"),
                x="total_requests", y="borough", orientation="h",
                title="Requests by borough",
                labels={"total_requests": "Requests", "borough": "Borough"},
            )
            st.plotly_chart(fig, use_container_width=True)

    st.markdown("#### Agency closure comparison")
    st.dataframe(agency_df, use_container_width=True, hide_index=True)
    st.markdown("#### Borough closure comparison")
    st.dataframe(borough_df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------
# 8. TIME TRENDS PAGE
# ---------------------------------------------------------------------

elif selected_page == "Time Trends":
    st.subheader("Temporal demand patterns")
    monthly_df = get_grouped(
        "DATE_FORMAT(created_date, '%Y-%m') AS created_month, COUNT(*) AS request_count",
        "AND created_date IS NOT NULL GROUP BY DATE_FORMAT(created_date, '%Y-%m') ORDER BY created_month",
    )
    weekday_df = get_grouped(
        "created_weekday AS weekday_name, COUNT(*) AS request_count",
        "AND created_weekday IS NOT NULL GROUP BY created_weekday "
        "ORDER BY FIELD(created_weekday, 'Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')",
    )
    hourly_df = get_grouped(
        "created_hour, COUNT(*) AS request_count",
        "AND created_hour IS NOT NULL GROUP BY created_hour ORDER BY created_hour",
    )

    if not monthly_df.empty:
        fig = px.line(
            monthly_df, x="created_month", y="request_count", markers=True,
            title="Monthly request trend in the project sample",
            labels={"created_month": "Month", "request_count": "Requests"},
        )
        st.plotly_chart(fig, use_container_width=True)

    left, right = st.columns(2)
    with left:
        if not weekday_df.empty:
            fig = px.bar(
                weekday_df, x="weekday_name", y="request_count",
                title="Demand by weekday",
                labels={"weekday_name": "Weekday", "request_count": "Requests"},
            )
            st.plotly_chart(fig, use_container_width=True)
    with right:
        if not hourly_df.empty:
            fig = px.bar(
                hourly_df, x="created_hour", y="request_count",
                title="Demand by hour of day",
                labels={"created_hour": "Hour (0–23)", "request_count": "Requests"},
            )
            st.plotly_chart(fig, use_container_width=True)

    st.info(
        "These temporal charts describe the extracted sample. For full-source monthly "
        "volume, consult the separate nyc311_monthly_source_counts report."
    )


# ---------------------------------------------------------------------
# 9. RESOLUTION ANALYTICS PAGE
# ---------------------------------------------------------------------

elif selected_page == "Resolution Analytics":
    st.subheader("Resolution-time analytics")
    if durations.empty:
        st.warning("No valid resolution durations match the selected filters.")
    else:
        q1 = float(durations.quantile(0.25))
        q2 = float(durations.median())
        q3 = float(durations.quantile(0.75))
        iqr = q3 - q1
        lower_fence = q1 - 1.5 * iqr
        upper_fence = q3 + 1.5 * iqr
        outlier_count = int(((durations < lower_fence) | (durations > upper_fence)).sum())
        skewness = float(durations.skew()) if len(durations) > 2 else None

        stats = pd.DataFrame(
            [
                {"Measure": "Valid resolution records", "Value": f"{len(durations):,}"},
                {"Measure": "Mean (hours)", "Value": moneyless_number(durations.mean(), 4)},
                {"Measure": "Median (hours)", "Value": moneyless_number(q2, 4)},
                {"Measure": "Minimum (hours)", "Value": moneyless_number(durations.min(), 4)},
                {"Measure": "Maximum (hours)", "Value": moneyless_number(durations.max(), 4)},
                {"Measure": "Standard deviation (hours)", "Value": moneyless_number(durations.std(ddof=0), 4)},
                {"Measure": "Q1 (hours)", "Value": moneyless_number(q1, 4)},
                {"Measure": "Q3 (hours)", "Value": moneyless_number(q3, 4)},
                {"Measure": "IQR (hours)", "Value": moneyless_number(iqr, 4)},
                {"Measure": "Upper outlier fence (hours)", "Value": moneyless_number(upper_fence, 4)},
                {"Measure": "IQR outliers", "Value": f"{outlier_count:,}"},
                {"Measure": "Skewness", "Value": moneyless_number(skewness, 4)},
            ]
        )
        st.dataframe(stats, use_container_width=True, hide_index=True)

        hist_data = durations[durations > 0]
        if not hist_data.empty:
            fig = px.histogram(
                pd.DataFrame({"resolution_hours": hist_data}),
                x="resolution_hours", nbins=60,
                title="Resolution duration distribution (positive values)",
                labels={"resolution_hours": "Resolution hours"},
            )
            st.plotly_chart(fig, use_container_width=True)

        # Calculate exact medians in Pandas for the filtered records.
        # The minimum group size prevents misleading rankings from tiny samples.
        complaint_duration_rows = read_query(
            "SELECT complaint_type, resolution_hours FROM service_requests"
            + where_sql
            + " AND resolution_hours IS NOT NULL AND resolution_hours >= 0",
            where_params,
        )
        if not complaint_duration_rows.empty:
            complaint_medians = (
                complaint_duration_rows
                .groupby("complaint_type", dropna=False)["resolution_hours"]
                .agg(valid_resolution_records="count", median_resolution_hours="median")
                .reset_index()
            )
            complaint_medians = complaint_medians[
                complaint_medians["valid_resolution_records"] >= 30
            ].sort_values("median_resolution_hours", ascending=False).head(20)
            st.markdown("#### Median resolution by complaint category (minimum 30 records)")
            st.dataframe(complaint_medians, use_container_width=True, hide_index=True)

        agency_duration_rows = read_query(
            "SELECT agency, agency_name, resolution_hours FROM service_requests"
            + where_sql
            + " AND resolution_hours IS NOT NULL AND resolution_hours >= 0",
            where_params,
        )
        if not agency_duration_rows.empty:
            agency_medians = (
                agency_duration_rows
                .groupby(["agency", "agency_name"], dropna=False)["resolution_hours"]
                .agg(valid_resolution_records="count", median_resolution_hours="median")
                .reset_index()
            )
            agency_medians = agency_medians[
                agency_medians["valid_resolution_records"] >= 30
            ].sort_values("median_resolution_hours", ascending=False)
            st.markdown("#### Median resolution by agency (minimum 30 records)")
            st.dataframe(agency_medians, use_container_width=True, hide_index=True)

        category_volume = get_grouped(
            "complaint_type, COUNT(*) AS request_count",
            "GROUP BY complaint_type",
        )
        if not category_volume.empty and not complaint_duration_rows.empty:
            category_medians = (
                complaint_duration_rows
                .groupby("complaint_type", dropna=False)["resolution_hours"]
                .median()
                .rename("median_resolution_hours")
                .reset_index()
            )
            combined = category_volume.merge(
                category_medians, on="complaint_type", how="left"
            )
            average_category_volume = float(category_volume["request_count"].mean())
            overall_median = float(durations.median()) if not durations.empty else None
            if overall_median is not None:
                high_volume_long_duration = combined[
                    (combined["request_count"] > average_category_volume)
                    & (combined["median_resolution_hours"] > overall_median)
                ].sort_values("request_count", ascending=False)
                st.markdown("#### High-volume categories with longer-than-overall median duration")
                st.caption(
                    f"Project-defined thresholds: category volume above {average_category_volume:.2f} "
                    f"requests and median duration above {overall_median:.4f} hours."
                )
                st.dataframe(
                    high_volume_long_duration,
                    use_container_width=True,
                    hide_index=True,
                )

        st.caption(
            "Outlier flags identify unusually long or short durations; they are not deleted. "
            "This page calculates statistics for the currently filtered records."
        )


# ---------------------------------------------------------------------
# 10. GEOGRAPHIC ANALYSIS PAGE
# ---------------------------------------------------------------------

elif selected_page == "Geographic Analysis":
    st.subheader("Geographic complaint concentration")
    zip_df = get_grouped(
        "COALESCE(incident_zip, 'Missing') AS incident_zip, COUNT(*) AS request_count",
        "GROUP BY COALESCE(incident_zip, 'Missing') ORDER BY request_count DESC LIMIT 20",
    )
    if not zip_df.empty:
        fig = px.bar(
            zip_df.sort_values("request_count"),
            x="request_count", y="incident_zip", orientation="h",
            title="Top 20 ZIP codes by request count",
            labels={"request_count": "Requests", "incident_zip": "ZIP code"},
        )
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(zip_df, use_container_width=True, hide_index=True)

    map_df = get_grouped(
        "latitude, longitude, complaint_type, borough",
        "AND latitude BETWEEN 40 AND 42 AND longitude BETWEEN -75 AND -72 "
        "AND latitude IS NOT NULL AND longitude IS NOT NULL LIMIT 10000",
    )
    if not map_df.empty:
        st.markdown("#### Sample of usable NYC coordinates (up to 10,000 records)")
        fig = px.scatter_geo(
            map_df,
            lat="latitude",
            lon="longitude",
            color="borough",
            hover_name="complaint_type",
            scope="north america",
            fitbounds="locations",
            title="Complaint locations",
        )
        fig.update_layout(geo=dict(showland=True, showcountries=True))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No usable coordinates match the current filters.")


# ---------------------------------------------------------------------
# 11. DATA EXPLORER PAGE
# ---------------------------------------------------------------------

elif selected_page == "Data Explorer":
    st.subheader("Filtered service request records")
    st.caption("Preview is limited to 100 rows. Use the sidebar filters to narrow the records.")
    preview_df = get_grouped(
        "unique_key, created_date, closed_date, agency, agency_name, "
        "complaint_type, descriptor, status, borough, incident_zip, "
        "latitude, longitude, is_closed, resolution_hours",
        "ORDER BY created_date DESC LIMIT 100",
    )
    st.dataframe(preview_df, use_container_width=True, hide_index=True)
    if not preview_df.empty:
        csv_bytes = preview_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download this 100-row preview as CSV",
            data=csv_bytes,
            file_name="urban_pulse_filtered_preview.csv",
            mime="text/csv",
        )


# ---------------------------------------------------------------------
# 12. DATA QUALITY & SAMPLING PAGE - PROJECT-SPECIFIC AUDIT
# ---------------------------------------------------------------------

elif selected_page == "Data Quality & Sampling":
    st.subheader("Data quality and sample coverage")
    st.caption(
        "This page separates record-level quality checks from the sampling frame. "
        "It is designed to make the limitations of the 100,000-row extract visible."
    )

    audit_df = get_grouped(
        "COUNT(*) AS total_rows, "
        "COUNT(DISTINCT unique_key) AS distinct_request_ids, "
        "SUM(unique_key IS NULL) AS missing_request_ids, "
        "SUM(duplicate_request_id = TRUE) AS duplicate_flagged_rows, "
        "SUM(closed_before_created = TRUE) AS chronology_issues, "
        "SUM(invalid_coordinates = TRUE) AS invalid_coordinate_rows, "
        "SUM(incomplete_coordinates = TRUE) AS incomplete_coordinate_rows, "
        "SUM(resolution_hours IS NOT NULL AND resolution_hours >= 0) AS valid_durations, "
        "SUM(is_closed = TRUE AND (status IS NULL OR status <> 'Closed')) AS closed_date_status_mismatch, "
        "SUM(is_closed = FALSE AND status = 'Closed') AS closed_status_without_date"
    )

    if not audit_df.empty:
        audit = audit_df.iloc[0]
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Rows in current filter", f"{int(audit['total_rows']):,}")
        a2.metric("Distinct request IDs", f"{int(audit['distinct_request_ids']):,}")
        a3.metric("Date-order anomalies", f"{int(audit['chronology_issues']):,}")
        a4.metric("Valid durations", f"{int(audit['valid_durations']):,}")

        b1, b2, b3 = st.columns(3)
        b1.metric("Incomplete coordinates", f"{int(audit['incomplete_coordinate_rows']):,}")
        b2.metric("Invalid coordinate ranges", f"{int(audit['invalid_coordinate_rows']):,}")
        b3.metric("Duplicate-flagged rows", f"{int(audit['duplicate_flagged_rows']):,}")

        st.markdown("#### Closure-date and text-status reconciliation")
        st.caption(
            "The project defines recorded closure from a non-null closed_date. "
            "The source status text is a separate field and can disagree with that rule."
        )
        mismatch = pd.DataFrame([
            {"Check": "A recorded closed_date, but status is not 'Closed'", "Records": int(audit["closed_date_status_mismatch"])},
            {"Check": "Status says 'Closed', but closed_date is missing", "Records": int(audit["closed_status_without_date"])},
            {"Check": "Missing request IDs", "Records": int(audit["missing_request_ids"])},
        ])
        st.dataframe(mismatch, use_container_width=True, hide_index=True)

    st.markdown("#### Completeness of key source fields")
    completeness_sql = (
        "SELECT 'Created date' AS field_name, SUM(created_date IS NULL) AS missing_rows, COUNT(*) AS total_rows FROM service_requests"
        + where_sql
        + " UNION ALL SELECT 'Agency', SUM(agency IS NULL), COUNT(*) FROM service_requests" + where_sql
        + " UNION ALL SELECT 'Complaint type', SUM(complaint_type IS NULL), COUNT(*) FROM service_requests" + where_sql
        + " UNION ALL SELECT 'Status', SUM(status IS NULL), COUNT(*) FROM service_requests" + where_sql
        + " UNION ALL SELECT 'Borough', SUM(borough IS NULL), COUNT(*) FROM service_requests" + where_sql
        + " UNION ALL SELECT 'ZIP code', SUM(incident_zip IS NULL), COUNT(*) FROM service_requests" + where_sql
        + " UNION ALL SELECT 'Latitude', SUM(latitude IS NULL), COUNT(*) FROM service_requests" + where_sql
        + " UNION ALL SELECT 'Longitude', SUM(longitude IS NULL), COUNT(*) FROM service_requests" + where_sql
        + " UNION ALL SELECT 'Closed date', SUM(closed_date IS NULL), COUNT(*) FROM service_requests" + where_sql
    )
    completeness_df = read_query(completeness_sql, where_params * 9)
    if not completeness_df.empty:
        completeness_df["missing_percentage"] = (
            100 * completeness_df["missing_rows"] / completeness_df["total_rows"].replace(0, pd.NA)
        ).round(2)
        st.dataframe(completeness_df, use_container_width=True, hide_index=True)
        st.download_button(
            "Download field completeness audit",
            data=completeness_df.to_csv(index=False).encode("utf-8-sig"),
            file_name="urban_pulse_field_completeness.csv",
            mime="text/csv",
        )

    st.markdown("#### Monthly sample coverage")
    if selected_boroughs or selected_complaints:
        st.info(
            "Clear the Borough and Complaint type filters to compare the sample with "
            "the full-source monthly counts. The source-count report is not split by those dimensions."
        )
    else:
        try:
            source_report, source_report_name = load_monthly_source_report()
        except Exception as exc:
            source_report, source_report_name = pd.DataFrame(), None
            st.warning(f"Unable to read the monthly source-count report: {exc}")

        if source_report.empty:
            st.warning(
                "No nyc311_monthly_source_counts_*.csv report was found in the reports folder. "
                "Copy the matching report from the extraction run to enable this comparison."
            )
        else:
            start_month = pd.Period(start_date, freq="M").strftime("%Y-%m")
            end_month = pd.Period(end_date, freq="M").strftime("%Y-%m")
            visible_source = source_report[
                source_report["month"].between(start_month, end_month)
            ].copy()
            sample_monthly = get_grouped(
                "DATE_FORMAT(created_date, '%Y-%m') AS month, COUNT(*) AS sample_rows",
                "AND created_date IS NOT NULL GROUP BY DATE_FORMAT(created_date, '%Y-%m') ORDER BY month",
            )
            comparison = visible_source.merge(sample_monthly, on="month", how="left")
            comparison["sample_rows"] = comparison["sample_rows"].fillna(0).astype(int)
            comparison["realized_sample_pct"] = (
                100 * comparison["records_extracted"] / comparison["source_count"].replace(0, pd.NA)
            ).round(3)
            st.caption(f"Source totals loaded from: {source_report_name}")
            st.caption(
                "Source totals are month-level counts from the extraction run; sample rows are the "
                "records currently matching the selected date range. Month-level source totals are not day-filtered."
            )
            left, right = st.columns(2)
            with left:
                fig = px.line(
                    comparison, x="month", y="source_count", markers=True,
                    title="Full-source requests by month",
                    labels={"month": "Month", "source_count": "Source requests"},
                )
                st.plotly_chart(fig, use_container_width=True)
            with right:
                fig = px.line(
                    comparison, x="month", y="realized_sample_pct", markers=True,
                    title="Realized sample as a share of monthly source count",
                    labels={"month": "Month", "realized_sample_pct": "Sample coverage (%)"},
                )
                st.plotly_chart(fig, use_container_width=True)
            st.dataframe(
                comparison[["month", "source_count", "sample_target", "records_extracted", "sample_rows", "realized_sample_pct"]],
                use_container_width=True,
                hide_index=True,
            )

    st.warning(
        "Interpretation note: this 100,000-row dataset is a monthly-proportional, "
        "distributed-page sample, not a random sample or a complete census of NYC 311 requests. "
        "Sample-based category, agency and borough shares should be labelled as sample results."
    )


# ---------------------------------------------------------------------
# 13. FOOTER
# ---------------------------------------------------------------------

st.divider()
st.caption(
    "Urban Pulse | GUVI AIML Mini Project | NYC 311 public data. Sample-based results "
    "are not a full census. Recorded closure does not measure citizen satisfaction "
    "or independently verified resolution."
)
