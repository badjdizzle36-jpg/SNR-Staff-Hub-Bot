# SNR Buns Staff Hub

A private, staff-only Discord bot for recording SNR Buns sales with only the customer’s character name.

The bot automatically handles:

- Loyalty points: 4 points awards one free 2-card trading pack
- Golden Ticket entries and one hidden Mystery Ticket within every 1,000 tickets
- An extremely rare £5,000 cash jackpot prize
- Customer histories, rewards, revenue, food and drink totals
- Ready-to-copy Birdy posts
- Name matching and misspelling suggestions
- Importing existing `loyalty_data.json` customers without changing the original file

Customers never need to join Discord.

## Deals built into the bot

| Deal | Price | Contents | Loyalty | Golden Tickets |
| --- | ---: | --- | ---: | ---: |
| SNR Loyalty Deal | £500 | 4 food + 4 drinks | 1 | 1 |
| SNR Crew Deal | £750 | 6 food + 6 drinks | 1 | 2 |
| SNR Big Feed | £1,000 | 8 food + 8 drinks | 1 | 3 |
| SNR Share Box | £1,200 | 10 food + 10 drinks | 2 | 4 |

## Staff workflow

1. Run `/snrhub_panel` once in a private staff channel.
2. Press **Record Sale**.
3. Enter the customer’s character name.
4. Choose the deal from the dropdown.
5. The bot records everything and immediately checks for loyalty and jackpot rewards.

The panel also contains **Check Customer**, **Redeem Reward**, **Golden Jackpot**, **Birdy Post**, and **Today’s Report**.

## Railway installation

### 1. Keep your old data safe

Do not delete your existing bot or `loyalty_data.json`. Download a backup before changing the Railway service.

This project uses a new database called `snr_staff_hub.db`. The old JSON file is read only once to import existing customers; it is never overwritten.

### 2. Upload these files

Upload the contents of this folder to a new GitHub repository or a new branch. Connecting a separate Railway service is safest while testing.

### 3. Add a Railway Volume

Mount the volume at:

`/data`

This keeps the database safe when Railway redeploys the bot.

### 4. Add environment variables

Add these variables in Railway:

- `DISCORD_TOKEN`: token for the Discord bot
- `GUILD_ID`: your Discord server ID
- `STAFF_ROLE_NAME`: `SNR Staff`
- `MANAGER_ROLE_NAME`: `SNR Management`
- `DATABASE_PATH`: `/data/snr_staff_hub.db`
- `LEGACY_DATA_FILE`: `/data/loyalty_data.json`
- `JACKPOT_POOL_SIZE`: `1000`

If the old `loyalty_data.json` is already stored somewhere else on your Volume, change `LEGACY_DATA_FILE` to that exact location.

### 5. Deploy

Railway will install `requirements.txt` and run:

`python bot.py`

The log should show that the bot signed in and synced its commands.

### 6. Create the staff panel

Make a private Discord channel that only SNR staff can see. Run:

`/snrhub_panel`

The panel remains usable after the bot restarts.

## Commands

- `/snrhub_panel` — post the permanent control panel
- `/snrhub_sale name` — open the deal dropdown for a customer
- `/snrhub_customer name` — check loyalty, sales and outstanding rewards
- `/snrhub_birdy` — generate a copy-ready Birdy post
- `/snrhub_report` — show 24-hour, 7-day, 30-day or all-time totals

Every slash command uses the unique `snrhub_` prefix so it cannot be confused with
the commands from the existing loyalty, Blue Light or raffle bots.

## Safety and fairness

- Every action requires the configured staff or management role.
- The winning ticket position is hidden from Discord users and staff.
- Every sale records the staff member, customer, time and transaction number.
- Rewards remain stored until a staff member marks them as claimed.
- The £5,000 cash jackpot has a 1-in-1,000 ticket rate (0.1%).
- A winner is guaranteed by ticket 1,000 and the jackpot resets automatically afterward.
- The original JSON data is never edited by this bot.

## Run tests locally

From inside this folder:

`python -m unittest -v test_snr_core.py`
