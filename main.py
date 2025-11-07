import re
import discord
import requests
import os
import pytz
import datetime
import asyncio
import helpers
import asyncpg
from discord.ext import commands
from dotenv import load_dotenv
from pymongo.mongo_client import MongoClient
from config import config

load_dotenv()

uri = os.getenv("MONGO_URI")

# Create a new client and connect to the server
client = MongoClient(uri)
db = client["Users"]
collection = db["users"]

async def create_pool():
    params = config()
    pool = await asyncpg.create_pool(**params, min_size=2, max_size=10)
    print("Connection pool created.")
    return pool

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
summs_json_url = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/summoner-spells.json"

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

response = requests.get(summs_json_url)
if response.status_code == 200:
    summs_mapping = response.json()  # Assuming data is a dictionary
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
bot.summs_mapping = summs_mapping

# Take a snapshot of games and LP for !today command
async def scheduler(pool, interval_minutes=5):
    """Runs the scheduled task every N minutes in the background."""
    await bot.wait_until_ready()
    eastern = pytz.timezone("America/New_York")

    print(f"Background scheduler started. Updating every {interval_minutes} minutes.")
    
    while not bot.is_closed():
        start_time = datetime.datetime.now(eastern).strftime("%Y-%m-%d %H:%M:%S %Z")
        try:
            async with pool.acquire() as conn:
                await helpers.update_db_games(conn, tft_token, lol_token)
            print(f"Updated games at {start_time}")
        except Exception as e:
            print(f"Scheduler failed at {start_time}: {e}")

        await asyncio.sleep(interval_minutes * 60)

# Show bot is online and invoke scheduled snapshot functionality
@bot.event
async def on_ready():
    pool = await create_pool()
    bot.loop.create_task(scheduler(pool, interval_minutes=5))
    try:
        await bot.load_extension('commands')
        await bot.tree.sync()
        print(f"Synced slash commands for {bot.user}")
    except Exception as e:
        print(f"Failed to load extension: {e}")

    print(f'🤖 Bot is online as {bot.user}')

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("Sorry, that command does not exist. Please check the available commands using `!commands`.")

@bot.event
async def on_command(ctx):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"Command used: {ctx.command} at {timestamp}")

bot.run(bot_token)