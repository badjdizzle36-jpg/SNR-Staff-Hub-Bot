# SNR Buns Staff Hub Pro

This is the standalone staff-only Discord bot and customer loyalty website. It keeps the existing Railway SQLite database, customer points, sales, finance history, Golden Tickets and pending requests.

## Latest features

- Customers create a zero-point loyalty account on the website without making a purchase first.
- New accounts require one staff verification in Discord. The configured `SNR Staff` role is mentioned.
- Password resets use the customer's memorable question and answer—no setup code or staff reset is required.
- Existing older accounts are prompted to add a memorable question after logging in.
- Customers can order several different deals and choose 0–10 of each (20 deals maximum per order).
- A live subtotal appears before checkout; the server recalculates it securely when submitted.
- The confirmation asks customers to allow 5–7 minutes.
- Website ordering is available only while at least one staff member is clocked in.
- The hub includes **Clock In**, **Clock Off** and **Who’s Clocked In**. Shifts expire after eight hours if somebody forgets to clock off.
- Record Sale, Check Customer and Redeem Reward include a paged character-name dropdown plus **Type / Suggest Name** for spelling correction.
- Completed account and pack alerts disappear after two minutes. Completed delivery alerts remain for 12 hours.
- Paid multi-deal orders record every selected deal in sales, finance, loyalty and Golden Tickets exactly once.

## Current deals

| Deal | Price | Contents | Loyalty | Golden Tickets |
| --- | ---: | --- | ---: | ---: |
| SNR Quick Fix | £150 | 1 food + 1 drink | 0 | 1 |
| SNR Happy Meal | £300 | 2 food + 2 drinks | 0 | 1 |
| SNR Sweet Treat Deal | £400 | 5 desserts | 0 | 1 |
| SNR Mega Deal | £500 | 4 food + 4 drinks | 1 | 1 |
| SNR Blue Light Deal | £600 | 8 food + 8 drinks | 0 | 1 |
| SNR Share Box | £1,200 | 10 food + 10 drinks | 2 | 4 |

At four or more available points, the website shows **Claim Trading Card Pack**. Staff click **Handed Over** after giving the two-card pack, which resets the entire available points balance to zero. Cancelling leaves points unchanged.

## Update the existing Railway service

Upload every file from this folder to the existing GitHub repository and commit directly to `main`. Do not delete the Railway volume or database. Railway redeploys automatically.

The new file `staff_shifts.py` must be uploaded with `bot.py`, `snr_core.py`, `web_portal.py`, `reward_claims.py`, `customer_accounts.py`, `delivery_orders.py`, `snr-logo.png`, `requirements.txt`, `Procfile` and `railway.json`.

Keep these Railway variables:

- `DISCORD_TOKEN`
- `GUILD_ID`
- `STAFF_ROLE_NAME` = `SNR Staff`
- `MANAGER_ROLE_NAME` = `SNR Management`
- `DATABASE_PATH` = `/data/snr_staff_hub.db`
- `LEGACY_DATA_FILE` = `/data/loyalty_data.json`
- `JACKPOT_POOL_SIZE` = `1000`

Railway supplies `PORT`; do not add it manually. The volume should remain mounted at `/data`.

## One-time Discord setup

In a private orders channel, an SNR Management member runs `/snrhub_orders_setup`. The bot needs View Channel, Send Messages and Embed Links. The `SNR Staff` role must be mentionable, or the bot must have permission to mention roles, for alerts to ping it.

Run `/snrhub_panel` again in the private staff channel to post the upgraded permanent panel. A staff member must press **Clock In** before delivery controls appear to customers.

## Staff workflow

1. Press **Clock In** when accepting deliveries.
2. For counter sales, press **Record Sale**, choose a saved character or type the name, then choose the deal.
3. For website orders, open **Delivery Orders** and press **Customer Paid** only after payment.
4. Press **Clock Off** when delivery closes. If everyone clocks off, the website immediately shows “No drivers are currently available.”

Pending orders and requests are durable. If Discord or Railway restarts, unsent alerts retry. Unique order references prevent double-counting.

## Slash commands

- `/snrhub_panel` — post the permanent staff panel
- `/snrhub_orders_setup` — set the private delivery/account-alert channel
- `/snrhub_claims_setup` — optionally set a separate pack-claim channel
- `/snrhub_accounts_pending` — review new-account verifications
- `/snrhub_claims_pending` — review pack requests
- `/snrhub_orders_pending` — review delivery orders
- `/snrhub_sale` — record a sale
- `/snrhub_customer` — check a customer
- `/snrhub_birdy` — generate copy-ready Birdy posts
- `/snrhub_report` — finance reporting

Every command uses the unique `snrhub_` prefix, so it will not conflict with other bots.

## Security

Passwords and memorable answers are stored only as salted PBKDF2 hashes. Five failed password or memorable-answer attempts cause a 15-minute lock. Password resets revoke old sessions. Public forms never reveal finance data, staff identities, password hashes or the hidden £5,000 jackpot position.

## Tests

Run `python -m unittest -v` from this folder.
