import sys
import twitchio
from twitchio.ext import commands
import pyttsx3
import random
import re
import threading
import yt_dlp
import os
import http.server
import socketserver
import webbrowser
import asyncio
import json
import configparser
import websockets

# Initialize TTS engine
engine = pyttsx3.init()

# Get available voices
voices = engine.getProperty('voices')

# Set up two different voices (you can adjust these indices based on available voices)
voice1 = voices[0].id  # Usually a male voice
voice2 = voices[1].id  # Usually a female voice

# Initialize the song queue
song_queue = []

# YouTube URL Regex pattern for validating links
yt_url_pattern = re.compile(r'(https?://)?(www.)?(youtube|youtu|youtube-nocookie).(com|be)/.+')
yt_playlist_pattern = re.compile(r'(https?://)?(www\.)?youtube\.com/playlist\?list=.+')

# WebSocket server port
WS_PORT = 8080


class PersistentHTTPServer:
    def __init__(self, initial_port=8000):
        self.initial_port = initial_port
        self.port = initial_port
        self.httpd = None
        self.server_thread = None

    class MyHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/queue.json':
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(song_queue).encode())
            else:
                super().do_GET()

    def start(self):
        if self.httpd is None:
            for port in range(self.initial_port, self.initial_port + 100):
                try:
                    self.httpd = socketserver.TCPServer(("", port), self.MyHandler)
                    self.port = port
                    break
                except OSError:
                    print(f"Port {port} is in use, trying next...")

            if self.httpd is None:
                raise Exception("Unable to find an available port")

            self.server_thread = threading.Thread(target=self.httpd.serve_forever)
            self.server_thread.start()
            print(f"Serving at http://localhost:{self.port}")

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.server_thread.join()
            self.httpd = None
            self.server_thread = None
            print("HTTP server stopped")


# Create a single instance of PersistentHTTPServer
http_server = PersistentHTTPServer()


class Bot(commands.Bot):

    def __init__(self, token, channel):
        super().__init__(token=token, prefix='!', initial_channels=[channel])
        self.last_voice = None
        self.currently_playing = None
        self.websocket = None

    async def event_ready(self):
        print(f'Logged in as | {self.nick}')
        await self.start_websocket_server()

    async def start_websocket_server(self):
        async def websocket_handler(websocket, path):
            self.websocket = websocket
            try:
                async for message in websocket:
                    if message == "NEXT_SONG":
                        await self.play_next_song(None)
            finally:
                self.websocket = None

        server = await websockets.serve(websocket_handler, "localhost", WS_PORT)
        print(f"WebSocket server started on ws://localhost:{WS_PORT}")

    async def event_message(self, message):
        if message.echo:
            return

        print(f"{message.author.name}: {message.content}")

        # Skip TTS for commands (messages that start with '!')
        if not message.content.startswith('!'):
            available_voices = [voice1, voice2]
            if self.last_voice:
                available_voices.remove(self.last_voice)
            chosen_voice = random.choice(available_voices)

            engine.setProperty('voice', chosen_voice)
            engine.say(f"{message.author.name} says {message.content}")
            engine.runAndWait()

            # Update last voice
            self.last_voice = chosen_voice

        await self.handle_commands(message)

    @commands.command(name='songreq')
    async def songreq(self, ctx: commands.Context):
        message_content = ctx.message.content
        yt_link = message_content.split(' ')[1] if len(message_content.split(' ')) > 1 else None

        if yt_link and (yt_url_pattern.match(yt_link) or yt_playlist_pattern.match(yt_link)):
            ydl_opts = {
                'format': 'bestaudio/best',
                'quiet': True,
                'extract_flat': 'in_playlist',
                'force_generic_extractor': True,
            }

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(yt_link, download=False)

                    if 'entries' in info:  # It's a playlist
                        playlist_title = info.get('title', 'Unknown Playlist')
                        for entry in info['entries']:
                            if entry:
                                song_queue.append({
                                    'url': f"https://www.youtube.com/watch?v={entry['id']}",
                                    'title': entry.get('title', 'Unknown Title')
                                })
                        await ctx.send(
                            f'Playlist "{playlist_title}" with {len(info["entries"])} songs added to the queue!')
                    else:  # It's a single video
                        song_queue.append({
                            'url': yt_link,
                            'title': info.get('title', 'Unknown Title')
                        })
                        await ctx.send(
                            f'Song "{info.get("title", "Unknown Title")}" added to the queue! Position: {len(song_queue)}')

                print(f"Queue: {song_queue}")

                if not self.currently_playing:
                    await self.play_next_song(ctx)
            except Exception as e:
                await ctx.send(f'Error processing YouTube link: {str(e)}')
        else:
            await ctx.send('Invalid YouTube link. Please provide a valid video URL or playlist URL.')

    async def play_next_song(self, ctx):
        if song_queue:
            song = song_queue.pop(0)
            yt_link = song['url']
            song_title = song['title']

            ydl_opts = {
                'format': 'bestaudio/best',
                'quiet': True,
                'noplaylist': True,
                'extract_flat': True,
            }

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info_dict = ydl.extract_info(yt_link, download=False)
                    stream_url = info_dict['url']

                    self.currently_playing = song_title
                    if ctx:
                        await ctx.send(f'Now playing: {song_title}')
                    print(f"Playing: {song_title}")

                    if self.websocket:
                        await self.websocket.send(json.dumps({
                            "action": "PLAY",
                            "title": song_title,
                            "url": stream_url
                        }))
            except Exception as e:
                if ctx:
                    await ctx.send(f"Error playing song: {str(e)}")
                print(f"Error playing song: {str(e)}")
                self.currently_playing = None
                await self.play_next_song(ctx)

    @commands.command(name='warteschlange')
    async def show_queue(self, ctx: commands.Context):
        if song_queue:
            await ctx.send("Warteschlange:")
            for i, song in enumerate(song_queue):
                await ctx.send(f"{i + 1}. {song['title']}")
        else:
            await ctx.send("Die Warteschlange ist leer.")

    @commands.command(name='femboy')
    async def femboy(self, ctx: commands.Context):
        femboy_percentage = random.randint(0, 100)
        await ctx.send(f'{ctx.author.name}, you are {femboy_percentage}% femboy!')

    @commands.command(name='skip')
    async def skip_song(self, ctx: commands.Context):
        if ctx.author.is_mod or ctx.author.name == ctx.channel.name:
            if self.currently_playing:
                await ctx.send(f'Skipping current song: {self.currently_playing}')
                if self.websocket:
                    await self.websocket.send(json.dumps({"action": "SKIP"}))
                self.currently_playing = None
                await self.play_next_song(ctx)
            else:
                await ctx.send('No song is currently playing.')
        else:
            await ctx.send('You do not have permission to skip songs.')

    @commands.command(name='server')
    async def list_commands(self, ctx: commands.Context):
        command_list = """
            JOIN MY MINECRAFT SERVER NOW! k8o.hopto.org (VERSION 1.21.1 (NEWEST!))
            """
        await ctx.send(command_list)

    @commands.command(name='clear')
    async def clear_queue(self, ctx: commands.Context):
        if ctx.author.is_mod or ctx.author.name == ctx.channel.name:
            song_queue.clear()
            await ctx.send('The song queue has been cleared.')
            if self.websocket:
                await self.websocket.send(json.dumps({"action": "CLEAR_QUEUE"}))
        else:
            await ctx.send('You do not have permission to clear the queue.')


def shutdown():
    http_server.stop()


def load_config():
    config = configparser.ConfigParser()
    config_file = 'config.ini'

    if not os.path.exists(config_file):
        config['Twitch'] = {'oauth_token': 'your_oauth_token_here',
                            'channel': 'your_channel_name_here'}
        with open(config_file, 'w') as configfile:
            config.write(configfile)
        print(f"Config file created at {config_file}. Please edit it with your Twitch OAuth token and channel name.")
        sys.exit(1)

    config.read(config_file)
    return config['Twitch']['oauth_token'], config['Twitch']['channel']


def create_html_file():
    html_content = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>k8o5 Queue</title>
            <style>
                body {
                    background-color: #181818;
                    color: white;
                    font-family: sans-serif;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    height: 100vh;
                    margin: 0;
                }
                h1 {
                    margin-bottom: 20px;
                    color: #FF3333;
                }
                h2 {
                    margin-bottom: 20px;
                }
                audio {
                    margin-bottom: 20px;
                }
                #queue {
                    width: 80%;
                    max-height: 300px;
                    overflow-y: auto;
                }
                #queue p {
                    margin: 5px 0;
                    cursor: pointer;
                }
                #queue p:hover {
                    text-decoration: line-through;
                }
            </style>
        </head>
        <body>
            <h1>k8o5 Queue</h1>
            <h2 id="nowPlaying">Now Playing: </h2>
            <audio id="audioPlayer" controls>
                Your browser does not support the audio element.
            </audio>
            <h2>Warteschlange:</h2>
            <div id="queue"></div>

            <script>
                const audio = document.getElementById('audioPlayer');
                const nowPlaying = document.getElementById('nowPlaying');
                let ws;

                function connectWebSocket() {
                    ws = new WebSocket('ws://localhost:8080');

                    ws.onmessage = function(event) {
                        const data = JSON.parse(event.data);
                        if (data.action === 'PLAY') {
                            nowPlaying.textContent = 'Now Playing: ' + data.title;
                            audio.src = data.url;
                            audio.play();
                        } else if (data.action === 'SKIP') {
                            audio.pause();
                            audio.currentTime = 0;
                            ws.send('NEXT_SONG');
                        } else if (data.action === 'CLEAR_QUEUE') {
                            updateQueue();
                        }
                    };

                    ws.onclose = function() {
                        setTimeout(connectWebSocket, 1000);
                    };
                }

                connectWebSocket();

                audio.onended = function() {
                    ws.send('NEXT_SONG');
                };

                function updateQueue() {
                    fetch('/queue.json')
                        .then(response => response.json())
                        .then(queue => {
                            const queueDiv = document.getElementById('queue');
                            queueDiv.innerHTML = '';
                            queue.forEach((song, index) => {
                                const songItem = document.createElement('p');
                                songItem.textContent = `${index + 1}. ${song.title}`;
                                songItem.onclick = function() {
                                    if (confirm('Remove this song from the queue?')) {
                                        removeSongFromQueue(index);
                                    }
                                };
                                queueDiv.appendChild(songItem);
                            });
                        });
                }

                function removeSongFromQueue(index) {
                    fetch('/queue.json', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({ action: 'remove', index: index }),
                    })
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            updateQueue();
                        } else {
                            alert('Failed to remove song from queue');
                        }
                    });
                }

                updateQueue();
                setInterval(updateQueue, 5000);
            </script>
        </body>
        </html>
        """

    with open("video.html", "w", encoding="utf-8") as f:
        f.write(html_content)


def main():
    token, channel = load_config()

    if token == 'your_oauth_token_here' or channel == 'your_channel_name_here':
        print("Please edit the config.ini file with your Twitch OAuth token and channel name.")
        sys.exit(1)

    create_html_file()
    try:
        http_server.start()
        webbrowser.open(f"http://localhost:{http_server.port}/video.html")
    except Exception as e:
        print(f"Error starting HTTP server: {str(e)}")
        sys.exit(1)

    bot = Bot(token, channel)
    try:
        bot.run()
    finally:
        shutdown()


if __name__ == "__main__":
    main()
