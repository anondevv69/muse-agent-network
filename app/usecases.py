"""Curated Muse use cases: real X posts embedded on the dashboard's Use cases tab
and served to agents as JSON via GET /v1/usecases.

To refresh the curation, edit USECASE_TWEETS below (category, handle, tweet URL).
The dashboard and the API both read from this single list.
"""

USECASE_TWEETS = [
    {"category": "Work", "handle": "thatguybg", "tweet_url": "https://x.com/thatguybg/status/2097454629839663173"},
    {"category": "Money", "handle": "borrowed_ideas", "tweet_url": "https://x.com/borrowed_ideas/status/2098172087550808218"},
    {"category": "Food", "handle": "davidnjiang", "tweet_url": "https://x.com/davidnjiang/status/2097510427185885266"},
    {"category": "Shopping", "handle": "jrlevine", "tweet_url": "https://x.com/jrlevine/status/2097402800355561610"},
    {"category": "Health", "handle": "jablamsky", "tweet_url": "https://x.com/jablamsky/status/2099566262431203737"},
    {"category": "Travel", "handle": "shivambharuka", "tweet_url": "https://x.com/shivambharuka/status/2097840314241433994"},
    {"category": "Work", "handle": "eric1021945", "tweet_url": "https://x.com/eric1021945/status/2099183756607422521"},
    {"category": "Setup", "handle": "Michaelzsguo", "tweet_url": "https://x.com/Michaelzsguo/status/2099483651062776105"},
    {"category": "Work", "handle": "thesaleemjaffer", "tweet_url": "https://x.com/thesaleemjaffer/status/2097051454351561064"},
    {"category": "Work", "handle": "syakirbuilds", "tweet_url": "https://x.com/syakirbuilds/status/2097225246646391282"},
    {"category": "Admin", "handle": "MrDlamini", "tweet_url": "https://x.com/MrDlamini/status/2097486454284796180"},
    {"category": "Goals", "handle": "denmarjo", "tweet_url": "https://x.com/denmarjo/status/2098528670918856851"},
    {"category": "Admin", "handle": "PipeBladex", "tweet_url": "https://x.com/PipeBladex/status/2099565152886837626"},
    {"category": "Admin", "handle": "freedomNov5", "tweet_url": "https://x.com/freedomNov5/status/2099506762348962026"},
    {"category": "Work", "handle": "attilah", "tweet_url": "https://x.com/attilah/status/2098993776253710363"},
    {"category": "Travel", "handle": "iamsomewalrus", "tweet_url": "https://x.com/iamsomewalrus/status/2098125520596783503"},
    {"category": "Money", "handle": "MindVestAtlas", "tweet_url": "https://x.com/MindVestAtlas/status/2097420621663940792"},
    {"category": "Work", "handle": "plumberbutt97", "tweet_url": "https://x.com/plumberbutt97/status/2097502931146707374"},
    {"category": "Health", "handle": "humblyonline", "tweet_url": "https://x.com/humblyonline/status/2099570866405229002"},
    {"category": "Work", "handle": "ManuInvests", "tweet_url": "https://x.com/ManuInvests/status/2099570332340269409"},
    {"category": "Admin", "handle": "0xKrampuss", "tweet_url": "https://x.com/0xKrampuss/status/2099561403522716142"},
    {"category": "Work", "handle": "creativactive", "tweet_url": "https://x.com/creativactive/status/2098645646085673467"},
    {"category": "Setup", "handle": "_joannhu", "tweet_url": "https://x.com/_joannhu/status/2097772966419574848"},
    {"category": "Work", "handle": "shydev69", "tweet_url": "https://x.com/shydev69/status/2099540655798362204"},
    {"category": "Work", "handle": "DustinDavis", "tweet_url": "https://x.com/DustinDavis/status/2097545505312252282"},
    {"category": "Admin", "handle": "dlippsYT", "tweet_url": "https://x.com/dlippsYT/status/2099589473642156117"},
    {"category": "Setup", "handle": "holman", "tweet_url": "https://x.com/holman/status/2099588794689437736"},
    {"category": "Admin", "handle": "TerenceChang", "tweet_url": "https://x.com/TerenceChang/status/2099585597572837382"},
    {"category": "Admin", "handle": "peterleeb", "tweet_url": "https://x.com/peterleeb/status/2099586072397406248"},
]

USECASE_CATEGORIES = sorted({t["category"] for t in USECASE_TWEETS})
