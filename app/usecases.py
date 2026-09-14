"""Curated Muse use cases: real X posts embedded on the dashboard's Use cases tab
and served to agents as JSON via GET /v1/usecases.

To refresh the curation, edit USECASE_TWEETS below (category, handle, tweet URL).
The dashboard and the API both read from this single list.
"""

USECASE_TWEETS = [
    {"category": "Work", "handle": "ucsandman", "tweet_url": "https://x.com/ucsandman/status/2099553345916576006"},
    {"category": "Setup", "handle": "dani_avila7", "tweet_url": "https://x.com/dani_avila7/status/2099548337254867237"},
    {"category": "Money", "handle": "Beliy_95", "tweet_url": "https://x.com/Beliy_95/status/2099528806906122645"},
    {"category": "Work", "handle": "DanKornas", "tweet_url": "https://x.com/DanKornas/status/2099520807563120885"},
    {"category": "Goals", "handle": "mustang_akin", "tweet_url": "https://x.com/mustang_akin/status/2099530958944776384"},
]

USECASE_CATEGORIES = sorted({t["category"] for t in USECASE_TWEETS})
