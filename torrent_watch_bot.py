import os
import re
import json
import requests
import feedparser
import subprocess

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

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
                send_message("Current watchlist 🎬\n\n" + "\n".join(watchlist))
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

    normalized_watchlist = [
        (item, normalize(item)) for item in watchlist
    ]

    for rss_url in RSS_URLS:
        feed = feedparser.parse(rss_url)

        for entry in feed.entries:
            title = entry.get("title", "")
            link = entry.get("link", "")

            item_id = title + "|" + link

            if item_id in seen:
                continue

            searchable_text = normalize(title + " " + link)

            for original_query, normalized_query in normalized_watchlist:
                if normalized_query and normalized_query in searchable_text:
                    send_message(
                        f"Found match 🎬\n\n"
                        f"Watching for: {original_query}\n\n"
                        f"{title}\n{link}"
                    )

                    seen.append(item_id)
                    save_json(SEEN_FILE, seen)
                    break


def main():
    handle_commands()
    check_feeds()
    commit_changes("Update bot state")
    print("Bot check complete.")


if __name__ == "__main__":
    main()
