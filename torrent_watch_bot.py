import os
import re
import json
import requests
import feedparser
import subprocess

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

OMDB_API_KEY = os.environ.get("OMDB_API_KEY", "")
TMDB_TOKEN = os.environ.get("TMDB_TOKEN", "")

WATCHLIST_FILE = "watchlist.json"
SEEN_FILE = "seen_matches.json"
OFFSET_FILE = "telegram_offset.json"

RSS_URLS = [

    # FOSS Torrents
    "https://fosstorrents.com/feed/torrents.xml",

    # Linux Tracker
    "https://linuxtracker.org/rss.php",

    # Internet Archive - Movies
    "https://archive.org/services/collection-rss.php?collection=feature_films",

    # Internet Archive - Open Source Media
    "https://archive.org/services/collection-rss.php?collection=opensource_movies",

    # Internet Archive - Community Video
    "https://archive.org/services/collection-rss.php?collection=community_video",

    # Public Domain Torrents
    "https://www.publicdomaintorrents.info/index.xml",

    # Etree (live music trading)
    "https://bt.etree.org/rss/bt_etree_rss.xml",

    # Academic / open datasets
    "https://academictorrents.com/rss.php"
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
        data={
            "chat_id": CHAT_ID,
            "text": text
        },
        timeout=20
    )
    response.raise_for_status()


def normalize(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


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

    try:
        response = requests.get(
            "https://www.omdbapi.com/",
            params={
                "apikey": OMDB_API_KEY,
                "i": imdb_id
            },
            timeout=20
        )

        response.raise_for_status()
        data = response.json()

        if data.get("Response") != "True":
            return None

        title = data.get("Title", "").strip()
        year = data.get("Year", "").strip()

        if not title:
            return None

        return {
            "imdb_id": imdb_id.lower(),
            "tmdb_id": None,
            "type": "movie",
            "title": title,
            "year": year,
            "display": f"{title} ({year})" if year else title
        }

    except requests.RequestException as error:
        print(f"OMDb lookup failed: {error}")
        return None


def tmdb_headers():
    return {
        "Authorization": f"Bearer {TMDB_TOKEN}",
        "accept": "application/json"
    }


def lookup_tmdb_by_imdb_id(imdb_id):
    if not TMDB_TOKEN:
        return None

    try:
        response = requests.get(
            f"https://api.themoviedb.org/3/find/{imdb_id}",
            headers=tmdb_headers(),
            params={"external_source": "imdb_id"},
            timeout=20
        )

        if response.status_code == 401:
            print("TMDb unauthorized. Check TMDB_TOKEN secret.")
            return None

        response.raise_for_status()
        data = response.json()

        movie_results = data.get("movie_results", [])
        tv_results = data.get("tv_results", [])

        if movie_results:
            result = movie_results[0]
            media_type = "movie"
        elif tv_results:
            result = tv_results[0]
            media_type = "series"
        else:
            return None

        title = result.get("title") or result.get("name")
        date = result.get("release_date") or result.get("first_air_date") or ""
        year = date[:4] if date else None

        if not title:
            return None

        return {
            "imdb_id": imdb_id.lower(),
            "tmdb_id": result.get("id"),
            "type": media_type,
            "title": title,
            "year": year,
            "display": f"{title} ({year})" if year else title
        }

    except requests.RequestException as error:
        print(f"TMDb IMDb lookup failed: {error}")
        return None


def search_tmdb_by_title_year(title, year=None):
    if not TMDB_TOKEN:
        return None

    try:
        params = {
            "query": title,
            "include_adult": "false"
        }

        if year:
            params["year"] = year
            params["first_air_date_year"] = year

        response = requests.get(
            "https://api.themoviedb.org/3/search/multi",
            headers=tmdb_headers(),
            params=params,
            timeout=20
        )

        if response.status_code == 401:
            print("TMDb unauthorized. Check TMDB_TOKEN secret.")
            return None

        response.raise_for_status()
        data = response.json()

        results = [
            item for item in data.get("results", [])
            if item.get("media_type") in ["movie", "tv"]
        ]

        if not results:
            return None

        result = results[0]
        media_type = "movie" if result.get("media_type") == "movie" else "series"

        clean_title = result.get("title") or result.get("name")
        date = result.get("release_date") or result.get("first_air_date") or ""
        found_year = date[:4] if date else year

        if not clean_title:
            return None

        return {
            "imdb_id": None,
            "tmdb_id": result.get("id"),
            "type": media_type,
            "title": clean_title,
            "year": found_year,
            "display": f"{clean_title} ({found_year})" if found_year else clean_title
        }

    except requests.RequestException as error:
        print(f"TMDb title search failed: {error}")
        return None


def make_watch_item(raw_query, notify_on_lookup_failure=False):
    raw_query = raw_query.strip()

    if is_imdb_id(raw_query):
        imdb_id = raw_query.lower()

        item = lookup_tmdb_by_imdb_id(imdb_id)

        if item:
            return item

        item = lookup_imdb(imdb_id)

        if item:
            return item

        if notify_on_lookup_failure:
            send_message(
                f"I could not look up this IMDb code via TMDb or OMDb:\n"
                f"{raw_query}\n\n"
                f"I added the code anyway, but title matching may not work."
            )

        return {
            "imdb_id": imdb_id,
            "tmdb_id": None,
            "type": "unknown",
            "title": imdb_id,
            "year": None,
            "display": imdb_id
        }

    title, year = parse_title_and_year(raw_query)

    item = search_tmdb_by_title_year(title, year)

    if item:
        return item

    display = f"{title} ({year})" if year else title

    return {
        "imdb_id": None,
        "tmdb_id": None,
        "type": "unknown",
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
        elif isinstance(item, dict):
            upgraded.append(item)

    return upgraded


def save_watchlist(watchlist):
    save_json(WATCHLIST_FILE, watchlist)


def watch_item_key(item):
    title = normalize(item.get("title", ""))

    if item.get("year"):
        title += "|" + str(item["year"])

    return title


def watch_item_display(item):
    return item.get("display") or item.get("title") or item.get("imdb_id") or "Unknown"


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
    watchlist = load_watchlist()
    updates = get_updates()

    for update in updates:
        message = update.get("message", {})
        chat = message.get("chat", {})
        text = message.get("text", "").strip()

        if str(chat.get("id")) != str(CHAT_ID):
            continue

        if text.startswith("/watch "):
            raw_query = text.replace("/watch ", "", 1).strip()

            if not raw_query:
                send_message("Send it like this:\n/watch Scary Movie 2000")
                continue

            item = make_watch_item(raw_query, notify_on_lookup_failure=True)
            existing_keys = [watch_item_key(x) for x in watchlist]

            if watch_item_key(item) not in existing_keys:
                watchlist.append(item)
                save_watchlist(watchlist)
                send_message(f"Added to watchlist 🎬\n{watch_item_display(item)}")
            else:
                send_message(f"Already watching 👀\n{watch_item_display(item)}")

        elif text == "/list":
            if watchlist:
                names = [watch_item_display(item) for item in watchlist]
                send_message("Current watchlist 🎬\n\n" + "\n".join(names))
            else:
                send_message("Your watchlist is empty.")

        elif text.startswith("/remove "):
            raw_query = text.replace("/remove ", "", 1).strip()

            if not raw_query:
                send_message("Send it like this:\n/remove Scary Movie 2000")
                continue

            target_item = make_watch_item(raw_query)
            target_key = watch_item_key(target_item)

            new_watchlist = [
                item for item in watchlist
                if watch_item_key(item) != target_key
            ]

            if len(new_watchlist) != len(watchlist):
                save_watchlist(new_watchlist)
                send_message(f"Removed from watchlist 🗑️\n{watch_item_display(target_item)}")
            else:
                send_message(f"Not found in watchlist:\n{raw_query}")

        elif text == "/help":
            send_message(
                "Commands:\n"
                "/watch movie title or IMDb code\n"
                "/watch movie title year\n"
                "/list\n"
                "/remove movie title or IMDb code\n"
                "/help"
            )


def check_feeds():
    watchlist = load_watchlist()
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
                if watch_item_matches_entry(item, entry):
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

    if notified_keys:
        watchlist = [
            item for item in watchlist
            if watch_item_key(item) not in notified_keys
        ]
        save_watchlist(watchlist)


def main():
    handle_commands()
    check_feeds()
    commit_changes("Update bot state")
    print("Bot check complete.")


if __name__ == "__main__":
    main()
