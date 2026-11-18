# Data cleaning notes

These notes describe what `src/load_awards.py` does to the raw data on its way into `data/processed/procurement.db`, and why. The counts come from running the loader against the raw files fetched by `src/fetch_awards.py`.

## The raw data

The fetch script saves one JSON file per fiscal year and competition code, `data/raw/awards_{year}_{code}.json`, for NASA contract awards newly signed in fiscal years 2022 to 2024. There are 27 files (3 years by 9 codes). Nine of them are empty lists, and the other 18 hold 15,890 award records between them.

Three terms are used throughout:

- **UEI** (Unique Entity ID) is the 12-character identifier that SAM.gov, the federal contractor registry, assigns to each organisation that does business with the government. It identifies a vendor more reliably than its name does.
- **NAICS** (North American Industry Classification System) is the 6-digit code that says what industry a contract falls under, for example 541715 for research and development in the physical, engineering and life sciences. Each code has an official description.
- **Extent competed** is the code in the Federal Procurement Data System that records how much competition there was for a contract, for example `A` for full and open competition or `C` for not competed. The full list of codes is in [schema.sql](schema.sql).

A fiscal year runs from October 1 to September 30 and is named for the calendar year it ends in, so fiscal year 2024 is October 1, 2023 to September 30, 2024.

## Summary of the load

| | Count |
|---|---|
| Files read | 27, of which 9 empty |
| Awards | 15,890 |
| Vendors (distinct UEIs) | 4,123 |
| NAICS codes | 397 |
| Total dollars | 19,163,023,374.55 |

Every raw record becomes exactly one row in `awards`. No records are dropped, and the dollar total in the database equals the total in the raw files.

## Cleaning decisions

### Whitespace

Leading and trailing whitespace is stripped from every text field. In the current data no value needed it. The step is there so that a stray space in a future fetch cannot create a second vendor or NAICS code that looks identical to an existing one.

### One name per vendor

The raw data repeats the recipient name on every award, and 6 of the 4,123 UEIs appear with two different names. Some are real renames and some are only punctuation.

The `vendors` table holds one name per UEI. The loader uses the name on that UEI's most recent award by start date, and breaks ties by taking the award with the higher `generated_internal_id` (USAspending's unique award key), so the result does not depend on the order the files are read in. The most recent name is the best available guess at what the vendor is called now.

| UEI | Names in the raw data (number of awards) | Name stored |
|---|---|---|
| CRWKJZGN3D89 | COLDQUANTA, INC. (2), INFLEQTION QUANTUM, LLC (1) | INFLEQTION QUANTUM, LLC |
| DUAZPJFEFKH4 | DRIVENDATA, INC. (10), DRIVENDATA, LLC (1) | DRIVENDATA, INC. |
| FE71DET4RML7 | MATHESON TRI-GAS, INC. (2), NIPPON SANSO MATHESON, INC. (1) | NIPPON SANSO MATHESON, INC. |
| GWGLE6NTKKX8 | NEXUS PHOTONICS, INC (2), NEXUS PHOTONICS, INC. (2) | NEXUS PHOTONICS, INC. |
| R98MW4ZKUUK3 | GOVERNMENT ACQUISITIONS INC (36), GOVERNMENT ACQUISITIONS LLC (1) | GOVERNMENT ACQUISITIONS INC |
| SMJQBS4YW7V5 | FIBER MATERIALS INC. (16), FMI SOLUTIONS, INC. (2) | FIBER MATERIALS INC. |

The rule is simple rather than perfect. For SMJQBS4YW7V5 the two names alternate over time (the FMI SOLUTIONS awards start in July and November 2023, and a FIBER MATERIALS award starts in May 2024), so the latest start date does not prove which name is current. Because awards are joined to vendors by UEI, the choice of name affects labels only, never totals.

### One description per NAICS code

Five of the 397 NAICS codes appear with two descriptions. In each case one is the older wording and one is the wording from a later revision of the NAICS standard, for example code 541380 as both "TESTING LABORATORIES" and "TESTING LABORATORIES AND SERVICES". The codes are 325992, 518210, 524292, 541380 and 561611.

The loader applies the same rule as for vendor names: the description on the code's most recent award by start date, ties broken by the higher `generated_internal_id`. For all five codes this selects the newer, longer wording.

### Zero-dollar awards are kept

211 awards have an amount of exactly 0. No award has a negative amount. The zero-dollar awards are real awards, so they stay in the database and count toward award counts. They add nothing to dollar totals. Any query that reports a share "by count" and a share "by dollars" will therefore treat them differently, which is the intended behaviour.

### Awards whose start date is outside their fiscal year are kept

The `fiscal_year` on each record is not a field from the API. The fetch script asks the API for awards newly signed within one fiscal year (the `new_awards_only` date filter) and stamps that year on the results. The `Start Date` field is something else: the start of the period of performance, which is when the work is scheduled to begin.

The two usually agree, but not always. Counting an award as outside its fiscal year when its start date is earlier than October 1 of the previous calendar year or later than September 30 of the fiscal year, 868 of the 15,890 awards fall outside: 91 start before their fiscal year begins and 777 start after it ends. Start dates range from 2002-02-27 to 2025-09-01. The comparison is done on the stored `YYYY-MM-DD` text, which sorts in date order.

These awards are kept, and `fiscal_year` is treated as the authoritative year for all analysis, because the project counts an award in the year it was signed. `start_date` is stored unchanged and is not used to assign awards to years. Reassigning or dropping these awards would remove about 2.04 billion dollars, a little over a tenth of the total, from the years they were signed in.

### Dropped fields

Two raw fields are not stored.

`Awarding Sub Agency` is "National Aeronautics and Space Administration" on all 15,890 records, because the fetch is filtered to NASA. The loader does not store it, but it does check it, and stops if any record has a different value, since that would mean the fetch covered the wrong agency.

`internal_id` is a numeric identifier from the API. It is unique in this data, but so is `generated_internal_id`, which is the key the API documents and the one the schema uses, so the numeric one is redundant.

### File name cross-check

Each record's `fiscal_year` and `extent_competed` are compared with the year and code in the name of the file it came from, and the loader stops on any mismatch. Both values are written by the fetch script rather than returned by the API, so a disagreement would mean a file was renamed or edited by hand. All 15,890 records agree with their file names.

### Errors stop the load

The loader does not skip bad records. It stops with a message naming the file and the award if a record is missing a field, has a text field that is not text, repeats a `generated_internal_id` already seen, or is rejected by a constraint in `schema.sql` (for example a negative amount, a start date that is not a real date, or a UEI that is not 12 characters). A file that is not valid JSON also stops the load. Files containing an empty list are expected and are skipped.

The database is rebuilt from scratch on every run. The previous database is deleted at the start, the new one is written under a temporary name inside a single transaction, and it is renamed to `procurement.db` only after the transaction commits. A failed run therefore leaves no database at all, rather than a partial one or a stale one from an earlier run.

The current data passes every check: no nulls, no empty strings and no duplicate keys.

## Noticed but left alone

**Empty files for three competition codes.** The files for `E` (follow on to competed action), `CDO` (competitive delivery order) and `NDO` (non-competitive delivery order) are empty in all three years. The loader accepts this. Whether NASA truly has no awards under these codes, or the API files them under other codes, has not been investigated. Only codes A, B, C, D, F and G appear in the database.

**The same vendor name under several UEIs.** 62 names appear under more than one UEI. A company can register several divisions or locations separately, which is the likely reason, but this was not checked name by name. They are kept as separate vendors. Merging them would need a parent-company mapping that the data does not contain, so vendor concentration figures are per UEI and may understate concentration at the corporate level.

**Award IDs are not unique.** There are 15,883 distinct `Award ID` values for 15,890 awards, because the same ID can be used under different parent contracts. This is why the table is keyed on `generated_internal_id`.

**Award amount is the current total.** The amount is the total obligated on the award as of the fetch, including later modifications, not the amount at signing. A fiscal year's total is therefore the current value of awards signed in that year, and more recent years have had less time to accumulate modifications.

**Amounts are stored as floating point.** `award_amount` is a SQLite `REAL`. Every raw amount has at most two decimal places and the database total matches the raw total to the cent, so this was not changed to integer cents.

**Mixed award sizes.** Positive amounts range from 0.01 to about 1.99 billion dollars. No outliers were removed or capped.
