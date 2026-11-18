-- Schema for NASA contract awards pulled from the USAspending API
-- (spending_by_award, FY2022-2024, new awards only).
--
-- Target: SQLite 3.37+ (STRICT tables). Foreign keys are only enforced when
-- the connection runs `PRAGMA foreign_keys = ON`, so the loader must set it.
--
-- One row in the raw JSON = one award. The vendor and NAICS attributes that
-- repeat on every award are split out into reference tables.

DROP TABLE IF EXISTS awards;
DROP TABLE IF EXISTS vendors;
DROP TABLE IF EXISTS naics;

-- One row per recipient, keyed by SAM Unique Entity ID.
-- The raw data has a handful of UEIs with more than one spelling of the name
-- (renames, "INC" vs "INC."); the loader picks one name per UEI. The same
-- name can legitimately appear under several UEIs, so name is not unique.
CREATE TABLE vendors (
    uei   TEXT PRIMARY KEY CHECK (length(uei) = 12),
    name  TEXT NOT NULL CHECK (length(trim(name)) > 0)
) STRICT;

-- One row per 6-digit NAICS industry code.
-- Stored as TEXT: it is an identifier, not a number, and the leading digits
-- carry meaning (first 2 = sector), which substr() can use for roll-ups.
CREATE TABLE naics (
    code         TEXT PRIMARY KEY
                 CHECK (length(code) = 6 AND code NOT GLOB '*[^0-9]*'),
    description  TEXT NOT NULL CHECK (length(trim(description)) > 0)
) STRICT;

-- One row per contract award.
CREATE TABLE awards (
    -- USAspending's unique award key, e.g.
    -- CONT_AWD_80TECH24FA101_8000_80TECH24AA004_8000. This is the primary key
    -- because award_id (the PIID) is not unique on its own: the same PIID can
    -- recur under a different parent contract.
    generated_internal_id  TEXT PRIMARY KEY,
    award_id               TEXT NOT NULL,
    vendor_uei             TEXT NOT NULL REFERENCES vendors (uei),
    naics_code             TEXT NOT NULL REFERENCES naics (code),

    -- Current total obligated dollars. Zero is allowed (it occurs in the raw
    -- data); negative is not.
    award_amount           REAL NOT NULL CHECK (award_amount >= 0),

    -- ISO-8601 date (YYYY-MM-DD) so string comparison sorts chronologically.
    -- This is the period-of-performance start and can fall outside the fiscal
    -- year the award was signed in, so it is not tied to fiscal_year.
    start_date             TEXT NOT NULL
                           CHECK (start_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
                                  AND date(start_date) IS start_date),

    contract_award_type    TEXT NOT NULL
                           CHECK (contract_award_type IN ('BPA CALL', 'PURCHASE ORDER',
                                                          'DELIVERY ORDER', 'DEFINITIVE CONTRACT')),

    -- Federal fiscal year the award was newly signed in (Oct 1 - Sep 30,
    -- named for the year it ends). Set by the fetch script, not the API.
    fiscal_year            INTEGER NOT NULL CHECK (fiscal_year BETWEEN 2000 AND 2100),

    -- FPDS "extent competed" code. Set by the fetch script from the filter
    -- used for each request.
    --   A    Full and open competition
    --   B    Not available for competition
    --   C    Not competed
    --   D    Full and open competition after exclusion of sources
    --   E    Follow on to competed action
    --   F    Competed under simplified acquisition procedures
    --   G    Not competed under simplified acquisition procedures
    --   CDO  Competitive delivery order
    --   NDO  Non-competitive delivery order
    extent_competed        TEXT NOT NULL
                           CHECK (extent_competed IN ('A', 'B', 'C', 'D', 'E', 'F', 'G', 'CDO', 'NDO'))
) STRICT;

-- Indexes for the two analysis questions: dollars by vendor (optionally
-- within a NAICS code), and competition by fiscal year.
CREATE INDEX idx_awards_vendor       ON awards (vendor_uei);
CREATE INDEX idx_awards_naics        ON awards (naics_code);
CREATE INDEX idx_awards_fy_competed  ON awards (fiscal_year, extent_competed);
