import discord
import requests
import helpers
import dicts
import asyncio
import random
import time
import os
import matplotlib.pyplot as plt
from collections import Counter
from discord.ui import View
from discord.ext import commands
from PIL import Image
from io import BytesIO
from pulsefire.clients import RiotAPIClient
from pulsefire.taskgroups import TaskGroup

# Classify file commands as a cog that can be loaded in main
class BotCommands(commands.Cog):
    def __init__(self, bot, riot_token, collection, region, mass_region, champ_mapping, item_mapping, trait_icon_mapping, companion_mapping):
        self.bot = bot
        self.riot_token = riot_token
        self.collection = collection
        self.region = region
        self.mass_region = mass_region
        self.champ_mapping = champ_mapping
        self.item_mapping = item_mapping
        self.trait_icon_mapping = trait_icon_mapping
        self.companion_mapping = companion_mapping

    # Basic test command
    @commands.command()
    async def ping(self, ctx):
        await ctx.send('Lima Oscar Lima!')

    # Command to fetch TFT stats
    @commands.command(name="stats", aliases=["stast", "s", "tft"])
    async def stats(self, ctx, *args):
        data = False
        if len(args) == 2:  # Expecting name and tagline
            gameName = args[0].replace("_", " ")
            tagLine = args[1]
            data, gameName, tagLine, region, _, puuid, discord_id = helpers.check_data_name_tag(gameName, tagLine, self.collection)
            if not data: # assume na player
                region = "na1"
                puuid = await helpers.get_puuid(gameName, tagLine, "americas", self.riot_token)
            data = True
        elif len(args) == 1 and args[0].startswith("<@"):  # Check if it's a mention
            mentioned_user = args[0]
            user_id = mentioned_user.strip("<@!>")  # Remove the ping format to get the user ID
            # Check if user is linked
            data, gameName, tagLine, region, _, puuid, discord_id = helpers.check_data(user_id, self.collection)
            if not data:
                await ctx.send(f"{mentioned_user} has not linked their name and tagline.")
        elif len(args) == 0: # Check for linked account by sender
            data, gameName, tagLine, region, _, puuid, discord_id = helpers.check_data(ctx.author.id, self.collection)
            if not data:
                await ctx.send("You have not linked any data or provided a player. Use `/link <name> <tag>` to link your account.")
        else: 
            # User formatted command incorrectly, let them know
            await ctx.send("Please use this command by typing in a name and tagline, by pinging someone, or with no extra text if your account is linked.")

        if data:
            rank_embed, error_message = await helpers.get_rank_embed(gameName, tagLine, region, self.riot_token, puuid)

            if error_message:
                await ctx.send(error_message)
            else:
                await ctx.send(embed=rank_embed)


    # Command to fetch last match data
    @commands.command(name="recent", aliases=["rs","r","rr","rn","rh","rd","rg"])
    async def recent(self, ctx, *args):
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
        region = None
        mass_region = None
        puuid = None
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
            data, gameName, tagLine, region, mass_region, puuid, discord_id = helpers.check_data(user_id, self.collection)
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
            data, gameName, tagLine, region, mass_region, puuid, discord_id = helpers.check_data(user_id, self.collection)
            if not data:
                await ctx.send(f"{mentioned_user} has not linked their name and tagline.")
        elif len(args) == 1 and str.isnumeric(args[0]):
            match_index = int(args[0])
            data, gameName, tagLine, region, mass_region, puuid, discord_id = helpers.check_data(ctx.author.id, self.collection)
            if not data:
                await ctx.send("You have not linked any data or provided a player. Use `/link <name> <tag>` to link your account.")
        elif len(args) == 0: # Check for linked account by sender
            data, gameName, tagLine, region, mass_region, puuid, discord_id = helpers.check_data(ctx.author.id, self.collection)
            if not data:
                await ctx.send("You have not linked any data or provided a player. Use `/link <name> <tag>` to link your account.")
        else: 
            # User formatted command incorrectly, let them know
            await ctx.send("""Please use this command by typing in a name and tagline, by pinging someone, or with no extra text if your account is linked.\n
You can also add a number as the first argument to specify which match you are looking for.""")
        if not data:
            return

        # Use NA1 and americas as default routing values if not found for user
        if not mass_region:
            mass_region = self.mass_region
        if not region:
            region = self.region
        if not puuid:
            puuid = await helpers.get_puuid(gameName, tagLine, mass_region, self.riot_token)
        # Fetch match data asynchronously
        result, match_id, avg_rank, master_plus_lp, time = await helpers.last_match(gameName, tagLine, game_type, mass_region, self.riot_token, region, match_index)

        embed = discord.Embed(
            title=f"Recent {game_type} TFT Match Placements:",
            description=result,
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Average Lobby Rank: {avg_rank} {master_plus_lp} LP\nTimestamp: {time}" if master_plus_lp else f"Average Lobby Rank: {avg_rank}\nTimestamp: {time}")
        await ctx.send(embed=embed)

        async with RiotAPIClient(default_headers={"X-Riot-Token": self.riot_token}) as client:
            match_info = await client.get_tft_match_v1_match(region=mass_region, id=match_id)

        participants = match_info['info']['participants']

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

                item_urls = [f"https://raw.communitydragon.org/latest/game/{helpers.get_item_icon(self.item_mapping, item).lower()}" for item in unit["item_names"]]

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
            level = participant.get('level','Unknown')
            gold_left = participant.get('gold_left','Unknown')
            stage, round = divmod(participant.get('last_round','Unknown') - 4 , 7)
            players_eliminated = participant.get('players_eliminated','Unknown')
            total_damage_to_players = participant.get('total_damage_to_players','Unknown')
            text = f"<:tft_up_arrow:1347339737014341662> Level: {level}\n💰 Gold Remaining: {gold_left}\n:skull: Last round: {stage+2}-{round}\n:crossed_swords: Players Eliminated: {players_eliminated}\n:drop_of_blood: Damage Dealt: {total_damage_to_players}"
            # Save & Return Image & Embed
            final_combined_image.save("player_board.png")
            file = discord.File("player_board.png", filename="player_board.png")
            embed = discord.Embed(title=f"{gameName}#{tagLine} - Placement {index + 1}/{len(puuid_list)}", description=text)
            embed.set_image(url="attachment://player_board.png")

            return embed, file

        Select_options = [
            discord.SelectOption(label=f"{index + 1} - {participant.get('riotIdGameName')}#{participant.get('riotIdTagline')}", value=index)
            for index, participant in enumerate(participants_sorted)
        ]

        # --- Dropdown View ---
        class PlayerSwitchView(View):
            def __init__(self, index, author_id):
                super().__init__()
                self.index = index
                self.author_id = author_id

            @discord.ui.select(placeholder="Select a board to view", options=Select_options, max_values= 1)
            async def next_player(self, interaction: discord.Interaction, select: discord.ui.Select):  
                if interaction.user.id != self.author_id:
                    await interaction.response.send_message("You cannot use this dropdown!", ephemeral=True)
                    return
                await interaction.response.defer()  # Avoid timeout
                new_index = int(interaction.data['values'][0])
                new_embed, new_file = await generate_board(new_index)

                # Update the message with new data
                await interaction.followup.edit_message(interaction.message.id, embed=new_embed, view=PlayerSwitchView(new_index, self.author_id))
                await interaction.message.edit(attachments=[new_file])  # Reattach the file separately

        # --- Send Initial Message ---
        embed, file = await generate_board(current_index)
        await ctx.send(embed=embed, file=file, view=PlayerSwitchView(current_index, ctx.author.id))
        os.remove("player_board.png")

    # Redirect user to /link
    @commands.command()
    async def link(self, ctx):
        await ctx.send('Please use /link to link your account.')

    # Command to link riot and discord accounts, stored in mongodb database
    @discord.app_commands.command(name="link", description="Link discord account to riot account")
    @discord.app_commands.describe(
        region="List of regions: BR1, EUN1, EUW1, JP1, KR1, LA1, LA2, NA1, OC1, TR1, RU, PH2, SG2, TH2, TW2, VN2"
    )
    async def slash_link(self, interaction: discord.Interaction, name: str, tag: str, region: str):
        user_id = str(interaction.user.id)

        # Check if the user already has linked data
        existing_user = self.collection.find_one({"discord_id": user_id})
        mass_region = dicts.region_to_mass[region.lower()]
        puuid = await helpers.get_puuid(name, tag, mass_region, self.riot_token)
        
        if existing_user:
            # If user already has data, update it
            if not puuid:
                await interaction.response.send_message(
                    f"Could not find {name}#{tag} in region {region}. Please re-link using the correct formatting of `/link <name> <tag>`.",
                    ephemeral=True  # Sends the response privately to the user
                )        
                return
            self.collection.update_one(
                {"discord_id": user_id},
                {"$set": {
                    "name": name.lower().replace("_", " "),
                    "tag": tag.lower(),
                    "region": region.lower(),
                    "mass_region": mass_region,
                    "puuid": puuid
                    }
                }
            )
            await interaction.response.send_message(
                f"Your data has been updated to: {name}#{tag} in region {region}. If this looks incorrect, please re-link using the correct formatting of `/link <name> <tag>`.",
                ephemeral=True  # Sends the response privately to the user
            )        
        else:
            # If no data exists, insert a new document for the user
            if not puuid:
                await interaction.response.send_message(
                    f"Could not find {name}#{tag} in region {region}. Please re-link using the correct formatting of `/link <name> <tag>`.",
                    ephemeral=True  # Sends the response privately to the user
                )        
                return
            
            self.collection.insert_one({
                "discord_id": user_id,
                "name": name.lower().replace("_", " "),
                "tag": tag.lower(),
                "region": region.lower(),
                "mass_region": mass_region,
                "puuid": puuid
            })
            await interaction.response.send_message(
                f"Your data has been linked: {name}#{tag} in region {region}. If this looks incorrect, please re-link using the correct formatting of `/link <name> <tag>`.",
                ephemeral=True
                
            )

    # Command to check leaderboard of all linked accounts for ranked tft
    @commands.command(name="lb", aliases=["leaderboard", "server", "serverlb"])
    async def lb(self, ctx):
        _, gameName, tagLine, region, mass_region, puuid, discord_id = helpers.check_data(ctx.author.id, self.collection)
        result = ""
        server = False
        if ctx.guild and ctx.invoked_with in {"server", "serverlb"}:
            guild = ctx.guild
            members = {member.id async for member in guild.fetch_members(limit=None)}
            server = True
        else:
            members = set()

        all_users = self.collection.find()
        
        # Create a list to store all users' elo and name
        user_elo_and_name = []
        async def process_user(user):
            if server:
                if not int(user['discord_id']) in members:
                    return

            name = user['name']
            tag = user['tag']
            region = user['region']
            puuid = user['puuid']
            try:                
                user_elo, user_tier, user_rank, user_lp = await helpers.calculate_elo(puuid, self.riot_token, region)
                name_and_tag = f"{name}#{tag}"
                user_elo_and_name.append((user_elo, user_tier, user_rank, user_lp, name_and_tag, region))
            except Exception as e:
                await ctx.send(f"Error processing {name}#{tag}: {e}")

        async with RiotAPIClient(default_headers={"X-Riot-Token": self.riot_token}) as client:
            async with TaskGroup(asyncio.Semaphore(20)) as tg:
                for user in all_users:
                    await tg.create_task(process_user(user))
        
        # Sort users by their elo score (assuming user_elo is a numeric value)
        user_elo_and_name.sort(reverse=True, key=lambda x: x[0])  # Sort in descending order

        # Prepare the leaderboard result
        for index, (user_elo, user_tier, user_rank, user_lp, name_and_tag, region) in enumerate(user_elo_and_name):
            name, tag = name_and_tag.split("#")
            icon = dicts.tier_to_rank_icon[user_tier]
            if user_tier != "UNRANKED":
                if name == gameName and tag == tagLine:
                    result += f"**{index + 1}** - **[__{name_and_tag}__](https://lolchess.gg/profile/{region[:-1]}/{name.replace(" ", "%20")}-{tag}/set14): {icon} {user_tier} {user_rank} • {user_lp} LP**\n"
                else:
                    result += f"**{index + 1}** - [{name_and_tag}](https://lolchess.gg/profile/{region[:-1]}/{name.replace(" ", "%20")}-{tag}/set14): {icon} {user_tier} {user_rank} • {user_lp} LP\n"
            else:
                if name == gameName and tag == tagLine:
                    result += f"**{index + 1}** - **[__{name_and_tag}__](https://lolchess.gg/profile/{region[:-1]}/{name.replace(" ", "%20")}-{tag}/set14): {icon} {user_tier} {user_rank}**\n"
                else:
                    result += f"**{index + 1}** - [{name_and_tag}](https://lolchess.gg/profile/{region[:-1]}/{name.replace(" ", "%20")}-{tag}/set14): {icon} {user_tier} {user_rank}\n"

        lb_embed = discord.Embed(
            title="Overall Bot Ranked Leaderboard",
            description=result,
            color=discord.Color.blue()
        )

        await ctx.send(embed=lb_embed)

    # Commnad to check the lp cutoff for challenger and grandmaster
    @commands.command(name="cutoff", aliases=["cutoffs", "challenger", "grandmaster", "grandmasters", "lpcutoff", "chall", "gm"])
    async def cutoff(self, ctx):
        challenger_cutoff, grandmaster_cutoff = await helpers.get_cutoff(self.riot_token, self.region)
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

    # History command 
    @commands.command(name="history", aliases=["h","hr","hn","hh","hd","hg"])
    async def history(self, ctx, *args):
        if ctx.invoked_with == "hn":
            game_type = "Normal"
        elif ctx.invoked_with == "hg":
            game_type = "Gamemode"
        elif ctx.invoked_with == "hh":
            game_type = "Hyper Roll"
        elif ctx.invoked_with == "hd":
            game_type = "Double Up"
        else:
            game_type = "Ranked"

        num_matches = 20
        data = False
        if len(args) == 3:  # Expecting matches to display, name and tagline
            num_matches = int(args[0])
            gameName = args[1]
            tagLine = args[2]
            data = True
            region = None
            mass_region = None
            puuid = None
        elif len(args) == 2 and args[1].startswith("<@"):  # Check if it's a mention
            num_matches = int(args[0])
            mentioned_user = args[1]
            user_id = mentioned_user.strip("<@!>")  # Remove the ping format to get the user ID
            # Check if user is linked
            data, gameName, tagLine, region, mass_region, puuid, discord_id = helpers.check_data(user_id, self.collection)
            if not data:
                await ctx.send(f"{mentioned_user} has not linked their name and tagline.")
        elif len(args) == 2:
            gameName = args[0]
            tagLine = args[1]
            data = True
            region = None
            mass_region = None
            puuid = None
        elif len(args) == 1 and args[0].startswith("<@"):  # Check if it's a mention
            mentioned_user = args[0]
            user_id = mentioned_user.strip("<@!>")  # Remove the ping format to get the user ID
            # Check if user is linked
            data, gameName, tagLine, region, mass_region, puuid, discord_id = helpers.check_data(user_id, self.collection)
            if not data:
                await ctx.send(f"{mentioned_user} has not linked their name and tagline.")
        elif len(args) == 1 and str.isnumeric(args[0]):
            num_matches = int(args[0])
            data, gameName, tagLine, region, mass_region, puuid, discord_id = helpers.check_data(ctx.author.id, self.collection)
            if not data:
                await ctx.send("You have not linked any data or provided a player. Use `/link <name> <tag>` to link your account.")
        elif len(args) == 0: # Check for linked account by sender
            data, gameName, tagLine, region, mass_region, puuid, discord_id = helpers.check_data(ctx.author.id, self.collection)
            if not data:
                await ctx.send("You have not linked any data or provided a player. Use `/link <name> <tag>` to link your account.")
        else: 
            # User formatted command incorrectly, let them know
            await ctx.send("""Please use this command by typing in a name and tagline, by pinging someone, or with no extra text if your account is linked.\n
You can also add a number as the first argument to specify how many matches to include.""")

        if data:
            if not mass_region:
                mass_region = self.mass_region
            if not region:
                region = self.region
            if not puuid:
                puuid = await helpers.get_puuid(gameName, tagLine, mass_region, self.riot_token)
                if not puuid:
                    print(f"Could not find PUUID for {gameName}#{tagLine}.")
                    return f"Could not find PUUID for {gameName}#{tagLine}.", None, None
            error_message, placements, real_num_matches = await helpers.recent_matches(gameName, tagLine, puuid, game_type, mass_region, self.riot_token, num_matches)  # Unpack tuple

            if error_message:
                await ctx.send(embed=discord.Embed(description=error_message,color=discord.Color.blue()))  # Send error as embed
            else:
                top4s = 0
                firsts = 0
                total_placement = 0
                text = ""
                for idx, placement in enumerate(placements):
                    if idx == 9:
                        text += dicts.number_to_num_icon[placement] + "\n"
                    else:
                        text += dicts.number_to_num_icon[placement] + " "
                    total_placement += placement
                    if int(placement) <= 4:
                        top4s += 1
                        if int(placement) == 1:
                            firsts += 1
                avg_placement = round(total_placement / len(placements), 1)
                text += f"\n\n:first_place: Firsts: {firsts}\n:dart: Top 4s: {top4s}\n:bar_chart: Average Placement: {avg_placement}"

                x_labels = list(range(1, 9)) # sets ticks 1 to 8 at every 1
                counts = Counter(placements)
                frequencies = [counts.get(num, 0) for num in x_labels]
                max_frequency = max(frequencies)
                num_ticks = max(5, max_frequency)  # Minimum 5 ticks
                y_labels = list(range(1, num_ticks+1)) # Sets ticks = to higher freq or 5 

                # Create the plot
                fig, ax = plt.subplots()
                bar_colors = ['#F0B52B', '#969696', '#A45F00', '#595988', '#596263', '#596263', '#596263', '#596263']

                ax.bar(x_labels, frequencies, color=bar_colors)
                # ax.set_xlabel('Placement', color = "white", fontsize=14)
                ax.set_ylabel('Count', color = "white", fontsize=17)
                # ax.set_title('Placement Frequency', color = "white")
                ax.set_xticks(x_labels)
                ax.set_yticks(y_labels)
                [t.set_color('white') for t in ax.xaxis.get_ticklabels()]
                [t.set_color('white') for t in ax.yaxis.get_ticklabels()]
                ax.tick_params(axis='x', color = 'white', labelsize=16)
                ax.tick_params(axis='y', color = 'white', labelsize=16)

                for spine in ax.spines.values():
                    spine.set_edgecolor('white')

                filename = 'placements.png'
                plt.tight_layout(pad=0.8)
                plt.savefig(filename,transparent=True,dpi=300)
                plt.close()
                img = Image.open(filename)
                img = img.resize((240, 180))  # Width, Height in pixels
                img.save(filename)
                embed = discord.Embed(
                    title=f"Recent {real_num_matches} {game_type} Matches for {gameName}#{tagLine}",
                    description=text,
                    color=discord.Color.blue()
                )
                file = discord.File('placements.png', filename='placements.png')
                embed.set_image(url="attachment://placements.png")
                
                await ctx.send(file=file, embed=embed)

                os.remove("placements.png")

    # Command that summarizes todays games, only works for linked accounts
    @commands.command(name="today", aliases=["t"])
    async def today(self, ctx, *args): 
        # Account must be linked for this command
        data = False
        if len(args) == 2:  # Expecting name and tagline
            gameName = args[0].replace("_", " ")
            tagLine = args[1]
            data, gameName, tagLine, region, mass_region, puuid, discord_id = helpers.check_data_name_tag(gameName, tagLine, self.collection) # Check if name and tag are in database
            if not data:
                await ctx.send(f"{gameName}#{tagLine} has not linked their name and tagline.")
        elif len(args) == 1 and args[0].startswith("<@"):  # Check if it's a mention
            mentioned_user = args[0]
            user_id = mentioned_user.strip("<@!>")  # Remove the ping format to get the user ID
            # Check if user is linked
            data, gameName, tagLine, region, mass_region, puuid, discord_id = helpers.check_data(user_id, self.collection)
            if not data:
                await ctx.send(f"{mentioned_user} has not linked their name and tagline.")
        elif len(args) == 0: # Check for linked account by sender
            data, gameName, tagLine, region, mass_region, puuid, discord_id = helpers.check_data(ctx.author.id, self.collection)
            if not data:
                await ctx.send("You have not linked any data or provided a player. Use `/link <name> <tag>` to link your account.")
        else: 
            # User formatted command incorrectly, let them know
            await ctx.send("Please use this command by typing in a name and tagline, by pinging someone, or with no extra text if your account is linked.")

        if data:
            text = ""
            if not mass_region:
                mass_region = self.mass_region
            if not region:
                region = self.region
            if not puuid:
                puuid = await helpers.get_puuid(gameName, tagLine, mass_region, self.riot_token)

            if not puuid:
                text = f"ERROR: Could not find PUUID for {gameName}#{tagLine}."
    
            rank_info = await helpers.get_rank_info(region, puuid, self.riot_token)
            db_user_data = self.collection.find_one({"name": gameName, "tag": tagLine})
            if not db_user_data:
                text = f"ERROR: Could not find user with name {gameName}#{tagLine}"
            if "games" not in db_user_data:
                text = f"ERROR: If you linked your account in the past day, this command will not work as we need to store the data from the previous day."
            if text == "":
                db_games = db_user_data['games']
                db_elo = int(db_user_data['elo'])
                for entry in rank_info:
                    if entry['queueType'] == 'RANKED_TFT':
                        tier = entry['tier']
                        rank = entry['rank']
                        lp = entry['leaguePoints']
                        elo = dicts.rank_to_elo[tier + " " + rank] + int(lp)
                        total_games = entry['wins'] + entry['losses']
                if total_games == db_games:
                    text = f"No games played today by {gameName}#{tagLine}."
                    embed = discord.Embed(
                        description=text,
                        color=discord.Color.blue()
                    )
                    embed.set_author(
                        name=f"Today: {gameName}#{tagLine}",
                        url=f"https://lolchess.gg/profile/{region[:-1]}/{gameName.replace(" ", "%20")}-{tagLine}/set14",
                        icon_url="https://cdn-b.saashub.com/images/app/service_logos/184/6odf4nod5gmf/large.png?1627090832"
                    )

                    await ctx.send(embed=embed)
                    return
                elo_diff = elo - db_elo
                lp_diff = ""
                lp_diff_emoji = ""
                if elo_diff >= 0:
                    lp_diff = "+" + str(elo_diff)
                    lp_diff_emoji = "📈"
                else:
                    lp_diff = str(elo_diff)
                    lp_diff_emoji = "📉"
                today_games = total_games - db_games
                async with RiotAPIClient(default_headers={"X-Riot-Token": self.riot_token}) as client:
                    match_list = await client.get_tft_match_v1_match_ids_by_puuid(region=mass_region, puuid=puuid)
                    
                    if not match_list:
                        return f"No matches found for {gameName}#{tagLine}."
                    
                    placements = []
                    num_matches = today_games

                    for match in match_list:
                        if num_matches <= 0:
                            break
                        try:
                            match_info = await client.get_tft_match_v1_match(region=mass_region, id=match)
                        except Exception as e:
                            print(f"Error fetching match {match}: {e}")
                            continue
                        
                        if match_info['info']['queue_id'] == dicts.game_type_to_id["Ranked"]:
                            num_matches -= 1
                            for participant in match_info['info']['participants']:
                                if participant['puuid'] == puuid:
                                    placements.append(participant['placement'])
                                    break
                if not placements:
                    return f"No ranked placements found for {gameName}#{tagLine}."

                total_placement = sum(placements)
                scores = " ".join(dicts.number_to_num_icon[placement] for placement in placements)
                avg_placement = round(total_placement / len(placements), 1)
                rank_icon = dicts.tier_to_rank_icon[tier]
                text = (
                    f"{rank_icon} **Rank: **{tier} {rank} ({lp} LP)\n"
                    f"📊 **Games Played:** {today_games}\n"
                    f"⭐ **AVP:** {avg_placement}\n"
                    f"{lp_diff_emoji} **LP Difference:** {lp_diff}\n"
                    f"🏅 **Scores: **{scores}"
                )

            embed = discord.Embed(
                description=text,
                color=discord.Color.blue()
            )
            embed.set_author(
                name=f"Today: {gameName}#{tagLine}",
                url=f"https://lolchess.gg/profile/{region[:-1]}/{gameName.replace(" ", "%20")}-{tagLine}/set14",
                icon_url="https://cdn-b.saashub.com/images/app/service_logos/184/6odf4nod5gmf/large.png?1627090832"
            )
            companion_content_ID = await helpers.get_last_game_companion(gameName, tagLine, mass_region, self.riot_token)
            companion_path = helpers.get_companion_icon(self.companion_mapping, companion_content_ID)
            companion_url = f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/" + companion_path.lower()
            embed.set_thumbnail(url=companion_url)
            embed.set_footer(text="Powered by Riot API | Data from TFT Ranked")
            await ctx.send(embed=embed)

    # Command to compare the profiles of two players
    @commands.command(name="compare", aliases=["c"])
    async def compare(self, ctx, *args): 
        text = ""
        data, error_message, p1_name, p1_tag, p1_region, p1_puuid, p2_name, p2_tag, p2_region, p2_puuid = await helpers.take_in_compare_args(args, self.collection, ctx.author.id, self.riot_token)
        if data:
            p1_gameName = p1_name.replace("_", " ")
            p2_gameName = p2_name.replace("_", " ")
            failedFetch = False
            try:
                p1_rank_info = await helpers.get_rank_info(p1_region, p1_puuid, self.riot_token)
            except Exception as err:
                error_message = f"Error fetching rank info for {p1_gameName}#{p1_tag}: {err}. "
                failedFetch = True
            try:
                p2_rank_info = await helpers.get_rank_info(p2_region, p2_puuid, self.riot_token)
            except Exception as err:
                error_message += f"Error fetching rank info for {p2_gameName}#{p2_tag}: {err}."
                failedFetch = True
            if not failedFetch:
                for p1_entry in p1_rank_info:
                    if p1_entry['queueType'] == 'RANKED_TFT':
                        p1_tier = p1_entry['tier']
                        p1_rank = p1_entry['rank']
                        p1_lp = p1_entry['leaguePoints']
                        p1_total_games = p1_entry['wins'] + p1_entry['losses']
                        p1_top_four_rate = round(p1_entry['wins'] / p1_total_games * 100, 2) if p1_total_games else 0
                        p1_elo = dicts.rank_to_elo[p1_tier + " " + p1_rank] + int(p1_lp)
                for p2_entry in p2_rank_info:
                    if p2_entry['queueType'] == 'RANKED_TFT':
                        p2_tier = p2_entry['tier']
                        p2_rank = p2_entry['rank']
                        p2_lp = p2_entry['leaguePoints']
                        p2_total_games = p2_entry['wins'] + p2_entry['losses']
                        p2_top_four_rate = round(p2_entry['wins'] / p2_total_games * 100, 2) if p2_total_games else 0
                        p2_elo = dicts.rank_to_elo[p2_tier + " " + p2_rank] + int(p2_lp)
                        
                embed = discord.Embed(
                    title=f"Comparing Profiles: {p1_gameName}#{p1_tag} and {p2_gameName}#{p2_tag}",
                    color=discord.Color.blue()
                )

                # Row 1: rank
                embed.add_field(name="🏆 Rank", value=f"{dicts.tier_to_rank_icon[p1_tier]} {p1_tier} {p1_rank} {p1_lp} LP", inline=True)
                if p1_elo > p2_elo:
                    vs_value = "⬅️"
                elif p1_elo < p2_elo:
                    vs_value = "➡️"
                else:
                    vs_value = "⚖️"
                embed.add_field(name="\u200b", value=vs_value, inline=True)
                embed.add_field(name="🏆 Rank", value=f"{dicts.tier_to_rank_icon[p2_tier]} {p2_tier} {p2_rank} {p2_lp} LP", inline=True)

                # Row 2: top 4 rate
                embed.add_field(name="🎯 Top 4 Rate", value=f"{p1_top_four_rate:.1f}%", inline=True)
                if p1_top_four_rate > p2_top_four_rate:
                    top4_value = "⬅️"
                elif p1_top_four_rate < p2_top_four_rate:
                    top4_value = "➡️"
                else:
                    top4_value = "⚖️"
                embed.add_field(name="\u200b", value=top4_value, inline=True)
                embed.add_field(name="🎯 Top 4 Rate", value=f"{p2_top_four_rate:.1f}%", inline=True)

                # Row 3: games played
                embed.add_field(name="📊 Total Games", value=str(p1_total_games), inline=True)
                if p1_total_games > p2_total_games:
                    tot_games_value = "⬅️"
                elif p1_total_games < p2_total_games:
                    tot_games_value = "➡️"
                else:
                    tot_games_value = "⚖️"
                embed.add_field(name="\u200b", value=tot_games_value, inline=True)
                embed.add_field(name="📊 Total Games", value=str(p2_total_games), inline=True)

                await ctx.send(embed=embed)
        await ctx.send(error_message)
        return 

    # Command to check all available commands, update as new commands are added (list alphabetically)
    @commands.command(name="commands", aliases=["command"])
    async def commands(self, ctx): 
        commands_embed = discord.Embed(
        title=f"Commands List",
        description=f"""
**!c** - Compare the profiles of two players\n
**!commands** - Get a list of all commands\n
**!cutoff** - Show the LP cutoffs for Challenger and GM\n
**!h** - Display recent placements for a player\n
**!lb** - View overall bot leaderboard\n
**/link** - Link discord account to riot account\n
**!ping** - Test that bot is active\n
**!r** - View most recent match\n
**!roll** - Rolls a random number (default 1-100)\n
**!s** - Check ranked stats for a player\n
**!t** - Gives summary of today's games
        """,
        color=discord.Color.blue()
        )
        await ctx.send(embed=commands_embed)

# Add this class as a cog to main
async def setup(bot):
    await bot.add_cog(BotCommands(bot, bot.riot_token, bot.collection, bot.region, 
                                  bot.mass_region, bot.champ_mapping, bot.item_mapping,  bot.trait_icon_mapping, bot.companion_mapping))