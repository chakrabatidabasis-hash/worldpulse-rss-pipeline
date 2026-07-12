import json
import os
import re
import html
import hashlib
from datetime import datetime, timedelta, timezone

import feedparser
import firebase_admin
from firebase_admin import credentials, firestore

# --- Setup Firebase ---
service_account_info = json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"])
cred = credentials.Certificate(service_account_info)
firebase_admin.initialize_app(cred)
db = firestore.client()

# --- Load country list ---
with open("countries.json", "r", encoding="utf-8") as f:
    countries = json.load(f)

RETENTION_DAYS = 7


def make_item_id(link: str) -> str:
    """Create a stable, safe document ID from a link."""
    return hashlib.sha256(link.encode("utf-8")).hexdigest()[:24]


def clean_summary(raw: str) -> str:
    """Strip HTML tags and unescape entities from RSS summary text."""
    if not raw:
        return ""
    text = re.sub(r"<[^<]+?>", "", raw)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:300]


def parse_published(entry):
    if getattr(entry, "published_parsed", None):
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def fetch_country_news(country_code: str, lang: str):
    url = f"https://news.google.com/rss?hl={lang}-{country_code}&gl={country_code}&ceid={country_code}:{lang}"
    feed = feedparser.parse(url)

    batch_id = f"batch_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    collection_ref = db.collection("countries").document(country_code).collection("news_items")

    new_count = 0
    for entry in feed.entries[:20]:  # limit per fetch to keep it light
        title = getattr(entry, "title", "").strip()
        link = getattr(entry, "link", "").strip()
        if not title or not link:
            continue

        item_id = make_item_id(link)
        doc_ref = collection_ref.document(item_id)
        existing = doc_ref.get()

        published_at = parse_published(entry)
        source_name = ""
        if hasattr(entry, "source") and hasattr(entry.source, "title"):
            source_name = entry.source.title

        data = {
            "title": title,
            "summary": clean_summary(getattr(entry, "summary", "")),
            "source_name": source_name,
            "source_url": link,
            "language": lang,
            "published_at": published_at,
            "category": "general",
            "media_type": "article",
            "thumbnail_url": "",
            "youtube_video_id": None,
            "fetch_batch_id": batch_id,
            "ttl_expire_at": published_at + timedelta(days=RETENTION_DAYS),
        }

        if existing.exists:
            doc_ref.update({"summary": data["summary"]})  # refresh cleaned summary only
        else:
            doc_ref.set(data)
            new_count += 1

    return new_count


def main():
    total_new = 0
    for country in countries:
        code = country["code"]
        lang = country["lang"]
        try:
            added = fetch_country_news(code, lang)
            total_new += added
            print(f"{code}: added {added} new items")
        except Exception as e:
            print(f"{code}: ERROR - {e}")

    db.collection("app_config").document("fetch_status").set({
        "last_fetch_at": datetime.now(timezone.utc),
        "status": "ok",
        "total_new_items": total_new,
    })
    print(f"Done. Total new items: {total_new}")


if __name__ == "__main__":
    main()
