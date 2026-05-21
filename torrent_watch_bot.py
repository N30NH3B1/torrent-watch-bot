import os
import re
import json
import requests
import subprocess

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

TRAKT_CLIENT_ID = os.environ.get("TRAKT_CLIENT_ID", "")
TRAKT_USERNAME = os.environ.get("TRAKT_USERNAME", "")
TRAKT_LIST_SLUG = os.environ.get("TRAKT_LIST_SLUG", "")
TRAKT_ACCESS_TOKEN = os.environ.get("TRAKT_ACCESS_TOKEN", "")

TORBOX_API_KEY = os.environ.get("TORBOX_API_KEY", "")

WATCHLIST_FILE = "watchlist.json"
SEEN_FILE = "seen_matches.json"
OFFSET_FILE = "telegram_offset.json"

TORBOX_SEARCH_BASE = "https://search-api.torbox.app"
TORBOX_API_BASE = "https://api.torbox.app"


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


def torbox_headers():
    return {
        "Authorization": f"Bearer {TORBOX_API_KEY}",
        "accept": "application/json"
    }


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

    media_type = "movies" if item.get("type") != "series" else "shows"

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
        item_type = "movie"
    elif media_type == "show":
        media = item.get("show", {})
        item_type = "series"
    else:
        return None

    title = media.get("title")
    year = media.get("year")
    ids = media.get("ids", {})

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
        "slug": ids.get("slug"),
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


def extract_hash(result):
    candidates = [
        result.get("hash"),
        result.get("info_hash"),
        result.get("torrent_hash")
    ]

    for candidate in candidates:
        if candidate:
            return str(candidate).lower()

    magnet = str(result.get("magnet") or result.get("magnet_uri") or "")
    match = re.search(r"btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})", magnet)

    if match:
        return match.group(1).lower()

    return None


def result_title(result):
    return (
        result.get("title")
        or result.get("name")
        or result.get("raw_title")
        or "Unknown result"
    )


def result_is_cached(result):
    cached_fields = [
        result.get("cached"),
        result.get("is_cached"),
        result.get("cache")
    ]

    for value in cached_fields:
        if value is True:
            return True

        if isinstance(value, str) and value.lower() in ["true", "cached", "yes"]:
            return True

    return False


def search_torbox_by_id(item):
    if not TORBOX_API_KEY:
        print("TorBox API key missing.")
        return []

    id_type = None
    media_id = None

    if item.get("imdb_id"):
        id_type = "imdb"
        media_id = item["imdb_id"]
    elif item.get("tmdb_id"):
        id_type = "tmdb"
        media_id = item["tmdb_id"]
    elif item.get("trakt_id"):
        id_type = "trakt"
        media_id = item["trakt_id"]

    if not id_type or not media_id:
        return []

    url = f"{TORBOX_SEARCH_BASE}/torrents/{id_type}:{media_id}"

    params = {
        "metadata": "true",
        "check_cache": "true",
        "check_owned": "false",
        "search_user_engines": "false"
    }

    print("Searching TorBox by ID:", id_type, media_id)

    response = requests.get(
        url,
        headers=torbox_headers(),
        params=params,
        timeout=30
    )

    if response.status_code >= 400:
        print("TorBox ID search failed:", response.status_code, response.text)
        return []

    data = response.json()

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in ["data", "results", "torrents"]:
            if isinstance(data.get(key), list):
                return data[key]

    return []


def search_torbox_by_title(item):
    if not TORBOX_API_KEY:
        print("TorBox API key missing.")
        return []

    query = item.get("title", "")

    if item.get("year"):
        query += f" {item['year']}"

    url = f"{TORBOX_SEARCH_BASE}/torrents/search"

    params = {
        "query": query,
        "metadata": "true",
        "check_cache": "true",
        "check_owned": "false",
        "search_user_engines": "false"
    }

    print("Searching TorBox by title:", query)

    response = requests.get(
        url,
        headers=torbox_headers(),
        params=params,
        timeout=30
    )

    if response.status_code == 404:
        print("TorBox title search endpoint not found. ID search may still work.")
        return []

    if response.status_code >= 400:
        print("TorBox title search failed:", response.status_code, response.text)
        return []

    data = response.json()

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in ["data", "results", "torrents"]:
            if isinstance(data.get(key), list):
                return data[key]

    return []


def check_torbox_cache_by_hash(torrent_hash):
    if not TORBOX_API_KEY or not torrent_hash:
        return False

    url = f"{TORBOX_API_BASE}/v1/api/torrents/checkcached"

    params = {
        "hash": torrent_hash,
        "format": "object",
        "list_files": "false"
    }

    response = requests.get(
        url,
        headers=torbox_headers(),
        params=params,
        timeout=30
    )

    if response.status_code >= 400:
        print("TorBox cache check failed:", response.status_code, response.text)
        return False

    data = response.json()
    payload = data.get("data")

    if payload is None:
        return False

    if isinstance(payload, dict):
        hash_data = payload.get(torrent_hash.lower()) or payload.get(torrent_hash.upper())

        if hash_data:
            return True

        for value in payload.values():
            if value:
                return True

    if isinstance(payload, list):
        return len(payload) > 0

    return False


def item_matches_result(item, result):
    searchable_text = normalize(result_title(result))
    wanted_title = normalize(item.get("title", ""))

    if wanted_title and wanted_title not in searchable_text:
        return False

    if item.get("year") and str(item["year"]) not in searchable_text:
        return False

    return True


def find_torbox_match(item):
    results = search_torbox_by_id(item)

    if not results:
        results = search_torbox_by_title(item)

    print("TorBox results found:", len(results))

    for result in results:
        title = result_title(result)
        print("Checking TorBox result:", title)

        if not item_matches_result(item, result):
            continue

        if result_is_cached(result):
            print("Cached TorBox match found:", title)
            return result

        torrent_hash = extract_hash(result)

        if torrent_hash and check_torbox_cache_by_hash(torrent_hash):
            print("Cached TorBox hash match found:", title)
            return result

    return None


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
                "Add movies/shows in Trakt. Telegram is only for status and notifications."
            )


def check_torbox():
    watchlist = load_json(WATCHLIST_FILE, [])
    seen = load_json(SEEN_FILE, [])

    if not watchlist:
        print("Watchlist is empty.")
        return

    notified_keys = []

    for item in watchlist:
        item_key = watch_item_key(item)
        already_notified_key = "NOTIFIED|" + item_key

        if already_notified_key in seen:
            print("Already notified:", watch_item_display(item))
            continue

        match = find_torbox_match(item)

        if not match:
            print("No TorBox cached match:", watch_item_display(item))
            continue

        title = result_title(match)
        source_link = match.get("link") or match.get("magnet") or match.get("magnet_uri") or "TorBox cached result"

        send_message(
            f"Available now 🎬\n\n"
            f"{watch_item_display(item)}\n\n"
            f"{title}\n"
            f"{source_link}"
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
    check_torbox()
    commit_changes("Update bot state")
    print("Bot check complete.")


if __name__ == "__main__":
    main()
