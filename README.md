# SNR Buns Staff Hub Pro

This is the standalone staff-only Discord bot and customer loyalty website. It keeps the existing Railway SQLite database, customer points, sales, finance history, Golden Tickets and pending requests.

## Latest features

- A dedicated **SNR Owner** control centre gives owners a private dashboard, manual staff clock-off, VIP management and bot-logo control.
- The official hub and website are branded **SNR Buns — Owned by Cody, Ash & Lola**.
- Customer memberships progress automatically: Regular (0+ purchases), Silver (5+), Gold (15+) and SNR VIP (30+). Owners can override a level or return it to automatic mode.
- Silver earns +1 Golden Ticket per purchase; Gold earns +1 loyalty point and +1 Golden Ticket; SNR VIP earns +1 loyalty point and +2 Golden Tickets. The bonuses are recorded in the same sale, finance, delivery and jackpot transaction.
- Customers create a zero-point loyalty account on the website without making a purchase first.
- New accounts activate immediately with no staff approval. Discord posts an informational alert and mentions the configured `SNR Staff` role.
- Password resets use the customer's memorable question and answer—no setup code or staff reset is required.
- Existing older accounts are prompted to add a memorable question after logging in.
- Customers can order several different deals and choose 0–10 of each (20 deals maximum per order).
- A live subtotal appears before checkout; the server recalculates it securely when submitted.
- Customers can add an optional 200-character order note for meeting points or food instructions.
- The confirmation asks customers to allow 5–7 minutes.
- Delivery orders move through Waiting, Accepted, Driver On The Way and Delivered/Paid.
- The customer page checks for delivery updates every five seconds and shows a bright status notification.
- Loyalty customers see a visual progress bar toward their four-point trading-card pack.
- Website ordering is available only while at least one staff member is clocked in.
- The hub includes **Clock In**, **Clock Off** and **Who’s Clocked In**. Shifts expire after eight hours if somebody forgets to clock off.
- Record Sale, Check Customer and Redeem Reward include an alphabetically ordered 25-name dropdown with **Previous Names** and **Next Names**, plus **Type / Suggest Name** for spelling correction.
- Delivery Orders opens a dashboard showing the live queue, drivers, today’s revenue and gross profit.
- New-account notices disappear after five minutes. Completed older account approvals and pack alerts disappear after two minutes. Completed delivery alerts remain for 12 hours.
- Paid multi-deal orders record every selected deal in sales, finance, loyalty and Golden Tickets exactly once.
- After a driver is marked On The Way, staff can mark a delivery as a **Wasted Journey**. This adds a £500 account fee, closes the order without recording a sale or rewards, and blocks new web deliveries.
- Outstanding fees appear in red on the customer's webpage and as a warning stamp beside their name in Discord. The Delivery Orders dashboard lets staff mark a fee **Paid** or **Waived**; every action is audited.

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
- `OWNER_ROLE_NAME` = `SNR Owner`
- `WEBSITE_URL` = `https://worker-production-2c48.up.railway.app`
- `DATABASE_PATH` = `/data/snr_staff_hub.db`
- `LEGACY_DATA_FILE` = `/data/loyalty_data.json`
- `JACKPOT_POOL_SIZE` = `1000`

Railway supplies `PORT`; do not add it manually. The volume should remain mounted at `/data`.

## One-time Discord setup

In a private orders channel, an SNR Management member runs `/snrhub_orders_setup`. The bot needs View Channel, Send Messages and Embed Links. The `SNR Staff` role must be mentionable, or the bot must have permission to mention roles, for alerts to ping it. This channel receives account-created notices; staff do not approve them.

Create a Discord role named **SNR Owner** and assign it to Cody, Ash and Lola. Server administrators also count as owners. Run `/snrhub_panel` again in the private staff channel to post the upgraded permanent panel; only an owner can post it. A staff member must press **Clock In** before delivery controls appear to customers.

Open **Owner Admin** (or run `/snrhub_owner`) to use the private owner controls. Press **Set Bot Logo** once to change the bot's Discord profile picture to the supplied official SNR Buns logo. Discord may temporarily rate-limit repeated picture changes, so do not repeatedly press it.

## Staff workflow

1. Press **Clock In** when accepting deliveries.
2. For counter sales, press **Record Sale**, choose a saved character or type the name, then choose the deal.
3. For website orders, open **Delivery Orders**, press **Accept Delivery**, then **Driver On The Way** when leaving.
4. After delivery and payment, press **Delivered & Customer Paid**. Only this final step records sales and rewards.
5. If the driver arrives but the journey is wasted, press **Wasted Journey — Charge £500**. Only the assigned driver or SNR Management can do this, and only after **Driver On The Way**.
6. In **Delivery Orders**, use **Fee Paid** or **Waive Fee** to clear the warning and restore that customer's delivery access.
7. Press **Clock Off** when delivery closes. If everyone clocks off, the website immediately shows “No drivers are currently available.”

## VIP membership workflow

- Every completed counter sale or paid delivery increases the customer's purchase count.
- The qualifying purchase receives the newly unlocked level's bonus immediately.
- The website displays the customer's level, exact bonuses and purchases remaining to the next level.
- Discord customer cards, name dropdowns, sale receipts and delivery orders display the same current level.
- Owners use **Owner Admin → Manage VIP Level** to set Regular, Silver, Gold or SNR VIP manually, or choose **Automatic progression**.
- Owner overrides and forced clock-offs are written to the audit log.

Pending orders and requests are durable. If Discord or Railway restarts, unsent alerts retry. Unique order references prevent double-counting.

## Slash commands

- `/snrhub_panel` — post the permanent staff panel
- `/snrhub_owner` — open the owner-only control centre
- `/snrhub_orders_setup` — set the private delivery/account-alert channel
- `/snrhub_claims_setup` — optionally set a separate pack-claim channel
- `/snrhub_accounts_pending` — review recent account activity and any older approval requests
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
