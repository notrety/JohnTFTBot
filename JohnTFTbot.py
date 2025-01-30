import discord
import requests
import os
from discord.ext import commands
from riotwatcher import TftWatcher
from dotenv import load_dotenv
from pymongo.mongo_client import MongoClient
from PIL import Image
from io import BytesIO

'''
uri = "mongodb+srv://sosafelix:lee2014kms2017@tfteamusers.deozh.mongodb.net/?retryWrites=true&w=majority&appName=TFTeamUsers"

# Create a new client and connect to the server
client = MongoClient(uri)

# Send a ping to confirm a successful connection
try:
    client.admin.command('ping')
    print("Pinged your deployment. You successfully connected to MongoDB!")
except Exception as e:
    print(e)
'''
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
        return f"Could not find PUUID for {gameName}#{tagLine}."

    try:
        # Fetch the latest match ID
        match_list = tft_watcher.match.by_puuid(region, puuid, count=1)
        if not match_list:
            return f"No matches found for {gameName}#{tagLine}."

        match_id = match_list[0]  # Get the latest match ID

        # Fetch match details
        match_info = tft_watcher.match.by_id(region, match_id)

        players_data = []
        embed = discord.Embed(title=f"Last Match for {gameName}#{tagLine}", color=0x00ff00)

        # Find player stats
        for participant in match_info['info']['participants']:
            player_puuid = participant['puuid']
            placement = participant['placement']
            player_game_name = participant['riotIdGameName']
            embed.add_field(name=f"{player_game_name} - Traits", value="Active Traits:", inline=False)

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

        # Format the message
        result = ""
        for placement, name in players_data:
            if placement != 8:
                result += f"🏆 **{placement}** - {name}\n"
            else:
                result += f"<:rety:1229135551714824354> **{placement}** - {name}\n"

        return result
    
    except Exception as err:
        return f"Error fetching last match for {gameName}#{tagLine}: {err}"

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
async def stats(ctx, gameName: str, tagLine: str):
    """Fetch and display TFT rank stats for a player."""
    rank_embed, error_message = get_rank_embed(gameName, tagLine)  # Unpack tuple

    if error_message:
        await ctx.send(error_message)  # Send error as text
    else:
        await ctx.send(embed=rank_embed)  # Send embed

# Command to fetch last match data
@bot.command(name="rs", aliases=["r"])
async def rs(ctx, gameName: str, tagLine: str):
    result = last_match(gameName, tagLine)
    result_embed = discord.Embed(
                    title=f"Last TFT Match Placements:",
                    description=result,
                    color=discord.Color.blue()
                )
    await ctx.send(embed=result_embed)

# Command to check all available commands, UPDATE THIS AS NEW COMMANDS ARE ADDED
@bot.command()
async def commands(ctx): 
    commands_embed = discord.Embed(
        title=f"Commands List",
        description=f"""
        **!rs / !r** - Fetch most recent match data
        **!stats** - Check ranked stats for a player
        **!ping** - Test that bot is active
        **!commands** - Get a list of all commands
        """,
        color=discord.Color.blue()
        )
    await ctx.send(embed=commands_embed)

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