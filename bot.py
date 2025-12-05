import discord
from discord.ext import tasks
from discord import app_commands
import json
import os
from datetime import datetime, timezone
from typing import Dict, Any, List

from Keep_Alive import keep_alive  # Replit keep-alive


# ======================================================
# CONFIG
# ======================================================
POST_HOUR_UTC = 8
POST_MINUTE_UTC = 25
DATA_FILE = "serverdata.json"
VALID_DAYS = [
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday"
]


# ======================================================
# SAFE HELPERS (Fix Optional / None issues)
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
# TOKEN LOADER
# ======================================================
def load_token() -> str:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        print("❌ ERROR: DISCORD_BOT_TOKEN missing in Replit Secrets!")
        raise SystemExit
    return token


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
# GUILD INITIALIZER (with migration & KeyError fix)
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
    # Migrate any old single-int schedules to lists
    fixed_sched: Dict[str, List[int]] = {}
    for day, val in sched.items():
        if isinstance(val, int):
            fixed_sched[day] = [val]
        elif isinstance(val, list):
            # keep only ints
            cleaned = [int(x) for x in val if isinstance(x, int)]
            if cleaned:
                fixed_sched[day] = cleaned
        else:
            # ignore weird stuff
            continue
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


# Popup modal
class AddMessageModal(discord.ui.Modal, title="Add Multi-Line Message"):
    text = discord.ui.TextInput(label="Message", style=discord.TextStyle.paragraph)

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

    guild_data = data[str(gid)]
    if str(message_id) not in guild_data["messages"]:
        return await interaction.response.send_message(
            "❌ Message not found.",
            ephemeral=True
        )

    guild_data["messages"][str(message_id)] = new_text
    save_data(data)

    await interaction.response.send_message(
        f"✏ Updated message {message_id}.",
        ephemeral=True
    )


@tree.command(name="removemessage", description="Delete a saved message")
async def removemessage(interaction: discord.Interaction, message_id: int):
    gid = safe_guild_id(interaction)
    ensure_guild(gid)

    guild_data = data[str(gid)]
    if str(message_id) not in guild_data["messages"]:
        return await interaction.response.send_message(
            "❌ Message not found.",
            ephemeral=True
        )

    del guild_data["messages"][str(message_id)]
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

    guild_data = data[str(gid)]
    msg = guild_data["messages"].get(str(message_id))
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
    messages = guild_data["messages"]
    if str(message_id) not in messages:
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
        # if empty, remove the day entirely
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


# Backwards-compatible alias:
@tree.command(name="removeschedule", description="(Alias) Clear all messages for a day")
async def removeschedule(interaction: discord.Interaction, day: str):
    await scheduleclear.callback(interaction, day=day)  # type: ignore


# ======================================================
# VIEWSCHEDULE / VIEWSETTINGS (multi-message aware)
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
keep_alive()
bot.run(load_token())
