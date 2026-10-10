# ==========================================
#   Discord Music Bot
#   Created & Maintained by Bikio
#   GitHub: https://github.com/bikiogirl
# ==========================================
import asyncio
import os

import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
print('done !')

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

sp = None
if os.getenv("SPOTIFY_CLIENT_ID") and os.getenv("SPOTIFY_CLIENT_SECRET"):
    sp = spotipy.Spotify(
        auth_manager=SpotifyClientCredentials(
            client_id=os.getenv("SPOTIFY_CLIENT_ID"),
            client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
        )
    )

YTDL_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "default_search": "ytsearch",
}
FFMPEG_OPTS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}


class MusicBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())
        self.queues: dict[int, list[dict]] = {}

    async def setup_hook(self):
        await self.tree.sync()  # register the slash commands


bot = MusicBot()


def _extract(query: str) -> dict:
    with yt_dlp.YoutubeDL(YTDL_OPTS) as ydl:
        info = ydl.extract_info(query, download=False)
        if "entries" in info:
            info = info["entries"][0]
        return {
            "title": info["title"],
            "url": info["url"],
            "link": info.get("webpage_url", ""),
        }


def get_spotify_queries(url: str) -> list[str]:
    """Turns a Spotify link into a list of searches like 'Artist - Title'."""
    if sp is None:
        raise RuntimeError("no spotify keys")

    def fmt(t):
        return f"{t['artists'][0]['name']} - {t['name']}"

    if "/track/" in url:
        return [fmt(sp.track(url))]
    if "/album/" in url:
        album = sp.album(url)
        artist = album["artists"][0]["name"]
        return [f"{artist} - {t['name']}" for t in album["tracks"]["items"]][:50]
    if "/playlist/" in url:
        items = sp.playlist_items(url, limit=50)["items"]
        return [fmt(i["track"]) for i in items if i.get("track")]
    raise ValueError("unsupported spotify link")


async def play_next(guild: discord.Guild):
    vc = guild.voice_client
    queue = bot.queues.get(guild.id, [])
    while vc and queue:
        song = queue.pop(0)
        try:
            if "url" not in song:  # Spotify track: search it on YouTube now
                song = await asyncio.get_running_loop().run_in_executor(
                    None, _extract, song["query"]
                )
        except Exception:
            continue  # if it can't be found, move on to the next one
        source = discord.FFmpegPCMAudio(song["url"], **FFMPEG_OPTS)
        vc.play(
            source,
            after=lambda e: asyncio.run_coroutine_threadsafe(
                play_next(guild), bot.loop
            ),
        )
        return


@bot.tree.command(name="play", description="Play a song (name or link)")
@app_commands.describe(song="Song name, YouTube link or Spotify link")
async def play(interaction: discord.Interaction, song: str):
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message(
            "Join a voice channel first! 🎧", ephemeral=True
        )
        return

    await interaction.response.defer()
    channel = interaction.user.voice.channel
    vc = interaction.guild.voice_client
    if vc is None:
        vc = await channel.connect()
    elif vc.channel != channel:
        await vc.move_to(channel)

    loop = asyncio.get_running_loop()
    try:
        if "open.spotify.com" in song:
            queries = await loop.run_in_executor(None, get_spotify_queries, song)
            tracks = [{"query": q, "title": q} for q in queries]
        else:
            tracks = [await loop.run_in_executor(None, _extract, song)]
    except RuntimeError:
        await interaction.followup.send(
            "Spotify isn't set up (SPOTIFY_CLIENT_ID/SECRET are missing)."
        )
        return
    except Exception:
        await interaction.followup.send("I couldn't find that song 😕")
        return

    if not tracks:
        await interaction.followup.send("I couldn't find any songs at that link 😕")
        return

    bot.queues.setdefault(interaction.guild.id, []).extend(tracks)
    was_idle = not (vc.is_playing() or vc.is_paused())
    if was_idle:
        await play_next(interaction.guild)

    if len(tracks) > 1:
        await interaction.followup.send(f"➕ Added {len(tracks)} songs to the queue")
    elif was_idle:
        await interaction.followup.send(f"🎶 Now playing: **{tracks[0]['title']}**")
    else:
        await interaction.followup.send(f"➕ Added to the queue: **{tracks[0]['title']}**")


@bot.tree.command(name="skip", description="Skip to the next song")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()  # the after= callback plays the next song automatically
        await interaction.response.send_message("⏭️ Skipped")
    else:
        await interaction.response.send_message("Nothing is playing.", ephemeral=True)


@bot.tree.command(name="pause", description="Pause the music")
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message("⏸️ Paused")
    else:
        await interaction.response.send_message("Nothing is playing.", ephemeral=True)


@bot.tree.command(name="resume", description="Resume the music")
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶️ Resumed")
    else:
        await interaction.response.send_message("The music isn't paused.", ephemeral=True)


@bot.tree.command(name="queue", description="Show the queue")
async def show_queue(interaction: discord.Interaction):
    queue = bot.queues.get(interaction.guild.id, [])
    if not queue:
        await interaction.response.send_message("The queue is empty.")
        return
    lines = [f"{i}. {s['title']}" for i, s in enumerate(queue[:10], 1)]
    await interaction.response.send_message("📜 **Up next:**\n" + "\n".join(lines))


@bot.tree.command(name="stop", description="Stop the music and leave the call")
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    bot.queues[interaction.guild.id] = []
    if vc:
        await vc.disconnect()
        await interaction.response.send_message("👋 See you!")
    else:
        await interaction.response.send_message("I'm not in a call.", ephemeral=True)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

bot.run('')
