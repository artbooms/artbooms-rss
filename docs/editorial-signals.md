# Editorial signals: publication headline and dates

Squarespace exposes separate fields for the visible article headline and the
SEO title. `itemprop="name"` contains the SEO title, including a site suffix;
`itemprop="headline"` and the article H1 contain the editorial title.

The parser now reads the article H1 first, then the native headline, retaining
the old name field only as a fallback. This corrects the primary acquisition
step for both RSS and News sitemap. SEO settings on the site are unaffected.
Google requires `news:title` to be the title as it appears on the article,
without the publication name. The obsolete `news:keywords` extension is
omitted; RSS still includes the approved `arte` category.

The real native modification timestamp is retained. Publication time alone
continues to control RSS order, the 48-hour News window and existing WebSub
publication detection. Existing explicit UTC offsets are unchanged. Dates
without an offset are interpreted in the site's Europe/Rome timezone.

## Archive and production boundaries

No archive snapshot, cursor, GUID, WebSub state, notification frequency or PWA
logic is replaced by this change. Existing cache records converge through
the ordinary protected refresh cycle; a new article uses the corrected
parser immediately. Do not reset or force-repopulate the archive to accelerate
that convergence. At three refreshed articles per 120-second cycle, a complete
1,141-entry pass takes about 13 hours, plus request time and interruptions.

The native Squarespace `Article` is the baseline article representation. A
second manual `NewsArticle` is not an admission requirement for Google News
or Discover. New manual copies should not be added. Existing manual copies
need individual review: an alternate concise headline alone does not prove a
semantic error. Correct proven wrong URL identities, authors or publication
instants at their CMS source, with a backup and a retained native Article.
Never add a third schema or a global JavaScript schema rewriter.

## Read-only preflight

The audit command compares original HTML, native and manual article schemas,
cache, RSS, News sitemap and ordinary sitemap. Its JSON distinguishes errors
from review items. It does not write to production, refresh the application
cache, notify a hub or block feed distribution.

For one article, capture the current public RSS and sitemaps once, then run:

```sh
python scripts/audit_editorial_signals.py \
  --cache cache/articles_cache.json \
  --sources /tmp/artbooms-audit/html \
  --output /tmp/artbooms-audit/report.json \
  --rss /tmp/artbooms-audit/rss.xml \
  --news /tmp/artbooms-audit/news.xml \
  --sitemap /tmp/artbooms-audit/sitemap.xml \
  --url https://www.artbooms.com/blog/ARTICLE-SLUG --fetch --refresh
```

Use only captured HTML, without `--fetch`, for offline review of a draft or
repeatable comparison. Names are preserved by the verified editorial override
map. Missing or inaccessible evidence is reported, not assumed correct.
The command reads the public site; it cannot automatically access unpublished
Squarespace drafts or certify the rendered DOM or Search Console state.

## Validation after release

1. Confirm the intended commit is live on the existing Render service.
2. Verify RSS still contains the complete archive with the same URL/GUIDs and
   original publication instants; check conditional GET and the legacy redirect.
3. On a new publication, capture the site, first RSS appearance and News entry
   times separately. The original timestamp must be the same instant in all.
4. Check WebSub's existing durable receipt without resetting its state. A hub
   acknowledgement or FeedFetcher request does not establish HTML crawling.
5. Use Search Console URL Inspection for the HTML URL, Google canonical and
   crawl result. Its latest crawl is not necessarily the first crawl.
6. Track Google News, Search's News tab and Discover separately. Neither XML
   validation nor a code deployment guarantees impressions on those surfaces.

References: [Google News sitemaps](https://developers.google.com/search/docs/crawling-indexing/sitemaps/news-sitemap),
[Article](https://developers.google.com/search/docs/appearance/structured-data/article),
[Discover](https://developers.google.com/search/docs/appearance/google-discover).
