"""Explicit editorial corrections, independent of backend access permissions.

Normally the parser reads the public author metadata of the article. In
Squarespace, assign the real author to the post; a Basic author needs no login.
Use this map only for a verified editorial attribution that differs from CMS
metadata. A person's administrative role is never a source of authorship.
"""

ARTICLE_AUTHOR_OVERRIDES = {
    # Public byline: https://www.artbooms.com/blog/mimmo-rotella-e-il-cinema
    "https://www.artbooms.com/blog/mimmo-rotella-e-il-cinema":
        "Martina e Mattia Stripartgallery",
}


def author_for(url, reported_author):
    """Prefer an explicit editorial attribution; do not invent missing names."""
    value = ARTICLE_AUTHOR_OVERRIDES.get(url, reported_author)
    return value if isinstance(value, str) else None
