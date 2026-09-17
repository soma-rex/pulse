# Pulse

Pulse is a modular Discord bot built in Python that combines utility features, interactive games, and AI-powered responses into a single system. It is designed to be flexible, easy to expand, and suitable for both learning and real server use.

The project focuses on providing a smooth user experience while keeping the codebase organized through a cog-based structure.

---

## Overview

Pulse includes a variety of systems such as casino-style games, server utilities, statistics tracking, and AI interaction. It uses a local SQLite database for persistent storage and integrates external AI services to enhance functionality.

The bot is built using the Discord.py framework and follows a modular design so that new features can be added without affecting existing ones.

---

## Features

Pulse currently includes:

* A modular command system using cogs
* Casino-style games such as poker, blackjack, roulette, and slots
* An anime guessing mode with progressive image reveals
* A basic economy system with balances and rewards
* User statistics tracking using a database
* AI-powered responses using an external API, with optional GIPHY reactions
* A symbolic calculator for algebra, equations, differentiation, integration, domains, and LaTeX previews
* Admin and configuration commands for server management
* Custom help command system

The structure allows additional features to be added easily without rewriting core logic.

---

## Tech Stack

* Language: Python
* Framework: Discord.py
* Database: SQLite
* AI Integration: Groq API
* Other Tools: dotenv for environment management

---

## Project Structure

The project is organized to keep logic separated and maintainable:

```
pulse/
│── cogs/             # All command modules (games, admin, AI, etc.)
│── data/             # Local database and persistent storage
│   └── messages.db   # SQLite database
│── bot.py            # Entry point of the bot
│── .env              # Environment variables (token, API keys)
```

Each cog is responsible for a specific feature, such as poker, stats, or AI interaction.

---

## Installation

1. Clone the repository:

```
git clone https://github.com/soma-rex/pulse.git
cd pulse
```

2. Install dependencies:

```
pip install -r requirements.txt
```

3. Create a `.env` file and add your credentials:

```
DISCORD_TOKEN=your_token_here
GROQ_API_KEY=your_api_key_here
GIPHY_API_KEY=your_giphy_api_key_here
```

4. Run the bot:

```
python main.py
```

---

## Usage

Once the bot is running, invite it to your server and use slash commands to interact with it.

Examples of usage include:

* Starting games like poker or blackjack
* Checking balances or stats
* Solving equations like `/calc solve x^2 - 5*x + 6 = 0`
* Running calculus commands like `/calc diff x^3 + sin(x)` or `/calc integrate x^2`
* Using AI-related commands
* Accessing the help command for a full list of features

Exact commands may vary depending on how the cogs are configured.

### Endless Poker Tables

Poker now runs as a persistent table flow instead of a single hand.

Current table behavior:

* `/poker create` opens a table with a buy-in and raise cap
* `/poker join` buys a player into the table or lets a busted player rebuy
* `/poker start` starts the table once, then hands continue automatically
* players who join during an active hand are added to the next hand
* the table ends when only one player has chips left, an admin uses `/poker end`, or the table is inactive for 30 minutes
* remaining table stacks are refunded back to player balances when the table ends

### Anime Guess Setup

The anime guessing game now fetches anime data and clue images from AniList at runtime, so you do not need to maintain a local JSON file of entries.

Current clue flow:

* hard clue: banner art when AniList provides it
* medium clue: up to two character images
* easy clue: cover art

The bot accepts multiple answer forms automatically by using AniList titles and synonyms.

Commands:

* `/anime start` - start a random round in the current channel
* `/anime stop` - stop the current round
* `/anime status` - show whether a round is active in the current channel

### AI GIF Replies

If `GIPHY_API_KEY` is set, AI chat replies can sometimes include a matching GIF or, more rarely, respond with only a GIF when it fits the tone.

If GIPHY is not configured or no GIF is found, the bot falls back to a normal text reply.

---

## Configuration

The bot supports configuration through environment variables and database-stored settings. Admin commands can be used to adjust behavior directly from Discord.

Sensitive data such as tokens and API keys should always be stored in the `.env` file and never committed to the repository.

---

## Contributing

Contributions are welcome. If you want to improve the project:

* Fork the repository
* Create a new branch
* Make your changes
* Submit a pull request

Try to keep the code consistent with the existing structure and style.

---

## Disclaimer

This project is provided for educational and entertainment purposes only.

Pulse includes game systems and virtual currency, but none of these have real-world monetary value. It is not intended for real gambling or financial use.

The developer is not responsible for any misuse of the bot, data loss, or issues arising from modifications. By using this project, you agree that any data stored (such as user IDs or stats) may be processed locally.

The bot relies on third-party services for some features. Availability and behavior of those services are outside the developer’s control.

This project is provided "as is" without any warranty.

