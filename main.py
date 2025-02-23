import discord
import requests
import os
import pytz
import datetime
import asyncio
import helpers
from discord.ext import commands
from riotwatcher import RiotWatcher, TftWatcher
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

# Setting json urls
json_url = "https://raw.communitydragon.org/latest/cdragon/tft/en_us.json"
item_json_url = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/tftitems.json"
companion_json_url = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/companions.json"

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

response = requests.get(companion_json_url)
if response.status_code == 200:
    companion_mapping = response.json()  # Assuming data is a dictionary
    print("Companions parsed successfully")
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

# Initialize Riot API Wrapper
tft_watcher = TftWatcher(riot_token)
riot_watcher = RiotWatcher(riot_token)

bot.tft_watcher = tft_watcher
bot.riot_watcher = riot_watcher
bot.collection = collection
bot.region = region
bot.mass_region = mass_region
bot.champ_mapping = champ_mapping
bot.item_mapping = item_mapping
bot.riot_token = riot_token
bot.trait_icon_mapping = trait_icon_mapping
bot.companion_mapping = companion_mapping

# Take a snapshot of games and LP for !today command
async def scheduler():
    """Runs the scheduled task at 5 AM EST every day."""
    await bot.wait_until_ready()  # Ensure bot is fully loaded before running the task
    eastern = pytz.timezone("America/New_York")

    while not bot.is_closed():
        now = datetime.datetime.now(eastern)
        target_time = now.replace(hour=5, minute=0, second=0, microsecond=0)

        if now > target_time:
            target_time += datetime.timedelta(days=1)  # Schedule for next day if already past 6 AM

        wait_time = (target_time - now).total_seconds()

        await asyncio.sleep(wait_time)  # Wait until 5 AM EST
        await helpers.store_elo_and_games(collection, mass_region, riot_watcher, tft_watcher, region)  # Run the task

# Show bot is online and invoke scheduled snapshot functionality
@bot.event
async def on_ready():
    print(f'Bot is online as {bot.user}')
    bot.loop.create_task(scheduler())  # Start the scheduler in the background

    try:
        # Load the commands cog after the bot is ready
        await bot.load_extension('commands')  # Make sure this is awaited
        await bot.tree.sync()
        print(f"Synced slash commands for {bot.user}")
    except Exception as e:
        print(f"Failed to load extension: {e}")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("Sorry, that command does not exist. Please check the available commands using `!commands`.")

@bot.event
async def on_command(ctx):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"Command used: {ctx.command} at {timestamp}")

# Run the bot with your token
bot.run(bot_token)