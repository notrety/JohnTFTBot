"""Upload every League of Legends champion icon as an application emoji for the bot.

Application emojis belong to the bot, so they work in every server it's in.
Each emoji is named after the champion's internal name (e.g. "Ahri", "MonkeyKing"),
which matches the champion names stored in league_games.

Run once, and again after new champions are released (existing emojis are skipped):
    python sync_champion_emojis.py            # upload missing champions
    python sync_champion_emojis.py --dry-run  # only list what would be uploaded

Uses DISCORD_BOT_TOKEN from .env. Restart the bot afterwards so it loads the new emojis.
"""
import base64
import os
import sys
import time

import requests
from dotenv import load_dotenv

DISCORD_API = "https://discord.com/api/v10"
CDRAGON = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/"

# champion-summary.json also lists non-playable entries (ids 60000+, e.g. "Jade_Annie")
MAX_CHAMPION_ID = 3000


def discord_request(session, method, path, **kwargs):
    """Call the Discord API, waiting and retrying when rate limited."""
    while True:
        response = session.request(method, DISCORD_API + path, timeout=30, **kwargs)
        if response.status_code == 429:
            retry_after = float(response.json().get("retry_after", 1))
            print(f"  rate limited, waiting {retry_after:.1f}s")
            time.sleep(retry_after + 0.1)
            continue
        if not response.ok:
            raise RuntimeError(f"{method} {path} failed ({response.status_code}): {response.text}")
        return response.json()


def main():
    dry_run = "--dry-run" in sys.argv

    load_dotenv()
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        sys.exit("DISCORD_BOT_TOKEN is not set in .env")

    discord = requests.Session()
    discord.headers["Authorization"] = f"Bot {token}"

    app_id = discord_request(discord, "GET", "/applications/@me")["id"]
    existing = discord_request(discord, "GET", f"/applications/{app_id}/emojis")["items"]
    existing_names = {emoji["name"].lower() for emoji in existing}
    print(f"Bot already has {len(existing)} application emojis")

    champions = requests.get(CDRAGON + "champion-summary.json", timeout=30).json()
    champions = [c for c in champions if 0 < c["id"] < MAX_CHAMPION_ID]
    missing = [c for c in champions if c["alias"].lower() not in existing_names]
    print(f"{len(champions)} champions, {len(missing)} missing emojis")

    uploaded, failed = 0, []
    for champ in sorted(missing, key=lambda c: c["alias"]):
        name = champ["alias"]
        try:
            icon = requests.get(f"{CDRAGON}champion-icons/{champ['id']}.png", timeout=30)
            icon.raise_for_status()

            if dry_run:
                print(f"  would upload {name} ({len(icon.content)} bytes)")
                continue

            image = "data:image/png;base64," + base64.b64encode(icon.content).decode()
            discord_request(discord, "POST", f"/applications/{app_id}/emojis",
                            json={"name": name, "image": image})
            uploaded += 1
            print(f"  uploaded {name}")
        except Exception as e:
            failed.append(name)
            print(f"  FAILED {name}: {e}")

    if dry_run:
        print("Dry run, nothing uploaded.")
    else:
        print(f"Done: {uploaded} uploaded, {len(failed)} failed")
    if failed:
        print("Failed: " + ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
