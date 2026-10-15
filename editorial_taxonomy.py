"""The approved general editorial category for every RSS article.

Categories are presentation metadata, independent of the stored article
record, titles and Squarespace tags.
"""

CATEGORY_VOCABULARY = frozenset({"arte"})
ARTICLE_CATEGORIES = {}


def categories_for(item):
    return ("arte",)
