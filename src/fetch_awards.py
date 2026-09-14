import requests, json, time
from pathlib import Path

URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"

AGENCY = "National Aeronautics and Space Administration"

FISCAL_YEARS = {
    2024: ("2023-10-01", "2024-09-30"),
    2023: ("2022-10-01", "2023-09-30"),
    2022: ("2021-10-01", "2022-09-30")
}

EXTENT_COMPETED_CODES = ["A", "B", "C", "D", "E", "F", "G", "CDO", "NDO"]

FIELDS = [
    "Award ID",
    "generated_internal_id",
    "Recipient Name",
    "Recipient UEI",
    "Award Amount",
    "Start Date",
    "End Date",
    "Contract Award Type",
    "NAICS",
    "Awarding Sub Agency",
]

PAGE_SIZE = 100
SLEEP_SECONDS = 0.5

RAW_DIR = Path("data/raw")

def create_payload(fy_dates, code, page):
    payload = {
        "filters": {
            "award_type_codes": ["A", "B", "C", "D"],
            "agencies": [{"type": "awarding", "tier": "subtier",
                       "name": AGENCY}],
            "time_period": [{"start_date": fy_dates[0], "end_date": fy_dates[1], "date_type": "new_awards_only"}],
            "extent_competed_type_codes": [code]
        },
        "fields": ["Award ID", "Recipient Name", "Recipient UEI", "Award Amount", "Start Date",
                   "Awarding Sub Agency", "Contract Award Type", "NAICS", "generated_internal_id"],
        "page": page,
        "limit": PAGE_SIZE,
    }

    return payload

def fetch_page(payload, retries=3):
    for attempt in range(retries):
        r = requests.post(URL, json=payload)
        if r.status_code == 200:
            time.sleep(SLEEP_SECONDS)
            return r.json()
        print(f"  attempt {attempt + 1} failed ({r.status_code}), retrying...")
        time.sleep(5 * (attempt + 1))
    r.raise_for_status

def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for year, dates in FISCAL_YEARS.items():
        for code in EXTENT_COMPETED_CODES:
            path = RAW_DIR / f"awards_{year}_{code}.json"
            if path.exists():
                print(f"{year} {code}: already done, skipping")
            continue

        page = 1
        collected = []

        while True:
            payload = create_payload(dates, code, page)
            response = fetch_page(payload)
            results = response["results"]
            for award in results:
                award["fiscal_year"] = year
                award["extent_competed"] = code
            collected += results
            if not response["page_metadata"]["hasNext"]:
                break
            page += 1

        path = RAW_DIR / f"awards_{year}_{code}.json"
        with open(path, "w") as f:
            json.dump(collected, f)
        print(f"{year} {code}: saved {len(collected)} awards to {path}")
        
if __name__ == "__main__":
    main()