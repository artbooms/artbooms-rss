"""Explicit editorial categories for RSS, not Google News Publisher Center labels.

The two site-wide subject categories correspond to the existing publication
keywords. Extra article-specific categories require explicit URL mapping and
approval; no categories are inferred from titles or Squarespace tags.
"""

CATEGORY_VOCABULARY = frozenset(("arte contemporanea", "arte e cultura"))
DEFAULT_CATEGORIES = ("arte contemporanea", "arte e cultura")
ARTICLE_CATEGORIES = {}


def categories_for(item):
    result = list(DEFAULT_CATEGORIES)
    values = ARTICLE_CATEGORIES.get(item.get("url"), ())
    if not isinstance(values, (list, tuple)):
        return tuple(result)
    for value in values:
        if (isinstance(value, str) and value.strip()
                and value in CATEGORY_VOCABULARY and value not in result):
            result.append(value)
    return tuple(result)
