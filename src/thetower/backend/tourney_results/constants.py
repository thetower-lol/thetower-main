from enum import Enum
from typing import Optional

from pydantic import BaseModel

how_many_results_hidden_site = 60000
how_many_results_public_site = 25000
how_many_results_public_site_other = 500
how_many_results_legacy = 200

the_color = "#807e29"

stratas = [0, 250, 500, 750, 1000, 1250, 1500, 1750, 2000, 2500, 3000, 100000]

colors = [
    "#FFF",
    "#992d22",
    "#e390dc",
    "#ff65b8",
    "#d69900",
    "#06d68a",
    "#3970ec",
    "#206c5b",
    "#ff0000",
    "#6dc170",
    "#00ff00",
    "#ffffff",
]
# colors = [the_color] * 11

strata_to_color = dict(zip(stratas, colors))

# position_stratas = [0, 10, 50, 100, 200, 2000][::-1]
# position_colors = ["#333333", "#5555FF", "green", the_color, "red", "#CCCCCC"][::-1]

# Just a placeholder, change me later to be dependant on patch!!!
position_stratas = [
    0,
    1,
    10,
    25,
    50,
    100,
    200,
    300,
    400,
    500,
    600,
    700,
    800,
    900,
    1000,
    1500,
    2000,
][::-1]
position_colors = [
    "#333333",
    "#cda6eb",
    "#7fffe8",  # 10
    "#00b194",
    "#0082ff",
    "#3fabff",  # 100
    "#8bcef9",
    "#ff1d05",
    "#ff6600",  # 400
    "#ff9500",
    "#ffc000",  # 600
    "#ffc000",
    "#fff200",  # 800
    "#fff200",
    "#fff200",
    "#fffa9f",  # 1500
    "#CCCCCC",
][::-1]

sus_person = "sus!!!"

stratas_boundaries = [0, 250, 500, 750, 1000, 1250, 1500, 1750, 2000, 2500, 3000, 100000]
colors_017 = [
    "#FFFFFF",
    "#992d22",
    "#e390dc",
    "#ff65b8",
    "#d69900",
    "#06d68a",
    "#3970ec",
    "#206c5b",
    "#ff0000",
    "#6dc170",
    "#00ff00",
]

stratas_boundaries_018 = [0, 250, 500, 750, 1000, 1250, 1500, 1750, 2000, 2250, 2500, 2750, 3000, 3500, 4000, 100000]
colors_018 = [
    "#FFFFFF",
    "#a4daac",
    "#7bff97",
    "#67f543",
    "#19b106",
    "#ffc447",  # "#ff8000",
    "#ff9931",  # "#ff4200",
    "#ff5f23",  # "#ff0000",
    "#ff0000",
    "#88cffc",  # "#89d1ff",
    "#3da8ff",  # "#61b8ff",
    "#2b7df4",  # "#5c8ddb",
    "#0061ff",  # "#3082ff",
    "#05a99d",  # "#00b1a5",
    "#7efdd3",  # "#7fffd4",
    "#ffffff",
]
strata_to_color_018 = dict(zip(stratas_boundaries_018, colors_018))


class Graph(Enum):
    all = "all"
    last_8 = "last_8"
    last_16 = "last_16"
    last_32 = "last_32"


class Options(BaseModel):
    links_toggle: bool
    current_player: Optional[str] = None
    current_player_id: Optional[str] = None
    compare_players: Optional[list[str]] = None
    default_graph: Graph
    average_foreground: bool
    current_league: Optional[str] = None


medals = ["🥇", "🥈", "🥉"]


legend = "Legend"
champ = "Champion"
plat = "Platinum"
gold = "Gold"
silver = "Silver"
copper = "Copper"

leagues = [legend, champ, plat, gold, silver, copper]


data_folder_name_mapping = {
    legend: legend,
    "data": champ,
    "plat": plat,
    "gold": gold,
    "silver": silver,
    "copper": copper,
}
league_to_folder = {value: key for key, value in data_folder_name_mapping.items()}


leagues_choices = [(league, league) for league in data_folder_name_mapping.values()]


wave_border_choices = [
    0,
    250,
    500,
    750,
    1000,
    1250,
    1500,
    1750,
    2000,
    2250,
    2500,
    2750,
    3000,
    3500,
    4000,
    4500,
    5000,
    100000,
    1000000,
]

all_relics = {
    0: ("No Spoon", "0-relic_NoSpoon.png", " 2%", " Defense Absolute", "350", "Medals", "Matrix"),
    1: ("Red Pill", "1-relic_RedPill.png", " 5%", " Health", "700", "Medals", "Matrix"),
    2: ("Copper Badge", "2-relic_CopperBadge.png", " 3%", " Damage", "", "Tournament", "Top 4"),
    3: ("Silver Badge", "3-relic_SilverBadge.png", " 5%", " Coins", "", "Tournament", "Top 4"),
    4: ("Gold Badge", "4-relic_GoldBadge.png", " 5%", "Critical Factor", "", "Tournament", "Top 4"),
    5: ("Platinum Badge", "5-relic_PlatinumBadge.png", " 5%", " Lab Speed", "", "Tournament", "Top 4"),
    6: ("Champion Badge", "6-relic_ChampionBadge.png", " 10%", "Damage / Meter", "", "Tournament", "Top 4"),
    7: ("Tower Master", "7-relic_ChampionFirst.png", " 10%", " Health", "", "Tournament", "Top 4"),
    8: ("T:I Flux", "8-relic_Flux.png", " 2%", " Coins", "4500", "Tier", ""),
    9: ("T:II Lumin", "9-relic_Lumin.png", "1.5%", " Lab Speed", "4500", "Tier", ""),
    10: ("T:III Pulse", "10-relic_Pulse.png", " 2%", " Critical Factor", "4500", "Tier", ""),
    11: ("T:IV Harmonic", "11-relic_Harmonic.png", " 2%", " Damage", "4500", "Tier", ""),
    12: ("T:V Ether", "12-relic_Ether.png", " 2%", " Health", "4500", "Tier", ""),
    13: ("T:VI Nova", "13-relic_Nova.png", " 5%", " Defense Absolute", "4500", "Tier", ""),
    14: ("T:VII Aether", "14-relic_Aether.png", " 5%", " Coins", "4500", "Tier", ""),
    15: ("T:VIII Graviton", "15-relic_Graviton.png", " 5%", "Damage / Meter", "4500", "Tier", ""),
    16: ("T:IX Fusion", "16-relic_Fusion.png", " 5%", " Health", "4500", "Tier", ""),
    17: ("T:X Plasma", "17-relic_Plasma.png", " 5%", "Damage / Meter", "4500", "Tier", ""),
    18: ("T:XI Resonance", "18-relic_Resonance.png", " 10%", " Defense Absolute", "4500", "Tier", ""),
    19: ("T:XII Chrono", "19-relic_Chrono.png", " 10%", " Lab Speed", "4500", "Tier", ""),
    20: ("T:XIII Hyper", "20-relic_Hyper.png", " 10%", " Coins", "4500", "Tier", ""),
    21: ("T:XIV Arcane", "21-relic_Arcane.png", " 10%", " Damage", "4500", "Tier", ""),
    22: ("T:XV Celestial", "22-relic_Celestial.png", " 10%", "Critical Factor", "4500", "Tier", ""),
    23: ("Year1", "23-relic_1Year.png", " 2%", "Damage", "365", "PlayTime (days)", ""),
    24: ("Year2", "24-relic_2Year.png", " 2%", " Critical Factor", "730", "PlayTime (days)", ""),
    25: ("Year3", "25-relic_3Year.png", " 2%", "Damage / Meter", "1095", "PlayTime (days)", ""),
    26: ("Dreamcatcher", "26-relic_Dreamcatcher.png", " 2%", " Coins", "350", "Medals", "Full Moon"),
    27: ("Spirit Wolf", "27-relic_Wolf.png", "5%", "Critical Factor", "700", "Medals", "Full Moon"),
    28: ("Bacteriophage", "28-relic_Bacteriophage.png", " 2%", " Damage", "350", "Medals", "Viral Outbreak"),
    29: ("Neuron", "29-relic_Neuron.png", " 5%", " Health", "700", "Medals", "Viral Outbreak"),
    30: ("Ionized Plasma", "30-relic_IonizedPlasma.png", " 2%", " Defense Absolute", "350", "Medals", "Plasma Returns"),
    31: ("Plasma Arc", "31-relic_PlasmaArc.png", "4%", " Lab Speed", "700", "Medals", "Plasma Returns"),
    32: ("Honey Drop", "32-relic_honeyDrop.png", " 2%", " Damage", "350", "Medals", "Honey"),
    33: ("Stinger", "33-relic_stinger.png", " 5%", "Critical Factor", "700", "Medals", "Honey"),
    34: ("Aurora Vortex", "34-relic_auroraVortex.png", "2%", "Health", "350", "Medals", "Aurora"),
    35: ("Contained Ions", "35-relic_containedIons.png", "5%", "Defense Absolute", "700", "Medals", "Aurora"),
    36: ("Alien Head", "36-relic_alienHead.png", "2%", "Coins", "350", "Medals", "Aliens"),
    37: ("Alien Warp Drive", "37-relic_alienWarpDrive.png", "5%", "Damage / Meter", "700", "Medals", "Aliens"),
    38: ("Ancient Tome", "38-relic_AncientTome.png", "1.5%", " Lab Speed", "350", "Medals", "Sands of Time"),
    39: ("Space Sundial", "39-relic_Sundial.png", " 5%", " Damage", "700", "Medals", "Sands of Time"),
    40: ("Spooky Bat", "40-relic_Bat.png", " 2%", " Critical Factor", "350", "Medals", "Halloween"),
    41: ("Man Skull", "41-relic_Skull.png", " 5%", " Health", "700", "Medals", "Halloween"),
    42: ("Cherry", "42-relic_Cherry.png", " 2%", " Defense Absolute", "350", "Medals", "Cherry Blossom"),
    43: ("Sakura Lantern", "43-relic_sakuraLantern.png", " 5%", " Coins", "700", "Medals", "Cherry Blossom"),
    44: ("Tower Latte", "44-relic_javaTowerLatte.png", " 2%", "Damage / Meter", "350", "Medals", "Autumn"),
    45: ("Pumpkin", "45-relic_pumpkin.png", " 4%", " Lab Speed", "700", "Medals", "Autumn"),
    46: ("Holy Joystick", "46-relic_holyJoystick.png", " 2%", " Damage", "350", "Medals", "Retro Arcade"),
    47: ("Controller", "47-relic_controller.png", " 5%", "Critical Factor", "700", "Medals", "Retro Arcade"),
    48: ("Firework", "48-relic_Firework.png", " 2%", " Health", "350", "Medals", "New Year"),
    49: ("Cheers", "49-relic_Cheers.png", " 5%", " Defense Absolute", "700", "Medals", "New Year"),
    50: ("Palm Tree", "50-relic_PalmTree.png", " 2%", " Lab Speed", "350", "Medals", "Retrowave"),
    51: ("Pixel Cube Heart", "51-relic_PixelHeart.png", "5%", "Health", "700", "Medals", "Retrowave"),
    52: ("Creepy Eye", "52-relic_CreepyEye.png", " 2%", "Damage / Meter", "350", "Medals", "Dark Strands"),
    53: ("Creepy Smile", "53-relic_CreepySmile.png", " 5%", " Damage", "700", "Medals", "Dark Strands"),
    54: ("Submarine", "54-relic_Submarine.png", " 2%", " Critical Factor", "350", "Medals", "Deep Blue Sea"),
    55: ("Kraken", "55-relic_Kraken.png", " 5%", " Defense Absolute", "700", "Medals", "Deep Blue Sea"),
    56: ("Warp Gate", "56-relic_WarpGate.png", " 2%", " Coins", "350", "Medals", "Faster Than Light"),
    57: ("Star Ship", "57-relic_StarShip.png", " 4%", " Lab Speed", "700", "Medals", "Faster Than Light"),
    58: ("Barnacle", "58-relic_Barnacle.png", " 2%", " Health", "350", "Medals", "Ocean Night"),
    59: ("Wave", "59-relic_Wave.png", " 5%", "Critical Factor", "700", "Medals", "Ocean Night"),
    60: ("Pizza", "60-relic_Pizza.png", " 2%", " Damage", "350", "Medals", "Invaders"),
    61: ("Illuminati ", "61-relic_illuminati.png", " 5%", " Defense Absolute", "700", "Medals", "Invaders"),
    62: ("Prismatic Shard", "62-prismatic_shard.png", " 5%", "Damage / Meter", "700", "Medals", "Prismatic Lines"),
    63: ("Refraction Array", "63-refraction_array.png", " 2%", " Coins", "350", "Medals", "Prismatic Lines"),
    64: ("Cobweb", "64-cobweb.png", " 2%", " Health", "350", "Medals", "Cobweb"),
    65: ("The Fly", "65-the_fly.png", " 5%", " Defense Absolute", "700", "Medals", "Cobweb"),
    66: ("Clip Ons", "66-clip_ons.png", " 2%", " Critical Factor", "350", "Medals", "Matrix"),
    67: ("Code Stream", "67-code_stream.png", " 5%", " Damage", "700", "Medals", "Matrix"),
    68: ("Summit Starlight", "68-summit_starlight.png", "2%", "Lab Speed", "350", "Medals", "Mountain Night"),
    69: ("Mountain Goat", "69-mountain_goat.png", "5%", "Health", "700", "Medals", "Mountain Night"),
    70: ("Hook", "70-hook.png", " 2%", "Damage / Meter", "350", "Medals", "Sunset Fishing"),
    71: ("Fish", "71-fish.png", " 5%", " Defense Absolute", "700", "Medals", "Sunset Fishing"),
    72: ("Gale Winds", "72-gale_winds.png", " 2%", " Coins", "350", "Medals", "Rainfall"),
    73: ("Flying House", "73-fly_house.png", " 5%", "Damage", "700", "Medals", "Rainfall"),
    74: ("Rain Jacket", "74-rain_jacket.png", " 2%", " Health", "350", "Medals", "Storm"),
    75: ("Cloud Lightning", "75-storm_clouds.png", " 5%", "Critical Factor", "700", "Medals", "Storm"),
    76: ("Rabies", "76-rabies.png", " 2%", " Lab Speed", "350", "Medals", "Viral Outbreak"),
    77: ("Ebola", "77-ebola.png", " 5%", " Defense Absolute", "700", "Medals", "Viral Outbreak"),
    78: ("Anubis", "78-anubis.png", " 2%", " Coins", "350", "Medals", "Sands of Time"),
    79: ("Sphinx", "79-sphinx.png", " 5%", " Damage", "700", "Medals", "Sands of Time"),
    80: ("Year4", "", "", "", "1460", "PlayTime (days)", ""),
    81: ("Year5", "", "", "", "1825", "PlayTime (days)", ""),
    82: ("Year6", "", "", "", "2190", "PlayTime (days)", ""),
    83: ("Remote Control", "83-remote_control.png", " 2%", "Damage / Meter", "350", "Medals", "TV"),
    84: ("Cathode Ray Tube", "84-cathode_ray_tube.png", " 5%", " Coins", "700", "Medals", "Tower's Channel"),
    85: ("T:XVI Quantum", "85-relic_Quantum.png", " 10%", " Health", "4500", "Tier", ""),
    86: ("T:XVII Nebula", "86-relic_Nebula.png", " 10%", "Damage / Meter", "4500", "Tier", ""),
    87: ("T:XVIII Singularity", "87relic_Singularity.png", " 10%", " Lab Speed", "4500", "Tier", ""),
    88: ("Comet", "88-comet.png", " 2%", " Damage", "350", "Medals", "Interstellar"),
    89: ("Planetary Rings", "89-planetary_rings.png", " 5%", "Critical Factor", "700", "Medals", "Interstellar"),
    90: ("Lava Flow", "90_lava.png", " 2%", " Health", "350", "Medals", "Volcano"),
    91: ("Ash Cloud", "91_ash_cloud.png", " 5%", " Defense Absolute", "700", "Medals", "Volcano"),
    92: ("Cassette", "92_cassete.png", "", "", "350", "Medals", ""),
    93: ("Neon Sunglasses", "93_neon_sunglasses.png", "", "", "700", "Medals", ""),
    94: ("Tea Ceremony", "94-tea_ceremony.png", "2%", "Health", "350", "Medals", "Cherry Blossom"),
    95: ("Kimono", "95-kimono.png", "5%", "Coins", "700", "Medals", "Cherry Blossom"),
    96: ("Acorn", "96-acorn.png", "2%", "Damage / Meter", "350", "Medals", "Autumn"),
    97: ("Scarf", "97-scarf.png", "5%", "Defense Absolute", "700", "Medals", "Autumn"),
    98: ("Cauldron", "98-cauldron.png", "2%", "Lab Speed", "350", "Medals", "Halloween"),
    99: ("Witch Hat", "99-witch_hat.png", "5%", "Damage", "700", "Medals", "Halloween"),
    100: ("Abduction Room", "100-abduction_room.png", "2%", "Health", "350", "Medals", "Abduction"),
    101: ("Crop Circle", "101-crop_circle.png", "5%", "Critical Factor", "700", "Medals", "Abduction"),
    102: ("Legend Badge", "102-relic_LegendBadge.png", "10%", "Critical Factor", "", "Tournament", "Top 1"),
    103: ("Icicle", "103-icicle.png", "2%", "Damage", "350", "Medals", "Snowstorm"),
    104: ("Sleigh Bell", "104-sleigh_ball.png", "5%", "Health", "700", "Medals", "Snowstorm"),
    105: ("Koi Fish", "105-koi_fish.png", "2%", "Lab Speed", "350", "Medals", "Cherry Blossom"),
    106: ("Bonsai Tree", "106-bonsai_tree.png", "5%", "Coins", "700", "Medals", "Cherry Blossom"),
    107: ("Power Glove", "107-bonsai_tree.png", "", "", "350", "Medals", ""),
    108: ("Arcade Token", "108-arcade_token.png", "", "", "700", "Medals", ""),
    109: ("Lunar Cat Paw", "109-lunar_cat_paw.png", "1%", "Critical Chance", "350", "Medals", "Meowy Night"),
    110: ("Pet Cat", "110-pet_cat.png", "2%", "Attack Speed", "700", "Medals", "Meowy Night"),
    111: ("Confetti Ball", "111-confetti_ball.png", "1%", "Free Attack Upgrade", "350", "Medals", "New Year"),
    112: ("Party Mask", "112-party_mask.png", "5%", "Ultimate Damage", "700", "Medals", "New Year"),
    113: ("Falling Apple", "113-falling_apple.png", "1%", "Free Defense Upgrade", "350", "Medals", "Gravity"),
    114: ("3 Body Solution", "114-3_body_solution.png", "2%", "Super Critical Chance", "700", "Medals", "Gravity"),
    115: ("Coral Crown", "115-coral_crown.png", "", "", "350", "Medals", ""),
    116: ("Angler Fish", "116-angler_fish.png", "", "", "700", "Medals", ""),
    117: ("Haunted Mirror", "117-haunted_mirror.png", "", "", "350", "Medals", ""),
    118: ("Shadow Puppet", "118-shadow_puppet.png", "", "", "700", "Medals", ""),
    119: ("Temporal Rift", "119-temporal_rift.png", "", "", "350", "Medals", ""),
    120: ("Dream Clock", "120-dream_clock.png", "", "", "700", "Medals", ""),
    121: ("Pulsar Core", "121-pulsar_core.png", "", "", "350", "Medals", ""),
    122: ("Light Speedometer", "122-light_speedometer.png", "", "", "700", "Medals", ""),
    123: ("UFO Beam", "123-ufo_beam.png", "", "", "350", "Medals", ""),
    124: ("Alien Egg", "124-alien_egg.png", "", "", "700", "Medals", ""),
    125: ("Unknown", "125-unknown.png", "", "", "350", "Medals", ""),
    126: ("Unknown", "126-unknown.png", "", "", "700", "Medals", ""),
    127: ("Unknown", "127-unknown.png", "", "", "350", "Medals", ""),
    128: ("Unknown", "128-unknown.png", "", "", "700", "Medals", ""),
    129: ("Unknown", "129-unknown.png", "", "", "350", "Medals", ""),
    130: ("Unknown", "130-unknown.png", "", "", "700", "Medals", ""),
    131: ("Unknown", "131-unknown.png", "", "", "350", "Medals", ""),
    132: ("Unknown", "132-unknown.png", "", "", "700", "Medals", ""),
}
