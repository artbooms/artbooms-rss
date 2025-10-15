import json, re
from dateutil import parser
from datetime import datetime
from pathlib import Path

# Percorso del file di input e output
INPUT_FILE = "cache.txt"
OUTPUT_FILE = "articles_cache_clean.json"

def parse_date_safe(value):
    try:
        return parser.parse(value)
    except Exception:
        return None

def main():
    path = Path(INPUT_FILE)
    text = path.read_text(encoding="utf-8", errors="ignore")

    # Estrai blocchi di JSON elementari
    blocks = re.findall(r'\{[^{}]*"url"[^{}]*\}', text)
    records = []
    for b in blocks:
        try:
            obj = json.loads(b)
            if "published" in obj and obj["published"]:
                dt = parse_date_safe(obj["published"])
                if dt:
                    obj["_dt"] = dt
                    records.append(obj)
        except Exception:
            continue

    # Ordina per data crescente
    records.sort(key=lambda x: x["_dt"])

    # Rimuovi duplicati http/https
    seen = set()
    clean = []
    for r in records:
        url = r["url"].replace("http://", "https://").rstrip("/")
        if url not in seen:
            seen.add(url)
            r["url"] = url
            clean.append(r)

    # Trova punto di rottura
    break_idx = None
    for i in range(1, len(clean)):
        if clean[i]["_dt"] < clean[i-1]["_dt"]:
            break_idx = i
            break

    if break_idx:
        clean = clean[:break_idx]

    # Rimuovi contaminazioni palesi
    for r in clean:
        txt = r.get("content_text", "")
        if any(y in txt for y in ["Biennale 2026", "Vivian Suter", "2025 ", "2024 "]):
            r["content_text"] = ""

    # Salva cache pulita
    out = {"items": clean}
    Path(OUTPUT_FILE).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ Cache pulita salvata in {OUTPUT_FILE}")
    print(f"📊 Articoli mantenuti: {len(clean)}")
    print(f"📅 Intervallo: {clean[0]['_dt'].isoformat()} → {clean[-1]['_dt'].isoformat()}")
    if break_idx:
        print(f"✂️ Taglio al punto {break_idx}")

if __name__ == "__main__":
    main()
