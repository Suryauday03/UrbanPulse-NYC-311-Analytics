-- ================================================================
-- URBAN PULSE - ADDITIONAL PROJECT-SPECIFIC INVESTIGATIONS
-- MySQL 8.0+
-- These extend the 15 core GUVI questions with five extra audits.
-- Results describe the 100,000-record project sample.
-- ================================================================

USE urban_pulse_db;

-- ----------------------------------------------------------------
-- 16. Reconcile the source status label with the recorded closed date.
-- The project closure indicator is based on closed_date, not status.
-- ----------------------------------------------------------------
SELECT
    COALESCE(status, '[Missing status]') AS source_status,
    is_closed AS has_recorded_closed_date,
    COUNT(*) AS request_count
FROM service_requests
GROUP BY COALESCE(status, '[Missing status]'), is_closed
ORDER BY source_status, has_recorded_closed_date;

-- ----------------------------------------------------------------
-- 17. Key-field completeness profile.
-- This provides a compact, auditable view of null values.
-- ----------------------------------------------------------------
SELECT 'created_date' AS field_name,
       SUM(created_date IS NULL) AS missing_rows,
       COUNT(*) AS total_rows,
       ROUND(100.0 * SUM(created_date IS NULL) / NULLIF(COUNT(*), 0), 2) AS missing_pct
FROM service_requests
UNION ALL
SELECT 'agency', SUM(agency IS NULL), COUNT(*),
       ROUND(100.0 * SUM(agency IS NULL) / NULLIF(COUNT(*), 0), 2)
FROM service_requests
UNION ALL
SELECT 'complaint_type', SUM(complaint_type IS NULL), COUNT(*),
       ROUND(100.0 * SUM(complaint_type IS NULL) / NULLIF(COUNT(*), 0), 2)
FROM service_requests
UNION ALL
SELECT 'status', SUM(status IS NULL), COUNT(*),
       ROUND(100.0 * SUM(status IS NULL) / NULLIF(COUNT(*), 0), 2)
FROM service_requests
UNION ALL
SELECT 'borough', SUM(borough IS NULL), COUNT(*),
       ROUND(100.0 * SUM(borough IS NULL) / NULLIF(COUNT(*), 0), 2)
FROM service_requests
UNION ALL
SELECT 'incident_zip', SUM(incident_zip IS NULL), COUNT(*),
       ROUND(100.0 * SUM(incident_zip IS NULL) / NULLIF(COUNT(*), 0), 2)
FROM service_requests
UNION ALL
SELECT 'latitude', SUM(latitude IS NULL), COUNT(*),
       ROUND(100.0 * SUM(latitude IS NULL) / NULLIF(COUNT(*), 0), 2)
FROM service_requests
UNION ALL
SELECT 'longitude', SUM(longitude IS NULL), COUNT(*),
       ROUND(100.0 * SUM(longitude IS NULL) / NULLIF(COUNT(*), 0), 2)
FROM service_requests
UNION ALL
SELECT 'closed_date', SUM(closed_date IS NULL), COUNT(*),
       ROUND(100.0 * SUM(closed_date IS NULL) / NULLIF(COUNT(*), 0), 2)
FROM service_requests
ORDER BY missing_pct DESC;

-- ----------------------------------------------------------------
-- 18. Top complaint category within each borough.
-- Ranks categories independently inside each borough.
-- ----------------------------------------------------------------
WITH borough_categories AS (
    SELECT
        COALESCE(borough, '[Missing]') AS borough_group,
        COALESCE(complaint_type, '[Missing]') AS complaint_group,
        COUNT(*) AS request_count
    FROM service_requests
    GROUP BY COALESCE(borough, '[Missing]'),
             COALESCE(complaint_type, '[Missing]')
), ranked_categories AS (
    SELECT
        borough_group,
        complaint_group,
        request_count,
        ROW_NUMBER() OVER (
            PARTITION BY borough_group
            ORDER BY request_count DESC, complaint_group
        ) AS category_rank
    FROM borough_categories
)
SELECT borough_group, complaint_group, request_count
FROM ranked_categories
WHERE category_rank <= 5
ORDER BY borough_group, category_rank;

-- ----------------------------------------------------------------
-- 19. Weekday and hour combinations with the highest request volume.
-- Useful as a descriptive staffing-planning signal only.
-- ----------------------------------------------------------------
SELECT
    created_weekday,
    created_hour,
    COUNT(*) AS request_count
FROM service_requests
WHERE created_weekday IS NOT NULL
  AND created_hour IS NOT NULL
GROUP BY created_weekday, created_hour
ORDER BY request_count DESC
LIMIT 20;

-- ----------------------------------------------------------------
-- 20. Resolution-duration bands.
-- Negative or missing durations are excluded; long cases are retained.
-- ----------------------------------------------------------------
SELECT
    CASE
        WHEN resolution_hours < 1 THEN 'Under 1 hour'
        WHEN resolution_hours < 24 THEN '1 to under 24 hours'
        WHEN resolution_hours <= 72 THEN '24 to 72 hours'
        ELSE 'Over 72 hours'
    END AS duration_band,
    COUNT(*) AS request_count,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS percentage_of_valid_durations
FROM service_requests
WHERE resolution_hours IS NOT NULL
  AND resolution_hours >= 0
GROUP BY duration_band
ORDER BY FIELD(
    duration_band,
    'Under 1 hour',
    '1 to under 24 hours',
    '24 to 72 hours',
    'Over 72 hours'
);

-- End of additional investigations.
