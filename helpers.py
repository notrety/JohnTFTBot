# File containing all helper functions for commands
import aiohttp
import asyncio
from collections import Counter
import discord
import requests
import dicts
import time
from datetime import datetime
from PIL import Image
from io import BytesIO
from pulsefire.clients import RiotAPIClient

async def get_all_set_placements(collection, mass_region, riot_token):
    players = list(collection.find({}))
    print("player list obtained")
    tasks = [process_player(player, collection, mass_region, riot_token) for player in players]
    await asyncio.gather(*tasks)

async def process_player(player_doc, collection, mass_region, riot_token):
    puuid = player_doc["puuid"]
    mass_region = player_doc["mass_region"]
    name = player_doc["name"]
    try:
        match_ids = await get_all_current_set_match_ids(puuid, riot_token, mass_region, target_set=14)
        print(f"match ids obtained for {name}")
    except Exception as e:
        print(f"Error fetching match IDs for {puuid}: {e}")
        return

    placement_counts = {str(i): 0 for i in range(1, 9)}  # placements 1 through 8

    for match_id in match_ids:
        print(f"obtaining placement in match {match_id} for {name}")
        try:
            async with RiotAPIClient(default_headers={"X-Riot-Token": riot_token}) as client:
                match_data = await client.get_tft_match_v1_match(region=mass_region, id=match_id)
            info = match_data["info"]

            for participant in info["participants"]:
                if participant["puuid"] == puuid:
                    placement = int(participant["placement"])
                    if 1 <= placement <= 8:
                        placement_counts[str(placement)] += 1
                    break

        except Exception as e:
            print(f"Error fetching match {match_id}: {e}")
    print(f"placement counts for {name}: {placement_counts}")
    # Update MongoDB with placements
    collection.update_one(
        {"_id": player_doc["_id"]},
        {"$set": {"placement_counts": placement_counts}}
    )
    total_games = sum(placement_counts.values())
    average_placement = (
        sum(int(p) * c for p, c in placement_counts.items()) / total_games
        if total_games > 0 else None
    )
    rounded_avp = round(average_placement, 2)
    wins = placement_counts["1"]
    win_rate = 100 * wins / total_games
    rounded_win_rate = round(win_rate, 1)
    # Update DB with AVP
    collection.update_one(
        {"_id": player_doc["_id"]},
        {"$set": {"average_placement": rounded_avp}}
    )
    # Update Win Rate
    collection.update_one(
        {"_id": player_doc["_id"]},
        {"$set": {"win_rate": rounded_win_rate}}
    )

async def get_all_current_set_match_ids(puuid, riot_token, mass_region):
    match_ids = []
    start = 0
    batch_size = 100
    startTime = 1743200400 # Unix timestamp for start of set 14

    while True:
        try:
            async with RiotAPIClient(default_headers={"X-Riot-Token": riot_token}) as client:
                ids = await client.get_tft_match_v1_match_ids_by_puuid(
                    region=mass_region,
                    puuid=puuid,
                    queries= {"startTime": startTime, "start": start, "count": batch_size}
                )
        except Exception as e:
            print(f"Error fetching match IDs at start={start}: {e}")
            break

        if not ids:
            break  # No more matches
        print(f"ids for {puuid}: {len(ids)}")
        for match_id in ids:
            try:
                async with RiotAPIClient(default_headers={"X-Riot-Token": riot_token}) as client:
                    match_data = await client.get_tft_match_v1_match(region=mass_region, id=match_id)
                    if match_data["info"].get("queue_id") == 1100:
                        match_ids.append(match_id)
            except Exception as e:
                print(f"Error fetching match {match_id}: {e}")
                continue
        print(f"match ids for {puuid}: {len(match_ids)}")
        start += batch_size
    print(match_ids)
    return match_ids


async def daily_store_stats(collection, riot_token):
    all_users = collection.find()
    for user in all_users:
        name = user.get('name')
        tag = user.get('tag')
        puuid = user.get('puuid')
        region = user.get('region')
        games = user.get('games')
        mass_region = user.get('mass_region')
        placement_counts = user.get('placement_counts')
        rank_info = await get_rank_info(region, puuid, riot_token)
        for entry in rank_info:
            if entry['queueType'] == 'RANKED_TFT':
                total_games = entry['wins'] + entry['losses']
                elo = dicts.rank_to_elo[entry['tier'] + " " + entry['rank']] + int(entry['leaguePoints'])
        today_games = total_games - games
        if today_games != 0:
            async with RiotAPIClient(default_headers={"X-Riot-Token": riot_token}) as client:
                match_list = await client.get_tft_match_v1_match_ids_by_puuid(region=mass_region, puuid=puuid)
                        
                if not match_list:
                    return f"No matches found for {name}#{tag}."
                
                placements = []
                num_matches = today_games

                for match in match_list:
                    if num_matches <= 0:
                        break
                    try:
                        match_info = await client.get_tft_match_v1_match(region=mass_region, id=match)
                    except Exception as e:
                        print(f"Error fetching match {match}: {e}")
                        continue
                    
                    if match_info['info']['queue_id'] == dicts.game_type_to_id["Ranked"]:
                        num_matches -= 1
                        for participant in match_info['info']['participants']:
                            if participant['puuid'] == puuid:
                                placements.append(participant['placement'])
                                break
            if not placements:
                return f"No ranked placements found for {name}#{tag}."
            # Build the $inc update dictionary
            counts = Counter(placements)
            update = {
                f"placement_counts.{placement}": count
                for placement, count in counts.items()
            }

            # Perform the update
            collection.update_one(
                {"puuid": puuid},  # Find the player
                {"$inc": update}   # Increment only the placements that occurred today
            )

            wins = user.get("placement_counts")["1"]
            win_rate = 100 * wins / total_games
            rounded_win_rate = round(win_rate, 1)
            average_placement = (
                sum(int(p) * c for p, c in placement_counts.items()) / total_games
                if total_games > 0 else None
            )
            rounded_avp = round(average_placement, 2)
            collection.update_one(
                    {"name": user["name"], "tag": user["tag"]},
                    {"$set": {"games": total_games, "elo": elo, "win_rate": rounded_win_rate, "average_placement": rounded_avp}}
                )
        

# Update after pulsefire adds endpoint for league_v1_by_puuid
async def get_rank_info(region, puuid, riot_token):
    url = f"https://{region}.api.riotgames.com/tft/league/v1/by-puuid/{puuid}"
    headers = {"X-Riot-Token": riot_token}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                print(response.json())
                return await response.json()
            else:
                print(f"Error fetching rank info: {response.status}")
                return None

# Function to return cutoff lp for challenger and grandmaster
async def get_cutoff(riot_token, region):
    # grab all players who are challenger, grandmaster, and master
    async with RiotAPIClient(default_headers={"X-Riot-Token": riot_token}) as client:
        challengers, grandmasters, masters = await asyncio.gather(
            client.get_tft_league_v1_challenger_league(region=region),
            client.get_tft_league_v1_grandmaster_league(region=region),
            client.get_tft_league_v1_master_league(region=region),
        )

        # put all the lps into a list
        lps = [entry.get('leaguePoints') for entry in challengers['entries']]
        lps.extend(entry.get('leaguePoints') for entry in grandmasters['entries'])
        lps.extend(entry.get('leaguePoints') for entry in masters['entries'])

        # sort lps 
        lps_sorted = sorted(lps, reverse=True)

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
    # temp patch for nitro, remove after set 14
    if characterName == "TFT14_SummonLevel4":
        return "assets/characters/tft14_summonlevel4/hud/tft14_summonlevel4_square.tft_set14"
    
    if characterName == "TFT14_SummonLevel2":
        return "assets/characters/tft14_summonlevel2/hud/tft14_summonlevel2_square.tft_set14"
    
    if characterName == "TFT14_Summon_Turret":
        return "assets/characters/tft14_summon_turret/hud/tft14_summon_turret_square.tft_set14"
    
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

# Function to fetch PUUID
async def get_puuid(gameName, tagLine, mass_region, riot_token):
    try:
        async with RiotAPIClient(default_headers={"X-Riot-Token": riot_token}) as client:
            account = await client.get_account_v1_by_riot_id(region=mass_region, game_name=gameName, tag_line=tagLine)
        return account['puuid']

    except Exception as err:
        print(f"Failed to retrieve PUUID for {gameName}#{tagLine}.{err}")
        return None

async def get_last_game_companion(name, tagLine, mass_region, riot_token):
    gameName = name.replace("_", " ")
    puuid = await get_puuid(gameName, tagLine, mass_region, riot_token)
    if not puuid:
        return None, f"Could not find PUUID for {gameName}#{tagLine}."

    try:
        async with RiotAPIClient(default_headers={"X-Riot-Token": riot_token}) as client:
            match = await client.get_tft_match_v1_match_ids_by_puuid(region = mass_region, puuid=puuid, queries= {"start": 0, "count": 1}) # Grabbing last match 
            match_info = await client.get_tft_match_v1_match(region = mass_region, id = match[0])
        for participant in match_info['info']['participants']:
            if participant['puuid'] == puuid:
                return participant['companion']['content_ID']
            
    except Exception as err:
        return None, f"Error fetching rank info for {gameName}#{tagLine}: {err}"

# Function to calculate ranked elo based on given PUUID
async def calculate_elo(puuid, riot_token, region):
    attempts = 0

    while True:
        try:
            # Fetch summoner data
            rank_info = await get_rank_info(region, puuid, riot_token)

            # Find Ranked TFT entry
            for entry in rank_info:
                if entry['queueType'] == 'RANKED_TFT':
                    return dicts.rank_to_elo[entry['tier'] + " " + entry['rank']] + int(entry['leaguePoints']), entry['tier'], entry['rank'], entry['leaguePoints']
            return 0, "UNRANKED", "", 0  # If no ranked TFT entry is found

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                retry_after = int(e.response.headers.get("Retry-After", 5))  # Get wait time
                wait_time = min(5 * (2 ** attempts), 60)  # Exponential backoff (max 60s)
                print(f"Rate limited! Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
                attempts += 1
            else:
                raise e  # Re-raise other errors

# Function to get rank info from puuid and return embed with rank icon
async def get_rank_embed(name, tagLine, region, riot_token, puuid):
    gameName = name.replace("_", " ")
    try: 
        rank_info = await get_rank_info(region, puuid, riot_token)
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
                    url=f"https://lolchess.gg/profile/{region[:-1]}/{gameName.replace(" ", "%20")}-{tagLine}/set14",
                    icon_url="https://cdn-b.saashub.com/images/app/service_logos/184/6odf4nod5gmf/large.png?1627090832"
                )

                return embed, None  # Return the embed
            
    return None, f"{gameName}#{tagLine} is unranked."

def round_elo_to_rank(avg_elo):
    if avg_elo > 2800:
        return "Master+", int(round(avg_elo - 2800))
    rounded_elo = (avg_elo // 100) * 100
    rank = next((key for key, val in dicts.rank_to_elo.items() if val == rounded_elo), None)
    return rank, 0

# Function to grab previous match data
async def last_match(gameName, tagLine, mode, mass_region, riot_token, region, game_num):
    puuid = await get_puuid(gameName, tagLine, mass_region, riot_token)
    if not puuid:
        return f"Could not find PUUID for {gameName}#{tagLine}.", None, None, 0, None

    if not (1 <= game_num <= 20):
        return f"Please enter a number between 1 and 20.", None, None, 0, None

    try:
        async with RiotAPIClient(default_headers={"X-Riot-Token": riot_token}) as client:
            match_list = await client.get_tft_match_v1_match_ids_by_puuid(region=mass_region, puuid=puuid)
            if not match_list:
                return f"No matches found for {gameName}#{tagLine}.", None, None, 0, None

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
                return f"No recent {mode.lower()} matches found for {gameName}#{tagLine}.", None, None, 0, None

            match_info = await client.get_tft_match_v1_match(region=mass_region, id=match_id)
            participants = match_info['info']['participants']
            timestamp = match_info['info']['game_datetime'] / 1000
            formatted_time = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
            time_and_time_ago = formatted_time + ", " + time_ago(timestamp)

            riot_ids_tasks = [client.get_account_v1_by_puuid(region=mass_region, puuid=p['puuid']) for p in participants]
            rank_info_tasks = [get_rank_info(region, p['puuid'], riot_token) for p in participants]
            riot_ids, ranks = await asyncio.gather(asyncio.gather(*riot_ids_tasks), asyncio.gather(*rank_info_tasks))

            players_data = []
            player_elos = 0
            ranked_players = 8

            for i, participant in enumerate(participants):
                placement = participant['placement']
                player_puuid = participant['puuid']
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
                else:
                    result += f"{icon} **{placement}** - {name}\n"

            return result, match_id, avg_rank, master_lp, time_and_time_ago

    except Exception as err:
        return f"Error fetching last match for {gameName}#{tagLine}: {err}", None, None, 0, None
    
# Get recent x matches
async def recent_matches(gameName, tagLine, puuid, mode, mass_region, riot_token, num_matches):
    try:
        # Fetch the latest x matches
        placements = []
        real_num_matches = 0
        if num_matches > 20 or num_matches < 0:
            print(f"Please enter a number between 1 and 20.")
            return f"Please enter a number between 1 and 20.", None, None
        
        async with RiotAPIClient(default_headers={"X-Riot-Token": riot_token}) as client:
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
def check_data(id, collection):
    user_id = str(id)
        
    # Query the database for the user's data
    user_data = collection.find_one({"discord_id": user_id})

    if user_data:
        # If user is linked, return stored data
        gameName = user_data.get('name')
        tagLine = user_data.get('tag')
        region = user_data.get('region')
        mass_region = user_data.get('mass_region')
        puuid = user_data.get('puuid')
        discord = user_data.get("discord_id")
        # Indicates that user has linked data
        return True, gameName, tagLine, region, mass_region, puuid, discord
    else:
        # If user isn't linked, inform the user
        return False, None, None, None, None, None, None
    

# Function to check if user is linked based on name and tag
def check_data_name_tag(name, tag, collection):
    query = {"name": name, "tag": tag} 
    user_data = collection.find_one(query) # Query the database

    if user_data:
        # If user is linked, return stored data
        gameName = user_data.get('name')
        tagLine = user_data.get('tag')
        region = user_data.get('region')
        mass_region = user_data.get('mass_region')
        puuid = user_data.get('puuid')
        discord = user_data.get("discord_id")
        # Indicates that user has linked data
        return True, gameName, tagLine, region, mass_region, puuid, discord
    return False, name, tag, None, None, None, None


# Command to get trait icon with texture 
def trait_image(trait_name: str, style: int, trait_icon_mapping):
    try:
        # Download the trait texture  
        texture_url = f"https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-tft/global/default/{dicts.style_to_texture.get(style, 'default_texture')}.png"
        texture_response = requests.get(texture_url)

        if texture_response.status_code != 200:
            print(f"Failed to fetch texture from {texture_url}, Status Code: {texture_response.status_code}")
            return None

        texture = Image.open(BytesIO(texture_response.content))

        # Download the trait icon
        icon_path = get_trait_icon(trait_icon_mapping, trait_name).lower()
        icon_url = f"https://raw.communitydragon.org/latest/game/{icon_path}.png"

        icon_response = requests.get(icon_url)
        if icon_response.status_code != 200:
            print(f"Failed to fetch icon from {icon_url}, Status Code: {icon_response.status_code}")
            return None

        icon = Image.open(BytesIO(icon_response.content))

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

# Helper function to take in arguments for compare command
async def take_in_compare_args(args, collection, author_id, riot_token):
    data = error_message = p1_name = p1_tag = p1_region = p1_puuid = p2_name = p2_tag = p2_region = p2_puuid = None
    if len(args) == 4:  # Expecting player 1 name, p1 tag, p2 name, p2 tag
        p1_name = args[0]
        p1_tag = args[1]
        p2_name = args[2]
        p2_tag = args[3]
        data = True
    elif len(args) == 3 and args[0].startswith("<@"):  # player 1 is ping, player 2 is name and tag
        mentioned_user = args[0]
        user_id = mentioned_user.strip("<@!>")  # Remove the ping format to get the user ID
        # Check if user is linked
        data, p1_name, p1_tag, p1_region, _, p1_puuid, discord = check_data(user_id, collection)
        if not data:
            error_message = f"{mentioned_user} has not linked their name and tagline."
        p2_name = args[1]
        p2_tag = args[2]
    elif len(args) == 3 and args[2].startswith("<@"):  # player 2 is ping, player 1 is name and tag
        mentioned_user = args[2]
        user_id = mentioned_user.strip("<@!>")  # Remove the ping format to get the user ID
        # Check if user is linked
        data, p2_name, p2_tag, p2_region, _, p2_puuid, discord = check_data(user_id, collection)
        if not data:
            error_message = f"{mentioned_user} has not linked their name and tagline."
        p1_name = args[0]
        p1_tag = args[1]
    elif len(args) == 2 and args[0].startswith("<@") and args[1].startswith("<@"): # both players are pinged
        user1 = args[0]
        user2 = args[1]
        user1_id = user1.strip("<@!>")  # Remove the ping format to get the user ID
        user2_id = user2.strip("<@!>")
        # Check if both users are linked
        data, p1_name, p1_tag, p1_region, _, p1_puuid, discord = check_data(user1_id, collection)
        if not data:
            error_message = f"{user1} has not linked their name and tagline. "
        data, p2_name, p2_tag, p2_region, _, p2_puuid, discord = check_data(user2_id, collection)
        if not data:
            error_message += f"{user2} has not linked their name and tagline."
    elif len(args) == 2:  # Expect p1 to be linked, args to be p2 name and tag
        data, p1_name, p1_tag, p1_region, _, p1_puuid, discord = check_data(author_id, collection)
        if not data:
            error_message = "You have not linked any data or provided a player. Use `/link <name> <tag>` to link your account."
        p2_name = args[0]
        p2_tag = args[1]
    elif len(args) == 1 and args[0].startswith("<@"): # Expect p1 to be linked, args to be pinged user
        data, p1_name, p1_tag, p1_region, _, p1_puuid, discord = check_data(author_id, collection)
        if not data:
            error_message = "You have not linked any data or provided a player. Use `/link <name> <tag>` to link your account."
        mentioned_user = args[0]
        user_id = mentioned_user.strip("<@!>")  # Remove the ping format to get the user ID
        # Check if user is linked
        data, p2_name, p2_tag, p2_region, _, p2_puuid, discord = check_data(user_id, collection)
        if not data:
            error_message = f"{mentioned_user} has not linked their name and tagline."
    else: 
        # User formatted command incorrectly, let them know
        error_message = "Please use this command by typing in two names and taglines (all separated with spaces), " \
        "by pinging two people, or typing one name and tag if you are linked and comparing to yourself."

    if not p1_region:
        p1_region = "na1"
    if not p2_region:
        p2_region = "na1"
    if not p1_puuid:
        p1_puuid = await get_puuid(p1_name, p1_tag, "americas", riot_token)
    if not p2_puuid:
        p2_puuid = await get_puuid(p2_name, p2_tag, "americas", riot_token)

    return data, error_message, p1_name, p1_tag, p1_region, p1_puuid, p2_name, p2_tag, p2_region, p2_puuid

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
