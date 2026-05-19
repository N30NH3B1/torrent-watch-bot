import os
import re
import json
import requests
import feedparser
import subprocess

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

TMDB_TOKEN = os.environ.get("TMDB_TOKEN", "")
OMDB_API_KEY = os.environ.get("OMDB_API_KEY", "")

TRAKT_CLIENT_ID = os.environ.get("TRAKT_CLIENT_ID", "")
TRAKT_USERNAME = os.environ.get("TRAKT_USERNAME", "")
TRAKT_LIST_SLUG = os.environ.get("TRAKT_LIST_SLUG", "")
TRAKT_ACCESS_TOKEN = os.environ.get("TRAKT_ACCESS_TOKEN", "")
TRAKT_REFRESH_TOKEN = os.environ.get("TRAKT_REFRESH_TOKEN", "")

WATCHLIST_FILE = "watchlist.json"
SEEN_FILE = "seen_matches.json"
OFFSET_FILE = "telegram_offset.json"

RSS_URLS = [
    "https://fosstorrents.com/feed/torrents.xml",
    "https://archive.org/services/collection-rss.php?collection=feature_films",
    "https://archive.org/services/collection-rss.php?collection=opensource_movies",
    "https://archive.org/services/collection-rss.php?collection=community_video",
    "https://4krss.nl",
    "https://feed.animetosho.org/rss2?only_tor=1&filter%5B0%5D%5Bt%5D=nyaa_class&filter%5B0%5D%5Bv%5D=remake&aid=18290",
    "https://fosstorrents.com/feed/torrents.xml",
    "https://fosstorrents.com/feed/rss.xml"
]


def load_json(filename, default):
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def commit_changes(message):
    subprocess.run(["git", "config", "user.name", "github-actions"], check=True)
    subprocess.run(["git", "config", "user.email", "github-actions@github.com"], check=True)
    subprocess.run(["git", "add", WATCHLIST_FILE, SEEN_FILE, OFFSET_FILE], check=True)

    result = subprocess.run(["git", "diff", "--cached", "--quiet"], check=False)

    if result.returncode != 0:
        subprocess.run(["git", "commit", "-m", message], check=True)
        subprocess.run(["git", "push"], check=True)


def send_message(text):
    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text},
        timeout=20
    )
    response.raise_for_status()


def normalize(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def trakt_headers(authenticated=False):
    headers = {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": TRAKT_CLIENT_ID
    }

    if authenticated and TRAKT_ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {TRAKT_ACCESS_TOKEN}"

    return headers


def fetch_trakt_list_items():
    if not TRAKT_CLIENT_ID or not TRAKT_USERNAME or not TRAKT_LIST_SLUG:
        print("Trakt config missing.")
        return []

    url = (
        f"https://api.trakt.tv/users/{TRAKT_USERNAME}"
        f"/lists/{TRAKT_LIST_SLUG}/items"
    )

    response = requests.get(url, headers=trakt_headers(), timeout=30)

    if response.status_code == 404:
        print("Trakt list not found. Check TRAKT_USERNAME and TRAKT_LIST_SLUG.")
        return []

    response.raise_for_status()
    return response.json()


def remove_from_trakt_list(item):
    trakt_id = item.get("trakt_id")

    if not trakt_id:
        print("Cannot remove from Trakt: missing trakt_id.")
        return False

    media_type = "movies"

    if item.get("type") == "series":
        media_type = "shows"

    payload = {
        media_type: [
            {
                "ids": {
                    "trakt": trakt_id
                }
            }
        ]
    }

    url = (
        f"https://api.trakt.tv/users/{TRAKT_USERNAME}"
        f"/lists/{TRAKT_LIST_SLUG}/items"
    )

    response = requests.delete(
        url,
        headers=trakt_headers(authenticated=True),
        json=payload,
        timeout=30
    )

    if response.status_code >= 400:
        print("Failed removing from Trakt:", response.status_code, response.text)
        return False

    print("Removed from Trakt:", watch_item_display(item))
    return True


def trakt_item_to_watch_item(item):
    media_type = item.get("type")

    if media_type == "movie":
        media = item.get("movie", {})
        title = media.get("title")
        year = media.get("year")
        ids = media.get("ids", {})
        item_type = "movie"

    elif media_type == "show":
        media = item.get("show", {})
        title = media.get("title")
        year = media.get("year")
        ids = media.get("ids", {})
        item_type = "series"

    else:
        return None

    if not title:
        return None

    return {
        "source": "trakt",
        "type": item_type,
        "title": title,
        "year": str(year) if year else None,
        "imdb_id": ids.get("imdb"),
        "tmdb_id": ids.get("tmdb"),
        "trakt_id": ids.get("trakt"),
        "display": f"{title} ({year})" if year else title
    }


def watch_item_key(item):
    title = normalize(item.get("title", ""))

    if item.get("year"):
        title += "|" + str(item["year"])

    return title


def watch_item_display(item):
    return item.get("display") or item.get("title") or item.get("imdb_id") or "Unknown"


def sync_trakt_list_to_watchlist():
    watchlist = load_json(WATCHLIST_FILE, [])
    trakt_items = fetch_trakt_list_items()

    updated_watchlist = []
    trakt_by_key = {}

    for trakt_item in trakt_items:
        watch_item = trakt_item_to_watch_item(trakt_item)

        if not watch_item:
            continue

        trakt_by_key[watch_item_key(watch_item)] = watch_item

    used_keys = set()
    upgraded = []
    added = []

    for existing_item in watchlist:
        existing_key = watch_item_key(existing_item)

        if existing_key in trakt_by_key:
            rich_item = trakt_by_key[existing_key]
            updated_watchlist.append(rich_item)
            used_keys.add(existing_key)

            if existing_item != rich_item:
                upgraded.append(watch_item_display(rich_item))
        else:
            updated_watchlist.append(existing_item)

    for key, watch_item in trakt_by_key.items():
        if key not in used_keys:
            updated_watchlist.append(watch_item)
            added.append(watch_item_display(watch_item))

    save_json(WATCHLIST_FILE, updated_watchlist)

    if added:
        print("Added from Trakt:", added)

    if upgraded:
        print("Upgraded from Trakt:", upgraded)


def watch_item_matches_entry(item, entry):
    title = entry.get("title", "")
    link = entry.get("link", "")

    searchable_text = normalize(title + " " + link)
    wanted_title = normalize(item.get("title", ""))

    if not wanted_title:
        return False

    if wanted_title not in searchable_text:
        return False

    if item.get("year") and str(item["year"]) not in searchable_text:
        return False

    return True


def get_updates():
    data = load_json(OFFSET_FILE, {"offset": None})
    params = {}

    if data.get("offset") is not None:
        params["offset"] = data["offset"]

    response = requests.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
        params=params,
        timeout=20
    )

    response.raise_for_status()
    updates = response.json().get("result", [])

    if updates:
        data["offset"] = updates[-1]["update_id"] + 1
        save_json(OFFSET_FILE, data)

    return updates


def handle_commands():
    watchlist = load_json(WATCHLIST_FILE, [])
    updates = get_updates()

    for update in updates:
        message = update.get("message", {})
        chat = message.get("chat", {})
        text = message.get("text", "").strip()

        if str(chat.get("id")) != str(CHAT_ID):
            continue

        if text == "/list":
            if watchlist:
                names = [watch_item_display(item) for item in watchlist]
                send_message("Current Trakt-synced watchlist 🎬\n\n" + "\n".join(names))
            else:
                send_message("Your watchlist is empty.")

        elif text == "/help":
            send_message(
                "Commands:\n"
                "/list - show current synced watchlist\n"
                "/help - show this message\n\n"
                "Add movies/shows in Trakt now. Telegram is only for notifications."
            )

        elif text.startswith("/watch ") or text.startswith("/remove "):
            send_message(
                "Adding/removing is now handled in Trakt.\n\n"
                "Add or remove items from your Trakt list, then run the workflow."
            )


def check_feeds():
    watchlist = load_json(WATCHLIST_FILE, [])
    seen = load_json(SEEN_FILE, [])

    if not watchlist:
        return

    notified_keys = []

    for item in watchlist:
        item_key = watch_item_key(item)
        already_notified_key = "NOTIFIED|" + item_key

        if already_notified_key in seen:
            continue

        found_entry = None

        for rss_url in RSS_URLS:
            feed = feedparser.parse(rss_url)

            for entry in feed.entries:

                print(
                    "Checking:",
                    watch_item_display(item),
                    "against",
                    entry.get("title", "")
                )
            
                if watch_item_matches_entry(item, entry):
            
                    print("MATCH FOUND:", watch_item_display(item))
            
                    found_entry = {
                        "title": entry.get("title", ""),
                        "link": entry.get("link", "")
                    }
            
                    break

            if found_entry:
                break

        if found_entry:
            send_message(
                f"Available now 🎬\n\n"
                f"{watch_item_display(item)}\n\n"
                f"{found_entry['title']}\n"
                f"{found_entry['link']}"
            )

            seen.append(already_notified_key)
            notified_keys.append(item_key)
            save_json(SEEN_FILE, seen)

            if item.get("source") == "trakt":
                remove_from_trakt_list(item)

    if notified_keys:
        watchlist = [
            item for item in watchlist
            if watch_item_key(item) not in notified_keys
        ]
        save_json(WATCHLIST_FILE, watchlist)


def main():
    sync_trakt_list_to_watchlist()
    handle_commands()
    check_feeds()
    commit_changes("Update bot state")
    print("Bot check complete.")


if __name__ == "__main__":
    main()
