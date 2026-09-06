# SNR Buns Staff Hub

## Secure LB Phone app (current version)

Customer access is now passwordless and lives inside a separate LB Phone custom
app. The public Railway webpage no longer lists customer names or offers a
password form.

On first use, the customer enters their existing loyalty name while physically
with SNR staff. The FiveM server—not the customer's form—adds the equipped LB
Phone number, permanent framework character ID and current roleplay name to the
request. Discord shows all of that information. Staff approve only after
checking the customer in person.

Approval binds one loyalty account to that exact character and equipped phone.
The app then opens the correct card automatically and can submit delivery and
reward requests to the existing database. A new phone requires a new approval;
approving it revokes the old phone. Management can immediately remove access
with `/snrhub_phone_unlink name`.

Add the same long random `LB_PHONE_API_SECRET` in Railway and in the FiveM
resource's server-only `server_config.lua`. Never put this secret in a client or
UI file. Install the separately supplied `snr_buns_lb_phone` resource after
`lb-phone`; its `INSTALL.md` contains the server steps.

The older password-account classes remain in the source only to migrate safely
without deleting historical database records. They are no longer exposed on
the customer website or used by the LB Phone app.

## LB Phone delivery orders

Paired customers can order one of the six current SNR deals directly from the
LB Phone app. Every deal shows its contents, exact price, loyalty
points and Golden Tickets. A postal or clear delivery location is required.

Create a private Discord channel such as `snr-delivery-orders`, make sure
@everyone cannot view it, and run `/snrhub_orders_setup` inside it as an SNR
Management member. New orders normally arrive there within 10 seconds with the
customer, selected deal, amount owed and delivery location.

Staff press **Customer Paid** only after taking payment. That single action
records the deal in the existing sales and finance system and awards its loyalty
points and Golden Tickets. **Cancel Order** adds no sale and no rewards. The
database uses a unique delivery reference, so restarts or repeated button clicks
cannot count the same order twice. Customers can have one waiting order at a
time and can follow its status on their account page.

## Reward requests

Every 4 available loyalty points can be exchanged for one pack containing 2
trading cards. Existing saved points qualify. Mega Deal still earns 1 point and
Share Box earns 2. No automatic free card is added to another meal.

Upload all supplied Railway bot files together at the repository root. Keep
`snr-logo.png` there and do not replace the Railway database or volume.

After deployment, an SNR Management member or administrator runs
`/snrhub_claims_setup` in the private staff text channel that should receive
alerts. The bot needs View Channel, Send Messages and Embed Links there. The
channel must not allow @everyone to view it. Website requests are disabled until
this setup is complete.

When a paired player requests a pack, Railway gets the customer identity from
the server-verified LB Phone pairing—not from a dropdown or editable form field.
Exactly four points are reserved transactionally. Only one pending request per
customer is allowed. Repeat submissions do not reserve again. Staff click
**Handed Over** after delivering the pack, or **Cancel & Return Points**. Both
actions are audited and work only once. There is no automatic in-game delivery.

Alerts normally arrive within 10 seconds while Discord is available. Requests
are stored in SQLite and unsent alerts retry after outages/restarts. A crash
between sending and saving the message ID can produce a duplicate alert; both
refer to the same claim and cannot cause a second deduction or refund. Pending
requests remain available through Pack Requests if a message is deleted.
Customers refresh their card to see status updates; website confirmation says
queued, not delivered to Discord. Staff actions remain private to Discord.

Finance reports still show food-production gross profit; they do not subtract
the cost of reward packs or other giveaways. Claims are audited separately.

The ordinary Railway web address now shows only instructions to open the secure
LB Phone app. It does not expose customer names, loyalty cards, finance data,
staff identities or the hidden jackpot position. Railway supplies `PORT`
automatically; do not change it manually.

A private, staff-only Discord bot for recording SNR Buns sales with only the customer’s character name.

The bot automatically handles:

- Loyalty points with optional website claims: four points for one two-card pack
- Golden Ticket entries and one hidden Mystery Ticket within every 1,000 tickets
- An extremely rare £5,000 cash jackpot prize
- Customer histories, rewards, revenue, food and drink totals
- Today, 7-day, 30-day and all-time finance reports with production cost, gross profit and margin
- Ready-to-copy Birdy posts
- Name matching and misspelling suggestions
- Character-and-phone-protected delivery orders with required locations
- Importing existing `loyalty_data.json` customers without changing the original file

Customers never need to join Discord.

## Deals built into the bot

| Deal | Price | Contents | Loyalty | Golden Tickets |
| --- | ---: | --- | ---: | ---: |
| SNR Quick Fix | £150 | 1 food + 1 drink | 0 | 1 |
| SNR Happy Meal | £300 | 2 food + 2 drinks | 0 | 1 |
| SNR Sweet Treat Deal | £400 | 5 desserts | 0 | 1 |
| SNR Mega Deal | £500 | 4 food + 4 drinks | 1 | 1 |
| SNR Blue Light Deal | £600 | 8 food + 8 drinks | 0 | 1 |
| SNR Share Box | £1,200 | 10 food + 10 drinks | 2 | 4 |

## Staff workflow

1. Run `/snrhub_panel` once in a private staff channel.
2. Press **Record Sale**.
3. Enter the customer’s character name.
4. Choose the deal from the dropdown.
5. The bot records everything and immediately updates loyalty and the jackpot.

The panel also contains **Account Approvals**, **Pack Requests**, **Delivery
Orders**, **Check Customer**, **Redeem Reward**, **Golden
Jackpot**, **Birdy Post**, and **Finance Check**. Trading cards are awarded only when a customer
exchanges four points for one two-card pack.

## Finance Check

The **Finance Check** button privately shows today’s figures in UK time:

- Revenue
- Estimated production cost
- Gross profit
- Profit margin percentage
- Food, dessert and drink quantities
- Profit and margin for every deal sold

The cost model uses the supplied item prices:

| Category | Item | Cost each |
| --- | --- | ---: |
| Food | Burger | £7.70 |
| Food | Chicken wrap | £5.60 |
| Drink | Cherry slush | £1.75 |
| Drink | Lemon slush | £0.70 |
| Drink | Pineapple slush | £6.25 |
| Dessert | Chocolate muffin | £7.00 |
| Dessert | Doughnut | £7.00 |
| Dessert | Chocolate ice cream | £3.50 |
| Dessert | Mint ice cream | £4.90 |
| Dessert | Strawberry ice cream | £8.20 |
| Dessert | Vanilla ice cream | £2.80 |

Because a sale records the meal deal rather than every flavour chosen, finance reports use
the category averages: **£6.65 per food, £2.90 per drink and £5.57 per dessert**.

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
- `LB_PHONE_API_SECRET`: one long random secret matching the FiveM resource's server-only setting

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
- `/snrhub_accounts_pending` — review secure LB Phone pairing approvals
- `/snrhub_phone_unlink name` — management removes a customer’s phone access
- `/snrhub_claims_setup` — choose the private staff channel for website alerts
- `/snrhub_claims_pending` — show pending website pack requests
- `/snrhub_orders_setup` — choose the private Discord delivery-orders channel
- `/snrhub_orders_pending` — show orders awaiting payment
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

`python -m unittest -v`
