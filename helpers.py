# File containing all helper functions for commands

import discord
import requests
import dicts
from PIL import Image
from io import BytesIO

# Function to get the trait icon path
def get_trait_icon(traits_data, traitName):
    for trait in traits_data:
        if trait.get("apiName") == traitName:
            print("Trait Found")
            return trait.get("icon", "")[:-4] # Removes the beginning of the datadragon icon_path 
    print("Trait not Found")
    return None  # Return None if the trait_id is not found

# Function to get the champ icon path
def get_champ_icon(champs_data, characterName):
    # Loop through each champion in the list
    for champion in champs_data:        
        # Check if the apiName matches the provided characterName
        if champion.get("apiName").lower() == characterName.lower():
            print("Champion Found")
            # Assuming 'champion' dictionary contains a 'squareIcon' key with the icon path
            return champion.get("tileIcon", "")[:-4]  # Remove the last 4 characters (usually file extension)
    
    print(f"{characterName} Not Found")
    return None

# Function to get the item icon path
def get_item_icon(items_data, itemName):
    for item in items_data:
            if item.get("nameId") == itemName:
                print("Item Found")
                return item.get("squareIconPath", "")[21:]
    print("Item Not Found")
    return None

# Function to fetch PUUID
def get_puuid(gameName, tagLine, mass_region, riot_token):
    try:
        api_url = f"https://{mass_region}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{gameName}/{tagLine}?api_key={riot_token}"
        resp = requests.get(api_url)
        player_info = resp.json()
        return player_info.get('puuid')
    except Exception as err:
        print("Failed to retrieve PUUID for {gameName}#{tagLine}.")
        return None

# Function to calculate ranked elo based on given PUUID
def calculate_elo(puuid, tft_watcher, region):
    summoner = tft_watcher.summoner.by_puuid(region, puuid)
    rank_info = tft_watcher.league.by_summoner(region, summoner['id'])

    for entry in rank_info:
        if entry['queueType'] == 'RANKED_TFT':
            return dicts.rank_to_elo[entry['tier'] + " " + entry['rank']] + int(entry['leaguePoints'])

# Function to get rank info from puuid and return embed with rank icon
def get_rank_embed(gameName, tagLine, mass_region, riot_token, tft_watcher, region):
    puuid = get_puuid(gameName, tagLine, mass_region, riot_token)
    if not puuid:
        return None, f"Could not find PUUID for {gameName}#{tagLine}."

    try:
        summoner = tft_watcher.summoner.by_puuid(region, puuid)
        rank_info = tft_watcher.league.by_summoner(region, summoner['id'])

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
def last_match(gameName, tagLine, mode, mass_region, riot_token, tft_watcher, region):

    puuid = get_puuid(gameName, tagLine, mass_region, riot_token)
    if not puuid:
        print(f"Could not find PUUID for {gameName}#{tagLine}.")
        return f"Could not find PUUID for {gameName}#{tagLine}.", None, None, 0

    try:
        # Fetch the latest 20 matches
        match_list = tft_watcher.match.by_puuid(region, puuid, count=20)
        if not match_list:
            print(f"No matches found for {gameName}#{tagLine}.")
            return f"No matches found for {gameName}#{tagLine}.", None, None, 0

        match_id = None
        match_found = False
        for index, match in enumerate(match_list):
            match_info = tft_watcher.match.by_id(region, match)
            if mode == "GameMode":
                if match_info['info']['queue_id'] > dicts.game_type_to_id[mode]:
                    match_id = match_list[index]  # Get the latest match ID
                    match_found = True
                    break
            else:
                if match_info['info']['queue_id'] == dicts.game_type_to_id[mode]:
                    match_id = match_list[index]  # Get the latest match ID
                    match_found = True
                    break
        if not match_found:
            mode = mode.lower()
            print(f"No recent {mode} matches found for {gameName}#{tagLine}.")
            return f"No recent {mode} matches found for {gameName}#{tagLine}.", None, None, 0

        # Fetch match details
        match_info = tft_watcher.match.by_id(region, match_id)
        players_data = []
        
        player_elos = 0

        # Find player stats
        for participant in match_info['info']['participants']:
            player_puuid = participant['puuid']
            placement = participant['placement']

            # Check elos of all players to calculate average
            summoner = tft_watcher.summoner.by_puuid(region, player_puuid)
            rank_info = tft_watcher.league.by_summoner(region, summoner['id'])

            tier_and_rank = ""
            ranked_players = 8
            for entry in rank_info:
                if entry['queueType'] == 'RANKED_TFT':
                    tier = entry['tier']
                    rank = entry['rank']
                    tier_and_rank = tier + " " + rank
                    lp = entry['leaguePoints']
            
            if not tier_and_rank == "":
                player_elos += dicts.rank_to_elo[tier_and_rank]
                player_elos += int(lp)
            else: 
                ranked_players-=1

            # Fetch gameName and tagLine from PUUID
            riot_id_url = f"https://{mass_region}.api.riotgames.com/riot/account/v1/accounts/by-puuid/{player_puuid}?api_key={riot_token}"
            response = requests.get(riot_id_url)
            riot_id_info = response.json()

            if 'gameName' in riot_id_info and 'tagLine' in riot_id_info:
                player_name = f"{riot_id_info['gameName']}#{riot_id_info['tagLine']}"
            else:
                player_name = "Unknown Player"

            # Store placement & name
            players_data.append((placement, player_name))

        # Sort players by placement
        players_data.sort()

        # Calculate average lobby elo
        avg_elo = player_elos / ranked_players
        master_plus_lp = 0 
        avg_rank = ""
        # Find all keys greater than value
        if avg_elo > 2800:
            #master+ lobby, return average lp instead
            avg_rank = "Master+"
            master_plus_lp = int(round(avg_elo - 2800))
        else:
            keys = [key for key, val in dicts.rank_to_elo.items() if val > avg_elo]
            avg_rank = keys[0]
        # Format the message
        result = ""
        for placement, name in players_data:
            full_name = gameName + "#" + tagLine
            if custom_equal(full_name, name, "_ "):
                if placement != 8:
                    result += f"🏆 **{placement}** - **__{name}__**\n"
                else:
                    result += f"<:rety:1229135551714824354> **{placement}** - **__{name}__**\n"
            else:
                if placement != 8:
                    result += f"🏆 **{placement}** - {name}\n"
                else:
                    result += f"<:rety:1229135551714824354> **{placement}** - {name}\n"

        return result, match_id, avg_rank, master_plus_lp
    
    except Exception as err:
        print(f"Error fetching last match for {gameName}#{tagLine}: {err}")
        return f"Error fetching last match for {gameName}#{tagLine}: {err}", None, None, 0

# Custom equal to handle spaces in usernames
def custom_equal(str1, str2, chars_to_ignore):
    str1 = str1.lower().translate(str.maketrans('', '', chars_to_ignore))
    str2 = str2.lower().translate(str.maketrans('', '', chars_to_ignore))
    return str1 == str2

# Function to check if user is linked 
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
        print(f"Fetching icon from: {icon_url}")

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
