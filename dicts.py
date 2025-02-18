# File containing all dictionaries used for commands

# Dictionary Converting Ranks to Elos
rank_to_elo = {
    "IRON IV": 0,
    "IRON III": 100,
    "IRON II": 200,
    "IRON I": 300,
    "BRONZE IV": 400,
    "BRONZE III": 500,
    "BRONZE II": 600,
    "BRONZE I": 700,
    "SILVER IV": 800,
    "SILVER III": 900,
    "SILVER II": 1000,
    "SILVER I": 1100,
    "GOLD IV": 1200,
    "GOLD III": 1300,
    "GOLD II": 1400,
    "GOLD I": 1500,
    "PLATINUM IV": 1600,
    "PLATINUM III": 1700,
    "PLATINUM II": 1800,
    "PLATINUM I": 1900,
    "EMERALD IV": 2000,
    "EMERALD III": 2100,
    "EMERALD II": 2200,
    "EMERALD I": 2300,
    "DIAMOND IV": 2400,
    "DIAMOND III": 2500,
    "DIAMOND II": 2600,
    "DIAMOND I": 2700,
    "MASTER I": 2800, 
    "GRANDMASTER I": 2800,
    "CHALLENGER I": 2800,
}

# Dictionary Converting Game Type to Queue ID
game_type_to_id = {
    "Ranked": 1100,
    "Normal": 1090,
    "Double Up ": 1160,
    "Hyper Roll": 1130,
    # "Fortune's Favor": 1170,
    # "Soul Brawl": 1180,
    # "Choncc's Treasure": 1190,
    "GameMode": 1165
}

# Dictionary Converting style to texture endpoint
style_to_texture = {
    0: "",
    1: "bronze-hover",
    2: "silver-hover",
    3: "unique-hover",
    4: "gold-1",
    5: "chromatic"
}

# Dictionary for custom order for styles (Unique > Pris > Gold > Silver > Bronze)
style_order = {
    3: 0,
    5: 1,
    4: 2,
    2: 3,
    1: 4
}

# Custom rarity mapping (For some reason api rarity and unit cost are not the same)
rarity_map = {
    8: 6,
    6: 5,
    4: 4,
    2: 3,
    1: 2,
    0: 1,
    9: 1
}

# Map tier to rank_icon emotes
tier_to_rank_icon = {
    "IRON": "<:RankIron:1336405365226733620>",
    "BRONZE": "<:RankBronze:1336405390270660708>",
    "SILVER": "<:RankSilver:1336405406007951481>",
    "GOLD": "<:RankGold:1336405422957007000>",
    "PLATINUM": "<:RankPlatinum:1336405442301268109>",
    "EMERALD": "<:RankEmerald:1336405459032342598>",
    "DIAMOND": "<:RankDiamond:1336405480590938235>",
    "MASTER": "<:RankMaster:1336405497162502174>",
    "GRANDMASTER": "<:RankGrandmaster:1336405512887078923>",
    "CHALLENGER": "<:RankChallenger:1336405530444431492>",
}

# Map numbers to number emote
number_to_num_icon = {
    1: "<:1st:1341538310585319484>",
    2: "<:2nd:1341540997875630173>",
    3: "<:3rd:1341544088700321884>",
    4: "<:4th:1341558096195227658>",
    5: "<:5th:1341547763204362260>",
    6: "<:6th:1341552575295651940>",
    7: "<:7th:1341551372432183316>",
    8: "<:8th:1341552569339740271>"
}