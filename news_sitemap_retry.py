"""Bounded, process-local recovery for the optional XML-topic notification.

No hub subscription or Google News ingestion is implied by an accepted POST.
RSS durable state is deliberately separate. Restarting does not replay articles.
"""
import logging

from cache_safety import parsed_time

logger = logging.getLogger("news_sitemap_retry")
NEWS_SECONDS = 48 * 60 * 60


class PendingNewsNotifications:
    MAX_ATTEMPTS = 8
    MAX_AGE = 30 * 60
    RETRY_INTERVAL = 120
    MAX_PENDING = 1000

    def __init__(self):
        self.pending = {}

    def clear(self):
        self.pending.clear()

    def observe(self, urls, items, *, now, monotonic):
        for url in urls:
            if (not isinstance(url, str)
                    or not url.startswith("https://www.artbooms.com/blog/")):
                continue
            item = items.get(url)
            published = parsed_time(item.get("published")) if isinstance(item, dict) else None
            if published is None or not 0 <= now - published.timestamp() < NEWS_SECONDS:
                continue
            if url not in self.pending and len(self.pending) < self.MAX_PENDING:
                self.pending[url] = {"published": published.timestamp(), "first": monotonic,
                                     "next": monotonic, "attempts": 0}

    def dispatch(self, sender, ready_urls, *, now, monotonic):
        expired = [url for url, event in self.pending.items()
                   if (not 0 <= now - event["published"] < NEWS_SECONDS
                       or monotonic - event["first"] >= self.MAX_AGE
                       or event["attempts"] >= self.MAX_ATTEMPTS)]
        for url in expired:
            self.pending.pop(url)
            logger.warning("News sitemap: tentativi conclusi per %s; nessun replay", url)
        due = [url for url, event in self.pending.items()
               if url in ready_urls and monotonic >= event["next"]]
        if not due:
            return False
        # Reserve the attempt before I/O; callbacks do not run under feed locks.
        for url in due:
            event = self.pending[url]
            event["attempts"] += 1
            event["next"] = monotonic + self.RETRY_INTERVAL
        try:
            accepted = sender(due)
        except Exception as exc:
            logger.warning("News sitemap: errore isolato nel tentativo (%s)", type(exc).__name__)
            accepted = False
        if accepted:
            for url in due:
                self.pending.pop(url, None)
            return True
        logger.warning("News sitemap: avviso non confermato; recupero limitato nei prossimi cicli")
        return False
