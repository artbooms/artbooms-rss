"""Editorial categories only. No inference from titles or Squarespace tags.

Initially empty: existing metadata and RSS items remain unchanged.
To activate, add approved labels to CATEGORY_VOCABULARY and assign them to
explicit article URLs in ARTICLE_CATEGORIES. Missing categories do not block.
"""

CATEGORY_VOCABULARY = frozenset()
ARTICLE_CATEGORIES = {}


def categories_for(item):
    values = ARTICLE_CATEGORIES.get(item.get("url"), ())
    if not isinstance(values, (list, tuple)):
        return ()
    result = []
    for value in values:
        if (isinstance(value, str) and value.strip()
                and value in CATEGORY_VOCABULARY and value not in result):
            result.append(value)
    return tuple(result)
