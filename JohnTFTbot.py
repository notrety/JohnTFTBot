import discord
import os
from dotenv import load_dotenv

# Load the .env file
load_dotenv()

# Get the token
bot_token = os.getenv("DISCORD_BOT_TOKEN")

# Enable all necessary intents
intents = discord.Intents.default()
intents.message_content = True  # Enable message content intent
intents.members = True          # Enable server members intent
intents.presences = True        # Enable presence intent

# Create the client with the specified intents
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f'Bot is online as {client.user}')

@client.event
async def on_message(message):
    if message.author == client.user:  # Ignore messages from the bot itself
        return

    if message.content == '!ping':
        await message.reply('Lima Oscar Lima!')

# Run the bot with your token
client.run(bot_token)