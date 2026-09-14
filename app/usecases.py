"""Curated Muse use cases: real X posts embedded on the dashboard's Use cases tab
and served to agents as JSON via GET /v1/usecases.

To refresh the curation, edit USECASE_TWEETS below (category, handle, tweet URL).
The dashboard and the API both read from this single list.
"""

USECASE_TWEETS = [
    {"category": "Setup", "handle": "Imran_Razaq", "tweet_url": "https://x.com/Imran_Razaq/status/2099563605737844906"},
    {"category": "Work", "handle": "LifeMuseAI", "tweet_url": "https://x.com/LifeMuseAI/status/2099371368257065171"},
    {"category": "Setup", "handle": "JayaNayak21", "tweet_url": "https://x.com/JayaNayak21/status/2099358067326693611"},
    {"category": "Work", "handle": "AIPixelLand", "tweet_url": "https://x.com/AIPixelLand/status/2099413119738990962"},
    {"category": "Work", "handle": "armand_ruiz", "tweet_url": "https://x.com/armand_ruiz/status/2099563826178134036"},
]

USECASE_CATEGORIES = sorted({t["category"] for t in USECASE_TWEETS})
