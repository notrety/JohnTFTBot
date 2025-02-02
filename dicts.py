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
