import re
import discord
import requests
import os
import pytz
import datetime
import asyncio
import helpers
from discord.ext import commands
from dotenv import load_dotenv
from pymongo.mongo_client import MongoClient

load_dotenv()

uri = os.getenv("MONGO_URI")

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

# Get the token
bot_token = os.getenv("DISCORD_BOT_TOKEN")
tft_token = os.getenv("TFT_API_TOKEN")
lol_token = os.getenv("LOL_API_TOKEN")

# Setting json urls
json_url = "https://raw.communitydragon.org/latest/cdragon/tft/en_us.json"
item_json_url = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/tftitems.json"
lol_item_json_url = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/items.json"
companion_json_url = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/companions.json"
keystone_json_url = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perks.json"
runes_json_url = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perkstyles.json"
summoner_spell_json_url = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/summoner-spells.json"

# Setting champion and trait mapping
response = requests.get(json_url)
if response.status_code == 200:
    data = response.json()  # Assuming data is a dictionary
    
    # Access the 'sets' property
    sets_data = data.get("sets", {})  # Get the 'sets' dictionary, default to empty if not found
    
    # Change this property at start of each set
    current_set = sets_data.get("15", {})
    
    # Access the 'champions' and 'traits' lists within current set
    champ_mapping = current_set.get("champions", [])  # Get the 'champions' list, default to empty list if not found
    trait_icon_mapping = current_set.get("traits", []) # Get the 'traits' list, default to empty list if not found
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

# Setting item mapping based on item json
response = requests.get(lol_item_json_url)
if response.status_code == 200:
    lol_item_mapping = response.json()  # Assuming data is a dictionary
    print("Lol items parsed successfully")
else:
    print("Failed to fetch data")


response = requests.get(companion_json_url)
if response.status_code == 200:
    companion_mapping = response.json()  # Assuming data is a dictionary
    print("Companions parsed successfully")
else:
    print("Failed to fetch data")
response = requests.get(keystone_json_url)
if response.status_code == 200:
    keystone_mapping = response.json()  # Assuming data is a dictionary
    print("Keystones parsed successfully")
else:
    print("Failed to fetch data")

response = requests.get(runes_json_url)
if response.status_code == 200:
    runes_data = response.json() 
    runes_mapping = runes_data.get("styles", [])
    print("Runes parsed successfully")
else:
    print("Failed to fetch data")

response = requests.get(summoner_spell_json_url)
if response.status_code == 200:
    summoner_spell_mapping = response.json()  # Assuming data is a dictionary
    print("Summoner spells parsed successfully")
else:
    print("Failed to fetch data")

# Enable all necessary intents
intents = discord.Intents.default()
intents.message_content = True  # Enable message content intent
intents.members = True          # Enable server members intent
intents.presences = True        # Enable presence intent

def bot_prefix(bot, message): 
    # Only match if "!" is followed by a letter
    if re.match(r"^![a-zA-Z]", message.content):
        return "!"
    return commands.when_mentioned(bot, message)

# Create bot instance
bot = commands.Bot(command_prefix=bot_prefix, intents=intents, case_insensitive=True)
# Define regions
mass_region = "americas"
region = "na1"        

bot.collection = collection
bot.tft_token = tft_token
bot.lol_token = lol_token
bot.region = region
bot.mass_region = mass_region
bot.champ_mapping = champ_mapping
bot.item_mapping = item_mapping
bot.trait_icon_mapping = trait_icon_mapping
bot.companion_mapping = companion_mapping
bot.lol_item_mapping = lol_item_mapping
bot.keystone_mapping = keystone_mapping
bot.runes_mapping = runes_mapping
bot.summoner_spell_mapping = summoner_spell_mapping

# Take a snapshot of games and LP for !today command
async def scheduler():
    """Runs the scheduled task at 1 AM EST every day."""
    await bot.wait_until_ready()  # Ensure bot is fully loaded before running the task
    eastern = pytz.timezone("America/New_York")

    while not bot.is_closed():
        now = datetime.datetime.now(eastern)
        target_time = now.replace(hour=1, minute=0, second=0, microsecond=0)

        if now > target_time:
            target_time += datetime.timedelta(days=1)  # Schedule for next day if already past 1 AM

        wait_time = (target_time - now).total_seconds()

        await asyncio.sleep(wait_time)  # Wait until 1 AM EST
        await helpers.daily_store_stats(collection, tft_token)  # Run the task
        print("Updated elo and games using scheduler. Time: " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

# Show bot is online and invoke scheduled snapshot functionality
@bot.event
async def on_ready():
    bot.loop.create_task(scheduler())  # Start the scheduler in the background
    try:
        # Load the commands cog after the bot is ready
        await bot.load_extension('commands')  # Make sure this is awaited
        await bot.tree.sync()
        print(f"Synced slash commands for {bot.user}")
    except Exception as e:
        print(f"Failed to load extension: {e}")
    print(f'Bot is online as {bot.user}')

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("Sorry, that command does not exist. Please check the available commands using `!commands`.")

@bot.event
async def on_command(ctx):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"Command used: {ctx.command} at {timestamp}")

# Only uncomment to manually run snapshot function
# asyncio.run(helpers.daily_store_stats(collection, tft_token))

# Only uncomment to manually run function to get all set placements (WORK IN PROGRESS, NOT FULLY WORKING YET)
# asyncio.run(helpers.get_all_set_placements(collection, region, tft_token))

# Only uncomment to fully reset database, do at start of set
# asyncio.run(helpers.reset_database(collection))

# Run the bot with your token
bot.run(bot_token)