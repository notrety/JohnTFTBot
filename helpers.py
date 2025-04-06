# File containing all helper functions for commands

import discord
import requests
import dicts
import time
from datetime import datetime
from PIL import Image
from io import BytesIO
from pulsefire.clients import RiotAPIClient

async def store_elo_and_games(collection, mass_region, riot_token, region):
    all_users = collection.find()
    for user in all_users:
        name = user['name']
        tag = user['tag']
        puuid = await get_puuid(name, tag, mass_region, riot_token)
        if not puuid:
            print(f"Error updating elo and games, couldn't get PUUID for {name}#{tag}")
        rank_info = await get_rank_info(region, puuid, riot_token)
        for entry in rank_info:
            if entry['queueType'] == 'RANKED_TFT':
                total_games = entry['wins'] + entry['losses']
                elo = dicts.rank_to_elo[entry['tier'] + " " + entry['rank']] + int(entry['leaguePoints'])
        collection.update_one(
                {"name": user["name"], "tag": user["tag"]},
                {"$set": {"games": total_games, "elo": elo}}
            )
    print("Games and elos stored.")
# Function to return cutoff lp for challenger and grandmaster

async def get_rank_info(region, puuid, riot_token):
    async with RiotAPIClient(default_headers={"X-Riot-Token": riot_token}) as client:
        summoner = await client.get_tft_summoner_v1_by_puuid(region=region, puuid=puuid)
        rank_info = await client.get_tft_league_v1_entries_by_summoner(region=region, summoner_id=summoner["id"])
    return rank_info

async def get_cutoff(riot_token, region):
    # grab all players who are challenger, grandmaster, and master
    async with RiotAPIClient(default_headers={"X-Riot-Token": riot_token}) as client:
        challengers = await client.get_tft_league_v1_challenger_league(region=region)
        grandmasters = await client.get_tft_league_v1_grandmaster_league(region=region)        
        masters = await client.get_tft_league_v1_master_league(region=region)


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
    # Loop through each champion in the list
    for champion in champs_data:        
        # Check if the apiName matches the provided characterName
        if champion.get("apiName").lower() == characterName.lower():
            # Assuming 'champion' dictionary contains a 'squareIcon' key with the icon path
            return champion.get("tileIcon", "")[:-4]  # Remove the last 4 characters (usually file extension)
    
    print(f"{characterName} Not Found")
    return None

# Function to get the item icon path
def get_item_icon(items_data, itemName):
    for item in items_data:
            if item.get("nameId") == itemName:
                return item.get("squareIconPath", "")[21:]
    print(f"{itemName} Not Found")
    return "assets/maps/tft/icons/items/hexcore/tft_item_blank.tft_set14.png"

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
            match_info = await client.get_tft_match_v1_match(region =mass_region, id = match[0])
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
async def get_rank_embed(name, tagLine, mass_region, region, riot_token):
    gameName = name.replace("_", " ")
    puuid = await get_puuid(gameName, tagLine, mass_region, riot_token)
    if not puuid:
        return None, f"Could not find PUUID for {gameName}#{tagLine}."

    try:
        rank_info = await get_rank_info(region, puuid, riot_token)

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
                    title=f"TFT Stats for {gameName}#{tagLine}",
                    description=f"🏆 **{tier} {rank}** ({lp} LP)\n🎯 **Top 4 Rate:** {top_four_rate}%\n📊 **Total Games:** {total_games}",
                    color=discord.Color.blue()
                )
                embed.set_thumbnail(url=rank_icon_url)  # Set the rank icon
                embed.set_footer(text="Powered by Riot API | Data from TFT Ranked")

                return embed, None  # Return the embed
    
        return None, f"{gameName}#{tagLine} has no ranked TFT games."

    except Exception as err:
        return None, f"Error fetching rank info for {gameName}#{tagLine}: {err}"

# Function to grab previous match data
async def last_match(gameName, tagLine, mode, mass_region, riot_token, region, game_num):

    puuid = await get_puuid(gameName, tagLine, mass_region, riot_token)
    if not puuid:
        print(f"Could not find PUUID for {gameName}#{tagLine}.")
        return f"Could not find PUUID for {gameName}#{tagLine}.", None, None, 0, None

    try:
        # Fetch the latest 20 matches
        async with RiotAPIClient(default_headers={"X-Riot-Token": riot_token}) as client:
            match_list = await client.get_tft_match_v1_match_ids_by_puuid(region=mass_region, puuid=puuid)

        if not match_list:
            print(f"No matches found for {gameName}#{tagLine}.")
            return f"No matches found for {gameName}#{tagLine}.", None, None, 0, None
        if game_num > 20 or game_num < 0:
            print(f"Please enter a number between 1 and 20.")
            return f"Please enter a number between 1 and 20.", None, None, 0, None
        elif game_num > len(match_list) and game_num <= 20:
            print(f"Not enough {mode} matches found for {gameName}#{tagLine}.")
            return f"Not enough {mode} matches found for {gameName}#{tagLine}.", None, None, 0, None

        match_id = None
        match_found = False
        for index, match in enumerate(match_list):
            async with RiotAPIClient(default_headers={"X-Riot-Token": riot_token}) as client:
                match_info = await client.get_tft_match_v1_match(region=mass_region, id=match)
            if mode == "GameMode":
                if match_info['info']['queue_id'] > dicts.game_type_to_id[mode]:
                    if game_num > 1:
                        game_num -= 1
                    else:
                        match_id = match_list[index]  # Get the latest match ID
                        match_found = True
                        break
            else:
                if match_info['info']['queue_id'] == dicts.game_type_to_id[mode]:
                    if game_num > 1:
                        game_num -= 1
                    else:
                        match_id = match_list[index]  # Get the latest match ID
                        match_found = True
                        break
        if not match_found:
            mode = mode.lower()
            print(f"No recent {mode} matches found for {gameName}#{tagLine}.")
            return f"No recent {mode} matches found for {gameName}#{tagLine}.", None, None, 0, None

        # Fetch match details
        async with RiotAPIClient(default_headers={"X-Riot-Token": riot_token}) as client:
            match_info = await client.get_tft_match_v1_match(region=mass_region, id=match_id)

        players_data = []
        player_elos = 0

        # Get timestamp to include in response
        timestamp = match_info['info']['game_datetime'] / 1000 # unix timestamp, divide by 1000 because its in milliseconds
        time = datetime.fromtimestamp(timestamp) # convert to time object
        formatted_time = time.strftime('%Y-%m-%d %H:%M:%S') # format nicely
        time_passed = time_ago(timestamp)
        time_and_time_ago = formatted_time + ", " + time_passed
        ranked_players = 8
        # Find player stats
        for participant in match_info['info']['participants']:
            player_puuid = participant['puuid']
            placement = participant['placement']
            rank_icon = None

            # Check elos of all players to calculate average
            rank_info = await get_rank_info(region, player_puuid, riot_token)
            tier_and_rank = ""
            for entry in rank_info:
                if entry['queueType'] == 'RANKED_TFT':
                    tier = entry['tier']
                    rank = entry['rank']
                    tier_and_rank = tier + " " + rank
                    lp = entry['leaguePoints']
            
            if not tier_and_rank == "":
                player_elos += dicts.rank_to_elo[tier_and_rank]
                player_elos += int(lp)
                rank_icon = dicts.tier_to_rank_icon[tier]
            else: 
                ranked_players-=1
                rank_icon = dicts.tier_to_rank_icon["UNRANKED"]

            # Fetch gameName and tagLine from PUUID
            async with RiotAPIClient(default_headers={"X-Riot-Token": riot_token}) as client:
                riot_id_info = await client.get_account_v1_by_puuid(region=mass_region, puuid=player_puuid)

            if 'gameName' in riot_id_info and 'tagLine' in riot_id_info:
                player_name = f"{riot_id_info['gameName']}#{riot_id_info['tagLine']}"
            else:
                player_name = "Unknown Player"

            # Store placement & name
            players_data.append((placement, player_name, rank_icon))

        # Sort players by placement
        players_data.sort()
        # Calculate average lobby elo
        avg_elo = player_elos / ranked_players
        rounded_elo = (avg_elo // 100) * 100  # Round down to the nearest 100, avg elo of 99 should still say iron iv, etc
        master_plus_lp = 0 
        avg_rank = ""
        # Find all keys greater than value
        if avg_elo > 2800:
            #master+ lobby, return average lp instead
            avg_rank = "Master+"
            master_plus_lp = int(round(avg_elo - 2800))
        else:
            avg_rank = next((key for key, val in dicts.rank_to_elo.items() if val == rounded_elo), None)
        # Format the message
        result = ""
        for placement, name, icon in players_data:
            full_name = gameName + "#" + tagLine
            if custom_equal(full_name, name, "_ "):
                result += f"{icon} **{placement}** - **__{name}__**\n"
            else:
                result += f"{icon} **{placement}** - {name}\n"

        return result, match_id, avg_rank, master_plus_lp, time_and_time_ago
    
    except Exception as err:
        print(f"Error fetching last match for {gameName}#{tagLine}: {err}")
        return f"Error fetching last match for {gameName}#{tagLine}: {err}", None, None, 0, None

# Get recent x matches
async def recent_matches(gameName, tagLine, mode, mass_region, riot_token, num_matches):
    puuid = await get_puuid(gameName, tagLine, mass_region, riot_token)
    if not puuid:
        print(f"Could not find PUUID for {gameName}#{tagLine}.")
        return f"Could not find PUUID for {gameName}#{tagLine}.", None, None
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
        # If user is linked, use stored data as name and tag
        gameName = user_data['name']
        tagLine = user_data['tag']
        # Indicates that user has linked data
        return True, gameName, tagLine
    else:
        # If user isn't linked, inform the user
        return False, None, None
    
# Function to check if user is linked based on name and tag
def check_data_name_tag(name, tag, collection):
    name_with_spaces = name.replace("_", " ")
    query = {"name": name_with_spaces, "tag": tag} 
    user_data = collection.find_one(query) # Query the database

    if user_data:
        return True
    return False

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

