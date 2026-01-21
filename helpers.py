import asyncio
import discord
import requests
import dicts
import time
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
from pulsefire.clients import RiotAPIClient
from pulsefire.taskgroups import TaskGroup
import os
import hashlib

CACHE_DIR = "image_cache"
set_15_unix = 1764763200
season_15_unix = 1736409600 + 10800

os.makedirs(CACHE_DIR, exist_ok=True)

async def update_db_games(pool, tft_token, lol_token):
    async with pool.acquire() as conn:
        users = await conn.fetch('SELECT discord_id FROM users;')
    
    async with TaskGroup(asyncio.Semaphore(10)) as tg:
        for user in users:
            await tg.create_task(update_user_games(pool, int(user['discord_id']), tft_token, lol_token))

    print("User games updated.")

async def update_ranks(pool, tft_token, lol_token):
    async with pool.acquire() as conn:

        users = await conn.fetch('SELECT discord_id, tft_puuid, league_puuid, region FROM users;')

        for user in users:
            discord_id = user['discord_id']

            current_tft_lp, _, _, _ = await calculate_elo(user['tft_puuid'], "TFT", tft_token, user['region'])
            current_lol_lp, _, _, _ = await calculate_elo(user['league_puuid'], "League", lol_token, user['region'])

            last_snapshot = await conn.fetchrow('''
                SELECT league_lp, tft_lp
                FROM lp
                WHERE discord_id = $1
                ORDER BY update_time DESC
                LIMIT 1;
            ''', discord_id)

            lp_changed = (
                last_snapshot is None or
                str(last_snapshot['league_lp']) != str(current_lol_lp) or
                str(last_snapshot['tft_lp']) != str(current_tft_lp)
            )

            if lp_changed:
                unix_timestamp = int(time.time())
                await conn.execute('''
                    INSERT INTO lp (discord_id, update_time, league_lp, tft_lp)
                    VALUES ($1, $2, $3, $4);
                ''', discord_id, unix_timestamp, current_lol_lp, current_tft_lp)

                print(f"LP updated for {discord_id}: TFT={current_tft_lp}, LoL={current_lol_lp}")
            else:
                if last_snapshot:
                    new_timestamp = int(time.time())
                    await conn.execute('''
                        UPDATE lp
                        SET update_time = $1
                        WHERE discord_id = $2
                        AND update_time = $3;
                    ''', new_timestamp, discord_id, last_snapshot['update_time'])

                print(f"No LP change for {discord_id}")

    print("Ranks updated")

async def update_user_games(pool, user_id, tft_token, lol_token):
    try:
        async with pool.acquire() as conn:
            user_id = int(user_id)
            user = await conn.fetchrow('''
                SELECT *
                FROM users
                WHERE discord_id = $1
            ''', user_id)

            if not user:
                print(f"[WARN] No user found with discord_id={user_id}")
                return

            region = user['region']
            mass_region = user['mass_region']

            # Fetch current LPs
            current_tft_lp, _, _, _ = await calculate_elo(user['tft_puuid'], "TFT", tft_token, region)
            current_lol_lp, _, _, _ = await calculate_elo(user['league_puuid'], "League", lol_token, region)

            # Get most recent snapshot
            last_snapshot = await conn.fetchrow('''
                SELECT update_time, league_lp, tft_lp
                FROM lp
                WHERE discord_id = $1
                ORDER BY update_time DESC
                LIMIT 1;
            ''', user_id)

            lp_changed = (
                last_snapshot is None or
                last_snapshot['tft_lp'] != current_tft_lp or
                last_snapshot['league_lp'] != current_lol_lp
            )

            if lp_changed:
                print(f"[INFO] Rank change detected for {user['game_name']}")

                timestamp = int(time.time())

                # TFT updates
                if last_snapshot and last_snapshot['tft_lp'] != current_tft_lp:
                    err, tft_match_list = await find_all_match_ids(
                        user['tft_puuid'], "TFT", mass_region, tft_token, last_snapshot['update_time']
                    )
                    if tft_match_list:
                        await add_new_match(conn, user['tft_puuid'], "TFT", mass_region, tft_token, tft_match_list)

                # League updates
                if last_snapshot and last_snapshot['league_lp'] != current_lol_lp:
                    err, lol_match_list = await find_all_match_ids(
                        user['league_puuid'], "League", mass_region, lol_token, last_snapshot['update_time']
                    )
                    if lol_match_list:
                        await add_new_match(conn, user['league_puuid'], "League", mass_region, lol_token, lol_match_list)

                # Insert new LP snapshot
                await conn.execute('''
                    INSERT INTO lp (discord_id, update_time, league_lp, tft_lp)
                    VALUES ($1, $2, $3, $4);
                ''', user_id, timestamp, current_lol_lp, current_tft_lp)

            else:
                # No LP change — just refresh snapshot timestamp
                if last_snapshot:
                    new_timestamp = int(time.time())
                    await conn.execute('''
                        UPDATE lp
                        SET update_time = $1
                        WHERE discord_id = $2
                        AND update_time = $3;
                    ''', new_timestamp, user_id, last_snapshot['update_time'])

                print(f"[INFO] No LP change for {user['game_name']}")

    except Exception as e:
        print(f"[ERROR] update_user_games failed for {user_id}: {e}")

    finally:
        print(f"[DONE] Completed update for {user_id}")
        
async def add_new_match(conn, puuid, game, mass_region, token, match_list):
    if not match_list:
        return
    
    target_queues = {
        "TFT": dicts.game_type_to_id["Ranked"],
        "League": dicts.game_type_to_id["Ranked Solo/Duo"]
    }
    try:
        async with RiotAPIClient(default_headers={'X-Riot-Token': token}) as client:
            for match_id in match_list:
                print(f"Attempting to add {game} match id {match_id} for {puuid}")
                if game == 'TFT':
                    match_info = await client.get_tft_match_v1_match(region=mass_region, id=match_id)
                    queue_id = match_info['info'].get('queue_id')
                    if queue_id != target_queues['TFT']:
                        print(f"{game} match id {match_id} not ranked")
                        continue

                    game_datetime = match_info['info']['game_datetime']
                    participants = match_info['info']['participants']

                    p = next((p for p in participants if p['puuid'] == puuid), None)
                    if p:
                        champions = [c['character_id'] for c in p.get('units', [])]
                        items = [i for c in p.get('units', []) for i in c.get('itemNames', [])]
                        traits = [{"name": t["name"], "style": t["style"]} for t in p.get("traits", [])]
                        await conn.execute(
                            '''
                            INSERT INTO tft_games (match_id, tft_puuid, game_datetime, placement, champions, items, traits, damage_dealt, level)
                            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                            ON CONFLICT (match_id, tft_puuid) DO NOTHING;
                            ''',
                            match_id, puuid, game_datetime, p['placement'], champions, items, traits,
                            p['total_damage_to_players'], p['level']
                        )

                elif game == 'League':
                    match_info = await client.get_lol_match_v5_match(region=mass_region, id=match_id)
                    queue_id = match_info['info'].get('queueId')
                    if queue_id != target_queues['League']:
                        print(f"{game} match id {match_id} not ranked")
                        continue

                    game_datetime = match_info['info']['gameEndTimestamp']
                    participants = match_info['info']['participants']
                    for p in participants:
                        if p['puuid'] == puuid:
                            cs = p['totalMinionsKilled'] + p['neutralMinionsKilled']
                            await conn.execute('''
                                INSERT INTO league_games (match_id, league_puuid, game_datetime, win_loss, champion, kills, deaths, assists, cs, game_duration)
                                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                                ON CONFLICT (match_id, league_puuid) DO NOTHING;
                            ''', match_id, puuid, game_datetime, p['win'], p['championName'], p['kills'], p['deaths'], p['assists'], cs, match_info['info']['gameDuration'])
                print(f"Inserting match {match_id}, placement {p['placement']}")
    except Exception as e:
        print(f"[ERROR] Failed to insert match {match_id}, placement {p['placement']}: {e}")

async def find_missing_games(pool, tft_token, lol_token):
    
    async with pool.acquire() as conn:

        rows = await conn.fetch('''
        WITH last_lp AS (
            SELECT DISTINCT ON (discord_id)
                discord_id,
                update_time,
                tft_lp,
                league_lp
            FROM lp
            ORDER BY discord_id, update_time DESC
        )
        SELECT
            u.discord_id,
            u.game_name,
            u.tft_puuid,
            u.league_puuid,
            u.mass_region,
            lp.update_time AS last_update_time
        FROM users u
        LEFT JOIN last_lp lp ON u.discord_id = lp.discord_id;
        ''')

        for row in rows:
            timestamp = row['last_update_time']

            err, tft_match_list = await find_all_match_ids(row['tft_puuid'], "TFT", row["mass_region"], tft_token, timestamp=timestamp)

            err, lol_match_list = await find_all_match_ids(row['league_puuid'], "League", row["mass_region"], lol_token, timestamp=timestamp)

            if tft_match_list:
                await add_new_match(conn, row['tft_puuid'], "TFT", row["mass_region"], tft_token, tft_match_list)

            if lol_match_list:
                await add_new_match(conn, row['league_puuid'], "League", row["mass_region"], lol_token, lol_match_list)

async def get_rank_info(region, puuid, tft_token):
    async with RiotAPIClient(default_headers={"X-Riot-Token": tft_token}) as client:
        info = await client.get_tft_league_v1_entries_by_puuid(region=region, puuid=puuid)
        return info

async def get_lol_rank_info(region, puuid, lol_token):
    async with RiotAPIClient(default_headers={"X-Riot-Token": lol_token}) as client:
        info = await client.get_lol_league_v4_entries_by_puuid(region=region, puuid=puuid)
        return info

# Function to return cutoff lp for challenger and grandmaster
async def get_cutoff(tft_token, region):
    # grab all players who are challenger, grandmaster, and master
    async with RiotAPIClient(default_headers={"X-Riot-Token": tft_token}) as client:
        start = time.perf_counter()
        challengers, grandmasters, masters = await asyncio.gather(
            client.get_tft_league_v1_challenger_league(region=region),
            client.get_tft_league_v1_grandmaster_league(region=region),
            client.get_tft_league_v1_master_league(region=region),
        )
        end = time.perf_counter()
        print(f"Execution time: {end - start:.4f} seconds")

        # put all the lps into a list
        lps = [entry.get('leaguePoints') for entry in challengers['entries']]
        lps.extend(entry.get('leaguePoints') for entry in grandmasters['entries'])
        lps.extend(entry.get('leaguePoints') for entry in masters['entries'])
        end = time.perf_counter()
        print(f"Execution time: {end - start:.4f} seconds")

        # sort lps 
        lps_sorted = sorted(lps, reverse=True)
        end = time.perf_counter()
        print(f"Execution time: {end - start:.4f} seconds")

        # in the case there are less than 250 masters+ players
        if len(lps_sorted) < 250:
            return 500, 200
        
        # in the case there are between 250 and 750 masters+ players
        if len(lps_sorted) < 750:
            challenger_cutoff = max(500,lps_sorted[249])
            return challenger_cutoff, 200
        
        # return cutoffs, default to 500 and 200 if not enough players to fill out chall/gm
        challenger_cutoff = max(500,lps_sorted[249])
        grandmaster_cutoff = max(200,lps_sorted[749])
    return challenger_cutoff, grandmaster_cutoff

# Function to get the trait icon path
def get_trait_icon(traits_data, traitName):
    for trait in traits_data:
        if trait.get("apiName") == traitName:
            return trait.get("icon", "")[:-4] # Removes the beginning of the datadragon icon_path 
    print("Trait not Found")
    return None  # Return None if the trait_id is not found

# Function to get the champ icon path
def get_champ_icon(champs_data, characterName):
    # Loop through each champion in the list
    for champion in champs_data:        
        # Check if the apiName matches the provided characterName
        if champion.get("apiName").lower() == characterName.lower():
            # Assuming 'champion' dictionary contains a 'squareIcon' key with the icon pat
            return champion.get("tileIcon", "")[:-4]  # Remove the last 4 characters (usually file extension)
    
    print(f"{characterName} Not Found")
    return None

# Function to get the item icon path
def get_item_icon(items_data, itemName):
    for item in items_data:
        if item.get("nameId") == itemName:
            return item.get("squareIconPath", "")[21:]
    print(f"{itemName} Not Found")
    return "assets/maps/tft/icons/items/hexcore/tft_item_blank.tft_set13.png"

def get_companion_icon(companions_json, contentId):
    for companion in companions_json:
        if contentId == companion.get("contentId"):
            return companion.get("loadoutsIcon")[21:]
    print(f"{contentId} Not Found")
    return None

def get_lol_item_icon(lol_item_json, itemId):
    if itemId == 0:
        return "assets/items/icons2d/gp_ui_placeholder.png"
    for item in lol_item_json:
        if itemId == item.get("id"):
            return item.get("iconPath", "")[21:]
    print(f"{itemId}Not Found")
    return None

def get_lol_champ_icon(championId):
    return f"v1/champion-icons/{championId}"

def get_keystone_icon(keystones_json, style):
    for keystone in keystones_json:
        if style == keystone.get("id"):
            return keystone.get("iconPath", "")[21:]
    print(f"{style} Not Found")
    return None

def get_rune_icon(runes_json, style):
    for rune in runes_json:
        if style == rune.get("id"):
            return rune.get("iconPath", "")[21:]
    print(f"{style} Not Found")
    return None

def get_summs_icon(summs_json, summonerId):
    for summ in summs_json:
        if summonerId == summ.get("id"):
            return summ.get("iconPath", "")[21:]
    print(f"{summ} Not Found")
    return None 

async def get_pfp(region, puuid, lol_token):
    async with RiotAPIClient(default_headers={"X-Riot-Token": lol_token}) as client:
        profile = await client.get_lol_summoner_v4_by_puuid(region=region, puuid=puuid)
        pfp_id = profile['profileIconId']
        url = f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/profile-icons/{pfp_id}.jpg"
        return url

def center_square_crop(image: Image.Image) -> Image.Image:
    w, h = image.size
    min_side = min(w, h)
    left = (w - min_side) // 2
    top = (h - min_side) // 2
    return image.crop((left, top, left + min_side, top + min_side))

def circular_crop(image: Image.Image) -> Image.Image:
    """
    Returns a circularly cropped version of the given RGBA image.
    Keeps transparency outside the circle.
    """
    # Ensure RGBA mode
    image = image.convert("RGBA")

    # Create same-sized mask
    mask = Image.new("L", image.size, 0)
    draw = ImageDraw.Draw(mask)

    # Draw white filled circle in the center
    width, height = image.size
    draw.ellipse((0, 0, width, height), fill=255)

    # Apply the mask as alpha channel
    result = Image.new("RGBA", image.size, (0, 0, 0, 0))
    result.paste(image, (0, 0), mask)

    return result

async def fetch_image(url: str, size: tuple = None):
    print("looking for " + url) # troubleshooting line
    # Generate a unique filename from the URL
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    ext = os.path.splitext(url)[1].split("?")[0] or ".png" or ".svg"
    cache_path = os.path.join(CACHE_DIR, f"{url_hash}{ext}")

    # Try loading from cache
    if os.path.exists(cache_path):
        image = Image.open(cache_path).convert("RGBA")
        if size:
            image = image.resize(size, Image.LANCZOS)
        return image

    # Otherwise, download the image in a thread
    response = await asyncio.to_thread(requests.get, url)
    if response.status_code != 200:
        raise Exception(f"Failed to fetch image from {url} (status {response.status_code})")

    # Save to cache
    with open(cache_path, "wb") as f:
        f.write(response.content)

    image = Image.open(BytesIO(response.content)).convert("RGBA")
    if size:
        image = image.resize(size, Image.LANCZOS)

    return image

# Function to fetch PUUID
async def get_puuid(gameName, tagLine, mass_region, riot_token):
    try:
        async with RiotAPIClient(default_headers={"X-Riot-Token": riot_token}) as client:
            account = await client.get_account_v1_by_riot_id(region=mass_region, game_name=gameName, tag_line=tagLine)
        return account['puuid']

    except Exception as err:
        print(f"Failed to retrieve PUUID for {gameName}#{tagLine}.{err}")
        return None

# Function to calculate ranked elo based on given PUUID
async def calculate_elo(puuid, game, token, region):
    attempts = 0
    while True:
        try:
            # Fetch summoner data
            if game == "TFT":
                rank_info = await get_rank_info(region, puuid, token)
                for entry in rank_info:
                    if entry['queueType'] == 'RANKED_TFT':
                        return dicts.rank_to_elo[entry['tier'] + " " + entry['rank']] + int(entry['leaguePoints']), entry['tier'], entry['rank'], entry['leaguePoints']
                return 0, "UNRANKED", "", 0  # If no ranked TFT entry is found
            else:
                rank_info = await get_lol_rank_info(region, puuid, token)
                for entry in rank_info:
                    if entry['queueType'] == 'RANKED_SOLO_5x5':
                        return dicts.rank_to_elo[entry['tier'] + " " + entry['rank']] + int(entry['leaguePoints']), entry['tier'], entry['rank'], entry['leaguePoints']
                return 0, "UNRANKED", "", 0  # If no ranked League entry is found

        except requests.exceptions.HTTPError as e:
            raise e  # Re-raise other errors

def time_ago(game_end_ts):
    # Convert from ms → seconds
    game_end_seconds = game_end_ts / 1000
    now_seconds = time.time()
    diff_seconds = int(now_seconds - game_end_seconds)

    minute = 60
    hour = 60 * minute
    day = 24 * hour
    month = 30 * day  # approx
    year = 12 * month

    if diff_seconds < minute:
        return f"{diff_seconds} second{'s' if diff_seconds != 1 else ''} ago"
    elif diff_seconds < hour:
        minutes = diff_seconds // minute
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    elif diff_seconds < day:
        hours = diff_seconds // hour
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    elif diff_seconds < month:
        days = diff_seconds // day
        return f"{days} day{'s' if days != 1 else ''} ago"
    elif diff_seconds < year:
        months = diff_seconds // month
        return f"{months} month{'s' if months != 1 else ''} ago"
    else:
        years = diff_seconds // year
        return f"{years} year{'s' if years != 1 else ''} ago"

# Function to get TFT rank info from puuid and return embed with rank icon
async def get_rank_embed(name, tagLine, region, tft_token, puuid):
    gameName = name.replace("_", " ")
    try: 
        rank_info = await get_rank_info(region, puuid, tft_token)
    except Exception as err:
        return None, f"Error fetching rank info for {gameName}#{tagLine}: {err}"
    if rank_info:
        for entry in rank_info:
            if entry['queueType'] == 'RANKED_TFT':
                tier = entry['tier']
                rank = entry['rank']
                lp = entry['leaguePoints']
                total_games = entry['wins'] + entry['losses']
                top_four_rate = round(entry['wins'] / total_games * 100, 2) if total_games else 0

                # Get rank icon URL from Data Dragon github 
                rank_icon_url = "https://raw.githubusercontent.com/InFinity54/LoL_DDragon/refs/heads/master/extras/tier/" + tier.lower() + ".png"

                # Create an embed message
                embed = discord.Embed(
                    description=f"🏆 **{tier} {rank}** ({lp} LP)\n🎯 **Top 4 Rate:** {top_four_rate}%\n📊 **Total Games:** {total_games}",
                    color=discord.Color.blue()
                )
                embed.set_thumbnail(url=rank_icon_url)  # Set the rank icon
                embed.set_footer(text="Powered by Riot API | Data from TFT Ranked")
                embed.set_author(
                    name=f"TFT Stats for {gameName}#{tagLine}",
                    url=f"https://lolchess.gg/profile/{region[:-1]}/{gameName.replace(" ", "%20")}-{tagLine}/set15",
                    icon_url="https://static.wikia.nocookie.net/leagueoflegends/images/6/67/Teamfight_Tactics_icon.png/revision/latest/scale-to-width/360?cb=20191018215638"
                )

                return embed, None  # Return the embed
            
    return None, f"{gameName}#{tagLine} is unranked."

# Function to get LOL rank info from puuid and return embed with rank icon
async def get_lol_rank_embed(name, tagLine, region, lol_token, puuid):
    gameName = name.replace("_", " ")
    try: 
        rank_info = await get_lol_rank_info(region, puuid, lol_token)
    except Exception as err:
        return None, f"Error fetching rank info for {gameName}#{tagLine}: {err}"
    if rank_info:
        for entry in rank_info:
            if entry['queueType'] == 'RANKED_SOLO_5x5':
                tier = entry['tier']
                rank = entry['rank']
                lp = entry['leaguePoints']
                total_games = entry['wins'] + entry['losses']
                win_rate = round(entry['wins'] / total_games * 100, 2) if total_games else 0

                # Get rank icon URL from Data Dragon github 
                rank_icon_url = "https://raw.githubusercontent.com/InFinity54/LoL_DDragon/refs/heads/master/extras/tier/" + tier.lower() + ".png"

                # Create an embed message
                embed = discord.Embed(
                    description=f"🏆 **{tier} {rank}** ({lp} LP)\n🎯 **Win Rate:** {win_rate}%\n📊 **Total Games:** {total_games}",
                    color=discord.Color.blue()
                )
                embed.set_thumbnail(url=rank_icon_url)  # Set the rank icon
                embed.set_footer(text="Powered by Riot API | Data from LOL Ranked")
                embed.set_author(
                    name=f"LOL Stats for {gameName}#{tagLine}",
                    url=f"https://op.gg/lol/summoners/{region[:-1]}/{gameName.replace(" ", "%20")}-{tagLine}",
                    icon_url="https://static.wikia.nocookie.net/leagueoflegends/images/9/9a/League_of_Legends_Update_Logo_Concept_05.jpg/revision/latest/scale-to-width-down/250?cb=20191029062637"
                )

                return embed, None  # Return the embed
            
    return None, f"{gameName}#{tagLine} is unranked."

def round_elo_to_rank(avg_elo):
    if avg_elo > 2800:
        return "Master+", int(round(avg_elo - 2800))
    rounded_elo = (avg_elo // 100) * 100
    rank = next((key for key, val in dicts.rank_to_elo.items() if val == rounded_elo), None)
    return rank, 0

async def find_all_match_ids(puuid, game, mass_region, token, timestamp):
    match_list = []
    counter = 0

    try:
        async with RiotAPIClient(default_headers={"X-Riot-Token": token}) as client:
            while counter < 1000:
                if game == "TFT":
                    if timestamp:
                        matches = await client.get_tft_match_v1_match_ids_by_puuid(
                            region=mass_region,
                            puuid=puuid,
                            queries={"start": counter, "startTime": timestamp, "count": 100}
                        )
                    else:
                        matches = await client.get_tft_match_v1_match_ids_by_puuid(
                            region=mass_region,
                            puuid=puuid,
                            queries={"start": counter, "count": 100}
                        )

                elif game == "League":
                    if timestamp:
                        matches = await client.get_lol_match_v5_match_ids_by_puuid(
                            region=mass_region,
                            puuid=puuid,
                            queries={"start": counter, "startTime": timestamp, "count": 100}
                        )
                    else:
                        matches = await client.get_lol_match_v5_match_ids_by_puuid(
                            region=mass_region,
                            puuid=puuid,
                            queries={"start": counter, "count": 100}
                        )

                else:
                    return f"Invalid game type: {game}", None, puuid

                # If no matches returned, stop
                if not matches:
                    break

                match_list.extend(matches)
                counter += 100  # Move to next batch

        if not match_list:
            return f"No matches found for {puuid}.", None

        return None, match_list

    except Exception as err:
        return f"Error fetching matches for {puuid}: {err}", None


async def find_match_ids(gameName, tagLine, mode, game, mass_region, token):
    puuid = await get_puuid(gameName, tagLine, mass_region, token)
    if not puuid:
        return f"Could not find PUUID for {gameName}#{tagLine}.", None

    try:
        async with RiotAPIClient(default_headers={"X-Riot-Token": token}) as client:
            if game == "TFT":
                match_list = await client.get_tft_match_v1_match_ids_by_puuid(region=mass_region, puuid=puuid)
            elif game == "League":
                match_list = await client.get_lol_match_v5_match_ids_by_puuid(region=mass_region, puuid=puuid, queries={"start": 0, "count": 20})

            if not match_list:
                return f"No matches found for {gameName}#{tagLine}.", None, puuid

            target_queue = dicts.game_type_to_id[mode]
            matching_data = []

            async def mark_match_time(game, match_id, target_queue):
                if game == "TFT":
                    match_info = await client.get_tft_match_v1_match(region=mass_region, id=match_id)
                    queue_id = match_info['info']['queue_id']
                    if queue_id == target_queue:
                        matching_data.append({
                            "match_id": match_id,
                            "timestamp": match_info['info']['game_datetime']
                        })
                elif game == "League":
                    match_info = await client.get_lol_match_v5_match(region=mass_region, id=match_id)
                    queue_id = match_info['info']['queueId']
                    if queue_id == target_queue:

                        matching_data.append({
                            "match_id": match_id,
                            "timestamp": match_info['info']['gameEndTimestamp']
                        })

            await asyncio.gather(*[mark_match_time(game, match_id, target_queue) for match_id in match_list])

            time_sorted = sorted(matching_data, key=lambda x: x["timestamp"], reverse=True)
            return None, time_sorted, puuid
    except Exception as err:
            return f"Error fetching last match for {gameName}#{tagLine}: {err}", None, puuid

async def generate_board_preview(index, puuid, region, mass_region, match_id, tft_token, mappings):

    async with RiotAPIClient(default_headers={"X-Riot-Token": tft_token}) as client:
        match_info = await client.get_tft_match_v1_match(region=mass_region, id=match_id)

    participants = match_info['info']['participants']

    # Sort in ascending order
    if puuid not in [p['puuid'] for p in participants]:
        return f"Could not find participant with PUUID: {puuid}"

    participants_sorted = sorted(participants, key=lambda x: x['placement'])
    puuid_list = [p['puuid'] for p in participants_sorted]
    current_index = puuid_list.index(puuid)
    participant = participants_sorted[index]

    # --- Traits Processing ---
    traits = participant['traits']
    filtered_traits = [trait for trait in traits if trait['style'] >= 1]
    sorted_traits = sorted(filtered_traits, key=lambda x: dicts.style_order.get(x['style'], 5))
    num_traits = len(sorted_traits)
    start_y = 15

    if num_traits == 0:
        trait_img_width = 0
    else:
        trait_img_width = int(min((675 / num_traits), 71))

    trait_img_height = 110
    trait_final_width = 675
    if puuid == participant['puuid']:
        background_color = "#2F3136"
    else:
        background_color = (0,0,0,0)

    trait_final_image = Image.new("RGBA", (trait_final_width, trait_img_height), background_color)

    async def process_trait(trait, i):
        temp_image = await trait_image(trait['name'], trait['style'], mappings["trait_icon_mapping"])
        if temp_image:
            temp_image = temp_image.convert("RGBA")
            temp_image.thumbnail((trait_img_width, int(trait_img_width*1.16)))
            mask = temp_image.split()[3]
            trait_final_image.paste(temp_image, (trait_img_width * i, start_y), mask)

    # Fetch trait images concurrently
    await asyncio.gather(*[process_trait(trait, i) for i, trait in enumerate(sorted_traits)])
    font = ImageFont.truetype("fonts/NotoSans-Bold.ttf", size=28)
    bold_font = ImageFont.truetype("fonts/NotoSans-Black.ttf", size=60)  

    companion_height = 235
    companion_width = 225
    companion_size = 100
    companion_final_image = Image.new("RGBA", (companion_width,  companion_height), background_color)
    companion_id = participant.get("companion", {}).get("content_ID")
    companion_path = get_companion_icon(mappings["companion_mapping"], companion_id)
    companion_url = f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/" + companion_path.lower()
    companion_image = await fetch_image(companion_url)
    square = center_square_crop(companion_image)
    circle_img = circular_crop(square)
    circle_img.thumbnail((companion_size,companion_size))
    draw = ImageDraw.Draw(companion_final_image)

    def truncate_text(draw, text, font, max_width):
        # Truncate text with ellipsis (…) if it exceeds the given pixel width.
        ellipsis = "…"
        if draw.textlength(text, font=font) <= max_width:
            return text
        while text and draw.textlength(text + ellipsis, font=font) > max_width:
            text = text[:-1]
        return text + ellipsis
        
    display_name = truncate_text(draw, participant.get('riotIdGameName', 'Unknown'), font, 210)
    bbox = font.getbbox(display_name)  # (x_min, y_min, x_max, y_max)
    name_w = bbox[2] - bbox[0]

    placements = ["1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th"]
    font_color = ['#F0B52B', "#B8B5B5", '#A45F00', '#595988', "#748283", '#748283', '#748283', '#748283']
    player_placement = participant.get("placement")
    
    draw.text((15, 5), placements[player_placement - 1], font=bold_font, fill=font_color[player_placement - 1])
    if name_w <= companion_size:
        draw_centered(draw, display_name, font, 65, y=190, fill="white")
    else:
        draw.text((15,190), display_name, font=font, fill="white")
    companion_final_image.paste(circle_img, (15,90), circle_img)

    try: 
        rank_info = await get_rank_info(region, participant["puuid"], tft_token)
    except Exception as err:
        return None, f"Error fetching rank info for {gameName}#{tagLine}: {err}"

    tier = "UNRANKED"
    rank = "I"
    if rank_info:
        for entry in rank_info:
            if entry['queueType'] == 'RANKED_TFT':
                tier = entry['tier']
                rank = entry['rank']

    rank_str = f"{dicts.rank_to_acronym[tier]}{dicts.rank_to_number[rank]}"

    bbox = font.getbbox(rank_str)  # (x_min, y_min, x_max, y_max)
    text_w = bbox[2] - bbox[0]
    padding_x = 12
    x, y = 130, 140
    rect_coords = [
        x, 
        y, 
        x + text_w + padding_x, 
        180
    ]
    draw.rounded_rectangle(rect_coords, radius=8, fill=dicts.rank_to_text_fill[tier], outline=None)
    draw.circle((100,175),15, fill="Black", outline="White")
    draw_centered(draw, str(participant.get("level","?")), font, 100, y=155, fill="White")
    draw.text((x + padding_x/2, y), rank_str, font=font, fill="#fdda82" if tier == "CHALLENGER" else "white")

    # --- Champions Processing ---
    units = participant.get('units', [])
    champ_unit_data_unsorted = []

    # Calculate total champion image width
    champ_img_width = int(min((655) / len(units), 70)) if units else 70
    champ_img_height = 125
    champ_final_image = Image.new("RGBA", (675, champ_img_height), background_color)

    async def process_unit(unit):
        champion_name = unit["character_id"]
        tier = unit["tier"]
        rarity = unit["rarity"]
        item_names = unit["itemNames"]

        custom_rarity = dicts.rarity_map.get(rarity, rarity)
        champ_icon_path = get_champ_icon(mappings["champ_mapping"], champion_name).lower()
        rarity_url = f"https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-tft-team-planner/global/default/images/cteamplanner_championbutton_tier{custom_rarity}.png"

        if champ_icon_path:
            champion_url = f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/{champ_icon_path}.png"
            champ_task = fetch_image(champion_url, (64, 64))
            rarity_task = fetch_image(rarity_url, (72, 72))
            
            icon_resized, rarity_resized = await asyncio.gather(champ_task, rarity_task)

            champ_unit_data_unsorted.append({
                "champion_name": champion_name,
                "icon_resized": icon_resized,
                "rarity_resized": rarity_resized,
                "rarity": rarity,
                "tier": tier,
                "item_names": item_names
            })

    # Fetch all champion icons & rarity images concurrently
    await asyncio.gather(*[process_unit(unit) for unit in units])
    champ_unit_data = sorted(champ_unit_data_unsorted, key=lambda x: x['rarity'])

    # --- Paste Champions & Items ---
    async def paste_champion(unit, i): 
        champ_image = await champion_image(unit, mappings["item_mapping"])
        if champ_image:
            champ_image.thumbnail((champ_img_width, champ_img_height))
            champ_final_image.paste(champ_image, (((champ_img_width + 1)* i), 0), champ_image)

    # Process and paste champions concurrently
    await asyncio.gather(*[paste_champion(unit, i) for i, unit in enumerate(champ_unit_data)])

    stats_height = 50
    stats_image = Image.new("RGBA", (900, stats_height), background_color)
    gold_image = await fetch_image("https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-tft/global/default/images/home/tft_icon_coins.png", (40,40))
    damage_image = await fetch_image("https://cdn.tft.tools/general/announce_icon_combat.png", (40,40))

    gold_left = str(participant.get('gold_left','Unknown')) + " |"
    stage, round_num  = divmod(participant.get('last_round','Unknown') - 4 , 7)
    total_damage_to_players = str(participant.get('total_damage_to_players','Unknown'))
    round_str = f"| {stage+2} - {round_num}"

    draw = ImageDraw.Draw(stats_image)
    bbox = font.getbbox(gold_left)  # (x_min, y_min, x_max, y_max)
    gold_w = bbox[2] - bbox[0]
    bbox = font.getbbox(total_damage_to_players) # (x_min, y_min, x_max, y_max)
    damage_w = bbox[2] - bbox[0]
    
    stats_image.paste(gold_image, (5,5), gold_image)
    draw.text((55,5), gold_left, font=font, fill="White")
    stats_image.paste(damage_image, (65 + gold_w, 5), damage_image)
    draw.text((105 + gold_w, 5), total_damage_to_players, font=font, fill="White")
    draw.text((115 + gold_w + damage_w, 5), round_str, font=font, fill="White")

    # --- Combine Images ---
    final_combined_image = Image.new("RGBA", (900, companion_height + stats_height), (0, 0, 0, 0))
    final_combined_image.paste(trait_final_image, (companion_width, 0), trait_final_image)
    final_combined_image.paste(champ_final_image, (companion_width, trait_img_height), champ_final_image)
    final_combined_image.paste(companion_final_image, (0,0), companion_final_image)
    final_combined_image.paste(stats_image, (0,companion_height), stats_image)

    # Get summoner's gameName and tagLine from the match_info
    gameName = participant.get('riotIdGameName', 'Unknown')
    tagLine = participant.get('riotIdTagline', 'Unknown')

    # Save & Return Image & Embed
    final_combined_image.save("player_board.png")
    embed_colors = ['#F0B52B', "#B8B5B5", '#A45F00', '#595988', "#748283", '#748283', '#748283', '#748283']
    embed = discord.Embed(
        color=discord.Color(int(embed_colors[player_placement - 1].strip("#"), 16))
    )

    file = discord.File("player_board.png", filename="player_board.png")
    embed.set_image(url="attachment://player_board.png")

    return embed, file, final_combined_image


# Function to grab previous match data
async def last_match(gameName, tagLine, mode, mass_region, tft_token, region, game_num):
    puuid = await get_puuid(gameName, tagLine, mass_region, tft_token)
    if not puuid:
        return f"Could not find PUUID for {gameName}#{tagLine}.", None, None, 0, None, None

    if not (1 <= game_num <= 20):
        return f"Please enter a number between 1 and 20.", None, None, 0, None, None

    try:
        async with RiotAPIClient(default_headers={"X-Riot-Token": tft_token}) as client:
            match_list = await client.get_tft_match_v1_match_ids_by_puuid(region=mass_region, puuid=puuid)
            if not match_list:
                return f"No matches found for {gameName}#{tagLine}.", None, None, 0, None, None

            target_queue = dicts.game_type_to_id[mode]
            match_id = None

            for match in match_list:
                match_info = await client.get_tft_match_v1_match(region=mass_region, id=match)
                queue_id = match_info['info']['queue_id']
                if (mode == "GameMode" and queue_id > target_queue) or (mode != "GameMode" and queue_id == target_queue):
                    game_num -= 1
                    if game_num == 0:
                        match_id = match
                        break

            if not match_id:
                return f"No recent {mode.lower()} matches found for {gameName}#{tagLine}.", None, None, 0, None, None

            match_info = await client.get_tft_match_v1_match(region=mass_region, id=match_id)
            participants = match_info['info']['participants']
            timestamp = match_info['info']['game_datetime'] / 1000
            formatted_time = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
            time_and_time_ago = formatted_time + ", " + time_ago(timestamp)
            riot_ids_tasks = [client.get_account_v1_by_puuid(region=mass_region, puuid=p['puuid']) for p in participants]
            rank_info_tasks = [get_rank_info(region, p['puuid'], tft_token) for p in participants]
            riot_ids, ranks = await asyncio.gather(asyncio.gather(*riot_ids_tasks), asyncio.gather(*rank_info_tasks))

            players_data = []
            player_elos = 0
            ranked_players = 8

            for i, participant in enumerate(participants):
                placement = participant['placement']
                riot_id = riot_ids[i]
                rank_info = ranks[i]

                name = f"{riot_id.get('gameName', 'Unknown')}#{riot_id.get('tagLine', '')}" if 'gameName' in riot_id else "Unknown Player"
                tier_and_rank = ""
                lp = 0

                for entry in rank_info:
                    if entry['queueType'] == 'RANKED_TFT':
                        tier = entry['tier']
                        rank = entry['rank']
                        tier_and_rank = f"{tier} {rank}"
                        lp = entry['leaguePoints']
                        break

                if tier_and_rank:
                    player_elos += dicts.rank_to_elo.get(tier_and_rank, 0) + lp
                    rank_icon = dicts.tier_to_rank_icon.get(tier, dicts.tier_to_rank_icon["UNRANKED"])
                else:
                    ranked_players -= 1
                    rank_icon = dicts.tier_to_rank_icon["UNRANKED"]

                players_data.append((placement, name, rank_icon))

            players_data.sort()
            avg_elo = player_elos / ranked_players if ranked_players else 0
            avg_rank, master_lp = round_elo_to_rank(avg_elo)

            result = ""
            full_name = f"{gameName}#{tagLine}"
            for placement, name, icon in players_data:
                if custom_equal(full_name, name, "_ "):
                    result += f"{icon} **{placement}** - **__{name}__**\n"
                    player_placement = placement
                else:
                    result += f"{icon} **{placement}** - {name}\n"

            return result, match_id, avg_rank, master_lp, time_and_time_ago, player_placement

    except Exception as err:
        return f"Error fetching last match for {gameName}#{tagLine}: {err}", None, None, 0, None

# Function to grab previous match data
async def league_last_match(gameName, tagLine, mass_region, lol_token, puuid, match_id, mode, mappings, background_color, header):
    try:
        async with RiotAPIClient(default_headers={"X-Riot-Token": lol_token}) as client:

            match_info = await client.get_lol_match_v5_match(region=mass_region, id=match_id)

            duration = match_info["info"]["gameDuration"]
            participants = match_info["info"]["participants"]
            endstamp = match_info["info"]["gameEndTimestamp"] / 1000
            maxDamage = participants[0]["totalDamageDealtToChampions"]
            maxTaken = participants[0]["totalDamageTaken"]
            blue_team = [p for p in participants if p["teamId"] == 100]
            red_team = [p for p in participants if p["teamId"] == 200]

            blue_win = match_info["info"]["teams"][0]["win"]
            remake = False
            for participant in participants:
                maxDamage = max(participant["totalDamageDealtToChampions"],maxDamage)
                maxTaken = max(participant["totalDamageTaken"],maxTaken)
                if participant["gameEndedInEarlySurrender"] == True:
                    remake = True

                if participant["puuid"] == puuid:
                    cs = participant["totalMinionsKilled"] + participant["neutralMinionsKilled"]
                    level = participant["champLevel"]
                    kills = participant["kills"]
                    deaths = participant["deaths"]
                    assists = participant["assists"]
                    gold = participant["goldEarned"]
                    killparticipation = participant.get("challenges", {}).get("killParticipation", 0)
                    win = participant["win"]
                    tab_final = Image.new("RGBA", (500, 100), background_color)

                    champ_path = get_lol_champ_icon(participant["championId"])
                    keystone_path = get_keystone_icon(mappings["keystone_mapping"], participant["perks"]["styles"][0]["selections"][0]["perk"]).lower()
                    runes_path = get_rune_icon(mappings["runes_mapping"], participant["perks"]["styles"][1]["style"]).lower()
                    summ1_path = get_summs_icon(mappings["summs_mapping"], participant["summoner1Id"]).lower()
                    summ2_path = get_summs_icon(mappings["summs_mapping"], participant["summoner2Id"]).lower()
                    items = [participant[f"item{i}"] for i in range(7)]
                    items_urls = [f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/{get_lol_item_icon(mappings['lol_item_mapping'], item).lower()}" for item in items]

                    # --- Fetch images concurrently ---
                    fetch_tasks = [
                        fetch_image(f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/{champ_path}.png", (60,60)),
                        fetch_image(f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/{keystone_path}", (30,30)),
                        fetch_image(f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/{runes_path}", (20,20)),
                        fetch_image(f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/{summ1_path}", (30,30)),
                        fetch_image(f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/{summ2_path}", (30,30)),
                        fetch_image(f"https://wiki.leagueoflegends.com/en-us/images/thumb/Gold_colored_icon.png/20px-Gold_colored_icon.png?39991", (20,20)),
                    ]
                    # Add item icons
                    fetch_tasks.extend([fetch_image(url, (30,30)) for url in items_urls])

                    images = await asyncio.gather(*fetch_tasks)
                    champ_image, keystone_image, runes_image, summ1_image, summ2_image, gold_image, *item_icons = images

                    for i, item_icon in enumerate(item_icons):
                        tab_final.paste(item_icon, (170 + 30*i, 70), item_icon)

                    tab_final.paste(champ_image, (170,0))
                    tab_final.paste(keystone_image, (260,0))
                    tab_final.paste(runes_image,(265,35))
                    tab_final.paste(summ1_image,(230,0))
                    tab_final.paste(summ2_image,(230,30))
                    tab_final.paste(gold_image, (390,75))
                    draw = ImageDraw.Draw(tab_final)

                    font = ImageFont.truetype("fonts/NotoSans-Bold.ttf", 15)
                    bold_font = ImageFont.truetype("fonts/NotoSans-Black.ttf", 17)  

                    kda_str = f"{kills} / {deaths} / {assists}"
                    if deaths == 0:
                        kda_ratio_str = "Perfect"
                    else:
                        kda_ratio = (kills + assists) / deaths
                        kda_ratio_str = f"{kda_ratio:.2f}:1  KDA"
                    minutes, secs = divmod(duration, 60)
                    cspm = cs * 60 / duration
                    time_str = f"{minutes}m {secs}s"
                    kp_str = f"P/Kill {killparticipation:.0%}"
                    cs_str = f"CS {cs} ({cspm:.1f})"
                    gold_str = f" {gold:,}"

                    if win:
                        font_color = "#5485eb"
                    else:
                        font_color = "#e64253"

                    if remake:
                        font_color = "#8a8a8a"

                    kda_color = "#8a8a8a"
                    if kda_ratio_str == "Perfect":
                        kda_color = "#f78324"
                    elif kda_ratio >= 5.00:
                        kda_color = "#f78324"
                    elif kda_ratio >= 4.00:
                        kda_color = "#188ae9"
                    elif kda_ratio >= 3.00:
                        kda_color = "#29b0a3"

                    if win:
                        result_str = "Victory"
                    elif remake:
                        result_str = "Remake"
                    else:
                        result_str = "Defeat"

                    end_str = time_ago(endstamp)

                    draw.text((15,0), f"{mode}", font=bold_font, fill=font_color)
                    draw.text((15,23), end_str, font=font, fill="white")
                    draw.text((15,61), result_str, font=font, fill="white")
                    draw.text((15,81), time_str, font=font, fill="white")
                    draw.text((295,0), kda_str, font=bold_font, fill="white")
                    draw.text((295,26), kda_ratio_str, font=font, fill=kda_color)
                    draw.text((390,0), kp_str, font=font, fill="white")
                    draw.text((390,26), cs_str, font=font, fill="white")
                    draw.text((410,75), gold_str, font=font, fill="white")
                    draw.rectangle([210,40,228,60], fill="black", outline=None)
                    draw_centered(draw, str(participant["champLevel"]), font, 220, y=40, fill="white")
                    buffer = BytesIO()
                    tab_final.save(buffer, format="PNG")
                    buffer.seek(0)

                    filename = f"tab_{match_id}.png"
                    final_file = discord.File(buffer, filename=filename)

                    tab_embed = discord.Embed(
                        title=f"Recent League match for {gameName}#{tagLine}"if header else None,
                        color=int(font_color.strip("#"), 16)
                    )

                    tab_embed.set_image(url=f"attachment://{filename}")

            return None, final_file, tab_embed, maxDamage, maxTaken, duration, blue_team, red_team, blue_win

    except Exception as err:
        return f"Error fetching last match for {gameName}#{tagLine}: {err}",  None, None, None, None, None, None, None, None
        
# Get recent x matches
async def recent_matches(gameName, tagLine, puuid, mode, mass_region, tft_token, num_matches):
    try:
        # Fetch the latest x matches
        placements = []
        real_num_matches = 0
        if num_matches > 20 or num_matches < 0:
            print(f"Please enter a number between 1 and 20.")
            return f"Please enter a number between 1 and 20.", None, None
        
        async with RiotAPIClient(default_headers={"X-Riot-Token": tft_token}) as client:
            match_list = await client.get_tft_match_v1_match_ids_by_puuid(region=mass_region, puuid=puuid)
            if not match_list:
                print(f"No matches found for {gameName}#{tagLine}.")
                return f"No matches found for {gameName}#{tagLine}.", None, None

            for match in match_list:
                match_info = await client.get_tft_match_v1_match(region=mass_region, id=match)
                if mode == "GameMode":
                    if match_info['info']['queue_id'] > dicts.game_type_to_id[mode]:
                        num_matches -= 1
                        real_num_matches += 1
                        for participant in match_info['info']['participants']:
                            player_puuid = participant['puuid']
                            if player_puuid == puuid:
                                placements.append(participant['placement'])
                                break
                else:
                    if match_info['info']['queue_id'] == dicts.game_type_to_id[mode]:
                        num_matches -= 1
                        real_num_matches += 1
                        for participant in match_info['info']['participants']:
                            player_puuid = participant['puuid']
                            if player_puuid == puuid:
                                placements.append(participant['placement'])
                                break
                if num_matches <= 0:
                    break
                
        if real_num_matches == 0:
            print(f"No recent {mode} matches found for {gameName}#{tagLine}.")
            return f"No recent {mode} matches found for {gameName}#{tagLine}.", None, None
        return None, placements, real_num_matches
        
    except Exception as err:
        print(f"Error fetching recent matches for {gameName}#{tagLine}: {err}")
        return f"Error fetching recent matches for {gameName}#{tagLine}: {err}", None, None

# Custom equal to handle spaces in usernames
def custom_equal(str1, str2, chars_to_ignore):
    str1 = str1.lower().translate(str.maketrans('', '', chars_to_ignore))
    str2 = str2.lower().translate(str.maketrans('', '', chars_to_ignore))
    return str1 == str2

# Function to check if user is linked based on discord id
async def check_data(id, pool, game):

    user_id = int(id)
    async with pool.acquire() as conn:
        row = await conn.fetchrow('''
            SELECT league_puuid, tft_puuid, game_name, tag_line, region, mass_region
            FROM users
            WHERE discord_id = $1
        ''', user_id)
    if row:
        gameName = row['game_name']
        tagLine = row['tag_line']
        region = row['region']
        mass_region = row['mass_region']
        puuid = row['tft_puuid'] if game == "TFT" else row['league_puuid']
        return True, gameName, tagLine, region, mass_region, puuid
    else:
        return False, None, None, None, None, None
    
# Function to check if user is linked based on name and tag
async def check_data_name_tag(name, tag, pool, game):
    async with pool.acquire() as conn:
        row = await conn.fetchrow('''
            SELECT discord_id, league_puuid, tft_puuid, game_name, tag_line, region, mass_region
            FROM users
            WHERE game_name = $1 AND tag_line = $2
        ''', name, tag)

    if row:
        gameName = row['game_name']
        tagLine = row['tag_line']
        region = row['region']
        mass_region = row['mass_region']
        puuid = row['tft_puuid'] if game.upper() == "TFT" else row['league_puuid']
        discord_id = row['discord_id']
        return True, gameName, tagLine, region, mass_region, puuid, discord_id
    else:
        # User not found
        return False, name, tag, None, None, None, None

# Command to get trait icon with texture 
async def trait_image(trait_name: str, style: int, trait_icon_mapping):
    try:
        # Download the trait texture  
        texture_url = f"https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-tft/global/default/{dicts.style_to_texture.get(style, 'default_texture')}.png"
        texture = await fetch_image(texture_url)

        # Download the trait icon
        icon_path = get_trait_icon(trait_icon_mapping, trait_name).lower()
        icon_url = f"https://raw.communitydragon.org/latest/game/{icon_path}.png"

        icon = await fetch_image(icon_url)

        # Convert white parts of the icon to black for better visibility
        icon = icon.convert("RGBA")
        pixels = icon.load()
        for i in range(icon.width):
            for j in range(icon.height):
                r, g, b, a = pixels[i, j]
                if r > 200 and g > 200 and b > 200:  # If the pixel is white
                    pixels[i, j] = (0, 0, 0, a)  # Change it to black (retain transparency)

        # Resize the icon
        icon_resized = icon.resize((64, 64), Image.LANCZOS)

        # Ensure the texture is large enough to paste the icon
        if texture.width < 64 or texture.height < 64:
            print(f"Warning: Texture is too small ({texture.width}x{texture.height}), skipping overlay.")
            return None

        # Paste the icon onto the texture
        texture.paste(icon_resized, (12, 18), icon_resized)

        return texture

    except Exception as e:
        print(f"Error in trait_image for {trait_name} (style {style}): {e}")
        return None

async def champion_image(unit, item_mapping):

    try:
        tier = unit["tier"]
        item_names = unit["item_names"]

        rarity_resized = unit["rarity_resized"]
        icon_resized = unit["icon_resized"]
        tile_image = Image.new("RGBA", (72, 115), (0, 0, 0, 0))

        tile_image.paste(rarity_resized, (0, 20), rarity_resized)
        tile_image.paste(icon_resized, (4, 24), icon_resized)

        # Add tier stars (2★ or 3★)
        if tier in {2, 3}:
            tier_url = f"https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-tft/global/default/tft-piece-star-{tier}.png"
            tier_icon = await fetch_image(tier_url, (72, 36))
            if tier_icon:
                tile_image.paste(tier_icon, (0, 0), tier_icon)

        # Add item icons if available
        if item_names:
            item_urls = [
                f"https://raw.communitydragon.org/latest/game/{get_item_icon(item_mapping, item).lower()}"
                for item in item_names
            ]

            fetch_tasks = [
                fetch_image(url, (24,24))
                for url in item_urls
            ]
            item_icons = await asyncio.gather(*fetch_tasks)

            # Center the items horizontally below the portrait
            for i, item_icon in enumerate(item_icons):
                tile_image.paste(item_icon, (24 * i, 92), item_icon)

        return tile_image

    except Exception as e:
        print(f"Error building champion image for {unit.get('champion_name', 'Unknown')}: {e}")
        return None

# Calculate how long ago timestamp was to include in !r response
def time_ago(timestamp):
    # Get the current time in Unix timestamp format
    current_time = time.time()
    
    # Calculate the difference between the current time and the given timestamp
    time_difference = current_time - timestamp

    # Define time units in seconds
    seconds_in_minute = 60
    seconds_in_hour = seconds_in_minute * 60
    seconds_in_day = seconds_in_hour * 24
    seconds_in_week = seconds_in_day * 7
    seconds_in_month = seconds_in_day * 30
    seconds_in_year = seconds_in_day * 365
    
    # Check which time unit is appropriate for the difference
    if time_difference < seconds_in_minute:
        return f"{int(time_difference)} seconds ago"
    elif time_difference < seconds_in_hour:
        minutes = time_difference // seconds_in_minute
        return f"{int(minutes)} minutes ago"
    elif time_difference < seconds_in_day:
        hours = time_difference // seconds_in_hour
        return f"{int(hours)} hours ago"
    elif time_difference < seconds_in_week:
        days = time_difference // seconds_in_day
        return f"{int(days)} days ago"
    elif time_difference < seconds_in_month:
        weeks = time_difference // seconds_in_week
        return f"{int(weeks)} weeks ago"
    elif time_difference < seconds_in_year:
        months = time_difference // seconds_in_month
        return f"{int(months)} months ago"
    else:
        years = time_difference // seconds_in_year
        return f"{int(years)} years ago"

def lp_to_div(elo_value):
    # Reverse mapping: Elo -> rank string
    elo_to_rank = {v: k for k, v in dicts.rank_to_elo.items()}
    sorted_ranks = sorted(elo_to_rank.items(), key=lambda x: x[0])

    rank_name = "UNRANKED"
    threshold = 0

    for value, name in sorted_ranks:
        if elo_value >= value:
            rank_name = name
            threshold = value
        else:
            break

    excess = elo_value - threshold

    if rank_name != "UNRANKED":
        tier, division = rank_name.split(" ")
    else:
        tier, division = rank_name, None

    return tier, division, excess

async def is_user_in_guild(guild: discord.Guild, user_id: int) -> bool:
    try:
        await guild.fetch_member(user_id)
        return True
    except discord.NotFound:
        return False
    except discord.Forbidden:
        print("Missing permissions to fetch member.")
        return False
    except discord.HTTPException as e:
        print(f"HTTP error while fetching member: {e}")
        return False

async def parse_args(ctx, args):
    gameNum, gameName, tagLine, user_id = None, None, None, None
    
    if len(args) == 3: # <gameNum, username, tagline>
        if args[0].isdigit():
            gameNum = int(args[0])
            gameName, tagLine = args[1], args[2]
        else:
            return None, None, None, None, "Format should be <game #, username, tagline>"
        
    elif len(args) == 2 and args[1].startswith("<@"): # <gameNum, @mention>
        if args[0].isdigit():
            gameNum = int(args[0])
            user_id = args[1].strip("<@!>")
        else:
            return None, None, None, None, "Format should be <game #, @mention>"
        
    elif len(args) == 2: # <username, tagline>
        gameName, tagLine = args[0], args[1]

    elif len(args) == 1 and args[0].startswith("<@"): # <@mention>
        user_id = args[0].strip("<@!>")

    elif len(args) == 1 and args[0].isdigit(): # <gameNum>
        gameNum = int(args[0])
        user_id = ctx.author.id

    elif len(args) == 0: # all defaults 
        user_id = ctx.author.id

    else:
        return None, None, None, None, (
            "Usage: `<matches?> <name> <tag>` | `<matches?> @mention` | "
            "`<matches?>` (self) | no args (self)"
        )

    return gameNum, gameName, tagLine, user_id, None

def draw_centered(draw, text, font, center_x, y, fill="white"):
    bbox = font.getbbox(text)
    text_w = bbox[2] - bbox[0]
    # left x so that text's midpoint sits on center_x
    x = center_x - text_w // 2
    draw.text((x, y), text, font=font, fill=fill)