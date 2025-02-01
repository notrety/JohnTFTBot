import discord
import requests
import os
from discord.ext import commands
from riotwatcher import TftWatcher
from dotenv import load_dotenv
from pymongo.mongo_client import MongoClient
from PIL import Image
from io import BytesIO
uri = "mongodb+srv://sosafelix:lee2014kms2017@tfteamusers.deozh.mongodb.net/?retryWrites=true&w=majority&appName=TFTeamUsers"

# Create a new client and connect to the server
client = MongoClient(uri)
db = client["Users"]
collection = db["users"]

# Send a ping to confirm a successful connection
try:
    client.admin.command('ping')
    print("Pinged your deployment. You successfully connected to MongoDB!")
except Exception as e:
    print(e)

# Load the .env file
load_dotenv()

# Get the token
bot_token = os.getenv("DISCORD_BOT_TOKEN")
riot_token = os.getenv("RIOT_API_TOKEN")

# Setting json urls
json_url = "https://raw.communitydragon.org/latest/cdragon/tft/en_us.json"
item_json_url = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/tftitems.json"

# Setting champion and trait mapping
response = requests.get(json_url)
if response.status_code == 200:
    data = response.json()  # Assuming data is a dictionary
    
    # Access the 'sets' property
    sets_data = data.get("sets", {})  # Get the 'sets' dictionary, default to empty if not found
    
    # Access the '13' property within 'sets'
    set_13 = sets_data.get("13", {})  # Get the '13' property, default to empty dict if not found
    
    # Access the 'champions' and 'traits' lists within '13'
    champ_mapping = set_13.get("champions", [])  # Get the 'champions' list, default to empty list if not found
    trait_icon_mapping = set_13.get("traits", []) # Get the 'traits' list, default to empty list if not found
    print("Traits and Champions parsed successfully")
else:
    print("Failed to fetch data")

# Setting item mapping based on item json
response = requests.get(item_json_url)
if response.status_code == 200:
    item_mapping = response.json()  # Assuming data is a dictionary
    print("Items parsed successfully")
else:
    print("Failed to fetch data")

# Enable all necessary intents
intents = discord.Intents.default()
intents.message_content = True  # Enable message content intent
intents.members = True          # Enable server members intent
intents.presences = True        # Enable presence intent

# Create bot instance
bot = commands.Bot(command_prefix="!", intents=intents, case_insensitive=True)

# Define regions
mass_region = "americas"
region = "na1"

# Dictionary Converting Ranks to Elos
rank_to_elo = {
    "IRON IV": 0,
    "IRON III": 100,
    "IRON II": 200,
    "IRON I": 300,
    "BRONZE IV": 400,
    "BRONZE III": 500,
    "BRONZE II": 600,
    "BRONZE I": 700,
    "SILVER IV": 800,
    "SILVER III": 900,
    "SILVER II": 1000,
    "SILVER I": 1100,
    "GOLD IV": 1200,
    "GOLD III": 1300,
    "GOLD II": 1400,
    "GOLD I": 1500,
    "PLATINUM IV": 1600,
    "PLATINUM III": 1700,
    "PLATINUM II": 1800,
    "PLATINUM I": 1900,
    "EMERALD IV": 2000,
    "EMERALD III": 2100,
    "EMERALD II": 2200,
    "EMERALD I": 2300,
    "DIAMOND IV": 2400,
    "DIAMOND III": 2500,
    "DIAMOND II": 2600,
    "DIAMOND I": 2700,
    "MASTER I": 2800, 
    "GRANDMASTER I": 2800,
    "CHALLENGER I": 2800,
}

# Dictionary Converting Game Type to Queue ID
game_type_to_id = {
    "Ranked": 1100,
    "Normal": 1090,
    "Double Up ": 1160,
    "Hyper Roll": 1130,
    # "Fortune's Favor": 1170,
    # "Soul Brawl": 1180,
    # "Choncc's Treasure": 1190,
    "GameMode": 1165
}

# Dictionary Converting style to texture endpoint
style_to_texture = {
    0: "",
    1: "bronze-hover",
    2: "silver-hover",
    3: "unique-hover",
    4: "gold-1",
    5: "chromatic"
}

# Dictionary for custom order for styles (Unique > Pris > Gold > Silver > Bronze)
style_order = {
    3: 0,
    5: 1,
    4: 2,
    2: 3,
    1: 4
}

# Custom rarity mapping (For some reason api rarity and unit cost are not the same)
rarity_map = {
    8: 6,
    6: 5,
    4: 4,
    2: 1,
    1: 2,
    0: 1
}

# Initialize Riot API Wrapper
tft_watcher = TftWatcher(riot_token)

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
        if champion.get("apiName") == characterName:
            print("Champion Found")
            # Assuming 'champion' dictionary contains a 'squareIcon' key with the icon path
            return champion.get("tileIcon", "")[:-4]  # Remove the last 4 characters (usually file extension)
    
    print("Champion Not Found")
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
def get_puuid(gameName, tagLine):
    try:
        api_url = f"https://{mass_region}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{gameName}/{tagLine}?api_key={riot_token}"
        resp = requests.get(api_url)
        player_info = resp.json()
        return player_info.get('puuid')
    except Exception as err:
        print("Failed to retrieve PUUID for {gameName}#{tagLine}.")
        return None

# Function to get rank info from puuid and return embed with rank icon
def get_rank_embed(gameName, tagLine):
    puuid = get_puuid(gameName, tagLine)
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
def last_match(gameName, tagLine, mode):

    puuid = get_puuid(gameName, tagLine)
    if not puuid:
        return f"Could not find PUUID for {gameName}#{tagLine}.", None, 0

    try:
        # Fetch the latest 20 matches
        match_list = tft_watcher.match.by_puuid(region, puuid, count=20)
        if not match_list:
            return f"No matches found for {gameName}#{tagLine}.", None, 0

        match_id = None
        match_found = False
        for index, match in enumerate(match_list):
            match_info = tft_watcher.match.by_id(region, match)
            if mode == "GameMode":
                if match_info['info']['queue_id'] > game_type_to_id[mode]:
                    match_id = match_list[index]  # Get the latest match ID
                    match_found = True
                    break
            else:
                if match_info['info']['queue_id'] == game_type_to_id[mode]:
                    match_id = match_list[index]  # Get the latest match ID
                    match_found = True
                    break
        if not match_found:
            mode = mode.lower()
            return f"No recent {mode} matches found for {gameName}#{tagLine}.", None, 0

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

            for entry in rank_info:
                if entry['queueType'] == 'RANKED_TFT':
                    tier = entry['tier']
                    rank = entry['rank']
                    tier_and_rank = tier + " " + rank
                    lp = entry['leaguePoints']
            
            player_elos += rank_to_elo[tier_and_rank]
            player_elos += int(lp)

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
        avg_elo = player_elos / 8
        master_plus_lp = 0 
        avg_rank = ""
        # Find all keys greater than value
        if avg_elo > 2800:
            #master+ lobby, return average lp instead
            avg_rank = "Master+"
            master_plus_lp = int(round(avg_elo - 2800))
        else:
            keys = [key for key, val in rank_to_elo.items() if val > avg_elo]
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

        return result, avg_rank, master_plus_lp
    
    except Exception as err:
        return f"Error fetching last match for {gameName}#{tagLine}: {err}", None, 0

# Custom equal to handle spaces in usernames
def custom_equal(str1, str2, chars_to_ignore):
    str1 = str1.lower().translate(str.maketrans('', '', chars_to_ignore))
    str2 = str2.lower().translate(str.maketrans('', '', chars_to_ignore))
    return str1 == str2

# Function to check if user is linked 
def check_data(id):
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
def trait_image(trait_name: str, style: int):
    try:
        # Download the trait texture  
        texture_url = f"https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-tft/global/default/{style_to_texture.get(style, 'default_texture')}.png"
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

# Show bot is online
@bot.event
async def on_ready():
    print(f'Bot is online as {bot.user}')

# Basic test command
@bot.command()
async def ping(ctx):
    await ctx.send('Lima Oscar Lima!')

# Command to fetch TFT stats
@bot.command(name="stats", aliases=["stast", "s", "tft"])
async def stats(ctx, *args):
    data = False
    if len(args) == 2:  # Expecting name and tagline
        gameName = args[0]
        tagLine = args[1]
        data = True
    elif len(args) == 1 and args[0].startswith("<@"):  # Check if it's a mention
        mentioned_user = args[0]
        user_id = mentioned_user.strip("<@!>")  # Remove the ping format to get the user ID
        # Check if user is linked
        data, gameName, tagLine = check_data(user_id)
        if not data:
            await ctx.send(f"{mentioned_user} has not linked their name and tagline.")
    elif len(args) == 0: # Check for linked account by sender
        data, gameName, tagLine = check_data(ctx.author.id)
        if not data:
            await ctx.send("You have not linked any data or provided a player. Use `!link <name> <tag>` to link your account.")
    else: 
        # User formatted command incorrectly, let them know
        await ctx.send("Please use this command by typing in a name and tagline, by pinging someone, or with no extra text if your account is linked.")

    if data:
        rank_embed, error_message = get_rank_embed(gameName, tagLine)  # Unpack tuple

        if error_message:
            await ctx.send(error_message)  # Send error as text
        else:
            await ctx.send(embed=rank_embed)  # Send embed

# Command to fetch last match data
@bot.command(name="r", aliases=["rs","recent","rr","rn","rh","rd","rg"])
async def r(ctx, *args):
    if ctx.invoked_with == "rn":
        game_type = "Normal"
    elif ctx.invoked_with == "rg":
        game_type = "Gamemode"
    elif ctx.invoked_with == "rh":
        game_type = "Hyper Roll"
    elif ctx.invoked_with == "rd":
        game_type = "Double Up"
    else:
        game_type = "Ranked"

    data = False
    if len(args) == 2:  # Expecting name and tagline
        gameName = args[0]
        tagLine = args[1]
        data = True
    elif len(args) == 1 and args[0].startswith("<@"):  # Check if it's a mention
        mentioned_user = args[0]
        user_id = mentioned_user.strip("<@!>")  # Remove the ping format to get the user ID
        # Check if user is linked
        data, gameName, tagLine = check_data(user_id)
        if not data:
            await ctx.send(f"{mentioned_user} has not linked their name and tagline.")
    elif len(args) == 0: # Check for linked account by sender
        data, gameName, tagLine = check_data(ctx.author.id)
        if not data:
            await ctx.send("You have not linked any data or provided a player. Use `!link <name> <tag>` to link your account.")
    else: 
        # User formatted command incorrectly, let them know
        await ctx.send("Please use this command by typing in a name and tagline, by pinging someone, or with no extra text if your account is linked.")

    if data:
        result, avg_rank, master_plus_lp = last_match(gameName, tagLine, game_type)
        result_embed = discord.Embed(
                        title=f"Recent {game_type} TFT Match Placements:",
                        description=result,
                        color=discord.Color.blue()
                    )
        if master_plus_lp == 0:
            result_embed.set_footer(text=f"Average Lobby Rank: {avg_rank}")
        else:
            result_embed.set_footer(text=f"Average Lobby Rank: {avg_rank} {master_plus_lp} LP")
        await ctx.send(embed=result_embed)

# Command to check all available commands, UPDATE THIS AS NEW COMMANDS ARE ADDED
@bot.command()
async def commands(ctx): 
    commands_embed = discord.Embed(
    title=f"Commands List",
    description=f"""
**!r** - View most recent ranked match\n
**!rn** - View most recent normal match\n
**!rh** - View most recent hyper roll match\n
**!rd** - View most recent double up match\n
**!rg** - View most recent game mode match\n
**!stats** - Check ranked stats for a player\n
**!ping** - Test that bot is active\n
**!commands** - Get a list of all commands\n
**!link** - Link discord account to riot account
    """,
    color=discord.Color.blue()
    )
    await ctx.send(embed=commands_embed)

# Command to link riot and discord accounts, stored in mongodb database
@bot.command()
async def link(ctx, name: str, tag: str):
    user_id = str(ctx.author.id)
    
    # Check if the user already has linked data
    existing_user = collection.find_one({"discord_id": user_id})
    
    if existing_user:
        # If user already has data, update it
        collection.update_one(
            {"discord_id": user_id},
            {"$set": {"name": name, "tag": tag}}
        )
        await ctx.send(f"Your data has been updated to: {name} {tag}")
    else:
        # If no data exists, insert a new document for the user
        collection.insert_one({
            "discord_id": user_id,
            "name": name,
            "tag": tag
        })
        await ctx.send(f"Your data has been linked: {name} {tag}")

# Command to return traits units and items of a player given puuid and match_id, to be merged into last_match
@bot.command()
async def player_board(ctx, puuid: str, match_id: str):
    match_info = tft_watcher.match.by_id(region, match_id)

    # Find the participant matching the given PUUID
    for participant in match_info['info']['participants']:
        if participant['puuid'] == puuid:
            # --- Traits Logic ---
            traits = participant['traits']
            
            # Filter traits to only include those with style 1 or higher
            filtered_traits = [trait for trait in traits if trait['style'] >= 1]

            # Sort the filtered traits by style using the custom order
            sorted_traits = sorted(filtered_traits, key=lambda x: style_order.get(x['style'], 5))  # Default to 5 if style not found

            num_traits = len(sorted_traits)
            if num_traits == 0:
                await ctx.send("No valid traits found for this player.")
                return

            # Create the trait image with extended width
            trait_img_width = 89 * num_traits
            trait_img_height = 103
            trait_final_image = Image.new("RGBA", (trait_img_width, trait_img_height), (0, 0, 0, 0))

            for i, trait in enumerate(sorted_traits):
                temp_image = trait_image(trait['name'], trait['style'])
                if temp_image:
                    temp_image = temp_image.convert("RGBA")  # Ensure it's RGBA
                    mask = temp_image.split()[3]  # Get the alpha channel
                    trait_final_image.paste(temp_image, (89 * i, 0), mask)

            # --- Champions Logic ---
            units = participant.get('units', [])
            champ_img_width = 0
            champ_img_height = 140
            champ_unit_data = []

            for unit in units:
                champion_name = unit["character_id"]
                tier = unit["tier"]
                rarity = unit["rarity"]
                item_names = unit["itemNames"]

                custom_rarity = rarity_map.get(rarity, rarity)

                # Get the champion icon
                champ_icon_path = get_champ_icon(champ_mapping, champion_name).lower()
                if champ_icon_path:
                    champion_url = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/" + champ_icon_path + ".png"
                    champion_response = requests.get(champion_url)
                    icon = Image.open(BytesIO(champion_response.content)).convert("RGBA")
                    icon_resized = icon.resize((64, 64), Image.LANCZOS)

                    # Get the rarity icon based on the custom rarity
                    rarity_url = f"https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-tft-team-planner/global/default/images/cteamplanner_championbutton_tier{custom_rarity}.png"
                    rarity_response = requests.get(rarity_url)
                    rarity_border = Image.open(BytesIO(rarity_response.content)).convert("RGBA")
                    rarity_resized = rarity_border.resize((72, 72), Image.LANCZOS)

                    champ_img_width += 72  # Add the width of the champion icon with rarity
                    champ_unit_data.append({
                        "champion_name": champion_name,
                        "icon_resized": icon_resized,
                        "rarity_resized": rarity_resized,
                        "tier": tier,
                        "item_names": item_names
                    })

            # Create the champion image
            champ_final_image = Image.new("RGBA", (champ_img_width, champ_img_height), (0, 0, 0, 0))
            current_x = 0

            for unit in champ_unit_data:
                champ_final_image.paste(unit["rarity_resized"], (current_x, 25), unit["rarity_resized"])
                champ_final_image.paste(unit["icon_resized"], (current_x + 4, 29), unit["icon_resized"])

                if unit["tier"] == 2 or unit["tier"] == 3:
                    tier_icon_path = f"https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-tft/global/default/tft-piece-star-{unit['tier']}.png"
                    tier_response = requests.get(tier_icon_path)
                    tier_icon = Image.open(BytesIO(tier_response.content)).convert("RGBA")
                    tier_resized = tier_icon.resize((72, 36), Image.LANCZOS)
                    champ_final_image.paste(tier_resized, (current_x, 0), tier_resized)

                for i, item_name in enumerate(unit["item_names"]):
                    item_icon_path = get_item_icon(item_mapping, item_name).lower()
                    item_url = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/" + item_icon_path
                    item_response = requests.get(item_url)
                    item_icon = Image.open(BytesIO(item_response.content)).convert("RGBA")
                    item_resized = item_icon.resize((23, 23), Image.LANCZOS)
                    champ_final_image.paste(item_resized, (current_x + 23 * i, 97), item_resized)

                current_x += 72

            # --- Combine the Trait and Champion Images ---
            # Create a new final image with combined height (traits + champions)
            final_img_height = trait_img_height + champ_img_height
            final_combined_image = Image.new("RGBA", (max(trait_img_width, champ_img_width), final_img_height), (0, 0, 0, 0))

            # Paste trait and champion images
            final_combined_image.paste(trait_final_image, (0, 0), trait_final_image)
            final_combined_image.paste(champ_final_image, (0, trait_img_height), champ_final_image)

            # Save and send the final image
            final_combined_image.save("player_board.png")
            file = discord.File("player_board.png", filename="player_board.png")
            embed = discord.Embed(title="Player Traits & Champions Overlay")
            embed.set_image(url="attachment://player_board.png")
            await ctx.send(embed=embed, file=file)
            return  # Exit after sending

    # If no participant matches the given PUUID
    await ctx.send(f"Could not find participant with PUUID: {puuid}")

# Run the bot with your token
bot.run(bot_token)