# ---- FIX FOR MISSING AUDIOOP IN PTERODACTYL/CYBRANCEE ----
import types
audioop = types.ModuleType("audioop")
audioop.error = Exception
import sys
sys.modules["audioop"] = audioop
# -----------------------------------------------------------

# ================================
# NORMAL IMPORTS
# ================================
import os
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List

import discord
from discord.ext import tasks
from discord import app_commands
from dotenv import load_dotenv

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None  # type: ignore


# ======================================================
# LOAD TOKEN (ENV or .env)
# ======================================================
load_dotenv()  # loads DISCORD_BOT_TOKEN from .env if present


def load_token() -> str:
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("❌ ERROR: DISCORD_BOT_TOKEN not set in environment or .env file.")
        raise SystemExit
    return token


# ======================================================
# CONFIG
# ======================================================
DATA_FILE = "serverdata.json"
VALID_DAYS = [
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday"
]

DEFAULT_TIMEZONE = "UTC"
DEFAULT_POST_HOUR = 8
DEFAULT_POST_MINUTE = 25


# ======================================================
# SAFE HELPERS
# ======================================================
def safe_guild_id(interaction: discord.Interaction) -> int:
    """Ensure command is used inside a guild."""
    if interaction.guild_id is None:
        raise ValueError("This command cannot be used in DMs.")
    return interaction.guild_id


def safe_member(interaction: discord.Interaction) -> discord.Member:
    """Ensure interaction.user is a Member."""
    if not isinstance(interaction.user, discord.Member):
        raise ValueError("This command cannot be used in DMs.")
    return interaction.user


def safe_text_channel(channel: discord.abc.GuildChannel) -> discord.TextChannel:
    """Ensure channel is a normal text channel."""
    if not isinstance(channel, discord.TextChannel):
        raise ValueError("You must choose a standard text channel.")
    return channel


def split_message(text: str, limit: int = 2000) -> List[str]:
    """Auto-split long messages to avoid Discord 2000-char limit."""
    chunks: List[str] = []
    while len(text) > limit:
        split_point = text.rfind("\n", 0, limit)
        if split_point == -1:
            split_point = limit
        chunks.append(text[:split_point])
        text = text[split_point:]
    chunks.append(text)
    return chunks


# ======================================================
# LOAD / SAVE DATA
# ======================================================
def load_data() -> Dict[str, Any]:
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_data(data: Dict[str, Any]) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


data: Dict[str, Any] = load_data()


# ======================================================
# TIMEZONE HELPERS
# ======================================================
def tz_supported() -> bool:
    return ZoneInfo is not None


def validate_timezone(tz_name: str) -> bool:
    if tz_name.upper() == "UTC":
        return True
    if not tz_supported():
        return False
    try:
        ZoneInfo(tz_name)  # type: ignore
        return True
    except Exception:
        return False


def get_guild_timezone(guild_id: int) -> str:
    ensure_guild(guild_id)
    tz_name = data[str(guild_id)].get("timezone", DEFAULT_TIMEZONE)
    if not isinstance(tz_name, str):
        tz_name = DEFAULT_TIMEZONE
    return tz_name


def get_tzinfo(tz_name: str):
    if tz_name.upper() == "UTC":
        return timezone.utc
    if not tz_supported():
        return timezone.utc
    try:
        return ZoneInfo(tz_name)  # type: ignore
    except Exception:
        return timezone.utc


def get_guild_post_time(guild_id: int) -> tuple[int, int]:
    ensure_guild(guild_id)
    g = data[str(guild_id)]
    hour = g.get("post_hour", DEFAULT_POST_HOUR)
    minute = g.get("post_minute", DEFAULT_POST_MINUTE)
    try:
        hour = int(hour)
        minute = int(minute)
    except Exception:
        return DEFAULT_POST_HOUR, DEFAULT_POST_MINUTE
    hour = max(0, min(23, hour))
    minute = max(0, min(59, minute))
    return hour, minute


def next_run_local(now_local: datetime, hour: int, minute: int) -> datetime:
    """Return next datetime (local tz) when the post should run."""
    candidate = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now_local:
        candidate += timedelta(days=1)
    return candidate


# ======================================================
# GUILD INITIALIZER (migration + safety)
# schedule[day] is ALWAYS a list[int]
# ======================================================
def ensure_guild(guild_id: int) -> None:
    gid = str(guild_id)

    # Create new guild entry
    if gid not in data:
        data[gid] = {
            "messages": {},
            "schedule": {},      # day -> list[int]
            "post_channel": None,
            "timezone": DEFAULT_TIMEZONE,
            "post_hour": DEFAULT_POST_HOUR,
            "post_minute": DEFAULT_POST_MINUTE,
        }
        save_data(data)
        return

    g = data[gid]

    # Messages always dict
    if "messages" not in g or not isinstance(g["messages"], dict):
        g["messages"] = {}

    # Schedule always dict[day] -> list[int]
    sched = g.get("schedule")
    if not isinstance(sched, dict):
        sched = {}
    fixed_sched: Dict[str, List[int]] = {}
    for day, val in sched.items():
        if isinstance(val, int):
            fixed_sched[day] = [val]
        elif isinstance(val, list):
            cleaned = [int(x) for x in val if isinstance(x, int)]
            if cleaned:
                fixed_sched[day] = cleaned
    g["schedule"] = fixed_sched

    # post_channel always present
    if "post_channel" not in g:
        g["post_channel"] = None

    # timezone
    tz_name = g.get("timezone", DEFAULT_TIMEZONE)
    if not isinstance(tz_name, str) or not tz_name:
        tz_name = DEFAULT_TIMEZONE
    if not validate_timezone(tz_name):
        tz_name = DEFAULT_TIMEZONE
    g["timezone"] = tz_name

    # post time
    hour = g.get("post_hour", DEFAULT_POST_HOUR)
    minute = g.get("post_minute", DEFAULT_POST_MINUTE)
    try:
        hour = int(hour)
        minute = int(minute)
    except Exception:
        hour, minute = DEFAULT_POST_HOUR, DEFAULT_POST_MINUTE
    g["post_hour"] = max(0, min(23, hour))
    g["post_minute"] = max(0, min(59, minute))

    save_data(data)


# ======================================================
# BOT SETUP
# ======================================================
intents = discord.Intents.default()
intents.message_content = False  # using slash commands only

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


# ======================================================
# ADD MESSAGE COMMANDS
# ======================================================
@tree.command(name="addmessage", description="Save a message to the bot")
async def addmessage(interaction: discord.Interaction, message_id: int, text: str):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    data[str(gid)]["messages"][str(message_id)] = text
    save_data(data)

    await interaction.response.send_message(
        f"✔ Message {message_id} saved.",
        ephemeral=True
    )


# Popup modal for multiline message
class AddMessageModal(discord.ui.Modal, title="Add Multi-Line Message"):
    text = discord.ui.TextInput(
        label="Message",
        style=discord.TextStyle.paragraph,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        gid = safe_guild_id(interaction)
        ensure_guild(gid)

        messages: Dict[str, str] = data[str(gid)]["messages"]
        new_id = str(max([int(i) for i in messages] + [0]) + 1)

        data[str(gid)]["messages"][new_id] = str(self.text)
        save_data(data)

        await interaction.response.send_message(
            f"✔ Saved as message {new_id}.",
            ephemeral=True
        )


@tree.command(name="addmessagepopup", description="Add a multiline message using a popup")
async def addmessagepopup(interaction: discord.Interaction):
    await interaction.response.send_modal(AddMessageModal())


# ======================================================
# EDIT / REMOVE MESSAGE
# ======================================================
@tree.command(name="editmessage", description="Edit a saved message")
async def editmessage(
    interaction: discord.Interaction,
    message_id: int,
    new_text: str
):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    if str(message_id) not in data[str(gid)]["messages"]:
        return await interaction.response.send_message(
            "❌ Message not found.",
            ephemeral=True
        )

    data[str(gid)]["messages"][str(message_id)] = new_text
    save_data(data)

    await interaction.response.send_message(
        f"✏ Updated message {message_id}.",
        ephemeral=True
    )


@tree.command(name="removemessage", description="Delete a saved message")
async def removemessage(interaction: discord.Interaction, message_id: int):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    if str(message_id) not in data[str(gid)]["messages"]:
        return await interaction.response.send_message(
            "❌ Message not found.",
            ephemeral=True
        )

    del data[str(gid)]["messages"][str(message_id)]
    save_data(data)

    await interaction.response.send_message(
        f"🗑 Message {message_id} deleted.",
        ephemeral=True
    )


# ======================================================
# VIEW MESSAGE COMMANDS
# ======================================================
@tree.command(name="viewmessage", description="View a specific message by ID")
async def viewmessage(interaction: discord.Interaction, message_id: int):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    msg = data[str(gid)]["messages"].get(str(message_id))
    if not msg:
        return await interaction.response.send_message(
            "❌ Message not found.",
            ephemeral=True
        )

    parts = split_message(msg)

    await interaction.response.send_message(
        f"📄 **Message {message_id}:**",
        ephemeral=True
    )
    for p in parts:
        await interaction.followup.send(p, ephemeral=True)


@tree.command(name="viewmessages", description="View all saved messages")
async def viewmessages(interaction: discord.Interaction):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    msgs = data[str(gid)]["messages"]
    if not msgs:
        return await interaction.response.send_message(
            "No saved messages.",
            ephemeral=True
        )

    desc_lines = []
    for mid, txt in msgs.items():
        desc_lines.append(f"`{mid}` – {len(txt)} chars")

    embed = discord.Embed(
        title="🗂️ Saved Messages",
        description="\n".join(desc_lines),
        colour=discord.Colour.blurple()
    )

    await interaction.response.send_message(embed=embed, ephemeral=False)


# ======================================================
# ADVANCED SCHEDULER (MULTI-MESSAGE PER DAY)
# ======================================================
@tree.command(name="schedule", description="Add a message to a day's schedule (append)")
async def schedule(
    interaction: discord.Interaction,
    day: str,
    message_id: int
):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    if day not in VALID_DAYS:
        return await interaction.response.send_message(
            "❌ Invalid day. Use Monday, Tuesday, etc.",
            ephemeral=True
        )

    guild_data = data[str(gid)]
    if str(message_id) not in guild_data["messages"]:
        return await interaction.response.send_message(
            "❌ Message ID does not exist.",
            ephemeral=True
        )

    schedule_data: Dict[str, List[int]] = guild_data["schedule"]
    current = schedule_data.get(day, [])
    current.append(message_id)
    schedule_data[day] = current
    save_data(data)

    await interaction.response.send_message(
        f"📅 Added message `{message_id}` to **{day}** queue position {len(current)}.",
        ephemeral=True
    )


@tree.command(name="schedulelist", description="View ordered schedule for a specific day")
async def schedulelist(interaction: discord.Interaction, day: str):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    if day not in VALID_DAYS:
        return await interaction.response.send_message(
            "❌ Invalid day.",
            ephemeral=True
        )

    guild_data = data[str(gid)]
    schedule_data: Dict[str, List[int]] = guild_data["schedule"]
    queue = schedule_data.get(day, [])

    if not queue:
        return await interaction.response.send_message(
            f"📅 No messages scheduled for **{day}**.",
            ephemeral=True
        )

    lines = []
    for idx, mid in enumerate(queue, start=1):
        lines.append(f"**{idx}.** Message `{mid}`")

    embed = discord.Embed(
        title=f"📅 {day} Schedule",
        description="\n".join(lines),
        colour=discord.Colour.blurple()
    )

    await interaction.response.send_message(embed=embed, ephemeral=False)


@tree.command(name="scheduleremove", description="Remove a message by index from a day's schedule")
async def scheduleremove(
    interaction: discord.Interaction,
    day: str,
    index: int
):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    if day not in VALID_DAYS:
        return await interaction.response.send_message(
            "❌ Invalid day.",
            ephemeral=True
        )

    guild_data = data[str(gid)]
    schedule_data: Dict[str, List[int]] = guild_data["schedule"]
    queue = schedule_data.get(day, [])

    if not queue:
        return await interaction.response.send_message(
            f"❌ No schedule for **{day}**.",
            ephemeral=True
        )

    if index < 1 or index > len(queue):
        return await interaction.response.send_message(
            f"❌ Index must be between 1 and {len(queue)}.",
            ephemeral=True
        )

    removed = queue.pop(index - 1)
    if queue:
        schedule_data[day] = queue
    else:
        del schedule_data[day]

    save_data(data)

    await interaction.response.send_message(
        f"🗑 Removed message `{removed}` from **{day}** at position {index}.",
        ephemeral=True
    )


@tree.command(name="schedulemove", description="Reorder items in a day's schedule")
async def schedulemove(
    interaction: discord.Interaction,
    day: str,
    from_index: int,
    to_index: int
):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    if day not in VALID_DAYS:
        return await interaction.response.send_message(
            "❌ Invalid day.",
            ephemeral=True
        )

    guild_data = data[str(gid)]
    schedule_data: Dict[str, List[int]] = guild_data["schedule"]
    queue = schedule_data.get(day, [])

    if not queue:
        return await interaction.response.send_message(
            f"❌ No schedule for **{day}**.",
            ephemeral=True
        )

    n = len(queue)
    if from_index < 1 or from_index > n or to_index < 1 or to_index > n:
        return await interaction.response.send_message(
            f"❌ Indexes must be between 1 and {n}.",
            ephemeral=True
        )

    item = queue.pop(from_index - 1)
    queue.insert(to_index - 1, item)
    schedule_data[day] = queue
    save_data(data)

    await interaction.response.send_message(
        f"🔁 Moved message `{item}` from position {from_index} to {to_index} on **{day}**.",
        ephemeral=True
    )


@tree.command(name="scheduleclear", description="Clear all scheduled messages for a day")
async def scheduleclear(interaction: discord.Interaction, day: str):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    if day not in VALID_DAYS:
        return await interaction.response.send_message(
            "❌ Invalid day.",
            ephemeral=True
        )

    guild_data = data[str(gid)]
    schedule_data: Dict[str, List[int]] = guild_data["schedule"]

    if day not in schedule_data:
        return await interaction.response.send_message(
            f"❌ No schedule for **{day}**.",
            ephemeral=True
        )

    del schedule_data[day]
    save_data(data)

    await interaction.response.send_message(
        f"🧹 Cleared schedule for **{day}**.",
        ephemeral=True
    )


@tree.command(name="removeschedule", description="(Alias) Clear all messages for a day")
async def removeschedule(interaction: discord.Interaction, day: str):
    await scheduleclear.callback(interaction, day=day)  # type: ignore


# ======================================================
# VIEWSCHEDULE / VIEWSETTINGS
# ======================================================
@tree.command(name="viewschedule", description="View weekly schedule summary")
async def viewschedule(interaction: discord.Interaction):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    guild_data = data[str(gid)]
    schedule_data: Dict[str, List[int]] = guild_data["schedule"]

    lines: List[str] = []
    for d in VALID_DAYS:
        queue = schedule_data.get(d, [])
        if queue:
            ids = ", ".join(str(mid) for mid in queue)
            lines.append(f"**{d}** → `{ids}`")
        else:
            lines.append(f"**{d}** → *(none)*")

    embed = discord.Embed(
        title="📅 Weekly Schedule",
        description="\n".join(lines),
        colour=discord.Colour.blurple()
    )

    await interaction.response.send_message(embed=embed, ephemeral=False)


@tree.command(name="viewchannel", description="Show configured auto-post channel")
async def viewchannel(interaction: discord.Interaction):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(
            "❌ Cannot be used in DMs.",
            ephemeral=True
        )

    guild_data = data[str(gid)]
    channel_id = guild_data.get("post_channel")
    if channel_id is None:
        return await interaction.response.send_message(
            "❌ No auto-post channel set.",
            ephemeral=True
        )

    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return await interaction.response.send_message(
            "⚠️ Channel not found or not a text channel.",
            ephemeral=True
        )

    await interaction.response.send_message(
        f"📌 Auto-posting in: {channel.mention}",
        ephemeral=False
    )


@tree.command(name="viewsettings", description="Full server bot settings")
async def viewsettings(interaction: discord.Interaction):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(
            "❌ Cannot use in DMs.",
            ephemeral=True
        )

    guild_data = data[str(gid)]

    # channel
    channel_id = guild_data.get("post_channel")
    if channel_id:
        ch = guild.get_channel(channel_id)
        channel_text = ch.mention if isinstance(ch, discord.TextChannel) else "Invalid / deleted"
    else:
        channel_text = "Not set"

    # timezone + post time
    tz_name = guild_data.get("timezone", DEFAULT_TIMEZONE)
    hour = guild_data.get("post_hour", DEFAULT_POST_HOUR)
    minute = guild_data.get("post_minute", DEFAULT_POST_MINUTE)

    # schedule
    schedule_data: Dict[str, List[int]] = guild_data["schedule"]
    schedule_lines: List[str] = []
    if not schedule_data:
        schedule_lines.append("→ No scheduled posts.")
    else:
        for d in VALID_DAYS:
            queue = schedule_data.get(d, [])
            if queue:
                schedule_lines.append(f"• **{d}** → `{', '.join(str(x) for x in queue)}`")

    embed = discord.Embed(
        title="🔧 Server Bot Settings",
        colour=discord.Colour.blurple()
    )
    embed.add_field(name="Auto-Post Channel", value=channel_text, inline=False)
    embed.add_field(name="Timezone", value=str(tz_name), inline=False)
    embed.add_field(name="Post Time", value=f"{int(hour):02d}:{int(minute):02d}", inline=False)
    embed.add_field(
        name="Saved Messages",
        value=str(len(guild_data["messages"])),
        inline=False
    )
    embed.add_field(
        name="Weekly Schedule",
        value="\n".join(schedule_lines) if schedule_lines else "→ No scheduled posts.",
        inline=False
    )

    await interaction.response.send_message(embed=embed, ephemeral=False)


# ======================================================
# CHANNEL CONFIGURATION
# ======================================================
@tree.command(name="setschedulechannel", description="Set channel for auto-posting")
async def setschedulechannel(
    interaction: discord.Interaction,
    channel: discord.abc.GuildChannel
):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    try:
        text_ch = safe_text_channel(channel)
    except ValueError:
        return await interaction.response.send_message(
            "❌ Select a normal text channel.",
            ephemeral=True
        )

    data[str(gid)]["post_channel"] = text_ch.id
    save_data(data)

    await interaction.response.send_message(
        f"📌 Auto-post channel set to {text_ch.mention}.",
        ephemeral=True
    )


@tree.command(name="deletechannel", description="Remove configured auto-post channel")
async def deletechannel(interaction: discord.Interaction):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    data[str(gid)]["post_channel"] = None
    save_data(data)

    await interaction.response.send_message(
        "🗑 Auto-post channel removed.",
        ephemeral=True
    )


# ======================================================
# TIMEZONE / POST TIME COMMANDS
# ======================================================
@tree.command(name="settimezone", description="Set this server's timezone (IANA name like America/Vancouver)")
async def settimezone(interaction: discord.Interaction, timezone_name: str):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    tz_name = timezone_name.strip()

    if not validate_timezone(tz_name):
        msg = (
            "❌ Invalid timezone.\n"
            "Use an IANA timezone like `America/Vancouver`, `America/Edmonton`, or `UTC`."
        )
        if not tz_supported():
            msg += "\n⚠️ This host may not support timezone data (zoneinfo missing); using UTC only."
        return await interaction.response.send_message(msg, ephemeral=True)

    data[str(gid)]["timezone"] = tz_name
    save_data(data)

    await interaction.response.send_message(
        f"🌍 Timezone set to **{tz_name}**.",
        ephemeral=True
    )


@tree.command(name="setposttime", description="Set the daily auto-post time (in this server's timezone)")
async def setposttime(interaction: discord.Interaction, hour: int, minute: int):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    if hour < 0 or hour > 23:
        return await interaction.response.send_message("❌ Hour must be 0-23.", ephemeral=True)
    if minute < 0 or minute > 59:
        return await interaction.response.send_message("❌ Minute must be 0-59.", ephemeral=True)

    data[str(gid)]["post_hour"] = hour
    data[str(gid)]["post_minute"] = minute
    save_data(data)

    tz_name = get_guild_timezone(gid)
    await interaction.response.send_message(
        f"⏰ Auto-post time set to **{hour:02d}:{minute:02d}** ({tz_name}).",
        ephemeral=True
    )


# ======================================================
# TIMEZONE EXAMPLES (NEW)
# ======================================================
@tree.command(name="timezoneexamples", description="Show valid timezone examples for /settimezone")
async def timezoneexamples(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🌍 Timezone Examples",
        description=(
            "**Use with:** `/settimezone <timezone>`\n\n"
            "**🇨🇦 Canada**\n"
            "• `America/Vancouver`\n"
            "• `America/Edmonton`\n"
            "• `America/Winnipeg`\n"
            "• `America/Toronto`\n"
            "• `America/Halifax`\n"
            "• `America/St_Johns`\n\n"
            "**🇺🇸 United States**\n"
            "• `America/Los_Angeles`\n"
            "• `America/Phoenix`\n"
            "• `America/Denver`\n"
            "• `America/Chicago`\n"
            "• `America/New_York`\n\n"
            "**🇬🇧 / 🇪🇺 Europe**\n"
            "• `Europe/London`\n"
            "• `Europe/Dublin`\n"
            "• `Europe/Paris`\n"
            "• `Europe/Berlin`\n"
            "• `Europe/Amsterdam`\n\n"
            "**🌏 Asia / Oceania**\n"
            "• `Asia/Tokyo`\n"
            "• `Asia/Seoul`\n"
            "• `Asia/Singapore`\n"
            "• `Australia/Sydney`\n"
            "• `Pacific/Auckland`\n\n"
            "**🌐 Universal / Fallback**\n"
            "• `UTC`\n\n"
            "_After setting your timezone, run `/timecheck` to confirm._"
        ),
        colour=discord.Colour.blurple()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ======================================================
# HELP (COMMAND LIST)
# ======================================================
@tree.command(name="help", description="Show command list")
async def help_command(interaction: discord.Interaction):
    commands = [
        "**Messages**",
        "• `/addmessage <id> <text>`",
        "• `/addmessagepopup`",
        "• `/editmessage <id> <new_text>`",
        "• `/removemessage <id>`",
        "• `/viewmessage <id>`",
        "• `/viewmessages`",
        "",
        "**Scheduling**",
        "• `/schedule <day> <message_id>`",
        "• `/schedulelist <day>`",
        "• `/scheduleremove <day> <index>`",
        "• `/schedulemove <day> <from_index> <to_index>`",
        "• `/scheduleclear <day>`",
        "• `/removeschedule <day>`",
        "• `/viewschedule`",
        "",
        "**Posting / Settings**",
        "• `/setschedulechannel <channel>`",
        "• `/deletechannel`",
        "• `/settimezone <timezone>`",
        "• `/timezoneexamples`",
        "• `/setposttime <hour> <minute>`",
        "• `/viewchannel`",
        "• `/viewsettings`",
        "• `/postnow <message_id>`",
        "• `/timecheck`",
        "",
        "**Admin**",
        "• `/clearall` (Admin only)"
    ]

    embed = discord.Embed(
        title="🤖 Bot Commands",
        description="\n".join(commands),
        colour=discord.Colour.blurple()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ======================================================
# CLEARALL (ADMIN ONLY)
# ======================================================
@tree.command(name="clearall", description="Delete ALL messages & schedules (Admin only)")
async def clearall(interaction: discord.Interaction):
    member = safe_member(interaction)
    if not member.guild_permissions.administrator:
        return await interaction.response.send_message(
            "❌ Admin only.",
            ephemeral=True
        )

    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    data[str(gid)]["messages"] = {}
    data[str(gid)]["schedule"] = {}
    data[str(gid)]["post_channel"] = None
    data[str(gid)]["timezone"] = DEFAULT_TIMEZONE
    data[str(gid)]["post_hour"] = DEFAULT_POST_HOUR
    data[str(gid)]["post_minute"] = DEFAULT_POST_MINUTE
    save_data(data)

    await interaction.response.send_message(
        "🧨 All bot data cleared for this server.",
        ephemeral=True
    )


# ======================================================
# POST NOW
# ======================================================
@tree.command(name="postnow", description="Post a message immediately in this channel")
async def postnow(interaction: discord.Interaction, message_id: int):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    guild_data = data[str(gid)]
    msg = guild_data["messages"].get(str(message_id))
    if not msg:
        return await interaction.response.send_message(
            "❌ Message not found.",
            ephemeral=True
        )

    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        return await interaction.response.send_message(
            "❌ Can't post in this channel type.",
            ephemeral=True
        )

    for part in split_message(msg):
        await channel.send(part)

    await interaction.response.send_message(
        "✔ Message posted.",
        ephemeral=True
    )


# ======================================================
# TIME CHECK (UPDATED)
# ======================================================
@tree.command(name="timecheck", description="Show UTC time, local server time, and next auto-post")
async def timecheck(interaction: discord.Interaction):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    now_utc = datetime.now(timezone.utc)

    tz_name = get_guild_timezone(gid)
    tzinfo = get_tzinfo(tz_name)

    now_local = now_utc.astimezone(tzinfo)
    hour, minute = get_guild_post_time(gid)

    nxt = next_run_local(now_local, hour, minute)

    await interaction.response.send_message(
        "🕒 **Time Check**\n"
        f"• **UTC:** {now_utc.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"• **Server Local ({tz_name}):** {now_local.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"• **Auto-post at:** {hour:02d}:{minute:02d} ({tz_name})\n"
        f"• **Next run:** {nxt.strftime('%Y-%m-%d %H:%M:%S')} ({tz_name})",
        ephemeral=True
    )


# ======================================================
# AUTO POST LOOP (PER-GUILD LOCAL TIME)
# ======================================================
@tasks.loop(minutes=1)
async def autopost():
    now_utc = datetime.now(timezone.utc)

    for gid, server in data.items():
        # Safety/migration
        try:
            ensure_guild(int(gid))
        except Exception:
            continue

        channel_id = server.get("post_channel")
        if not isinstance(channel_id, int):
            continue

        tz_name = server.get("timezone", DEFAULT_TIMEZONE)
        if not isinstance(tz_name, str) or not tz_name:
            tz_name = DEFAULT_TIMEZONE

        tzinfo = get_tzinfo(tz_name)
        now_local = now_utc.astimezone(tzinfo)

        hour = server.get("post_hour", DEFAULT_POST_HOUR)
        minute = server.get("post_minute", DEFAULT_POST_MINUTE)
        try:
            hour = int(hour)
            minute = int(minute)
        except Exception:
            hour, minute = DEFAULT_POST_HOUR, DEFAULT_POST_MINUTE

        if now_local.hour != hour or now_local.minute != minute:
            continue

        today = now_local.strftime("%A")

        schedule_data = server.get("schedule", {})
        queue = schedule_data.get(today)
        if not isinstance(queue, list) or not queue:
            continue

        guild = bot.get_guild(int(gid))
        if guild is None:
            continue

        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            continue

        messages_map = server.get("messages", {})
        if not isinstance(messages_map, dict):
            continue

        for mid in queue:
            msg_text = messages_map.get(str(mid))
            if not isinstance(msg_text, str):
                continue
            for part in split_message(msg_text):
                await channel.send(part)


@autopost.before_loop
async def before_autopost():
    await bot.wait_until_ready()


# ======================================================
# BOT READY
# ======================================================
@bot.event
async def on_ready():
    print("✅ Bot is online!")
    await tree.sync()
    autopost.start()


# ======================================================
# START BOT
# ======================================================
if __name__ == "__main__":
    bot.run(load_token())# CONFIG
# ======================================================
POST_HOUR_UTC = 8
POST_MINUTE_UTC = 25
DATA_FILE = "serverdata.json"
VALID_DAYS = [
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday"
]


# ======================================================
# SAFE HELPERS
# ======================================================
def safe_guild_id(interaction: discord.Interaction) -> int:
    """Ensure command is used inside a guild."""
    if interaction.guild_id is None:
        raise ValueError("This command cannot be used in DMs.")
    return interaction.guild_id


def safe_member(interaction: discord.Interaction) -> discord.Member:
    """Ensure interaction.user is a Member."""
    if not isinstance(interaction.user, discord.Member):
        raise ValueError("This command cannot be used in DMs.")
    return interaction.user


def safe_text_channel(channel: discord.abc.GuildChannel) -> discord.TextChannel:
    """Ensure channel is a normal text channel."""
    if not isinstance(channel, discord.TextChannel):
        raise ValueError("You must choose a standard text channel.")
    return channel


# ======================================================
# AUTO-SPLIT LONG MESSAGES
# ======================================================
def split_message(text: str, limit: int = 2000) -> List[str]:
    chunks: List[str] = []
    while len(text) > limit:
        split_point = text.rfind("\n", 0, limit)
        if split_point == -1:
            split_point = limit
        chunks.append(text[:split_point])
        text = text[split_point:]
    chunks.append(text)
    return chunks


# ======================================================
# LOAD / SAVE DATA
# ======================================================
def load_data() -> Dict[str, Any]:
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_data(data: Dict[str, Any]) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


data: Dict[str, Any] = load_data()


# ======================================================
# GUILD INITIALIZER (migration + safety)
# schedule[day] is ALWAYS a list[int]
# ======================================================
def ensure_guild(guild_id: int) -> None:
    gid = str(guild_id)

    # Create new guild entry
    if gid not in data:
        data[gid] = {
            "messages": {},
            "schedule": {},      # day -> list[int]
            "post_channel": None
        }
        save_data(data)
        return

    g = data[gid]

    # Messages always dict
    if "messages" not in g or not isinstance(g["messages"], dict):
        g["messages"] = {}

    # Schedule always dict[day] -> list[int]
    sched = g.get("schedule")
    if not isinstance(sched, dict):
        sched = {}
    fixed_sched: Dict[str, List[int]] = {}
    for day, val in sched.items():
        if isinstance(val, int):
            fixed_sched[day] = [val]
        elif isinstance(val, list):
            cleaned = [int(x) for x in val if isinstance(x, int)]
            if cleaned:
                fixed_sched[day] = cleaned
    g["schedule"] = fixed_sched

    # post_channel always present
    if "post_channel" not in g:
        g["post_channel"] = None

    save_data(data)


# ======================================================
# BOT SETUP
# ======================================================
intents = discord.Intents.default()
intents.message_content = False  # using slash commands only

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


# ======================================================
# ADD MESSAGE COMMANDS
# ======================================================
@tree.command(name="addmessage", description="Save a message to the bot")
async def addmessage(interaction: discord.Interaction, message_id: int, text: str):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    data[str(gid)]["messages"][str(message_id)] = text
    save_data(data)

    await interaction.response.send_message(
        f"✔ Message {message_id} saved.",
        ephemeral=True
    )


@tree.command(name="addmessagefile", description="Upload a .txt file as a message")
async def addmessagefile(
    interaction: discord.Interaction,
    message_id: int,
    file: discord.Attachment
):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    if not file.filename.endswith(".txt"):
        return await interaction.response.send_message(
            "❌ Only .txt files allowed.",
            ephemeral=True
        )

    content_bytes = await file.read()
    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return await interaction.response.send_message(
            "❌ Could not decode file as UTF-8 text.",
            ephemeral=True
        )

    data[str(gid)]["messages"][str(message_id)] = content
    save_data(data)

    await interaction.response.send_message(
        f"📁 File saved as message {message_id}.",
        ephemeral=True
    )


# Popup modal for multiline message
class AddMessageModal(discord.ui.Modal, title="Add Multi-Line Message"):
    text = discord.ui.TextInput(
        label="Message",
        style=discord.TextStyle.paragraph,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        gid = safe_guild_id(interaction)
        ensure_guild(gid)

        messages: Dict[str, str] = data[str(gid)]["messages"]
        new_id = str(max([int(i) for i in messages] + [0]) + 1)

        data[str(gid)]["messages"][new_id] = str(self.text)
        save_data(data)

        await interaction.response.send_message(
            f"✔ Saved as message {new_id}.",
            ephemeral=True
        )


@tree.command(name="addmessagepopup", description="Add a multiline message using a popup")
async def addmessagepopup(interaction: discord.Interaction):
    await interaction.response.send_modal(AddMessageModal())


# ======================================================
# EDIT / REMOVE MESSAGE
# ======================================================
@tree.command(name="editmessage", description="Edit a saved message")
async def editmessage(
    interaction: discord.Interaction,
    message_id: int,
    new_text: str
):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    if str(message_id) not in data[str(gid)]["messages"]:
        return await interaction.response.send_message(
            "❌ Message not found.",
            ephemeral=True
        )

    data[str(gid)]["messages"][str(message_id)] = new_text
    save_data(data)

    await interaction.response.send_message(
        f"✏ Updated message {message_id}.",
        ephemeral=True
    )


@tree.command(name="removemessage", description="Delete a saved message")
async def removemessage(interaction: discord.Interaction, message_id: int):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    if str(message_id) not in data[str(gid)]["messages"]:
        return await interaction.response.send_message(
            "❌ Message not found.",
            ephemeral=True
        )

    del data[str(gid)]["messages"][str(message_id)]
    save_data(data)

    await interaction.response.send_message(
        f"🗑 Message {message_id} deleted.",
        ephemeral=True
    )


# ======================================================
# VIEW MESSAGE COMMANDS
# ======================================================
@tree.command(name="viewmessage", description="View a specific message by ID")
async def viewmessage(interaction: discord.Interaction, message_id: int):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    msg = data[str(gid)]["messages"].get(str(message_id))
    if not msg:
        return await interaction.response.send_message(
            "❌ Message not found.",
            ephemeral=True
        )

    parts = split_message(msg)

    await interaction.response.send_message(
        f"📄 **Message {message_id}:**",
        ephemeral=True
    )
    for p in parts:
        await interaction.followup.send(p, ephemeral=True)


@tree.command(name="viewmessages", description="View all saved messages")
async def viewmessages(interaction: discord.Interaction):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    msgs = data[str(gid)]["messages"]
    if not msgs:
        return await interaction.response.send_message(
            "No saved messages.",
            ephemeral=True
        )

    desc_lines = []
    for mid, txt in msgs.items():
        desc_lines.append(f"`{mid}` – {len(txt)} chars")

    embed = discord.Embed(
        title="🗂️ Saved Messages",
        description="\n".join(desc_lines),
        colour=discord.Colour.blurple()
    )

    await interaction.response.send_message(embed=embed, ephemeral=False)


# ======================================================
# ADVANCED SCHEDULER (MULTI-MESSAGE PER DAY)
# ======================================================
@tree.command(name="schedule", description="Add a message to a day's schedule (append)")
async def schedule(
    interaction: discord.Interaction,
    day: str,
    message_id: int
):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    if day not in VALID_DAYS:
        return await interaction.response.send_message(
            "❌ Invalid day. Use Monday, Tuesday, etc.",
            ephemeral=True
        )

    guild_data = data[str(gid)]
    if str(message_id) not in guild_data["messages"]:
        return await interaction.response.send_message(
            "❌ Message ID does not exist.",
            ephemeral=True
        )

    schedule_data: Dict[str, List[int]] = guild_data["schedule"]
    current = schedule_data.get(day, [])
    current.append(message_id)
    schedule_data[day] = current
    save_data(data)

    await interaction.response.send_message(
        f"📅 Added message `{message_id}` to **{day}** queue position {len(current)}.",
        ephemeral=True
    )


@tree.command(name="schedulelist", description="View ordered schedule for a specific day")
async def schedulelist(interaction: discord.Interaction, day: str):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    if day not in VALID_DAYS:
        return await interaction.response.send_message(
            "❌ Invalid day.",
            ephemeral=True
        )

    guild_data = data[str(gid)]
    schedule_data: Dict[str, List[int]] = guild_data["schedule"]
    queue = schedule_data.get(day, [])

    if not queue:
        return await interaction.response.send_message(
            f"📅 No messages scheduled for **{day}**.",
            ephemeral=True
        )

    lines = []
    for idx, mid in enumerate(queue, start=1):
        lines.append(f"**{idx}.** Message `{mid}`")

    embed = discord.Embed(
        title=f"📅 {day} Schedule",
        description="\n".join(lines),
        colour=discord.Colour.blurple()
    )

    await interaction.response.send_message(embed=embed, ephemeral=False)


@tree.command(name="scheduleremove", description="Remove a message by index from a day's schedule")
async def scheduleremove(
    interaction: discord.Interaction,
    day: str,
    index: int
):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    if day not in VALID_DAYS:
        return await interaction.response.send_message(
            "❌ Invalid day.",
            ephemeral=True
        )

    guild_data = data[str(gid)]
    schedule_data: Dict[str, List[int]] = guild_data["schedule"]
    queue = schedule_data.get(day, [])

    if not queue:
        return await interaction.response.send_message(
            f"❌ No schedule for **{day}**.",
            ephemeral=True
        )

    if index < 1 or index > len(queue):
        return await interaction.response.send_message(
            f"❌ Index must be between 1 and {len(queue)}.",
            ephemeral=True
        )

    removed = queue.pop(index - 1)
    if queue:
        schedule_data[day] = queue
    else:
        del schedule_data[day]

    save_data(data)

    await interaction.response.send_message(
        f"🗑 Removed message `{removed}` from **{day}** at position {index}.",
        ephemeral=True
    )


@tree.command(name="schedulemove", description="Reorder items in a day's schedule")
async def schedulemove(
    interaction: discord.Interaction,
    day: str,
    from_index: int,
    to_index: int
):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    if day not in VALID_DAYS:
        return await interaction.response.send_message(
            "❌ Invalid day.",
            ephemeral=True
        )

    guild_data = data[str(gid)]
    schedule_data: Dict[str, List[int]] = guild_data["schedule"]
    queue = schedule_data.get(day, [])

    if not queue:
        return await interaction.response.send_message(
            f"❌ No schedule for **{day}**.",
            ephemeral=True
        )

    n = len(queue)
    if from_index < 1 or from_index > n or to_index < 1 or to_index > n:
        return await interaction.response.send_message(
            f"❌ Indexes must be between 1 and {n}.",
            ephemeral=True
        )

    item = queue.pop(from_index - 1)
    queue.insert(to_index - 1, item)
    schedule_data[day] = queue
    save_data(data)

    await interaction.response.send_message(
        f"🔁 Moved message `{item}` from position {from_index} to {to_index} on **{day}**.",
        ephemeral=True
    )


@tree.command(name="scheduleclear", description="Clear all scheduled messages for a day")
async def scheduleclear(interaction: discord.Interaction, day: str):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    if day not in VALID_DAYS:
        return await interaction.response.send_message(
            "❌ Invalid day.",
            ephemeral=True
        )

    guild_data = data[str(gid)]
    schedule_data: Dict[str, List[int]] = guild_data["schedule"]

    if day not in schedule_data:
        return await interaction.response.send_message(
            f"❌ No schedule for **{day}**.",
            ephemeral=True
        )

    del schedule_data[day]
    save_data(data)

    await interaction.response.send_message(
        f"🧹 Cleared schedule for **{day}**.",
        ephemeral=True
    )


@tree.command(name="removeschedule", description="(Alias) Clear all messages for a day")
async def removeschedule(interaction: discord.Interaction, day: str):
    await scheduleclear.callback(interaction, day=day)  # type: ignore


# ======================================================
# VIEWSCHEDULE / VIEWSETTINGS
# ======================================================
@tree.command(name="viewschedule", description="View weekly schedule summary")
async def viewschedule(interaction: discord.Interaction):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    guild_data = data[str(gid)]
    schedule_data: Dict[str, List[int]] = guild_data["schedule"]

    lines: List[str] = []
    for d in VALID_DAYS:
        queue = schedule_data.get(d, [])
        if queue:
            ids = ", ".join(str(mid) for mid in queue)
            lines.append(f"**{d}** → `{ids}`")
        else:
            lines.append(f"**{d}** → *(none)*")

    embed = discord.Embed(
        title="📅 Weekly Schedule",
        description="\n".join(lines),
        colour=discord.Colour.blurple()
    )

    await interaction.response.send_message(embed=embed, ephemeral=False)


@tree.command(name="viewchannel", description="Show configured auto-post channel")
async def viewchannel(interaction: discord.Interaction):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(
            "❌ Cannot be used in DMs.",
            ephemeral=True
        )

    guild_data = data[str(gid)]
    channel_id = guild_data.get("post_channel")
    if channel_id is None:
        return await interaction.response.send_message(
            "❌ No auto-post channel set.",
            ephemeral=True
        )

    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return await interaction.response.send_message(
            "⚠️ Channel not found or not a text channel.",
            ephemeral=True
        )

    await interaction.response.send_message(
        f"📌 Auto-posting in: {channel.mention}",
        ephemeral=False
    )


@tree.command(name="viewsettings", description="Full server bot settings")
async def viewsettings(interaction: discord.Interaction):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(
            "❌ Cannot use in DMs.",
            ephemeral=True
        )

    guild_data = data[str(gid)]

    # channel
    channel_id = guild_data.get("post_channel")
    if channel_id:
        ch = guild.get_channel(channel_id)
        channel_text = ch.mention if isinstance(ch, discord.TextChannel) else "Invalid / deleted"
    else:
        channel_text = "Not set"

    # schedule
    schedule_data: Dict[str, List[int]] = guild_data["schedule"]
    schedule_lines: List[str] = []
    if not schedule_data:
        schedule_lines.append("→ No scheduled posts.")
    else:
        for d in VALID_DAYS:
            queue = schedule_data.get(d, [])
            if queue:
                schedule_lines.append(f"• **{d}** → `{', '.join(str(x) for x in queue)}`")

    embed = discord.Embed(
        title="🔧 Server Bot Settings",
        colour=discord.Colour.blurple()
    )
    embed.add_field(name="Auto-Post Channel", value=channel_text, inline=False)
    embed.add_field(
        name="Saved Messages",
        value=str(len(guild_data["messages"])),
        inline=False
    )
    embed.add_field(
        name="Weekly Schedule",
        value="\n".join(schedule_lines) if schedule_lines else "→ No scheduled posts.",
        inline=False
    )

    await interaction.response.send_message(embed=embed, ephemeral=False)


# ======================================================
# CHANNEL CONFIGURATION
# ======================================================
@tree.command(name="setschedulechannel", description="Set channel for auto-posting")
async def setschedulechannel(
    interaction: discord.Interaction,
    channel: discord.abc.GuildChannel
):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    try:
        text_ch = safe_text_channel(channel)
    except ValueError:
        return await interaction.response.send_message(
            "❌ Select a normal text channel.",
            ephemeral=True
        )

    data[str(gid)]["post_channel"] = text_ch.id
    save_data(data)

    await interaction.response.send_message(
        f"📌 Auto-post channel set to {text_ch.mention}.",
        ephemeral=True
    )


@tree.command(name="deletechannel", description="Remove configured auto-post channel")
async def deletechannel(interaction: discord.Interaction):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    data[str(gid)]["post_channel"] = None
    save_data(data)

    await interaction.response.send_message(
        "🗑 Auto-post channel removed.",
        ephemeral=True
    )


# ======================================================
# CLEARALL (ADMIN ONLY)
# ======================================================
@tree.command(name="clearall", description="Delete ALL messages & schedules (Admin only)")
async def clearall(interaction: discord.Interaction):
    member = safe_member(interaction)
    if not member.guild_permissions.administrator:
        return await interaction.response.send_message(
            "❌ Admin only.",
            ephemeral=True
        )

    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    data[str(gid)]["messages"] = {}
    data[str(gid)]["schedule"] = {}
    data[str(gid)]["post_channel"] = None
    save_data(data)

    await interaction.response.send_message(
        "🧨 All bot data cleared for this server.",
        ephemeral=True
    )


# ======================================================
# POST NOW
# ======================================================
@tree.command(name="postnow", description="Post a message immediately in this channel")
async def postnow(interaction: discord.Interaction, message_id: int):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    guild_data = data[str(gid)]
    msg = guild_data["messages"].get(str(message_id))
    if not msg:
        return await interaction.response.send_message(
            "❌ Message not found.",
            ephemeral=True
        )

    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        return await interaction.response.send_message(
            "❌ Can't post in this channel type.",
            ephemeral=True
        )

    for part in split_message(msg):
        await channel.send(part)

    await interaction.response.send_message(
        "✔ Message posted.",
        ephemeral=True
    )


# ======================================================
# TIME CHECK
# ======================================================
@tree.command(name="timecheck", description="Show UTC time and posting time")
async def timecheck(interaction: discord.Interaction):
    now = datetime.now(timezone.utc)
    await interaction.response.send_message(
        f"🕒 **UTC Time:** {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"🕒 **Auto-posts at:** {POST_HOUR_UTC:02d}:{POST_MINUTE_UTC:02d}",
        ephemeral=True
    )


# ======================================================
# AUTO POST LOOP (MULTI-MESSAGE PER DAY)
# ======================================================
@tasks.loop(minutes=1)
async def autopost():
    now = datetime.now(timezone.utc)
    if now.hour != POST_HOUR_UTC or now.minute != POST_MINUTE_UTC:
        return

    today = now.strftime("%A")

    for gid, server in data.items():
        channel_id = server.get("post_channel")
        if not isinstance(channel_id, int):
            continue

        schedule_data = server.get("schedule", {})
        queue = schedule_data.get(today)
        if not isinstance(queue, list) or not queue:
            continue

        guild = bot.get_guild(int(gid))
        if guild is None:
            continue

        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            continue

        messages_map = server.get("messages", {})
        for mid in queue:
            msg_text = messages_map.get(str(mid))
            if not isinstance(msg_text, str):
                continue
            for part in split_message(msg_text):
                await channel.send(part)


@autopost.before_loop
async def before_autopost():
    await bot.wait_until_ready()


# ======================================================
# BOT READY
# ======================================================
@bot.event
async def on_ready():
    print("✅ Bot is online!")
    await tree.sync()
    autopost.start()


# ======================================================
# START BOT
# ======================================================
if __name__ == "__main__":
    bot.run(load_token())
