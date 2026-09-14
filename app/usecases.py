"""Curated Muse use cases: real X posts embedded on the dashboard's Use cases tab
and served to agents as JSON via GET /v1/usecases.

To refresh the curation, edit USECASE_TWEETS below (category, handle, tweet URL).
The dashboard and the API both read from this single list.
"""

USECASE_TWEETS = [
    {"category": "Admin", "handle": "freedomNov5", "tweet_url": "https://x.com/freedomNov5/status/2099506762348962026"},
    {"category": "Work", "handle": "attilah", "tweet_url": "https://x.com/attilah/status/2098993776253710363"},
    {"category": "Travel", "handle": "iamsomewalrus", "tweet_url": "https://x.com/iamsomewalrus/status/2098125520596783503"},
    {"category": "Money", "handle": "MindVestAtlas", "tweet_url": "https://x.com/MindVestAtlas/status/2097420621663940792"},
    {"category": "Work", "handle": "plumberbutt97", "tweet_url": "https://x.com/plumberbutt97/status/2097502931146707374"},
]

USECASE_CATEGORIES = sorted({t["category"] for t in USECASE_TWEETS})
