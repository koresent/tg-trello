# Trello Telegram Bot

A stateless Telegram bot designed for quick interaction with a personal Trello board.

## Features
- **Task Gacha (`/roll`)**: Randomly selects a task from a specific "To Do" column based on weighted probabilities (70% chance for `Tier 1`, 30% chance for `Tier 2`).
- **Quick Inbox**: Any regular text message sent to the bot is automatically converted into a new card in the specified "Inbox" column.
