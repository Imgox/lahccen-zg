import os
from dotenv import load_dotenv
import discord
from discord.ext import commands, tasks
import yt_dlp as youtube_dl  # For playing music from YouTube
import aiohttp  # For HTTP requests (e.g., checking Kick.com status)
import ssl
import certifi

# Load environment variables from .env file
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Set up intents
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

# Create a custom Bot subclass so we can set up the connector after the loop starts
class MyBot(commands.Bot):
    async def setup_hook(self):
        # Now that the event loop is running, create the SSL context and connector
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        self.http.connector = connector

bot = MyBot(command_prefix='!', intents=intents)

# ----------------- On Ready ----------------- #
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    check_streamer_status.start()

# ----------------- Moderation Commands ----------------- #
@bot.command()
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason=None):
    try:
        await member.kick(reason=reason)
        await ctx.send(f'Kicked {member.mention} for reason: {reason}')
    except Exception as e:
        await ctx.send("Failed to kick the member.")
        print(e)

# ----------------- Reaction Roles ----------------- #
TARGET_MESSAGE_ID = 123456789012345678  # Replace with your message ID  
ROLE_NAME = 'Subscriber'

@bot.event
async def on_raw_reaction_add(payload):
    if payload.message_id == TARGET_MESSAGE_ID:
        guild = bot.get_guild(payload.guild_id)
        role = discord.utils.get(guild.roles, name=ROLE_NAME)
        if role:
            member = guild.get_member(payload.user_id)
            if member:
                await member.add_roles(role)
                print(f"Assigned {role.name} to {member.display_name}")

# yt-dlp options
ytdl_opts = {
    'format': 'bestaudio',
    'noplaylist': True,
    'quiet': True,
}

# ----------------- Interactive Selection View ----------------- #
class SelectionButton(discord.ui.Button):
    def __init__(self, label: str, index: int):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        # Ensure that only the command author can interact.
        view: SelectionView = self.view
        if interaction.user.id != view.author.id:
            await interaction.response.send_message("These buttons aren’t for you!", ephemeral=True)
            return

        view.selected = self.index
        view.stop()  # Stop listening for more interactions.
        await interaction.response.send_message(f"You selected option **{self.label}**.", ephemeral=True)

class SelectionView(discord.ui.View):
    def __init__(self, results: list, author: discord.User, timeout: int = 60):
        super().__init__(timeout=timeout)
        self.results = results  # List of video entries
        self.author = author
        self.selected = None
        # Dynamically add a button for each search result.
        for i in range(len(results)):
            self.add_item(SelectionButton(label=str(i+1), index=i))

    async def on_timeout(self):
        # Called when the view times out.
        if self.selected is None:
            for child in self.children:
                child.disabled = True

# ----------------- Play Command with YouTube Search ----------------- #
@bot.command()
async def play(ctx, *, query: str):
    if not ctx.author.voice:
        await ctx.send("You must be in a voice channel to play music.")
        return

    # Connect to the user's voice channel.
    voice_channel = ctx.author.voice.channel
    voice_client = ctx.voice_client
    if voice_client is None:
        voice_client = await voice_channel.connect()

    # Use yt-dlp's search feature to get top 6 results.
    with youtube_dl.YoutubeDL(ytdl_opts) as ytdl:
        try:
            search_query = f"ytsearch6:{query}"
            search_result = ytdl.extract_info(search_query, download=False)
            entries = search_result.get('entries')
            if not entries:
                await ctx.send("No results found.")
                return
        except Exception as e:
            await ctx.send("An error occurred while searching.")
            print(e)
            return

    # Build an embed listing the top 6 results.
    description = ""
    for i, entry in enumerate(entries):
        title = entry.get('title', 'Unknown Title')
        duration = entry.get('duration')
        minutes = duration // 60 if duration else 0
        seconds = duration % 60 if duration else 0
        description += f"**{i+1}.** {title} [{minutes}:{seconds:02d}]\n"
    embed = discord.Embed(title="YouTube Search Results", description=description, color=discord.Color.blue())
    embed.set_footer(text="Click one of the buttons below to choose your track.")

    # Create and send the interactive view.
    view = SelectionView(entries, ctx.author)
    message = await ctx.send(embed=embed, view=view)
    view.message = message  # Save the message reference for potential edits.

    # Wait for the user to make a selection or time out.
    await view.wait()

    # If no selection was made, exit.
    if view.selected is None:
        await ctx.send("No selection was made in time. Please try again.")
        return

    # Retrieve the chosen entry.
    chosen_entry = entries[view.selected]
    # To get a reliable stream URL, re-extract full info using the webpage_url.
    with youtube_dl.YoutubeDL(ytdl_opts) as ytdl:
        try:
            full_info = ytdl.extract_info(chosen_entry['webpage_url'], download=False)
            stream_url = full_info['formats'][0]['url']
        except Exception as e:
            await ctx.send("Failed to retrieve stream URL.")
            print(e)
            return

    # Prepare the audio source and play the track.
    try:
        source = await discord.FFmpegOpusAudio.from_probe(stream_url)
    except Exception as e:
        await ctx.send("An error occurred while processing the audio.")
        print(e)
        return

    if voice_client.is_playing():
        voice_client.stop()
    voice_client.play(source)
    await ctx.send(f"Now playing: **{chosen_entry.get('title', 'Unknown Title')}**")

# ----------------- Kick.com Stream Monitor ----------------- #
STREAMER_KICK_URL = "https://kick.com/api/streamer/status"  # Placeholder URL
NOTIFICATION_CHANNEL_ID = 987654321098765432  # Replace with your channel ID

@tasks.loop(minutes=1)
async def check_streamer_status():
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(STREAMER_KICK_URL) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("is_live"):
                        channel = bot.get_channel(NOTIFICATION_CHANNEL_ID)
                        if channel:
                            await channel.send("The streamer is now live on Kick.com!")
        except Exception as e:
            print(f"Error checking stream status: {e}")

# ----------------- Run Bot ----------------- #
bot.run(DISCORD_TOKEN)