import discord
import helpers
import dicts
import asyncio
import random
import os
import matplotlib.pyplot as plt
from collections import Counter
from discord.ui import View
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont
from pulsefire.clients import RiotAPIClient
from pulsefire.taskgroups import TaskGroup
from datetime import datetime, timedelta, timezone
import pytz

# Classify file commands as a cog that can be loaded in main
class BotCommands(commands.Cog):
    def __init__(self, bot, tft_token, lol_token, region, mass_region, champ_mapping, item_mapping, trait_icon_mapping, companion_mapping, lol_item_mapping, keystone_mapping, runes_mapping, summs_mapping, pool):
        self.bot = bot
        self.tft_token = tft_token
        self.lol_token = lol_token
        self.region = region
        self.mass_region = mass_region
        self.champ_mapping = champ_mapping
        self.item_mapping = item_mapping
        self.trait_icon_mapping = trait_icon_mapping
        self.companion_mapping = companion_mapping
        self.lol_item_mapping = lol_item_mapping
        self.keystone_mapping = keystone_mapping
        self.runes_mapping = runes_mapping
        self.summs_mapping = summs_mapping
        self.pool = pool

    # Basic test command
    @commands.command()
    async def ping(self, ctx):
        await ctx.send('Lima Oscar Lima!')

    # Command to fetch TFT stats
    @commands.command(name="stats", aliases=["stast", "s", "tft"])
    async def stats(self, ctx, *args):
        gameNum, gameName, tagLine, user_id, error_message = await helpers.parse_args(ctx, args)
        
        if user_id:
            data, gameName, tagLine, region, mass_region, puuid = await helpers.check_data(user_id, self.pool, "TFT")
        else:
            region = self.region
            mass_region = self.mass_region
            puuid = await helpers.get_puuid(gameName, tagLine, mass_region, self.tft_token)
            if not puuid:
                print(f"Could not find PUUID for {gameName}#{tagLine}.")
                return f"Could not find PUUID for {gameName}#{tagLine}.", None, None
            
        rank_embed, error_message = await helpers.get_rank_embed(gameName, tagLine, region, self.tft_token, puuid)

        if error_message:
            await ctx.send(error_message)
        else:
            await ctx.send(embed=rank_embed)

    # Command to fetch LOL stats
    @commands.command(name="leaguestats", aliases=["lstats", "ls", "lol"])
    async def leagueStats(self, ctx, *args):
        gameNum, gameName, tagLine, user_id, error_message = await helpers.parse_args(ctx, args)
        if user_id:
            data, gameName, tagLine, region, mass_region, puuid = await helpers.check_data(user_id, self.pool, "League")
        else: 
            region = self.region
            mass_region = self.mass_region
            puuid = await helpers.get_puuid(gameName, tagLine, mass_region, self.lol_token)
            if not puuid:
                print(f"Could not find PUUID for {gameName}#{tagLine}.")
                return f"Could not find PUUID for {gameName}#{tagLine}.", None, None
            
        rank_embed, error_message = await helpers.get_lol_rank_embed(gameName, tagLine, region, self.lol_token, puuid)

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
        
        mappings = {
            "item_mapping": self.item_mapping,     # item ID → icon path
            "companion_mapping": self.companion_mapping,     # keystone ID → icon path
            "champ_mapping": self.champ_mapping,           # rune ID → icon path
            "trait_icon_mapping": self.trait_icon_mapping            # summoner spell ID → icon path
        }

        gameNum, gameName, tagLine, user_id, error_message = await helpers.parse_args(ctx, args)

        if not gameNum:
            gameNum = 1

        if user_id:
            data, gameName, tagLine, region, mass_region, puuid = await helpers.check_data(user_id, self.pool, "TFT")
        else:
            region = self.region
            mass_region = self.mass_region
            puuid = await helpers.get_puuid(gameName, tagLine, mass_region, self.tft_token)
            if not puuid:
                print(f"Could not find PUUID for {gameName}#{tagLine}.")
                return f"Could not find PUUID for {gameName}#{tagLine}.", None, None

        result, match_id, avg_rank, master_plus_lp, time, player_placement = await helpers.last_match(gameName, tagLine, game_type, mass_region, self.tft_token, region, gameNum)

        embed_colors = ['#F0B52B', "#B8B5B5", '#A45F00', '#595988', "#748283", '#748283', '#748283', '#748283']
        embed = discord.Embed(
            title=f"Recent {game_type} TFT Match Placements:",
            description=result,
            color=discord.Color(int(embed_colors[player_placement - 1].strip("#"), 16))
        )

        embed.set_footer(text=f"Average Lobby Rank: {avg_rank} {master_plus_lp} LP\nTimestamp: {time}" if master_plus_lp else f"Average Lobby Rank: {avg_rank}\nTimestamp: {time}")
        await ctx.send(embed=embed)

        async with RiotAPIClient(default_headers={"X-Riot-Token": self.tft_token}) as client:
            match_info = await client.get_tft_match_v1_match(region=mass_region, id=match_id)

        participants = match_info['info']['participants']

        # Sort in ascending order
        if puuid not in [p['puuid'] for p in participants]:
            await ctx.send(f"Could not find participant with PUUID: {puuid}")
            return

        participants_sorted = sorted(participants, key=lambda x: x['placement'])
        puuid_list = [p['puuid'] for p in participants_sorted]
        current_index = puuid_list.index(puuid)
        
        async def generate_board_strip(index):
            participant = participants_sorted[index]

            # --- Traits Processing ---
            traits = participant['traits']
            filtered_traits = [trait for trait in traits if trait['style'] >= 1]
            sorted_traits = sorted(filtered_traits, key=lambda x: dicts.style_order.get(x['style'], 5))
            num_traits = len(sorted_traits)
            if num_traits <= 5:
                start_y = 40
            elif num_traits <= 9:
                start_y = 20
            else: 
                start_y = 0

            trait_img_width = 56
            trait_img_height = 150
            trait_final_width = 310
            if puuid == participant['puuid']:
                background_color = "#2F3136"
            else:
                background_color = (0,0,0,0)

            trait_final_image = Image.new("RGBA", (trait_final_width, trait_img_height), background_color)

            async def process_trait(trait, i):
                temp_image = await helpers.trait_image(trait['name'], trait['style'], mappings["trait_icon_mapping"])
                if temp_image:
                    temp_image = temp_image.convert("RGBA")
                    temp_image.thumbnail((trait_img_width, int(trait_img_width*1.16)))
                    mask = temp_image.split()[3]
                    if i < 5:
                        trait_final_image.paste(temp_image, (10 + trait_img_width * i, start_y), mask)
                    elif i < 9:
                        trait_final_image.paste(temp_image, (10 + int(trait_img_width * (i - 4.5)), start_y + int(trait_img_width * 0.8)), mask)
                    else:
                        trait_final_image.paste(temp_image, (10 + trait_img_width * int(i - 9), start_y + int(trait_img_width*1.6)), mask)

            # Fetch trait images concurrently
            await asyncio.gather(*[process_trait(trait, i) for i, trait in enumerate(sorted_traits)])
            font = ImageFont.truetype("fonts/NotoSans-Bold.ttf", size=36)

            companion_height = 150
            companion_width = 220
            companion_final_image = Image.new("RGBA", (companion_width,  companion_height), background_color)
            companion_id = participant.get("companion", {}).get("content_ID")
            companion_path = helpers.get_companion_icon(mappings["companion_mapping"], companion_id)
            companion_url = f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/" + companion_path.lower()
            companion_image = await helpers.fetch_image(companion_url)
            square = helpers.center_square_crop(companion_image)
            circle_img = helpers.circular_crop(square)
            circle_img.thumbnail((70,70))
            draw = ImageDraw.Draw(companion_final_image)

            def truncate_text(draw, text, font, max_width):
                # Truncate text with ellipsis (…) if it exceeds the given pixel width.
                ellipsis = "…"
                if draw.textlength(text, font=font) <= max_width:
                    return text
                while text and draw.textlength(text + ellipsis, font=font) > max_width:
                    text = text[:-1]
                return text + ellipsis

            display_name = truncate_text(draw, participant.get('riotIdGameName', 'Unknown'), font, 200)

            draw.text((20,90), display_name, font=font, fill="white")
            companion_final_image.paste(circle_img, (20,20), circle_img)

            try: 
                rank_info = await helpers.get_rank_info(region, participant["puuid"], self.tft_token)
            except Exception as err:
                return None, f"Error fetching rank info for {gameName}#{tagLine}: {err}"
        
            tier = "UNRANKED"
            rank = "I"
            if rank_info:
                for entry in rank_info:
                    if entry['queueType'] == 'RANKED_TFT':
                        tier = entry['tier']
                        rank = entry['rank']

            rank_str = f"{dicts.rank_to_acronym[tier]}{dicts.rank_to_number[rank]}"

            bbox = font.getbbox(rank_str)  # (x_min, y_min, x_max, y_max)
            text_w = bbox[2] - bbox[0]
            padding_x = 12
            x, y = 100, 25
            rect_coords = [
                x, 
                y, 
                x + text_w + padding_x, 
                80
            ]
            draw.rounded_rectangle(rect_coords, radius=8, fill=dicts.rank_to_text_fill[tier], outline=None)
            draw.text((x + padding_x/2, y), rank_str, font=font, fill="#fdda82" if tier == "CHALLENGER" else "white")

            # --- Champions Processing ---
            units = participant.get('units', [])
            champ_unit_data_unsorted = []

            # Calculate total champion image width
            champ_img_width = int(min((1180 - trait_final_width - companion_width) / len(units), 70)) if units else 70
            champ_img_height = 150
            champ_final_image = Image.new("RGBA", ((1200 - trait_final_width - companion_width), champ_img_height), background_color)

            async def process_unit(unit):
                champion_name = unit["character_id"]
                tier = unit["tier"]
                rarity = unit["rarity"]
                item_names = unit["itemNames"]

                custom_rarity = dicts.rarity_map.get(rarity, rarity)
                champ_icon_path = helpers.get_champ_icon(mappings["champ_mapping"], champion_name).lower()
                rarity_url = f"https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-tft-team-planner/global/default/images/cteamplanner_championbutton_tier{custom_rarity}.png"

                if champ_icon_path:
                    champion_url = f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/{champ_icon_path}.png"
                    champ_task = helpers.fetch_image(champion_url, (64, 64))
                    rarity_task = helpers.fetch_image(rarity_url, (72, 72))
                    
                    icon_resized, rarity_resized = await asyncio.gather(champ_task, rarity_task)

                    champ_unit_data_unsorted.append({
                        "champion_name": champion_name,
                        "icon_resized": icon_resized,
                        "rarity_resized": rarity_resized,
                        "rarity": rarity,
                        "tier": tier,
                        "item_names": item_names
                    })

            # Fetch all champion icons & rarity images concurrently
            await asyncio.gather(*[process_unit(unit) for unit in units])
            champ_unit_data = sorted(champ_unit_data_unsorted, key=lambda x: x['rarity'])

            # --- Paste Champions & Items ---
            async def paste_champion(unit, i): 
                champ_image = await helpers.champion_image(unit, mappings["item_mapping"])
                if champ_image:
                    champ_image.thumbnail((champ_img_width, 150))
                    champ_final_image.paste(champ_image, (((champ_img_width + 1)* i), 15), champ_image)

            # Process and paste champions concurrently
            await asyncio.gather(*[paste_champion(unit, i) for i, unit in enumerate(champ_unit_data)])
                        
            # --- Combine Images ---
            final_combined_image = Image.new("RGBA", (1200, 150), (0, 0, 0, 0))
            final_combined_image.paste(trait_final_image, (companion_width, 0), trait_final_image)
            final_combined_image.paste(champ_final_image, (companion_width + trait_final_width, 0), champ_final_image)
            final_combined_image.paste(companion_final_image, (0,0), companion_final_image)

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

            return embed, file, final_combined_image

        async def generate_multiple_boards(start, end):
            # Step 1: Generate boards concurrently
            board_tasks = [generate_board_strip(i) for i in range(start, end)]
            board_results = await asyncio.gather(*board_tasks)  # [(embed, file, image), ...]

            embeds, files, board_images = zip(*board_results)  # unpack into three tuples
            combined_image = Image.new("RGBA", (1200, 600), (255, 255, 255, 0))

            current_y = 0
            for img in board_images:
                combined_image.paste(img, (0, current_y), img)
                current_y += img.height

            return combined_image
        Select_options =[
            discord.SelectOption(label=f"All Boards", value=0),
        ] + [
            discord.SelectOption(label=f"{index + 1} - {participant.get('riotIdGameName')}#{participant.get('riotIdTagline')}", value=index+1) for index, participant in enumerate(participants_sorted)
        ] 

        tft_token = self.tft_token
        # --- Dropdown View ---
        class PlayerSwitchView(discord.ui.View):
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
                if new_index == 0:
                    top4_img = await generate_multiple_boards(0, 4)
                    bot4_img = await generate_multiple_boards(4, 8)

                    top4_img.save("top4.png")
                    bot4_img.save("bot4.png")

                    top4_file = discord.File("top4.png", filename="top4.png") 
                    bot4_file = discord.File("bot4.png", filename="bot4.png")

                    top4_embed = discord.Embed(
                        title="All Boards",
                    )
                    top4_embed.set_image(url="attachment://top4.png")

                    bot4_embed = discord.Embed(
                        # title="All Boards",
                    )
                    bot4_embed.set_image(url="attachment://bot4.png")

                    await ctx.send(embed= top4_embed, file = top4_file)
                    await ctx.send(embed= bot4_embed, file = bot4_file)

                else:
                    new_embed, new_file, _ = await helpers.generate_board_preview(new_index-1, puuid, region, mass_region, match_id, tft_token, mappings)
                    await interaction.followup.edit_message(
                        message_id=interaction.message.id,
                        embed=new_embed,
                        attachments=[new_file],
                        view=PlayerSwitchView(new_index, self.author_id)
                    )
        # index, puuid, region, mass_region, match_id, tft_token, mappings
        # --- Send Initial Message ---
        embed, file, _ = await helpers.generate_board_preview(current_index, puuid, region, mass_region, match_id, tft_token, mappings)
        await ctx.send(embed=embed, file=file, view=PlayerSwitchView(current_index, ctx.author.id))
        os.remove("player_board.png")

    # League recent command
    @commands.command(name="recentleague", aliases=["rl","lr","lrn","lrd","lra","lrf","lrc","lrac","lrq"])
    async def recent_league(self, ctx, *args):
        if ctx.invoked_with == "lrn" or ctx.invoked_with == "lrd":
            game_type = "Draft Pick"
        elif ctx.invoked_with == "lra":
            game_type = "ARAM"
        elif ctx.invoked_with == "lrf":
            game_type = "Ranked Flex"
        elif ctx.invoked_with == "lrc":
            game_type = "Clash"
        elif ctx.invoked_with == "lrac":
            game_type = "ARAM Clash"
        elif ctx.invoked_with == "lrq":
            game_type = "Swiftplay"
        else:
            game_type = "Ranked Solo/Duo"

        mappings = {
            "lol_item_mapping": self.lol_item_mapping,     # item ID → icon path
            "keystone_mapping": self.keystone_mapping,     # keystone ID → icon path
            "runes_mapping": self.runes_mapping,           # rune ID → icon path
            "summs_mapping": self.summs_mapping            # summoner spell ID → icon path
        }

        game_num, gameName, tagLine, user_id, error_message = await helpers.parse_args(ctx, args)
        if not game_num:
            game_num = 0

        if user_id:
            data, gameName, tagLine, region, mass_region, puuid = await helpers.check_data(user_id, self.pool, "League")
        else:
            region = self.region
            mass_region = self.mass_region
            puuid = await helpers.get_puuid(gameName, tagLine, mass_region, self.lol_token)
            if not puuid:
                print(f"Could not find PUUID for {gameName}#{tagLine}.")
                return f"Could not find PUUID for {gameName}#{tagLine}.", None, None
            
        background_color = (0,0,0,0)

        error, match_ids, puuid = await helpers.find_match_ids(gameName, tagLine, game_type, "League", mass_region, self.lol_token)
        if error:
            return error, None, None, None, None, None, None, None, None

        match_id = match_ids[game_num]['match_id']

        if not match_id:
            return f"No recent {game_type} matches found for {gameName}#{tagLine}.",  None, None, None, None, None, None, None, None

        error, final_file, tab_embed, max_damage, max_taken, duration, blue_team, red_team, blue_win  = await helpers.league_last_match(gameName, tagLine, mass_region, self.lol_token, puuid, match_id, game_type, mappings, background_color, True)
        async def process_participant(participant, duration, mappings):
            items = []
            gameName = participant["riotIdGameName"]
            tagLine = participant["riotIdTagline"]
            cs = participant["totalMinionsKilled"] + participant["neutralMinionsKilled"]
            kills = participant["kills"]
            deaths = participant["deaths"]
            assists = participant["assists"]
            items.append(participant["item0"]) 
            items.append(participant["item1"]) 
            items.append(participant["item2"]) 
            items.append(participant["item3"]) 
            items.append(participant["item4"]) 
            items.append(participant["item5"]) 
            items.append(participant["item6"]) 
            killparticipation = participant.get("challenges", {}).get("killParticipation", 0)
            damagedealt = participant["totalDamageDealtToChampions"]
            damagetaken = participant["totalDamageTaken"]

            bold_font = ImageFont.truetype("fonts/NotoSans-Black.ttf", 18)  

            if participant["puuid"] == puuid:
                strip = Image.new("RGBA", (600, 60), "#2F3136")
            else:
                strip = Image.new("RGBA", (600, 60), background_color)
            draw = ImageDraw.Draw(strip)
            if deaths == 0:
                kda_ratio_str = "Perfect"
            else:
                kda_ratio = (kills + assists) / deaths
                kda_ratio_str = f"{kda_ratio:.2f}:1 KDA"
            cspm = cs * 60 / duration
            kda_str = f"{kills}/{deaths}/{assists}"
            cs_str = f" CS {cs} "
            cspm_str = f"{cspm:.1f}/m"

            if len(gameName) > 15:
                gameName = gameName[:14] + "…"

            champ_path = helpers.get_lol_champ_icon(participant["championId"])
            keystone_path = helpers.get_keystone_icon(mappings["keystone_mapping"], participant["perks"]["styles"][0]["selections"][0]["perk"]).lower()
            runes_path = helpers.get_rune_icon(mappings["runes_mapping"], participant["perks"]["styles"][1]["style"]).lower()
            summ1_path = helpers.get_summs_icon(mappings["summs_mapping"], participant["summoner1Id"]).lower()
            summ2_path = helpers.get_summs_icon(mappings["summs_mapping"], participant["summoner2Id"]).lower()
            items = [participant[f"item{i}"] for i in range(7)]
            items_urls = [f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/{helpers.get_lol_item_icon(mappings['lol_item_mapping'], item).lower()}" for item in items]

            # --- Fetch images concurrently ---
            fetch_tasks = [
                helpers.fetch_image(f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/{champ_path}.png", (50,50)),
                helpers.fetch_image(f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/{keystone_path}", (25,25)),
                helpers.fetch_image(f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/{runes_path}", (16,16)),
                helpers.fetch_image(f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/{summ1_path}", (25,25)),
                helpers.fetch_image(f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/{summ2_path}", (25,25)),
                helpers.fetch_image(f"https://wiki.leagueoflegends.com/en-us/images/thumb/Gold_colored_icon.png/20px-Gold_colored_icon.png?39991", (20,20)),
            ]
            # Add item icons
            fetch_tasks.extend([helpers.fetch_image(url, (25,25)) for url in items_urls])

            # Gather all images concurrently
            images = await asyncio.gather(*fetch_tasks)
            champ_image, keystone_image, runes_image, summ1_image, summ2_image, gold_image, *item_icons = images

            try: 
                rank_info = await helpers.get_lol_rank_info(region, participant["puuid"], self.lol_token)
            except Exception as err:
                return None, f"Error fetching rank info for {gameName}#{tagLine}: {err}"
        
            tier = "UNRANKED"
            rank = "I"
            if rank_info:
                for entry in rank_info:
                    if entry['queueType'] == 'RANKED_SOLO_5x5':
                        tier = entry['tier']
                        rank = entry['rank']
            
            strip.paste(champ_image, (15,5))
            strip.paste(summ1_image, (65,5))
            strip.paste(summ2_image, (65,30))
            strip.paste(keystone_image, (90,5), keystone_image)
            strip.paste(runes_image, (94,34), runes_image)
            strip.paste(gold_image, (515,35), gold_image)
            for i, item_icon in enumerate(item_icons):
                strip.paste(item_icon, (410 + i*25, 5), item_icon)  # adjust position as needed
            
            percentMaxDamage = damagedealt / max_damage

            damage_coords = [
                410,
                35,
                410 + percentMaxDamage * 100,
                55
            ]

            damageFill_coords = [
                410 + percentMaxDamage * 100,
                35,
                510,
                55

            ]
            bbox = bold_font.getbbox(kda_str)  # (x_min, y_min, x_max, y_max)
            width1 = bbox[2] - bbox[0]         # text width

            kda_color = "#8a8a8a"
            if kda_ratio_str == "Perfect":
                kda_color = "#f78324"
            elif kda_ratio >= 5.00:
                kda_color = "#f78324"
            elif kda_ratio >= 4.00:
                kda_color = "#188ae9"
            elif kda_ratio >= 3.00:
                kda_color = "#29b0a3"

            rank_str = f"{dicts.rank_to_acronym[tier]}{dicts.rank_to_number[rank]}"
            damage_str = f"{damagedealt/1000:.1f}k"
            participant_gold_str = f"{participant["goldEarned"]/1000:.1f}k"

            damage_font = ImageFont.truetype("fonts/NotoSans-Black.ttf", 16)  
            bbox = bold_font.getbbox(rank_str)  # (x_min, y_min, x_max, y_max)
            text_w = bbox[2] - bbox[0]

            padding_x = 6

            x, y = 115, 5

            rect_coords = [
                x, 
                y, 
                x + text_w + padding_x, 
                30
            ]
            
            draw.rectangle([409,34,511,56], fill="black", outline=None)
            draw.rectangle(damage_coords, fill="#e94054", outline=None)
            draw.rectangle(damageFill_coords, fill="#2a2736", outline=None)
            draw.rectangle([45,35,65,55], fill="black", outline=None)
            draw.text((412,35), damage_str, font = damage_font, fill = "white")
            draw.rounded_rectangle(rect_coords, radius=8, fill=dicts.rank_to_text_fill[tier], outline=None)
            draw.text((118, 5), rank_str, font=bold_font, fill="#fdda82" if tier == "CHALLENGER" else "white")
            draw.text((130 + text_w,5), f"{gameName}", font=bold_font, fill="white")
            draw.text((115,30), kda_str, font=bold_font, fill="white")
            draw.text((125 + width1, 30), kda_ratio_str, font=bold_font, fill=kda_color)
            draw.text((535,35),participant_gold_str, font=damage_font, fill="white")

            helpers.draw_centered(draw, cs_str, damage_font, 360, y=10, fill="white")
            helpers.draw_centered(draw, cspm_str, damage_font, 360, y=30, fill="white")
            helpers.draw_centered(draw, str(participant["champLevel"]), damage_font, 55, y=35, fill="white")

            return strip

        async def build_team_image(team_participants, duration, mappings):
            # Run process_participant for each player concurrently
            strips = await asyncio.gather(*(process_participant(p, duration, mappings) for p in team_participants))

            # Each strip is 600x60 → total height = 300
            team_img = Image.new("RGBA", (600, 300), background_color)

            for i, strip in enumerate(strips):
                team_img.paste(strip, (0, i * 60)) 

            return team_img
        
        blue_img = await build_team_image(blue_team, duration, mappings)
        red_img = await build_team_image(red_team, duration, mappings)

        # Now these are PIL Images → you can save them
        blue_img.save("blue_team.png")
        red_img.save("red_team.png")

        blue_file = discord.File("blue_team.png", filename="blue_team.png")
        red_file = discord.File("red_team.png", filename="red_team.png")

        blue_embed = discord.Embed(
            # title="Victory (Blue team)" if blue_win else "Defeat (Blue team)",
            color=discord.Color.blue() if blue_win else discord.Color.red()
        )
        blue_embed.set_image(url="attachment://blue_team.png")

        red_embed = discord.Embed(
            # title="Defeat (Red team)" if blue_win else "Victory (Red team)",
            color=discord.Color.red() if blue_win else discord.Color.blue()
            )
        red_embed.set_image(url="attachment://red_team.png")

        # # --- Dropdown View ---
        # class ToggleTeamView(discord.ui.View):
        #     def __init__(self, mode):
        #         super().__init__()
        #         self.mode = mode

        #     @discord.ui.select(
        #         placeholder="Display Type", 
        #         options=[
        #             discord.SelectOption(label="Compact", value="0"),
        #             discord.SelectOption(label="Full", value="1")
        #         ], 
        #         max_values= 1)
            
        #     async def switch_mode(self, interaction: discord.Interaction, select: discord.ui.Select):  

        #         await interaction.response.defer()
        #         new_mode = int(select.values[0])

        #         if new_mode == 1:
        #             await interaction.message.edit(
        #                 files=[final_file,blue_file, red_file], 
        #                 embeds=[tab_embed,blue_embed, red_embed],
        #                 view=ToggleTeamView(new_mode)
        #             )
        #         elif new_mode == 0:
        #             await interaction.message.edit(
        #                 files=[final_file], 
        #                 embeds=[tab_embed, red_embed],
        #                 view=ToggleTeamView(new_mode)
        #             )

        # await ctx.send(files=[final_file], embeds=[tab_embed],view=ToggleTeamView(0))
        await ctx.send(files=[final_file,blue_file,red_file], embeds=[tab_embed, blue_embed, red_embed])

    @commands.command(name="todayleague", aliases=["tl","lt"])
    async def today_league(self, ctx, *args):
        mappings = {
            "lol_item_mapping": self.lol_item_mapping,     # item ID → icon path
            "keystone_mapping": self.keystone_mapping,     # keystone ID → icon path
            "runes_mapping": self.runes_mapping,           # rune ID → icon path
            "summs_mapping": self.summs_mapping            # summoner spell ID → icon path
        }
        game_type = "Ranked Solo/Duo"
        game_num, gameName, tagLine, user_id, error_message = await helpers.parse_args(ctx, args)
        if not game_num:
            game_num = 0
        if user_id:
            user_id = int(user_id)
            data, gameName, tagLine, region, mass_region, puuid = await helpers.check_data(user_id, self.pool, "League")
        else:
            return f"Must be a linked user to use this command.", None, None

        background_color = (0,0,0,0)
        
        await helpers.update_user_games(self.pool, user_id, self.tft_token, self.lol_token)
        eastern = pytz.timezone("America/New_York")
        now = datetime.now(eastern)
        today_6am = now.replace(hour=6, minute=0, second=0, microsecond=0)
        if now < today_6am:
            today_6am -= timedelta(days=1)

        cutoff = int(today_6am.timestamp())

        async with self.pool.acquire() as conn:
            user_row = await conn.fetchrow('''
                    SELECT league_puuid, region
                    FROM users
                    WHERE discord_id = $1;
                ''', user_id)
            if not user_row:
                    print(f"No user found with discord_id {user_id}")
                    await ctx.send("❌ Could not find a linked TFT account for this user.")
                    return
            
            rows = await conn.fetch('''
                SELECT *
                FROM league_games
                WHERE league_puuid = $1
                AND game_datetime >= $2
                ORDER BY game_datetime DESC;
            ''', user_row['league_puuid'], cutoff*1000)

            matches = [row['match_id'] for row in rows]
            matches_outcomes = [row['win_loss'] for row in rows]
            wins = sum(matches_outcomes)

            first_snapshot = await conn.fetchrow('''
                SELECT league_lp
                FROM lp
                WHERE discord_id = $1
                AND update_time >= $2
                ORDER BY update_time ASC
                LIMIT 1;
            ''', user_id, cutoff)

            # If no LP snapshot after cutoff, get the *latest* one before it
            if not first_snapshot:
                first_snapshot = await conn.fetchrow('''
                    SELECT league_lp
                    FROM lp
                    WHERE discord_id = $1
                    AND update_time < $2
                    ORDER BY update_time DESC
                    LIMIT 1;
                ''', user_id, cutoff)

            latest_snapshot = await conn.fetchrow('''
                SELECT league_lp
                FROM lp
                WHERE discord_id = $1
                ORDER BY update_time DESC
                LIMIT 1;
            ''', user_id)

            league_diff = 0  
            if first_snapshot and latest_snapshot:
                league_diff = latest_snapshot['league_lp'] - first_snapshot['league_lp']
                old_rank, old_tier, old_lp = helpers.lp_to_div(first_snapshot['league_lp'])
                new_rank, new_tier, new_lp = helpers.lp_to_div(latest_snapshot['league_lp'])
            else:
                print("Missing snapshot(s)")

        if league_diff < 0:
            lp_diff_emoji = "📉"
        else:
            league_diff = "+" + str(league_diff)
            lp_diff_emoji = "📈"

        url = await helpers.get_pfp(user_row['region'], user_row['league_puuid'], self.lol_token)
        text = (
            f"{dicts.tier_to_rank_icon.get(old_rank, '')} {old_rank} {old_tier} {old_lp} LP "
            f"→ {dicts.tier_to_rank_icon.get(new_rank, '')} {new_rank} {new_tier} {new_lp} LP\n"
            f"{lp_diff_emoji} **LP Difference:** {league_diff}\n"
            f"🏆**Record:** {wins} - {len(matches) - wins}"
        )

        embed = discord.Embed(
            description=text,
            color=discord.Color.blue()
        )
        embed.set_author(
            name=f"Today: {gameName}#{tagLine}",
            url=f"https://op.gg/lol/summoners/{region[:-1]}/{gameName.replace(' ', '%20')}-{tagLine}",
            icon_url="https://static.wikia.nocookie.net/leagueoflegends/images/9/9a/League_of_Legends_Update_Logo_Concept_05.jpg/revision/latest/scale-to-width-down/250?cb=20191029062637"
        )
        embed.set_thumbnail(url=url)
        embed.set_footer(text="Powered by Riot API | Data from League Ranked")
        await ctx.send(embed=embed)

        results = []
        async with TaskGroup(asyncio.Semaphore(20)) as tg:
            tasks = []
            for match_id in matches[:5]:

                task = await tg.create_task(
                    helpers.league_last_match(
                        gameName, tagLine, mass_region, self.lol_token,
                        puuid, match_id, game_type, mappings, background_color, False
                    )
                )
                tasks.append(task)

        for task in tasks:
            results.append(task.result())

        embeds = []
        files = []
        for res in results:
            error, final_file, tab_embed, *_ = res
            if error:
                await ctx.send(error)
                continue
            if tab_embed:
                embeds.append(tab_embed)
            if final_file:
                files.append(final_file)

        # Send all at once
        if embeds or files:
            await ctx.send(files=files, embeds=embeds)
        else:
            await ctx.send("No valid matches to display.")

    @commands.command(name="leaguesummary", aliases=["lsum","suml"])
    async def league_summary(self, ctx, *args):
        await helpers.update_db_games(self.pool, self.tft_token, self.lol_token)
        eastern = pytz.timezone("America/New_York")
        now = datetime.now(eastern)
        today_6am = now.replace(hour=6, minute=0, second=0, microsecond=0)
        if now < today_6am:
            today_6am -= timedelta(days=1)
        cutoff = int(today_6am.timestamp())

        guild = ctx.guild
        members = [member.id async for member in guild.fetch_members(limit=None)]
        async with self.pool.acquire() as conn:
            user_rows = await conn.fetch('''
                    SELECT *
                    FROM users
                    WHERE discord_id = ANY($1)
                ''', members)
            final_str = []
            for user_row in user_rows:
                rows = await conn.fetch('''
                    SELECT win_loss
                    FROM league_games
                    WHERE league_puuid = $1
                    AND game_datetime >= $2
                    ORDER BY game_datetime DESC;
                ''', user_row['league_puuid'], cutoff*1000)

                matches = [row['win_loss'] for row in rows]
                if matches:
                    wins = sum(matches)
                    user_id = user_row['discord_id']
                    first_snapshot = await conn.fetchrow('''
                        SELECT league_lp
                        FROM lp
                        WHERE discord_id = $1
                        AND update_time >= $2
                        ORDER BY update_time ASC
                        LIMIT 1;
                    ''', user_id, cutoff)

                    if not first_snapshot:
                        first_snapshot = await conn.fetchrow('''
                            SELECT league_lp
                            FROM lp
                            WHERE discord_id = $1
                            AND update_time < $2
                            ORDER BY update_time DESC
                            LIMIT 1;
                        ''', user_id, cutoff)

                    latest_snapshot = await conn.fetchrow('''
                        SELECT league_lp
                        FROM lp
                        WHERE discord_id = $1
                        ORDER BY update_time DESC
                        LIMIT 1;
                    ''', user_id)

                    league_diff = 0  
                    if first_snapshot and latest_snapshot:
                        league_diff = latest_snapshot['league_lp'] - first_snapshot['league_lp']
                        old_rank, old_tier, old_lp = helpers.lp_to_div(first_snapshot['league_lp'])
                        new_rank, new_tier, new_lp = helpers.lp_to_div(latest_snapshot['league_lp'])
                    else:
                        print("Missing snapshot(s)")

                    if league_diff < 0:
                        lp_diff_emoji = "📉"
                    else:
                        lp_diff_emoji = "📈"
                    league_diff_str = f"+{league_diff}" if league_diff >= 0 else str(league_diff)
                    text = (
                        f"**{user_row['game_name']}#{user_row['tag_line']}**\n"
                        f"{dicts.tier_to_rank_icon.get(old_rank, '')} **{dicts.rank_to_acronym(old_rank)} {dicts.rank_to_number(old_tier)} {old_lp} LP → "
                        f"{dicts.tier_to_rank_icon.get(new_rank, '')} {dicts.rank_to_acronym(new_rank)} {dicts.rank_to_number(new_tier)} {new_lp} LP**\n"
                        f"**{lp_diff_emoji} LP: {league_diff_str}**\n"
                        f"🏆 **Record:** {wins} - {len(matches) - wins}\n"
                    )
                    final_str.append(text)

        description = "\n".join(final_str)

        embed = discord.Embed(
            title="League Server Summary",
            description=description,
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)

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
        puuid = await helpers.get_puuid(name, tag, mass_region, self.tft_token)
        
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
        data, gameName, tagLine, region, mass_region, puuid = await helpers.check_data(ctx.author.id, self.pool, "TFT")
        result = ""
        server = False
        if ctx.guild and ctx.invoked_with in {"server", "serverlb"}:
            guild = ctx.guild
            members = {member.id async for member in guild.fetch_members(limit=None)}
            server = True
        else:
            members = set()

        async with self.pool.acquire() as conn:
            users = await conn.fetch('SELECT discord_id, game_name, tag_line, tft_puuid, league_puuid, region, mass_region FROM users;')
        
        # Create a list to store all users' elo and name
        user_elo_and_name = []

        async def process_user(user):

            print(f"processing {user['game_name']}")
            if server:
                if not int(user['discord_id']) in members:
                    return
            try:
                user_elo, user_tier, user_rank, user_lp = await helpers.calculate_elo(
                    user['tft_puuid'], "TFT", self.tft_token, user['region']
                )
                name_and_tag = f"{user['game_name']}#{user['tag_line']}"
                user_elo_and_name.append(
                    (user_elo, user_tier, user_rank, user_lp, name_and_tag, user['region'])
                )
            except Exception as e:
                await ctx.send(f"Error processing {user['game_name']}#{user['tag_line']}: {e}")

        async with TaskGroup(asyncio.Semaphore(20)) as tg:
            for user in users:
                await tg.create_task(process_user(user))
            
        # Sort users by their elo score (assuming user_elo is a numeric value)
        user_elo_and_name.sort(reverse=True, key=lambda x: x[0])  # Sort in descending order

        # Prepare the leaderboard result
        for index, (user_elo, user_tier, user_rank, user_lp, name_and_tag, region) in enumerate(user_elo_and_name):
            name, tag = name_and_tag.split("#")
            icon = dicts.tier_to_rank_icon[user_tier]
            if user_tier != "UNRANKED":
                if name == gameName and tag == tagLine:
                    result += f"**{index + 1}** - **[__{name_and_tag}__](https://lolchess.gg/profile/{region[:-1]}/{name.replace(" ", "%20")}-{tag}/set15): {icon} {user_tier} {user_rank} • {user_lp} LP**\n"
                else:
                    result += f"**{index + 1}** - [{name_and_tag}](https://lolchess.gg/profile/{region[:-1]}/{name.replace(" ", "%20")}-{tag}/set15): {icon} {user_tier} {user_rank} • {user_lp} LP\n"
            else:
                if name == gameName and tag == tagLine:
                    result += f"**{index + 1}** - **[__{name_and_tag}__](https://lolchess.gg/profile/{region[:-1]}/{name.replace(" ", "%20")}-{tag}/set15): {icon} {user_tier} {user_rank}**\n"
                else:
                    result += f"**{index + 1}** - [{name_and_tag}](https://lolchess.gg/profile/{region[:-1]}/{name.replace(" ", "%20")}-{tag}/set15): {icon} {user_tier} {user_rank}\n"
        
        if server:
            embed_title= "Server TFT Ranked Leaderboard"
        else:
            embed_title= "Overall TFT Ranked Leaderboard"

        lb_embed = discord.Embed(
            title=embed_title,
            description=result,
            color=discord.Color.blue()
        )

        await ctx.send(embed=lb_embed)

    @commands.command(name="leaguelb", aliases=["leagueleaderboard", "lserver", "lserverlb", "llb"])
    async def leaguelb(self, ctx):
        data, gameName, tagLine, region, mass_region, puuid = await helpers.check_data(ctx.author.id, self.pool, "TFT")
        result = ""
        server = False
        if ctx.guild and ctx.invoked_with in {"lserver", "lserverlb"}:
            guild = ctx.guild
            members = {member.id async for member in guild.fetch_members(limit=None)}
            server = True
        else:
            members = set()

        async with self.pool.acquire() as conn:
            users = await conn.fetch('SELECT discord_id, game_name, tag_line, tft_puuid, league_puuid, region, mass_region FROM users;')
        user_elo_and_name = []

        async def process_user(user):
            if server and int(user["discord_id"]) not in members:
                return

            try:
                user_elo, user_tier, user_rank, user_lp = await helpers.calculate_elo(
                    user['league_puuid'], "League", self.lol_token, user['region'])

                name_and_tag = f"{user['game_name']}#{user['tag_line']}"
                user_elo_and_name.append(
                    (user_elo, user_tier, user_rank, user_lp, name_and_tag, user["region"])
                )

            except Exception as e:
                await ctx.send(f"Error processing {user['game_name']}#{user['tag_line']}: {e}")

        async with TaskGroup() as tg:
            for user in users:
                await tg.create_task(process_user(user))

        user_elo_and_name.sort(reverse=True, key=lambda x: x[0])

        for index, (user_elo, user_tier, user_rank, user_lp, name_and_tag, region) in enumerate(user_elo_and_name):
            name, tag = name_and_tag.split("#")
            icon = dicts.tier_to_rank_icon[user_tier]

            profile_url = f"https://op.gg/lol/summoners/{region[:-1]}/{name.replace(' ', '%20')}-{tag}"
            if user_tier != "UNRANKED":
                text = f"{icon} {user_tier} {user_rank} • {user_lp} LP"
            else:
                text = f"{icon} {user_tier} {user_rank}"

            if name == gameName and tag == tagLine:
                result += f"**{index + 1}** - **[__{name_and_tag}__]({profile_url}): {text}**\n"
            else:
                result += f"**{index + 1}** - [{name_and_tag}]({profile_url}): {text}\n"

        if server:
            embed_title= "Server LOL Ranked Leaderboard"
        else:
            embed_title= "Overall LOL Ranked Leaderboard"

        lb_embed = discord.Embed(
            title=embed_title,
            description=result,
            color=discord.Color.blue()
        )

        await ctx.send(embed=lb_embed)

    # Commnad to check the lp cutoff for challenger and grandmaster
    @commands.command(name="cutoff", aliases=["cutoffs", "challenger", "grandmaster", "grandmasters", "lpcutoff", "chall", "gm"])
    async def cutoff(self, ctx):
        challenger_cutoff, grandmaster_cutoff = await helpers.get_cutoff(self.tft_token, self.region)
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

        gameNum, gameName, tagLine, user_id, error_message = await helpers.parse_args(ctx, args)
        if not gameNum:
            gameNum = 20

        # pull user data if registered
        if user_id:
            data, gameName, tagLine, region, mass_region, puuid = await helpers.check_data(user_id, self.pool, "TFT")
        else:
            mass_region = self.mass_region
            puuid = await helpers.get_puuid(gameName, tagLine, mass_region, self.tft_token)
            if not puuid:
                print(f"Could not find PUUID for {gameName}#{tagLine}.")
                return f"Could not find PUUID for {gameName}#{tagLine}.", None, None
        
        error_message, placements, real_num_matches = await helpers.recent_matches(gameName, tagLine, puuid, game_type, mass_region, self.tft_token, gameNum)  # Unpack tuple

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
        gameNum, gameName, tagLine, user_id, error_message = await helpers.parse_args(ctx, args)
        eastern = pytz.timezone("America/New_York")
        now = datetime.now(eastern)
        today_6am = now.replace(hour=6, minute=0, second=0, microsecond=0)
        if now < today_6am:
            today_6am -= timedelta(days=1)
        cutoff = int(today_6am.timestamp())

        if user_id:
            user_id = int(user_id)
            data, gameName, tagLine, region, mass_region, puuid = await helpers.check_data(user_id, self.pool, "TFT")
        else:
            region = self.region
            mass_region = self.mass_region
            puuid = await helpers.get_puuid(gameName, tagLine, mass_region, self.tft_token)
            if not puuid:
                print(f"Could not find PUUID for {gameName}#{tagLine}.")
                return f"Could not find PUUID for {gameName}#{tagLine}.", None, None
            
        await helpers.update_user_games(self.pool, user_id, self.tft_token, self.lol_token)
        
        async with self.pool.acquire() as conn:
            print("Running fetch with:", user_id, cutoff, type(cutoff))
            user_row = await conn.fetchrow('''
                    SELECT league_puuid, tft_puuid, region
                    FROM users
                    WHERE discord_id = $1;
                ''', user_id)
            
            if not user_row:
                    print(f"No user found with discord_id {user_id}")
                    await ctx.send("❌ Could not find a linked TFT account for this user.")
                    return
            
            rows = await conn.fetch('''
                SELECT *
                FROM tft_games
                WHERE tft_puuid = $1
                AND game_datetime >= $2
                ORDER BY game_datetime DESC;
            ''', user_row['tft_puuid'], cutoff*1000)

            print("Fetched rows:", len(rows))

            placements = [row['placement'] for row in rows]

            first_snapshot = await conn.fetchrow('''
                SELECT tft_lp
                FROM lp
                WHERE discord_id = $1
                AND update_time >= $2
                ORDER BY update_time ASC
                LIMIT 1;
            ''', user_id, cutoff)

            latest_snapshot = await conn.fetchrow('''
                SELECT tft_lp
                FROM lp
                WHERE discord_id = $1
                ORDER BY update_time DESC
                LIMIT 1;
            ''', user_id)

            tft_diff = 0  
            if first_snapshot and latest_snapshot:
                tft_diff = latest_snapshot['tft_lp'] - first_snapshot['tft_lp']
                old_rank, old_tier, old_lp = helpers.lp_to_div(first_snapshot['tft_lp'])
                new_rank, new_tier, new_lp = helpers.lp_to_div(latest_snapshot['tft_lp'])

        if tft_diff < 0:
            lp_diff_emoji = "📉"
        else:
            tft_diff = "+" + str(tft_diff)
            lp_diff_emoji = "📈"
        url = await helpers.get_pfp(user_row['region'], user_row['league_puuid'], self.lol_token)

        total_placement = sum(placements)
        scores = " ".join(dicts.number_to_num_icon[placement] for placement in placements)
        if len(placements) > 0:
            avg_placement = round(total_placement / len(placements), 1)
        else:
            avg_placement = "N/A"
        text = (
            f"{dicts.tier_to_rank_icon[old_rank]} {old_rank} {old_tier} {old_lp} LP -> {dicts.tier_to_rank_icon[new_rank]} {new_rank} {new_tier} {new_lp} LP\n"
            f"{lp_diff_emoji} **LP Difference:** {tft_diff}\n"
            f"📊 **Games Played:** {len(placements)}\n"
            f"⭐ **AVP:** {avg_placement}\n"
            f"🏅 **Scores: **{scores}"
        )
        embed = discord.Embed(
            description=text,
            color=discord.Color.blue()
        )
        embed.set_author(
            name=f"Today: {gameName}#{tagLine}",
            url=f"https://lolchess.gg/profile/{region[:-1]}/{gameName.replace(" ", "%20")}-{tagLine}/set15",
            icon_url="https://cdn-b.saashub.com/images/app/service_logos/184/6odf4nod5gmf/large.png?1627090832"
        )
        embed.set_thumbnail(url=url)
        embed.set_footer(text="Powered by Riot API | Data from TFT Ranked")
        await ctx.send(embed=embed)

    # Command to compare the profiles of two players
    @commands.command(name="compare", aliases=["c"])
    async def compare(self, ctx, *args): 
        text = ""
        data, error_message, p1_name, p1_tag, p1_region, p1_puuid, p2_name, p2_tag, p2_region, p2_puuid = await helpers.take_in_compare_args(args, self.collection, ctx.author.id, self.tft_token)
        if data:
            p1_gameName = p1_name.replace("_", " ")
            p2_gameName = p2_name.replace("_", " ")
            failedFetch = False
            try:
                p1_rank_info = await helpers.get_rank_info(p1_region, p1_puuid, self.tft_token)
            except Exception as err:
                error_message = f"Error fetching rank info for {p1_gameName}#{p1_tag}: {err}. "
                failedFetch = True
            try:
                p2_rank_info = await helpers.get_rank_info(p2_region, p2_puuid, self.tft_token)
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
    await bot.add_cog(BotCommands(bot, bot.tft_token, bot.lol_token, bot.region, 
                                  bot.mass_region, bot.champ_mapping, bot.item_mapping,  bot.trait_icon_mapping, bot.companion_mapping, bot.lol_item_mapping, bot.keystone_mapping, bot.runes_mapping, bot.summs_mapping, bot.pool))