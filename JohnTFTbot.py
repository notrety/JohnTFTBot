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
def get_rank(gameName, tagLine):
    puuid = get_puuid(gameName, tagLine)
    if not puuid:
        return f"Could not find PUUID for {gameName}#{tagLine}."

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
                return f"**{gameName}#{tagLine}** - {tier} {rank} ({lp} LP) - **Top 4 Rate:** {top_four_rate}%"
        
        return f"{gameName}#{tagLine} has no ranked TFT games."
    except Exception as err:
        return f"Error fetching rank info for {gameName}#{tagLine}: {err}"

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

        # Find player stats
        for participant in match_info['info']['participants']:
            if participant['puuid'] == puuid:
                placement = participant['placement']
                level = participant['level']
                total_damage_to_players = participant['total_damage_to_players']
                return (
                    f"**{gameName}#{tagLine}** - Last Match\n"
                    f"🏆 **Placement:** {placement}\n"
                    f"⭐ **Level:** {level}\n"
                    f"🔥 **Damage Dealt:** {total_damage_to_players}"
                )

        return f"Could not find match data for {gameName}#{tagLine}."
    
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
    result = get_rank(gameName, tagLine)
    await ctx.send(result)

@bot.command()
async def rs(ctx, gameName: str, tagLine: str):
    result = last_match(gameName, tagLine)
    await ctx.send(result)
    
# Run the bot with your token
bot.run(bot_token)