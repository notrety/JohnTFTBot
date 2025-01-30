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

# URL for the TFT traits JSON data from Community Dragon
json_url = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/tfttraits.json"

# Fetch JSON data
response = requests.get(json_url)

if response.status_code == 200:
    try:
        print("JSON Parsed successfully")
        trait_icon_mapping = response.json()  # Parse JSON safely
    except ValueError:
        print("Failed to parse JSON.")
        trait_icon_mapping = []  # Default empty list
else:
    print(f"Failed to fetch JSON, status code: {response.status_code}")
    trait_icon_mapping = []  # Default empty list

# Function to get the trait icon path
def get_trait_icon(traits_data, trait_id):
    """Finds the trait in the JSON data and returns its icon path."""
    for trait in traits_data:
        if trait.get("trait_id") == trait_id:
            return trait.get("icon_path")[43:] # removes the beginning of the datadragon icon_path 
    return None  # Return None if the trait_id is not found

# Enable all necessary intents
intents = discord.Intents.default()
intents.message_content = True  # Enable message content intent
intents.members = True          # Enable server members intent
intents.presences = True        # Enable presence intent

# Create bot instance
bot = commands.Bot(command_prefix="!", intents=intents)

# Define regions
mass_region = "americas"
region = "na1"

# Dictionary to store rank icons from GitHub
RANK_ICON_URLS = {
    "IRON": "https://raw.githubusercontent.com/notrety/JohnTFTbot/main/ranked_emblems/iron.png",
    "BRONZE": "https://raw.githubusercontent.com/notrety/JohnTFTbot/main/ranked_emblems/bronze.png",
    "SILVER": "https://raw.githubusercontent.com/notrety/JohnTFTbot/main/ranked_emblems/silver.png",
    "GOLD": "https://raw.githubusercontent.com/notrety/JohnTFTbot/main/ranked_emblems/gold.png",
    "PLATINUM": "https://raw.githubusercontent.com/notrety/JohnTFTbot/main/ranked_emblems/platinum.png",
    "EMERALD": "https://raw.githubusercontent.com/notrety/JohnTFTbot/main/ranked_emblems/emerald.png",
    "DIAMOND": "https://raw.githubusercontent.com/notrety/JohnTFTbot/main/ranked_emblems/diamond.png",
    "MASTER": "https://raw.githubusercontent.com/notrety/JohnTFTbot/main/ranked_emblems/master.png",
    "GRANDMASTER": "https://raw.githubusercontent.com/notrety/JohnTFTbot/main/ranked_emblems/grandmaster.png",
    "CHALLENGER": "https://raw.githubusercontent.com/notrety/JohnTFTbot/main/ranked_emblems/challenger.png"
}

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


# Initialize Riot API Wrapper
tft_watcher = TftWatcher(riot_token)

# Function to fetch PUUID
def get_puuid(gameName, tagLine):
    try:
        api_url = f"https://{mass_region}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{gameName}/{tagLine}?api_key={riot_token}"
        resp = requests.get(api_url)
        player_info = resp.json()
        return player_info.get('puuid')
    except Exception as err:
        return None

# Function to get rank info
def get_rank_embed(gameName, tagLine):
    """Fetch TFT rank and return a Discord embed with a rank icon."""
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

                # Get rank icon URL from GitHub
                rank_icon_url = RANK_ICON_URLS.get(tier.upper(), "https://raw.githubusercontent.com/notrety/JohnTFTbot/main/ranked_emblems/" + rank + ".png")

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
def last_match(gameName, tagLine):
    puuid = get_puuid(gameName, tagLine)
    if not puuid:
        return f"Could not find PUUID for {gameName}#{tagLine}.", None, 0

    try:
        # Fetch the latest match ID
        match_list = tft_watcher.match.by_puuid(region, puuid, count=1)
        if not match_list:
            return f"No matches found for {gameName}#{tagLine}.", None, 0

        match_id = match_list[0]  # Get the latest match ID

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

def custom_equal(str1, str2, chars_to_ignore):
    str1 = str1.lower().translate(str.maketrans('', '', chars_to_ignore))
    str2 = str2.lower().translate(str.maketrans('', '', chars_to_ignore))
    return str1 == str2

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

# Show bot is online
@bot.event
async def on_ready():
    print(f'Bot is online as {bot.user}')

# Basic test command
@bot.command()
async def ping(ctx):
    await ctx.send('Lima Oscar Lima!')

# Command to fetch TFT stats
@bot.command()
async def stats(ctx, *args):
    """Fetch and display TFT rank stats for a player."""
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
@bot.command(name="r", aliases=["rs"])
async def r(ctx, *args):
    """Display recent match data for a player."""
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
        result, avg_rank, master_plus_lp = last_match(gameName, tagLine)
        result_embed = discord.Embed(
                        title=f"Last TFT Match Placements:",
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
**!rs / !r** - Fetch most recent match data\n
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
    

@bot.command()
async def overlay(ctx, trait_name: str, style_name: str):
    """Overlay a trait icon onto the main sprite image."""
    
    # Download the base texture atlas (sprite)
    atlas_url = "https://raw.communitydragon.org/pbe/game/assets/ux/tft/tft_traits_texture_atlas.png"
    atlas_response = requests.get(atlas_url)
    atlas = Image.open(BytesIO(atlas_response.content))
    if (style_name == "kBronze"):
        left = 0 + 49 * 3   # x-coordinate of the left edge
        top = 3     # y-coordinate of the top edge
    elif (style_name == "kSilver"):
        left = 49 * 5   # x-coordinate of the left edge
        top = 3     # y-coordinate of the top edge
    elif (style_name == "kGold"):
        left = 49 * 7   # x-coordinate of the left edge
        top = 3     # y-coordinate of the top edge
    elif (style_name == "kChromatic"):
        left = 49 * 7  # x-coordinate of the left edge
        top = 60     # y-coordinate of the top edge
    elif (style_name == "kUnique"):
        left = 49 * 7    # x-coordinate of the left edge
        top = 180     # y-coordinate of the top edge
    right = left + 52  # x-coordinate of the right edge
    bottom = top + 52  # y-coordinate of the bottom edge
    cropped_atlas_section = atlas.crop((left, top, right, bottom))

    # Download the overlay icon (this should be a separate image)
    icon_path = get_trait_icon(trait_icon_mapping, trait_name)
    icon_url = "https://raw.communitydragon.org/latest/game/assets/ux/traiticons/" + icon_path.lower()
    icon_response = requests.get(icon_url)
    icon = Image.open(BytesIO(icon_response.content))

    # Converting icon color from white to black for visibility
    icon = icon.convert("RGBA")
    pixels = icon.load()
    for i in range(icon.width):
        for j in range(icon.height):
            r, g, b, a = pixels[i, j]
            if r > 200 and g > 200 and b > 200:  # If the pixel is white
                pixels[i, j] = (0, 0, 0, a)  # Change it to black (retain transparency)

    # Resize the icon to fit the 32x32 section (optional)
    icon_resized = icon.resize((32, 32), Image.LANCZOS)

    # Paste the icon onto the cropped section of the atlas
    cropped_atlas_section.paste(icon_resized, (10, 10), icon_resized)  # Pasting icon in the top-left corner of the cropped area

    # Resize the final image to 50x50
    final_image = cropped_atlas_section.resize((200, 200), Image.LANCZOS)

    # Save or use in Discord
    final_image.save("final_trait_overlay.png")

    # Send the final image as an embed
    file = discord.File("final_trait_overlay.png", filename="trait.png")
    embed = discord.Embed(title="Trait Icon Overlay")
    embed.set_image(url="attachment://trait.png")
    await ctx.send(embed=embed, file=file)

# Run the bot with your token
bot.run(bot_token)