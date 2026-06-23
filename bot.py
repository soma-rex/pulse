import ast
import asyncio
from contextlib import redirect_stdout
import io
import os
import sqlite3
import textwrap
import time
import re
import random

import discord
from discord import app_commands
from discord.ext import commands
from groq import Groq
from dotenv import load_dotenv
from difflib import SequenceMatcher

load_dotenv()

api_key = os.getenv("GROQ_API_KEY")
if not api_key:
    raise ValueError("GROQ_API_KEY not found in environment")

groq_client = Groq(api_key=api_key)

def similar(a, b):
    return SequenceMatcher(None, a, b).ratio() > 0.6

def clean_name(name):
    name = name.replace("\\", "")
    name = name.lower()
    name = re.sub(r"\bthe\s+\w+", "", name)
    name = name.strip()
    name = re.sub(r"[^a-z0-9]", "", name)
    return name

def is_match(user_clean, dead_clean):
    user_clean = clean_name(user_clean)
    dead_clean = clean_name(dead_clean)
    if user_clean == dead_clean:
        return True
    if len(user_clean) > 4 and user_clean in dead_clean:
        return True
    if len(dead_clean) > 4 and dead_clean in user_clean:
        return True
    return False

def extract_emojis(text):
    emoji_pattern = re.compile(
        "[\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF]+",
        flags=re.UNICODE
    )
    return emoji_pattern.findall(text)

DB_PATH = "messages.db"
TOKEN_ENV_VARS = ("DISCORD_TOKEN", "BOT_TOKEN")
DEFAULT_COOLDOWN = 10

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

cursor.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        user_id INTEGER PRIMARY KEY,
        count INTEGER NOT NULL DEFAULT 0
    )
""")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS bot_config (
        key TEXT PRIMARY KEY,
        value TEXT
    )
""")
conn.commit()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned_or(";", "&"),
    intents=intents,
)
bot.help_command = None

bot.conn = conn
bot.cursor = cursor
bot.groq_client = groq_client


def build_moderation_permissions() -> discord.Permissions:
    return discord.Permissions(
        view_channel=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        add_reactions=True,
        use_external_emojis=True,
        manage_messages=True,
        moderate_members=True,
        kick_members=True,
        ban_members=True,
    )


def build_invite_url(application_id: int | None) -> str | None:
    if not application_id:
        return None
    return discord.utils.oauth_url(
        application_id,
        permissions=build_moderation_permissions(),
        scopes=("bot", "applications.commands"),
    )


async def setup_hook():
    # Original cogs
    await bot.load_extension("cogs.help_cog")
    await bot.load_extension("cogs.poker")
    await bot.load_extension("cogs.blackjack")
    await bot.load_extension("cogs.roulette")
    await bot.load_extension("cogs.slots")
    await bot.load_extension("cogs.fun")
    await bot.load_extension("cogs.calculator")
    await bot.load_extension("cogs.anime_guess")
    # Temporarily disabled: rumble tracker needs fixes before re-enabling.
    # await bot.load_extension("cogs.rumble")
    await bot.load_extension("cogs.stats")
    await bot.load_extension("cogs.config_cog")
    await bot.load_extension("cogs.admin")
    await bot.load_extension("cogs.ai_cog")
    await bot.load_extension("cogs.hello_gif")
    await bot.load_extension("cogs.pulse_verification")
    await bot.load_extension("cogs.uno")
    await bot.load_extension("cogs.staff_logger")
    # New progression cogs
    await bot.load_extension("cogs.economy_cog")
    await bot.load_extension("cogs.leveling_cog")
    await bot.load_extension("cogs.achievements_cog")
    await bot.load_extension("cogs.quests_cog")
    await bot.load_extension("cogs.gambling_bridge")

bot.setup_hook = setup_hook


def get_token() -> str:
    for env_var in TOKEN_ENV_VARS:
        token = os.getenv(env_var)
        if token:
            return token.strip()
    env_path = ".env"
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key.strip() in TOKEN_ENV_VARS:
                    return value.strip().strip('"').strip("'")
    raise RuntimeError("Bot token not found. Set DISCORD_TOKEN or BOT_TOKEN in your environment or .env file.")


@bot.event
async def on_ready():
    synced = await bot.tree.sync()
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.playing,
            name="dealing cards at the high table",
        ),
        status=discord.Status.online,
    )
    print(f"Synced {len(synced)} commands")
    print(f"Bot ready: {bot.user}")
    invite_url = build_invite_url(bot.application_id)
    if invite_url:
        print(f"Invite with moderation perms: {invite_url}")


@bot.tree.command(name="invite", description="Get the bot invite link with moderation permissions")
async def invite(interaction: discord.Interaction):
    invite_url = build_invite_url(interaction.client.application_id)
    if not invite_url:
        await interaction.response.send_message(
            "I couldn't build the invite link right now.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        f"Use this link to invite me with moderation permissions:\n{invite_url}",
        ephemeral=True,
    )


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.errors.MissingPermissions):
        message = "You need admin permission to use this command."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
        return
    if isinstance(error, app_commands.errors.CheckFailure):
        message = (
            "Only the bot owner can use this command."
            if getattr(interaction.command, "name", "") == "echo"
            else "You need admin permission to use this command."
        )
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
        return
    raise error


@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"<:timer:1480098142379577394> You can use this again in {round(error.retry_after)} seconds.")
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need admin permission to use this command.")
        return
    if isinstance(error, commands.NotOwner):
        await ctx.send("Only the bot owner can use this command.")
        return
    if isinstance(error, commands.CommandNotFound):
        return
    raise error


bot.run(get_token())
