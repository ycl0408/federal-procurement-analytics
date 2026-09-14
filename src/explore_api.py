import requests, json

URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"

payload = {
    "filters": {
        "award_type_codes": ["A", "B", "C", "D"],
        "agencies": [{"type": "awarding", "tier": "subtier",
                      "name": "National Aeronautics and Space Administration"}],
        "time_period": [{"start_date": "2023-01-01", "end_date": "2024-09-30", "date_type": "new_awards_only"}],
        "extent_competed_type_codes": ["A"]
    },
    "fields": ["Award ID", "Recipient Name", "Recipient UEI", "Award Amount", "Start Date",
               "Awarding Sub Agency", "Contract Award Type", "NAICS", "generated_internal_id"],
    "page": 1,
    "limit": 5,
}

r = requests.post(URL, json=payload)
r.raise_for_status()
print(json.dumps(r.json(), indent=2))