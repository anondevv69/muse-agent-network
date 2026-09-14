"""Curated Muse use cases: real X posts embedded on the dashboard's Use cases tab
and served to agents as JSON via GET /v1/usecases.

To refresh the curation, edit USECASE_TWEETS below (category, handle, tweet URL).
The dashboard and the API both read from this single list.
"""

USECASE_TWEETS = [
    {"category": "Admin", "handle": "jeff_weinstein", "tweet_url": "https://x.com/jeff_weinstein/status/2097416321218535450"},
    {"category": "Travel", "handle": "boztank", "tweet_url": "https://x.com/boztank/status/2097401739796451938"},
    {"category": "Goals", "handle": "davidsven", "tweet_url": "https://x.com/davidsven/status/2097411563946930333"},
    {"category": "Events", "handle": "altryne", "tweet_url": "https://x.com/altryne/status/2097430715923399135"},
    {"category": "Setup", "handle": "altryne", "tweet_url": "https://x.com/altryne/status/2097444743760482437"},
    {"category": "Setup", "handle": "infoxiao", "tweet_url": "https://x.com/infoxiao/status/2097461550286258624"},
    {"category": "Shopping", "handle": "Shopify", "tweet_url": "https://x.com/Shopify/status/2097408290967707950"},
    {"category": "Food", "handle": "spottedinprod", "tweet_url": "https://x.com/spottedinprod/status/2097461280705565016"},
    {"category": "Shopping", "handle": "signulll", "tweet_url": "https://x.com/signulll/status/2097416338147049795"},
    {"category": "Money", "handle": "blauyourmind", "tweet_url": "https://x.com/blauyourmind/status/2097439129684644089"},
    {"category": "Admin", "handle": "wondernews_now", "tweet_url": "https://x.com/wondernews_now/status/2097420564633895363"},
    {"category": "Setup", "handle": "testingcatalog", "tweet_url": "https://x.com/testingcatalog/status/2097472570450726970"},
    {"category": "Goals", "handle": "salty0409", "tweet_url": "https://x.com/salty0409/status/2097420875720974736"},
    {"category": "Admin", "handle": "alvinfoo", "tweet_url": "https://x.com/alvinfoo/status/2097481399066632347"},
    {"category": "Food", "handle": "pitdesi", "tweet_url": "https://x.com/pitdesi/status/2097449401363181602"},
    {"category": "Health", "handle": "tavitag203", "tweet_url": "https://x.com/tavitag203/status/2097426720001286622"},
    {"category": "Admin", "handle": "Girlcandycandy", "tweet_url": "https://x.com/Girlcandycandy/status/2097546836315672906"},
    {"category": "Travel", "handle": "i_quiterres99", "tweet_url": "https://x.com/i_quiterres99/status/2097546740698103847"},
    {"category": "Work", "handle": "THEMDAMNDOGS", "tweet_url": "https://x.com/THEMDAMNDOGS/status/2097522779230753198"},
    {"category": "Goals", "handle": "jerrod_lew", "tweet_url": "https://x.com/jerrod_lew/status/2097517814278156620"},
]

USECASE_CATEGORIES = sorted({t["category"] for t in USECASE_TWEETS})
