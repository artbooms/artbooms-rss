// Cloudflare Worker per Artbooms News Sitemap
//
// - NON tocca il sito, né Squarespace, né il feed su Render
// - Legge SOLO la cache JSON pubblica su GitHub
// - Genera una sitemap news per gli ultimi N giorni

const CACHE_URL = "https://raw.githubusercontent.com/artbooms/artbooms-rss-v2/main/cache/articles_cache.json";

// Finestra temporale: 29 giorni (restiamo sotto i 30, se Google News ignora il vecchio non è un problema)
const DAYS_WINDOW = 29;

const SITE_NAME = "ARTBOOMS";
const LANG = "it";
const KEYWORDS = "arte contemporanea, arte e cultura";

function escapeXml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

async function buildNewsSitemap() {
  let res;
  try {
    res = await fetch(CACHE_URL, {
      headers: {
        "User-Agent": "ArtboomsNewsSitemapBot/1.0",
      },
    });
  } catch (e) {
    // in caso di errore rete → sitemap vuota ma valida
    return emptyNewsSitemap();
  }

  if (!res.ok) {
    return emptyNewsSitemap();
  }

  let data;
  try {
    data = await res.json();
  } catch (e) {
    return emptyNewsSitemap();
  }

  let itemsRaw = data && data.items ? data.items : [];
  let items = [];

  if (Array.isArray(itemsRaw)) {
    items = itemsRaw;
  } else if (typeof itemsRaw === "object" && itemsRaw !== null) {
    items = Object.values(itemsRaw);
  }

  const now = new Date();
  const cutoffMs = now.getTime() - DAYS_WINDOW * 24 * 60 * 60 * 1000;

  const recentItems = items
    .filter((it) => it && typeof it === "object")
    // SOLO articoli del blog
    .filter((it) => (it.url || "").includes("/blog/"))
    .map((it) => {
      return {
        url: (it.url || "").trim(),
        title: (it.title || "").trim(),
        published: (it.published || "").trim(),
      };
    })
    .filter((it) => it.url && it.title && it.published)
    .map((it) => {
      const d = new Date(it.published.replace("Z", "+00:00"));
      return { ...it, dateObj: d };
    })
    .filter((it) => !isNaN(it.dateObj.getTime()))
    // solo articoli nella finestra temporale
    .filter(
      (it) =>
        it.dateObj.getTime() >= cutoffMs &&
        it.dateObj.getTime() <= now.getTime() + 60 * 60 * 1000
    )
    // ordina dal più recente al meno recente
    .sort((a, b) => b.dateObj.getTime() - a.dateObj.getTime());

  // Costruzione XML
  let xmlParts = [];
  xmlParts.push('<?xml version="1.0" encoding="UTF-8"?>');
  xmlParts.push(
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" ' +
      'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">'
  );

  for (const it of recentItems) {
    const loc = escapeXml(it.url);
    const title = escapeXml(it.title);
    const pubIso = it.dateObj.toISOString();

    xmlParts.push("  <url>");
    xmlParts.push(`    <loc>${loc}</loc>`);
    xmlParts.push("    <news:news>");
    xmlParts.push("      <news:publication>");
    xmlParts.push(`        <news:name>${escapeXml(SITE_NAME)}</news:name>`);
    xmlParts.push(`        <news:language>${LANG}</news:language>`);
    xmlParts.push("      </news:publication>");
    xmlParts.push(`      <news:keywords>${KEYWORDS}</news:keywords>`);
    xmlParts.push(`      <news:publication_date>${pubIso}</news:publication_date>`);
    // Titolo con suffisso " — ARTBOOMS" come avevamo deciso
    xmlParts.push(`      <news:title>${title} — ${escapeXml(SITE_NAME)}</news:title>`);
    xmlParts.push("    </news:news>");
    xmlParts.push("  </url>");
  }

  xmlParts.push("</urlset>");

  return xmlParts.join("\n");
}

function emptyNewsSitemap() {
  return (
    '<?xml version="1.0" encoding="UTF-8"?>' +
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" ' +
    'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9"></urlset>'
  );
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // Endpoint principale: /news-sitemap.xml
    if (url.pathname === "/news-sitemap.xml") {
      const xml = await buildNewsSitemap();
      return new Response(xml, {
        status: 200,
        headers: {
          "Content-Type": "application/xml; charset=UTF-8",
          "Cache-Control": "no-cache",
        },
      });
    }

    // Pagina principale ( / ) con meta tag di verifica di Search Console
    if (url.pathname === "/" || url.pathname === "") {
      const html = `
        <html>
          <head>
            <meta name="google-site-verification" content="FNFRJ_2vO9IDH-7Vk7z7FmoUh4ralTwuoITmQaGYEto" />
          </head>
          <body>
            <h2>✅ Artbooms News Sitemap worker attivo</h2>
            <p>Sitemap news: <a href="/news-sitemap.xml">/news-sitemap.xml</a></p>
          </body>
        </html>`;
      return new Response(html, {
        status: 200,
        headers: { "Content-Type": "text/html; charset=UTF-8" },
      });
    }

    // Tutto il resto → 404 semplice
    return new Response("Not found", { status: 404 });
  },
};
