import os
import re
import json
import requests
import feedparser
import subprocess

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
OMDB_API_KEY = os.environ.get("OMDB_API_KEY", "")

WATCHLIST_FILE = "watchlist.json"
SEEN_FILE = "seen_matches.json"
OFFSET_FILE = "telegram_offset.json"

RSS_URLS = [
    "https://fosstorrents.com/feed/torrents.xml"
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

    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        check=False
    )

    if result.returncode != 0:
        subprocess.run(["git", "commit", "-m", message], check=True)
        subprocess.run(["git", "push"], check=True)


def send_message(text):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={
            "chat_id": CHAT_ID,
            "text": text
        },
        timeout=20
    )


def normalize(text):
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()

def is_imdb_id(text):
    return re.fullmatch(r"tt\d{7,9}", text.strip().lower()) is not None


def parse_title_and_year(text):
    match = re.match(r"^(.*?)(?:\s+(\d{4}))?$", text.strip())

    if not match:
        return text.strip(), None

    title = match.group(1).strip()
    year = match.group(2)

    return title, year


def lookup_imdb(imdb_id):
    if not OMDB_API_KEY:
        return None

    response = requests.get(
        "https://www.omdbapi.com/",
        params={
            "apikey": OMDB_API_KEY,
            "i": imdb_id
        },
        timeout=20
    )

    data = response.json()

    if data.get("Response") != "True":
        return None

    return {
        "imdb_id": imdb_id,
        "title": data.get("Title"),
        "year": data.get("Year"),
        "display": f"{data.get('Title')} ({data.get('Year')})"
    }


def make_watch_item(raw_query):
    raw_query = raw_query.strip()

    if is_imdb_id(raw_query):
        movie = lookup_imdb(raw_query.lower())

        if movie:
            return movie

        return {
            "imdb_id": raw_query.lower(),
            "title": raw_query.lower(),
            "year": None,
            "display": raw_query.lower()
        }

    title, year = parse_title_and_year(raw_query)

    display = f"{title} ({year})" if year else title

    return {
        "imdb_id": None,
        "title": title,
        "year": year,
        "display": display
    }


def load_watchlist():
    raw = load_json(WATCHLIST_FILE, [])

    upgraded = []

    for item in raw:
        if isinstance(item, str):
            upgraded.append(make_watch_item(item))
        else:
            upgraded.append(item)

    return upgraded


def watch_item_key(item):
    if item.get("imdb_id"):
        return item["imdb_id"]

    key = normalize(item.get("title", ""))

    if item.get("year"):
        key += "|" + item["year"]

    return key


def watch_item_matches_entry(item, entry):
    title = entry.get("title", "")
    link = entry.get("link", "")

    searchable_text = normalize(title + " " + link)
    wanted_title = normalize(item.get("title", ""))

    if not wanted_title:
        return False

    if wanted_title not in searchable_text:
        return False

    if item.get("year") and item["year"] not in searchable_text:
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

        if text.startswith("/watch "):
            query = text.replace("/watch ", "", 1).strip()

            if not query:
                send_message("Send it like this:\n/watch Nosferatu")
                continue

            if query not in watchlist:
                watchlist.append(query)
                save_json(WATCHLIST_FILE, watchlist)
                send_message(f"Added to watchlist 🎬\n{query}")
            else:
                send_message(f"Already watching 👀\n{query}")

        elif text == "/list":
            if watchlist:
                names = [
                    item.get("display", item.get("title", "Unknown"))
                    for item in watchlist
                ]
                send_message("Current watchlist 🎬\n\n" + "\n".join(names))
            else:
                send_message("Your watchlist is empty.")

        elif text.startswith("/remove "):
            query = text.replace("/remove ", "", 1).strip()

            if query in watchlist:
                watchlist.remove(query)
                save_json(WATCHLIST_FILE, watchlist)
                send_message(f"Removed from watchlist 🗑️\n{query}")
            else:
                send_message(f"Not found in watchlist:\n{query}")

        elif text == "/help":
            send_message(
                "Commands:\n"
                "/watch movie title or IMDb code\n"
                "/list\n"
                "/remove movie title or IMDb code\n"
                "/help"
            )


def check_feeds():
    watchlist = load_json(WATCHLIST_FILE, [])
    seen = load_json(SEEN_FILE, [])

    if not watchlist:
        return

    notified_items = []

    normalized_watchlist = [
        (item, normalize(item)) for item in watchlist
    ]

    for original_query, normalized_query in normalized_watchlist:
        if not normalized_query:
            continue

        already_notified_key = "NOTIFIED|" + original_query

        if already_notified_key in seen:
            continue

        for rss_url in RSS_URLS:
            feed = feedparser.parse(rss_url)

            found_entry = None

            for entry in feed.entries:
                title = entry.get("title", "")
                link = entry.get("link", "")

                searchable_text = normalize(title + " " + link)

                if normalized_query in searchable_text:
                    found_entry = {
                        "title": title,
                        "link": link
                    }
                    break

            if found_entry:
                send_message(
                    f"Available now 🎬\n\n"
                    f"{original_query}\n\n"
                    f"Found source:\n{found_entry['title']}\n{found_entry['link']}"
                )

                seen.append(already_notified_key)
                notified_items.append(original_query)
                save_json(SEEN_FILE, seen)
                break

    if notified_items:
        watchlist = [
            item for item in watchlist
            if item not in notified_items
        ]
        save_json(WATCHLIST_FILE, watchlist)

def main():
    handle_commands()
    check_feeds()
    commit_changes("Update bot state")
    print("Bot check complete.")


if __name__ == "__main__":
    main()
