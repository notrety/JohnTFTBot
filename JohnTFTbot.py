import discord
import pandas as pd
import requests
import os
from discord.ext import commands
from riotwatcher import TftWatcher
from dotenv import load_dotenv
from pymongo.mongo_client import MongoClient

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

        # Find player stats
        for participant in match_info['info']['participants']:
            player_puuid = participant['puuid']
            placement = participant['placement']

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

        return result
    
    except Exception as err:
        return f"Error fetching last match for {gameName}#{tagLine}: {err}"

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
async def stats(ctx, gameName: str = None, tagLine: str = None):
    """Fetch and display TFT rank stats for a player."""
    data = False
    if gameName is None and tagLine is None:
        # No args present, check if account is linked
        data, gameName, tagLine = check_data(ctx.author.id)
    else: 
        # Name and tag were typed as args, no need to check for link
        args = True

    if data or args:
        rank_embed, error_message = get_rank_embed(gameName, tagLine)  # Unpack tuple

        if error_message:
            await ctx.send(error_message)  # Send error as text
        else:
            await ctx.send(embed=rank_embed)  # Send embed
    else:
        await ctx.send("You have not linked any data or provided a player. Use `!link <name> <tag>` to link your account.")


# Command to fetch last match data
@bot.command(name="r", aliases=["rs"])
async def r(ctx, gameName: str = None, tagLine: str = None):
    data = False
    if gameName is None and tagLine is None:
        # No args present, check if account is linked
        data, gameName, tagLine = check_data(ctx.author.id)
    else: 
        # Name and tag were typed as args, no need to check for link
        args = True

    if data or args:
        result = last_match(gameName, tagLine)
        result_embed = discord.Embed(
                        title=f"Last TFT Match Placements:",
                        description=result,
                        color=discord.Color.blue()
                    )
        await ctx.send(embed=result_embed)
    else:
        await ctx.send("You have not linked any data or provided a player. Use `!link <name> <tag>` to link your account.")

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
    
# Run the bot with your token
bot.run(bot_token)