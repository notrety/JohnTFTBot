import discord
import pandas as pd
import requests
import os
from discord.ext import commands
from riotwatcher import TftWatcher
from dotenv import load_dotenv

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
        result = "**Last TFT Match Placements:**\n"
        for placement, name in players_data:
            result += f"🏆 **{placement}** - {name}\n"

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
    embed, error_message = get_rank_embed(gameName, tagLine)  # Unpack tuple

    if error_message:
        await ctx.send(error_message)  # Send error as text
    else:
        await ctx.send(embed=embed)  # Send embed

# Command to fetch last match data
@bot.command()
async def rs(ctx, gameName: str, tagLine: str):
    result = last_match(gameName, tagLine)
    await ctx.send(result)
    
# Run the bot with your token
bot.run(bot_token)