import discord
import requests
import helpers
import dicts
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
            result, avg_rank, master_plus_lp = helpers.last_match(gameName, tagLine, game_type, self.mass_region, self.riot_token, self.tft_watcher, self.region)
            result_embed = discord.Embed(
                            title=f"Recent {game_type} TFT Match Placements:",
                            description=result,
                            color=discord.Color.blue()
                        )
            if master_plus_lp == 0:
                result_embed.set_footer(text=f"Average Lobby Rank: {avg_rank}")
            else:
                result_embed.set_footer(text=f"Average Lobby Rank: {avg_rank} {master_plus_lp} LP")
            await ctx.send(embed=result_embed)

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

    # Command to return traits units and items of a player given puuid and match_id, to be merged into last_match
    @commands.command()
    async def player_board(self, ctx, puuid: str, match_id: str):
        match_info = self.tft_watcher.match.by_id(self.region, match_id)

        # Find the participant matching the given PUUID
        for participant in match_info['info']['participants']:
            if participant['puuid'] == puuid:
                # --- Traits Logic ---
                traits = participant['traits']
                
                # Filter traits to only include those with style 1 or higher
                filtered_traits = [trait for trait in traits if trait['style'] >= 1]

                # Sort the filtered traits by style using the custom order
                sorted_traits = sorted(filtered_traits, key=lambda x: dicts.style_order.get(x['style'], 5))  # Default to 5 if style not found

                num_traits = len(sorted_traits)
                if num_traits == 0:
                    await ctx.send("No valid traits found for this player.")
                    return

                # Create the trait image with extended width
                trait_img_width = 89 * num_traits
                trait_img_height = 103
                trait_final_image = Image.new("RGBA", (trait_img_width, trait_img_height), (0, 0, 0, 0))

                for i, trait in enumerate(sorted_traits):
                    temp_image = helpers.trait_image(trait['name'], trait['style'], self.trait_icon_mapping)
                    if temp_image:
                        temp_image = temp_image.convert("RGBA")  # Ensure it's RGBA
                        mask = temp_image.split()[3]  # Get the alpha channel
                        trait_final_image.paste(temp_image, (89 * i, 0), mask)

                # --- Champions Logic ---
                units = participant.get('units', [])
                champ_img_width = 0
                champ_img_height = 140
                champ_unit_data = []

                for unit in units:
                    champion_name = unit["character_id"]
                    tier = unit["tier"]
                    rarity = unit["rarity"]
                    item_names = unit["itemNames"]

                    custom_rarity = dicts.rarity_map.get(rarity, rarity)

                    # Get the champion icon
                    champ_icon_path = helpers.get_champ_icon(self.champ_mapping, champion_name).lower()
                    if champ_icon_path:
                        champion_url = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/" + champ_icon_path + ".png"
                        champion_response = requests.get(champion_url)
                        icon = Image.open(BytesIO(champion_response.content)).convert("RGBA")
                        icon_resized = icon.resize((64, 64), Image.LANCZOS)

                        # Get the rarity icon based on the custom rarity
                        rarity_url = f"https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-tft-team-planner/global/default/images/cteamplanner_championbutton_tier{custom_rarity}.png"
                        rarity_response = requests.get(rarity_url)
                        rarity_border = Image.open(BytesIO(rarity_response.content)).convert("RGBA")
                        rarity_resized = rarity_border.resize((72, 72), Image.LANCZOS)

                        champ_img_width += 72  # Add the width of the champion icon with rarity
                        champ_unit_data.append({
                            "champion_name": champion_name,
                            "icon_resized": icon_resized,
                            "rarity_resized": rarity_resized,
                            "tier": tier,
                            "item_names": item_names
                        })

                # Create the champion image
                champ_final_image = Image.new("RGBA", (champ_img_width, champ_img_height), (0, 0, 0, 0))
                current_x = 0

                for unit in champ_unit_data:
                    champ_final_image.paste(unit["rarity_resized"], (current_x, 25), unit["rarity_resized"])
                    champ_final_image.paste(unit["icon_resized"], (current_x + 4, 29), unit["icon_resized"])

                    if unit["tier"] == 2 or unit["tier"] == 3:
                        tier_icon_path = f"https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-tft/global/default/tft-piece-star-{unit['tier']}.png"
                        tier_response = requests.get(tier_icon_path)
                        tier_icon = Image.open(BytesIO(tier_response.content)).convert("RGBA")
                        tier_resized = tier_icon.resize((72, 36), Image.LANCZOS)
                        champ_final_image.paste(tier_resized, (current_x, 0), tier_resized)

                    for i, item_name in enumerate(unit["item_names"]):
                        item_icon_path = helpers.get_item_icon(self.item_mapping, item_name).lower()
                        item_url = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/" + item_icon_path
                        item_response = requests.get(item_url)
                        item_icon = Image.open(BytesIO(item_response.content)).convert("RGBA")
                        item_resized = item_icon.resize((23, 23), Image.LANCZOS)
                        champ_final_image.paste(item_resized, (current_x + 23 * i, 97), item_resized)

                    current_x += 72

                # --- Combine the Trait and Champion Images ---
                # Create a new final image with combined height (traits + champions)
                final_img_height = trait_img_height + champ_img_height
                final_combined_image = Image.new("RGBA", (max(trait_img_width, champ_img_width), final_img_height), (0, 0, 0, 0))

                # Paste trait and champion images
                final_combined_image.paste(trait_final_image, (0, 0), trait_final_image)
                final_combined_image.paste(champ_final_image, (0, trait_img_height), champ_final_image)

                # Save and send the final image
                final_combined_image.save("player_board.png")
                file = discord.File("player_board.png", filename="player_board.png")
                embed = discord.Embed(title="Player Traits & Champions Overlay")
                embed.set_image(url="attachment://player_board.png")
                await ctx.send(embed=embed, file=file)
                return  # Exit after sending

        # If no participant matches the given PUUID
        await ctx.send(f"Could not find participant with PUUID: {puuid}")

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
            
            if name == gameName and tag == tagLine:
                result += f"**{index + 1}** - **__{name_and_tag}__: {tier} {division} {lp} LP**\n"
            else:
                result += f"**{index + 1}** - {name_and_tag}: {tier} {division} {lp} LP\n"
        
        lb_embed = discord.Embed(
            title=f"Overall Bot Ranked Leaderboard",
            description=result,
            color=discord.Color.blue()
        )
        await ctx.send(embed=lb_embed)



    # Command to check all available commands, UPDATE THIS AS NEW COMMANDS ARE ADDED
    @commands.command()
    async def commands(self, ctx): 
        commands_embed = discord.Embed(
        title=f"Commands List",
        description=f"""
    **!r** - View most recent ranked match\n
    **!rn** - View most recent normal match\n
    **!rh** - View most recent hyper roll match\n
    **!rd** - View most recent double up match\n
    **!rg** - View most recent game mode match\n
    **!stats** - Check ranked stats for a player\n
    **!lb** - View overall bot leaderboard\n
    **!ping** - Test that bot is active\n
    **!commands** - Get a list of all commands\n
    **!link** - Link discord account to riot account
        """,
        color=discord.Color.blue()
        )
        await ctx.send(embed=commands_embed)

# Add this class as a cog to main
async def setup(bot):
    await bot.add_cog(BotCommands(bot, bot.tft_watcher, bot.collection, bot.region, 
                                  bot.mass_region, bot.champ_mapping, bot.item_mapping, bot.riot_token, bot.trait_icon_mapping))