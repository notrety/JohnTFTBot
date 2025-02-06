import re
import discord
import requests
import helpers
import dicts
import asyncio
import random
from discord.ui import View
from discord.ext import commands
from PIL import Image
from io import BytesIO

# Classify file commands as a cog that can be loaded in main
class BotCommands(commands.Cog):
    def __init__(self, bot, tft_watcher, collection, region, mass_region, champ_mapping, item_mapping, riot_token, trait_icon_mapping):
        self.bot = bot
        self.tft_watcher = tft_watcher
        self.collection = collection
        self.region = region
        self.mass_region = mass_region
        self.champ_mapping = champ_mapping
        self.item_mapping = item_mapping
        self.riot_token = riot_token
        self.trait_icon_mapping = trait_icon_mapping

    # Basic test command
    @commands.command()
    async def ping(self, ctx):
        await ctx.send('Lima Oscar Lima!')

    # Command to fetch TFT stats
    @commands.command(name="stats", aliases=["stast", "s", "tft"])
    async def stats(self, ctx, *args):
        data = False
        if len(args) == 2:  # Expecting name and tagline
            gameName = args[0]
            tagLine = args[1]
            data = True
        elif len(args) == 1 and args[0].startswith("<@"):  # Check if it's a mention
            mentioned_user = args[0]
            user_id = mentioned_user.strip("<@!>")  # Remove the ping format to get the user ID
            # Check if user is linked
            data, gameName, tagLine = helpers.check_data(user_id, self.collection)
            if not data:
                await ctx.send(f"{mentioned_user} has not linked their name and tagline.")
        elif len(args) == 0: # Check for linked account by sender
            data, gameName, tagLine = helpers.check_data(ctx.author.id, self.collection)
            if not data:
                await ctx.send("You have not linked any data or provided a player. Use `!link <name> <tag>` to link your account.")
        else: 
            # User formatted command incorrectly, let them know
            await ctx.send("Please use this command by typing in a name and tagline, by pinging someone, or with no extra text if your account is linked.")

        if data:
            rank_embed, error_message = helpers.get_rank_embed(gameName, tagLine, self.mass_region, self.riot_token, self.tft_watcher, self.region)  # Unpack tuple

            if error_message:
                await ctx.send(error_message)  # Send error as text
            else:
                await ctx.send(embed=rank_embed)  # Send embed

    # Command to fetch last match data
    @commands.command(name="r", aliases=["rs","recent","rr","rn","rh","rd","rg"])
    async def r(self, ctx, *args):
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

        match_index = 1
        data = False
        if len(args) == 3:  # Expecting match num, name and tagline
            match_index = int(args[0])
            gameName = args[1]
            tagLine = args[2]
            data = True
        elif len(args) == 2 and args[1].startswith("<@"):  # Check if it's a mention
            match_index = int(args[0])
            mentioned_user = args[1]
            user_id = mentioned_user.strip("<@!>")  # Remove the ping format to get the user ID
            # Check if user is linked
            data, gameName, tagLine = helpers.check_data(user_id, self.collection)
            if not data:
                await ctx.send(f"{mentioned_user} has not linked their name and tagline.")
        elif len(args) == 2:
            gameName = args[0]
            tagLine = args[1]
            data = True
        elif len(args) == 1 and args[0].startswith("<@"):  # Check if it's a mention
            mentioned_user = args[0]
            user_id = mentioned_user.strip("<@!>")  # Remove the ping format to get the user ID
            # Check if user is linked
            data, gameName, tagLine = helpers.check_data(user_id, self.collection)
            if not data:
                await ctx.send(f"{mentioned_user} has not linked their name and tagline.")
        elif len(args) == 1 and str.isnumeric(args[0]):
            match_index = int(args[0])
            data, gameName, tagLine = helpers.check_data(ctx.author.id, self.collection)
            if not data:
                await ctx.send("You have not linked any data or provided a player. Use `!link <name> <tag>` to link your account.")
        elif len(args) == 0: # Check for linked account by sender
            data, gameName, tagLine = helpers.check_data(ctx.author.id, self.collection)
            if not data:
                await ctx.send("You have not linked any data or provided a player. Use `!link <name> <tag>` to link your account.")
        else: 
            # User formatted command incorrectly, let them know
            await ctx.send("""Please use this command by typing in a name and tagline, by pinging someone, or with no extra text if your account is linked.\n
You can also add a number as the first argument to specify which match you are looking for.""")

        if not data:
            return

        # Fetch match data asynchronously
        result, match_id, avg_rank, master_plus_lp, time = await asyncio.to_thread(
            helpers.last_match, gameName, tagLine, game_type, self.mass_region, self.riot_token, self.tft_watcher, self.region, match_index
        )

        embed = discord.Embed(
            title=f"Recent {game_type} TFT Match Placements:",
            description=result,
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Average Lobby Rank: {avg_rank} {master_plus_lp} LP\nTimestamp: {time}" if master_plus_lp else f"Average Lobby Rank: {avg_rank}\nTimestamp: {time}")
        await ctx.send(embed=embed)

        match_info = await asyncio.to_thread(
            self.tft_watcher.match.by_id, self.region, match_id
        )
        participants = match_info['info']['participants']

        puuid = await asyncio.to_thread(
            helpers.get_puuid, gameName, tagLine, self.mass_region, self.riot_token
        )

        # Sort in ascending order
        if puuid not in [p['puuid'] for p in participants]:
            await ctx.send(f"Could not find participant with PUUID: {puuid}")
            return

        participants_sorted = sorted(participants, key=lambda x: x['placement'])
        puuid_list = [p['puuid'] for p in participants_sorted]
        current_index = puuid_list.index(puuid)
        
        #Fetches an image from a URL and resizes it if a size is provided.
        async def fetch_image(url: str, size: tuple = None):
            
            response = await asyncio.to_thread(requests.get, url)
            image = Image.open(BytesIO(response.content)).convert("RGBA")

            if size:
                image = image.resize(size, Image.LANCZOS)

            return image

        async def generate_board(index):
            participant = participants_sorted[index]

            # --- Traits Processing ---
            traits = participant['traits']
            filtered_traits = [trait for trait in traits if trait['style'] >= 1]
            sorted_traits = sorted(filtered_traits, key=lambda x: dicts.style_order.get(x['style'], 5))
            num_traits = len(sorted_traits)

            trait_img_width = 89 * num_traits
            trait_img_height = 103
            trait_final_image = Image.new("RGBA", (trait_img_width, trait_img_height), (0, 0, 0, 0))

            async def process_trait(trait, i):
                temp_image = await asyncio.to_thread(helpers.trait_image, trait['name'], trait['style'], self.trait_icon_mapping)
                if temp_image:
                    temp_image = temp_image.convert("RGBA")
                    mask = temp_image.split()[3]
                    trait_final_image.paste(temp_image, (89 * i, 0), mask)

            # Fetch trait images concurrently
            await asyncio.gather(*[process_trait(trait, i) for i, trait in enumerate(sorted_traits)])

            # --- Champions Processing ---
            units = participant.get('units', [])
            champ_unit_data_unsorted = []

            async def process_unit(unit):
                champion_name = unit["character_id"]
                tier = unit["tier"]
                rarity = unit["rarity"]
                item_names = unit["itemNames"]

                custom_rarity = dicts.rarity_map.get(rarity, rarity)
                champ_icon_path = helpers.get_champ_icon(self.champ_mapping, champion_name).lower()
                rarity_url = f"https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-tft-team-planner/global/default/images/cteamplanner_championbutton_tier{custom_rarity}.png"

                if champ_icon_path:
                    champion_url = f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/{champ_icon_path}.png"
                    champ_task = fetch_image(champion_url, (64, 64))
                    rarity_task = fetch_image(rarity_url, (72, 72))
                    
                    icon_resized, rarity_resized = await asyncio.gather(champ_task, rarity_task)

                    champ_unit_data_unsorted.append({
                        "champion_name": champion_name,
                        "icon_resized": icon_resized,
                        "rarity_resized": rarity_resized,
                        "rarity": rarity,
                        "tier": tier,
                        "item_names": item_names,
                        "width": 72  # Store individual width
                    })

            # Fetch all champion icons & rarity images concurrently
            await asyncio.gather(*[process_unit(unit) for unit in units])
            champ_unit_data = sorted(champ_unit_data_unsorted, key=lambda x: x['rarity'])

            # Calculate total champion image width
            champ_img_width = sum(unit['width'] for unit in champ_unit_data)
            champ_img_height = 140
            champ_final_image = Image.new("RGBA", (champ_img_width, champ_img_height), (0, 0, 0, 0))

            # --- Paste Champions & Items ---
            async def paste_champion(unit, current_x):
                champ_final_image.paste(unit["rarity_resized"], (current_x, 25), unit["rarity_resized"])
                champ_final_image.paste(unit["icon_resized"], (current_x + 4, 29), unit["icon_resized"])

                if unit["tier"] in {2, 3}:
                    tier_icon_path = f"https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-tft/global/default/tft-piece-star-{unit['tier']}.png"
                    tier_resized = await fetch_image(tier_icon_path, (72, 36))
                    champ_final_image.paste(tier_resized, (current_x, 0), tier_resized)

                item_urls = [
                    f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/{helpers.get_item_icon(self.item_mapping, item).lower()}"
                    for item in unit["item_names"]
                ]

                fetch_tasks = [fetch_image(url, (23, 23)) for url in item_urls]
                item_icons = await asyncio.gather(*fetch_tasks)

                for i, item_icon in enumerate(item_icons):
                    champ_final_image.paste(item_icon, (current_x + 23 * i, 97), item_icon)

            # Process and paste champions concurrently
            await asyncio.gather(*[paste_champion(unit, x) for unit, x in zip(champ_unit_data, range(0, champ_img_width, 72))])

            # --- Combine Images ---
            final_img_height = trait_img_height + champ_img_height
            final_combined_image = Image.new("RGBA", (max(trait_img_width, champ_img_width), final_img_height), (0, 0, 0, 0))
            final_combined_image.paste(trait_final_image, (0, 0), trait_final_image)
            final_combined_image.paste(champ_final_image, (0, trait_img_height), champ_final_image)

             # Get summoner's gameName and tagLine from the match_info
            gameName = participant.get('riotIdGameName', 'Unknown')
            tagLine = participant.get('riotIdTagline', 'Unknown')

            # Save & Return Image & Embed
            final_combined_image.save("player_board.png")
            file = discord.File("player_board.png", filename="player_board.png")
            embed = discord.Embed(title=f"{gameName}#{tagLine} - Placement {index + 1}/{len(puuid_list)}", description="")
            embed.set_image(url="attachment://player_board.png")

            return embed, file

            # --- Button View ---
        class PlayerSwitchView(View):
            def __init__(self, index, author_id):
                super().__init__()
                self.index = index
                self.author_id = author_id

            @discord.ui.button(label="Next Player", style=discord.ButtonStyle.primary)
            async def next_player(self, interaction: discord.Interaction, button: discord.ui.Button):  
                if interaction.user.id != self.author_id:
                    await interaction.response.send_message("You cannot use this button!", ephemeral=True)
                    return
                await interaction.response.defer()  # Avoid timeout

                new_index = (self.index + 1) % 8  # Loop through players
                new_embed, new_file = await generate_board(new_index)

                # Update the message with new data
                await interaction.followup.edit_message(interaction.message.id, embed=new_embed, view=PlayerSwitchView(new_index, self.author_id))
                await interaction.message.edit(attachments=[new_file])  # Reattach the file separately

        # --- Send Initial Message ---
        embed, file = await generate_board(current_index)
        await ctx.send(embed=embed, file=file, view=PlayerSwitchView(current_index, ctx.author.id))

    # Command to link riot and discord accounts, stored in mongodb database
    @commands.command()
    async def link(self, ctx, name: str, tag: str):
        user_id = str(ctx.author.id)
        
        # Check if the user already has linked data
        existing_user = self.collection.find_one({"discord_id": user_id})
        
        if existing_user:
            # If user already has data, update it
            self.collection.update_one(
                {"discord_id": user_id},
                {"$set": {"name": name, "tag": tag}}
            )
            await ctx.send(f"Your data has been updated to: {name} {tag}")
        else:
            # If no data exists, insert a new document for the user
            self.collection.insert_one({
                "discord_id": user_id,
                "name": name,
                "tag": tag
            })
            await ctx.send(f"Your data has been linked: {name} {tag}")

    # Command to check leaderboard of all linked accounts for ranked tft
    @commands.command()
    async def lb(self, ctx):
        _, gameName, tagLine = helpers.check_data(ctx.author.id, self.collection)
        result = ""
        all_users = self.collection.find()
        
        # Create a list to store all users' elo and name
        user_elo_and_name = []
        
        for user in all_users:
            name = user['name']
            tag = user['tag']
            puuid = helpers.get_puuid(name, tag, self.mass_region, self.riot_token)
            
            if not puuid:
                await ctx.send(f"Error retrieving PUUID for user {name}#{tag}")
                continue  # Skip to the next user if there's an issue retrieving the PUUID
            
            user_elo = helpers.calculate_elo(puuid, self.tft_watcher, self.region)
            name_and_tag = name + "#" + tag
            
            # Append each user's data (elo, name_and_tag) to the list
            user_elo_and_name.append((user_elo, name_and_tag))
        
        # Sort users by their elo score (assuming user_elo is a numeric value)
        user_elo_and_name.sort(reverse=True, key=lambda x: x[0])  # Sort in descending order
        
        # Prepare the leaderboard result
        for index, (user_elo, name_and_tag) in enumerate(user_elo_and_name):
            name, tag = name_and_tag.split("#")
            summoner = self.tft_watcher.summoner.by_puuid(self.region, helpers.get_puuid(name, tag, self.mass_region, self.riot_token))
            rank_info = self.tft_watcher.league.by_summoner(self.region, summoner['id'])
            
            for entry in rank_info:
                if entry['queueType'] == 'RANKED_TFT':
                    tier = entry['tier']
                    division = entry['rank']
                    lp = entry['leaguePoints']
                    icon = dicts.tier_to_rank_icon[tier]
            
            if name == gameName and tag == tagLine:
                result += f"**{index + 1}** - **__{name_and_tag}__: {icon} {tier} {division} • {lp} LP**\n"
            else:
                result += f"**{index + 1}** - {name_and_tag}: {icon} {tier} {division} • {lp} LP\n"
        
        lb_embed = discord.Embed(
            title=f"Overall Bot Ranked Leaderboard",
            description=result,
            color=discord.Color.blue()
        )
        await ctx.send(embed=lb_embed)

    # Commnad to check the lp cutoff for challenger and grandmaster
    @commands.command(name="cutoff", aliases=["cutoffs, challenger, grandmaster, grandmasters, lpcutoff"])
    async def cutoff(self, ctx):
        challenger_cutoff, grandmaster_cutoff = helpers.get_cutoff(self.tft_watcher, self.region)
        cutoff_embed = discord.Embed(
            title=f"Cutoff LPs",
            description=f"<:RankChallenger:1336405530444431492> Challenger Cutoff: {challenger_cutoff}\n<:RankGrandmaster:1336405512887078923> Grandmaster Cutoff: {grandmaster_cutoff}",
            color=discord.Color.blue()
        )
        await ctx.send(embed=cutoff_embed)

    # Roll command 
    @commands.command()
    async def roll(self, ctx, *args):
        user = ctx.author.id
        roll_result = random.randint(1,100)
        if(args):
            if(args[0].isdigit()):
                max = int(args[0])
                if(max > 0):
                    roll_result = random.randint(1,max)

        roll_embed = discord.Embed(
            description=f"<@{user}> rolled a {roll_result}",
            color=discord.Color.blue()
        )
        await ctx.send(embed=roll_embed)

    # Command to check all available commands, UPDATE THIS AS NEW COMMANDS ARE ADDED
    @commands.command()
    async def commands(self, ctx): 
        commands_embed = discord.Embed(
        title=f"Commands List",
        description=f"""
    **!r** - View most recent match\n
    **!stats** - Check ranked stats for a player\n
    **!lb** - View overall bot leaderboard\n
    **!ping** - Test that bot is active\n
    **!commands** - Get a list of all commands\n
    **!link** - Link discord account to riot account\n
    **!cutoff** - Show the LP cutoffs for Challenger and GM\n
    **!roll** - Rolls a random number (default 1-100)
        """,
        color=discord.Color.blue()
        )
        await ctx.send(embed=commands_embed)

# Add this class as a cog to main
async def setup(bot):
    await bot.add_cog(BotCommands(bot, bot.tft_watcher, bot.collection, bot.region, 
                                  bot.mass_region, bot.champ_mapping, bot.item_mapping, bot.riot_token, bot.trait_icon_mapping))