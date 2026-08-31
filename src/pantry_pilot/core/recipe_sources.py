"""Vetted recipe-source domains for source #2 ("what's hot right now").

WHAT  Two frozensets of bare, lowercased netlocs (no "www.").
WHY   ALLOW_DOMAINS is the deterministic gate: services/trending.py._filter keeps a
      recipe only if its source_url's domain is on this list. The persona *steers* the
      model toward these sites; _filter *enforces* it (belt-and-suspenders). BLOCK_DOMAINS
      names the famous paywalls so the persona can warn the model off them — _filter needs
      only the allow-check, since anything not allowed (paywalled or otherwise) is dropped.
HOW   Config-driven + extensible: add a domain here to widen coverage. Promote a BLOCK to
      ALLOW only alongside auth wiring (paywalled sites need a logged-in session).
"""

ALLOW_DOMAINS: frozenset[str] = frozenset(
    {
        "seriouseats.com", "cookwell.com", "budgetbytes.com", "cookieandkate.com",
        "simplyrecipes.com", "thekitchn.com", "onceuponachef.com", "damndelicious.net",
        "loveandlemons.com", "minimalistbaker.com", "recipetineats.com", "smittenkitchen.com",
        "pinchofyum.com", "halfbakedharvest.com", "skinnytaste.com", "tasty.co",
        "delish.com", "allrecipes.com", "foodnetwork.com", "bbcgoodfood.com",
    }
)

BLOCK_DOMAINS: frozenset[str] = frozenset(
    {
        "cooking.nytimes.com", "nytimes.com", "americastestkitchen.com", "cooksillustrated.com",
        "washingtonpost.com", "bonappetit.com", "epicurious.com",
    }
)
