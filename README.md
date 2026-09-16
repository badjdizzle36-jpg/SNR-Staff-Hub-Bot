# SNR Buns Staff Hub Pro

This is the standalone staff-only Discord bot and customer loyalty website. It keeps the existing Railway SQLite database, customer points, sales, finance history, Golden Tickets and pending requests.

## Latest features

- **Permanent owner announcements:** route **Owner Announcements** from `/snrhub_channels`, then use **Owner Admin → Post Announcement**. The bot posts a public branded announcement with no deletion timer and records it in the audit trail. Temporary private menus remain temporary, while owner announcements stay visible unless someone deliberately deletes them.
- **Discord Command Centre V2:** the permanent hub now has only four starting buttons—New Sale, Live Queue, Customers and Staff & Tools. Live Queue combines orders, collections, in-store payments, customer help, rewards, raffle payments, account work and outstanding journey fees into one prioritised dropdown instead of opening many separate messages. Overdue orders rise to the top, and temporary screens replace themselves to keep Discord clean.
- **Owner-managed monthly leaderboard:** open **More Tools → Customer Leaderboard** or **Owner Admin → Manage Leaderboard** to remove testing accounts from the monthly chase and restore them later. This never deletes their account, points, membership or sales history.
- **Smaller premium loyalty card:** the Home card now keeps its credit-card proportions while using much less screen space—310px wide on normal phones and 286px on compact phones—with the points, chip, cardholder and reward progress still readable.
- **SNR Champions monthly leaderboard:** the website shows a fun top-five customer chase, the logged-in customer's position, exact spend needed to overtake the next place, and days remaining. Staff can open the top ten from **More Tools → Customer Leaderboard**, while the owner dashboard shows the current leader. Rankings use confirmed, non-voided spend and reset automatically each London calendar month. The final #1 customer qualifies for the monthly giveaway, with staff confirming the prize handover.

- **Permanent Discord records:** owner-triggered bot replies no longer receive timed deletion. City Run reward claims remain in their channel with their final status, new-account notices remain visible, and resolved delivery-fee records remain available for audit. Completed orders still move from Active Deliveries into Completed Deliveries so the active queue stays accurate.

- **Premium member cards and phone fix:** the live card and six-level gallery now look like restrained luxury payment cards, with metallic EMV chips, contactless marks, masked member numbers, cardholder details and an SNR Elite signature. The customer header no longer collapses long names into a vertical column. Responsive sizing and older-browser fallbacks cover iOS Safari, ordinary desktop/mobile browsers and embedded in-game phone browsers.

- **Compact homepage and live progress:** fixes the older CSS rule that hid the delivery illustration. Smaller credit cards, tighter shortcuts and less glow; membership statistics and Golden Ticket information live under More. An active order gets a sticky progress bar above the banner on every customer tab, with delivery or pickup stages and automatic status refresh. The empty current-order panel is removed from Home.

- **Preview 2 artwork and layout correction:** includes the original neon city/burger banner and illustrated Click & Collect and Delivery tiles, a live reward strip, three shortcuts and four bottom navigation buttons. The membership area stays a real credit-card shape. Upload the new `neon-design.png` alongside `web_portal.py`; it is required for the new artwork. The banner's service availability and customer balances are live, not the example values from the design preview.

- **Neon Street Food redesign:** the entire customer website now uses a consistent black city-night interface with red/orange neon, restrained cyan status accents, clearer page hierarchy, compact phone navigation, redesigned login/forms/orders/rewards/raffle/history and responsive desktop layouts. The main live membership display remains a realistic chip-and-contactless credit card, with the customer's membership tier and point balance built into the card face. Existing membership gallery cards keep their individual level colours and credit-card proportions.
- **SNR City Run:** all 38 confirmed RP businesses are split across eight digital collection routes. One loyalty point creates one secure reveal credit. Existing balances are copied into the first season when the owner starts it, without removing or resetting the loyalty balance.
- **Owner password recovery:** open **Customers → Reset Password**, choose the customer from the searchable 25-name pages, and confirm. The owner receives a private one-use website link to pass to the verified customer. The old password and all active sessions are disabled immediately; the customer chooses their new password privately, and the link expires after 24 hours or after first use.
- **Membership card gallery:** view all six card designs and their live membership thresholds, bonus points, bonus Golden Tickets and delivery prices under More. Your current level is marked.
- **Physical trading-card packs retired:** no new pack can be requested or handed out. Pending requests are cancelled automatically; only legacy requests that had already reserved points are refunded. Historical records remain for audit.
- **Clock In / Clock Out** are directly on the permanent Discord home panel. After updating, run `/snrhub_panel` to post the updated panel.
- **Scan Card In Store:** the large gold digital member card is the main Home shortcut. Customers tap it, choose meal quantities and press **Send to Counter - Pay In Store**. This opens the menu; it does not require a camera scan.
- In-store orders appear in the existing private orders channel, mention SNR staff and display the account name, basket and total. Staff collect payment and press **Confirm Payment** directly. No acceptance, driver or collection steps are needed.
- In-store ordering has no delivery fee and requires no driver or postal. Customers must be logged in; service must be open. Payment confirmation records each meal's rewards, membership progress, Golden Tickets and finance through the existing system. Repeat clicks cannot add the same sale twice.
- **Quick Hub customer redesign:** the signed-in website now opens on a compact phone-first home screen with the customer's loyalty progress, membership, Golden Tickets, visits and website service status visible immediately.
- **VIP After Dark redesign:** the website surrounding the loyalty card now uses a premium black, charcoal and gold visual system across login, ordering, rewards, raffle and navigation. The existing membership-card artwork and each level's individual card colour remain unchanged.
- **Two-way Live Actions:** customers can call staff, request an order change or cancellation, or report a driver-location problem from the existing Home screen. Requests alert the private Discord channel and appear together under the permanent panel's Live Actions button. Staff replies and decisions update the customer's open webpage automatically.
- **Existing voucher wallet preserved:** vouchers issued before City Run remain visible and redeemable. New loyalty points feed City Run instead of the retired point-spend catalogue.
- **Unified staff inbox:** Live Actions brings together open orders, customer problems, City Run claims, raffle payments and direct customer requests without adding duplicate customer tabs.
- **Owner operations controls:** Offers & Alerts connects the existing website banner and expiring/limited discount-code tools, Reward Choices controls the website catalogue, and Audit Trail displays the latest recorded actions. Existing sale undo remains available.
- Large Order, Raffle and City Run shortcuts open the correct page without a long scroll. A live current-order card refreshes automatically when staff move the order forward.
- A fixed **Home, Order, City Run and More** navigation bar stays within thumb reach. Birthday settings, VIP details, existing vouchers, recent visits and logout remain safely available under More.
- **Integrated SNR Raffle Centre:** owners create one live raffle at a time with numbers 1–100, a prize and a price per number. Logged-in customers can request up to ten available numbers from the new website **Raffle** page.
- Website requests reserve numbers immediately, but only enter the draw after staff collect payment and press **Confirm Payment** in Discord. Rejecting a request releases its numbers. Simultaneous requests cannot receive the same number.
- Staff open **More Tools → Raffle Centre** or `/snrhub_raffle` to view the board, approve/reject payments and add paid entries for a saved customer. Owner controls can create, edit, close, reopen, cancel and securely draw the winner.
- The secure draw chooses only from confirmed paid entries, locks the finished raffle and shows the winning name and number in Discord and on the customer website. Raffle revenue is reported inside the Raffle Centre and remains separate from meal finance, loyalty points and the £5,000 Golden Ticket jackpot.
- **Permanent panel protection:** `/snrhub_panel` posts one public staff panel that is never used as a temporary interaction response. New Sale and every other private menu open separately, while the cleanup system refuses to delete any public source message.
- **Loyalty sale write strengthened:** every qualifying counter sale and confirmed website order now increases points with an atomic database update, verifies the new balance before committing, and records the before/added/after totals in the audit entry. Simultaneous staff sales cannot overwrite one another's points.
- **New Sale timeout fixed:** the Recent Customers dropdown, full alphabetical dropdown and navigation buttons now use separate Discord rows. The permanent panel acknowledges the click immediately before loading customer data, so the sale menu opens reliably instead of showing “didn’t respond in time.”
- **Express Sale** removes the unnecessary quantity screen from normal counter sales. Choose the customer, then tap one of the six meal buttons to record ×1 immediately; the receipt remains visible for ten seconds.
- **Multiple Items** keeps the full ×1–×10 quantity route for larger orders. Back buttons return to the meal or customer screen without recording anything.
- The customer picker now shows up to ten **recently served customers** above the complete alphabetical 25-name pages, while typed names retain capital correction and close-spelling suggestions.
- **Owner Admin → Undo Last Counter Sale** shows a confirmation before reversing the newest Discord counter-sale action. A quantity sale is reversed as one group, correcting finance, visits, loyalty and customer Golden Ticket totals while retaining a permanent audit record. Jackpot-winning transactions are locked from quick undo for safety.

- Owners can switch the customer website between **Open, Busy, Pickup Only, Deliveries Paused and Closed** from **More Tools → Owner Admin → Website Service Mode**. Busy mode keeps orders open with longer estimates; paused modes still allow pickup; Closed blocks new orders safely.
- Active orders now display a **live ETA and real queue position**. The estimate refreshes automatically from the order's actual status and pending position without the customer reloading the page.
- A **1–2 star rating rescue flow** asks what went wrong, alerts SNR Management, and creates a durable case with a **Mark Customer Helped** button. Higher ratings continue through the quick normal flow.
- Customers can press **Report a problem** on a completed order, choose Food quality, Delivery time, Driver behaviour, Missing items or Other, and send a short message. It appears in the private Discord orders channel and stays open until staff resolve it.

- As soon as staff mark an order paid, the customer's open webpage checks within about two seconds and opens a large **Rate Your Experience** pop-up automatically—no scrolling or opening Previous Orders.
- The mobile-friendly pop-up has five direct star choices, an optional short comment and a Maybe Later button. It only appears for the newest completed order and disappears permanently after that order is reviewed.
- Customers can leave a verified **1–5 star driver or pickup-experience rating**. The order automatically selects the assigned staff member, so customers cannot choose or rate somebody else.
- Each completed order can be reviewed only once. An optional 250-character comment is saved with the star rating, and the SNR Staff role receives a Discord alert in the private orders channel.
- Staff can open **More Tools → Staff Ratings** for weekly and monthly leaderboards, or run `/snrhub_ratings`. Rankings show average stars, number of reviews, five-star reviews, deliveries and pickups.
- Every website alert can now have its own private Discord destination. Run `/snrhub_channels` inside each destination channel and tap the alert type that belongs there. Categories include New Loyalty Accounts, Active Deliveries, Completed Deliveries, City Run Reward Claims, Raffle Number Requests, Customer Help, Reward Requests, and Reviews & Problems.
- Active delivery and pickup orders now show a visual **live progress tracker** on the customer webpage. Discord actions move the tracker through Placed, Accepted, On Way/Ready, Arrived/Payment and Complete, while the existing webpage alert continues checking every five seconds.
- Completed website orders now include **Order Again**. It safely refills the old basket and fulfillment choice for review; it never submits a new order until the customer presses Place Order.
- **Owner Admin → Daily Closing Report** gives an owner-only UK-day snapshot of sales, revenue, production cost, gross profit, margin, paid deliveries/pickups, delivery fees, code discounts, birthday discounts, open orders and wasted journeys. It does not reset data.
- Customers can save their birthday day and month once from the website. The annual birthday reward activates after a seven-day security wait, applies automatically to one order on their birthday, and cannot be used twice in the same year.
- **Owner Admin → Birthday Reward** can set a percentage reward, fixed cash reduction, or switch birthday rewards off. Owners can also correct a customer's saved birthday; close spellings of saved names are recognised.
- Birthday reductions are displayed in the customer checkout, Discord order receipt and daily closing report. The default on first deployment is **20% off one birthday order**.
- Discord quantity sales now guarantee the deal's loyalty points for **every individual deal sold**: 2 Mega Deals award 2 base points, while 2 Share Boxes award 4 base points. Any Gold, Platinum or SNR VIP membership bonus is added separately on top, and the receipt shows the full calculation.
- The signed-in website is now a compact app-style dashboard with **Home, Order, City Run and More** pages. Customers tap between pages instead of scrolling through the entire system, and the navigation stays visible on phones.
- Mobile spacing, logo size, dashboard cards and order controls have been tightened so the important information fits on screen faster without removing any features.
- A dedicated **SNR Owner** control centre gives owners a private dashboard, manual staff clock-off, VIP management and bot-logo control.
- A new valid square SNR Buns brand poster replaces the broken wide logo file and is designed for clean Discord avatar cropping and website display.
- Customer memberships progress automatically through Regular (0+), Bronze (10+), Silver (25+), Gold (50+), Platinum (100+) and SNR VIP (200+). Owners can override a level or return it to automatic mode.
- Bronze and Silver earn +1 Golden Ticket per purchase; Gold earns +1 loyalty point and +1 ticket; Platinum earns +1 loyalty point and +2 tickets; SNR VIP earns +2 loyalty points and +3 tickets. Bonuses use the same sale, finance, delivery and jackpot transaction.
- Membership delivery prices are automatic: Regular £100, Bronze £90, Silver £75, Gold £50, Platinum £25 and SNR VIP free delivery.
- Owners can create fixed-price or percentage delivery discount codes with optional expiry dates and usage limits, then disable them from the Owner panel.
- Delivery checkout and Discord receipts show food subtotal, discount, membership delivery fee and final total. Finance records the actual final amount paid.
- The website and Discord now explain that meal-deal Golden Tickets are issued, checked and entered automatically. A winning ticket creates an immediate £5,000 alert for both the customer webpage and staff receipt.
- Customers create a zero-point loyalty account on the website without making a purchase first.
- New accounts activate immediately with no staff approval. Discord posts an informational alert and mentions the configured `SNR Staff` role.
- Customers can normally reset their own password with their memorable question. If they cannot, the SNR Owner can issue a secure one-use recovery link from Discord without seeing or choosing the new password.
- Existing older accounts are prompted to add a memorable question after logging in.
- Customers can order several different deals and choose 0–10 of each (20 deals maximum per order).
- Customers choose **Delivery** or **Pickup from SNR Buns** on the same order page. Pickup is always free and remains available when no delivery driver is clocked in.
- Pickup orders use their own Discord workflow: **Accept Pickup Order → Ready for Collection → Collected & Customer Paid**. The customer webpage alerts them when collection is ready.
- A live subtotal appears before checkout; the server recalculates it securely when submitted.
- Customers can add an optional 200-character order note for meeting points or food instructions.
- The confirmation asks customers to allow 5–7 minutes.
- Delivery orders move through Waiting, Accepted, Driver On The Way, Driver Arrived and Delivered/Paid.
- The customer page checks for delivery updates every five seconds and shows a bright status notification.
- Loyalty customers see a visual progress bar across all 38 City Run businesses.
- Website delivery is available while at least one staff member is clocked in; pickup ordering stays open without a driver.
- The uncluttered hub has five starting buttons: **New Sale**, **Deliveries**, **Customers**, **Staff Shift** and **More Tools**. Clock controls live together inside **Staff Shift**. Shifts expire after eight hours if somebody forgets to clock off.
- **New Sale** supports quantities from ×1 to ×10, so several identical deals can be recorded together with one combined receipt.
- Record Sale, Check Customer and Redeem Reward include an alphabetically ordered 25-name dropdown with **Previous Names** and **Next Names**, plus **Type / Suggest Name** for spelling correction.
- Delivery Orders opens a dashboard showing the live queue, drivers, today’s revenue and gross profit.
- New-account notices, City Run claim records and owner announcements stay available for staff audit. Completed delivery alerts move to Completed Deliveries so the active queue stays accurate.
- Paid multi-deal orders record every selected deal in sales, finance, loyalty and Golden Tickets exactly once.
- After a driver is marked Arrived, staff can mark a delivery as a **Wasted Journey**. This adds a £500 account fee, closes the order without recording a sale or rewards, and blocks new web deliveries.
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

When City Run is active, every loyalty point earned produces one reveal credit. Customers reveal digital business stickers on the website, complete routes for the configured RP rewards, and send a verified claim to Discord. Reveals never reset the displayed loyalty balance.

## Update the existing Railway service

Upload every file from this folder to the existing GitHub repository and commit directly to `main`. Do not delete the Railway volume or database. Railway redeploys automatically.

The files `city_run.py`, `raffles.py` and `staff_shifts.py` must be uploaded with `bot.py`, `snr_core.py`, `web_portal.py`, `reward_claims.py`, `customer_accounts.py`, `delivery_orders.py`, `snr-logo.png`, `requirements.txt`, `Procfile` and `railway.json`.

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

For the fully separated setup, create the private staff channels you want. Inside each channel run `/snrhub_channels`, then tap the matching category. Active orders stay in Active Deliveries while they are being handled; after payment, cancellation or a wasted journey, the finished card moves to Completed Deliveries.

Create another private Discord text channel named `new-accounts-created`, open it and run `/snrhub_accounts_setup`. All future loyalty-account creation notices—and any account notices still waiting to be sent—will go there instead of Delivery Orders. Staff are notified, but do not approve accounts.

The bot needs View Channel, Send Messages and Embed Links in both channels. The `SNR Staff` role must be mentionable, or the bot must have permission to mention roles, for alerts to ping it.

Create a Discord role named **SNR Owner** and assign it to the appropriate business owners. Server administrators also count as owners. Run `/snrhub_panel` again in the private staff channel to post the upgraded permanent panel; only an owner can post it. A staff member must press **Clock In** before delivery controls appear to customers.

Open **More Tools → Owner Admin** (or run `/snrhub_owner`) to use the private owner controls. Press **Set Bot Logo** once to change the bot's Discord profile picture to the supplied official SNR Buns logo. Discord may temporarily rate-limit repeated picture changes, so do not repeatedly press it.

## Staff workflow

1. Open **Staff Shift** and press **Clock In** when accepting deliveries.
2. For counter sales, press **New Sale**, choose a recent/saved character or type the name, then tap the meal. A normal ×1 sale records immediately and its receipt disappears after ten seconds. Use **Multiple Items** only for ×2–×10.
3. For website orders, open **Delivery Orders**, press **Accept Delivery**, then **Driver On The Way** when leaving.
4. At the customer's location press **Driver Has Arrived**. This alerts their live webpage.
5. After receiving payment, press **Delivered & Customer Paid**. Only this final step records sales and rewards.
6. The customer can then open **Previous orders** on their website Order page and rate the automatically assigned driver from 1–5 stars. Discord alerts SNR Staff when it is submitted.
   A 1–2 star rating requires a reason, alerts SNR Management and remains open until a manager presses **Mark Customer Helped**.
7. Customers can use **Report a problem** for a paid order. Resolve the Discord help card after the customer has been assisted.
8. If the journey is wasted after arrival, press **Wasted Journey — Charge £500**. Only the assigned driver or SNR Management can do this.
9. In **Delivery Orders**, use **Fee Paid** or **Waive Fee** to clear the warning and restore that customer's delivery access.
10. Open **Staff Shift** and press **Clock Off** when delivery closes. If everyone clocks off, website delivery is disabled but customers can still place pickup orders.

## Raffle workflow

1. An owner opens **More Tools → Raffle Centre → Owner Controls → Create New**, then enters the raffle title, prize and price for each number. The website automatically opens numbers 1–100.
2. A logged-in customer opens **Raffle**, chooses up to ten available numbers and presses **Request My Numbers**. Their numbers are reserved while payment is waiting.
3. Discord alerts `SNR Staff` in the Raffle Number Requests channel configured through `/snrhub_channels`. Staff collect the displayed total and press **Confirm Payment**. Press **Reject & Release** if payment is not made.
4. Staff can use **Add Paid Entry** for an in-person customer, choose their saved name and enter comma-separated numbers such as `4, 17, 82`.
5. The owner presses **Close Entries** after every pending payment is resolved, then **Draw Winner**. The draw uses only confirmed paid numbers and cannot be drawn twice.
6. The finished winner and number appear in Discord and on the website. Create the next raffle only after the previous raffle has been drawn or cancelled.

For pickup orders, press **Accept Pickup Order**, then **Ready for Collection** when the food is ready. The customer is alerted on their webpage. Press **Collected & Customer Paid** only after payment; that final step records finance, loyalty and Golden Tickets.

## VIP membership workflow

- Every completed counter sale or paid delivery increases the customer's purchase count.
- The qualifying purchase receives the newly unlocked level's bonus immediately.
- The website displays the customer's level, exact bonuses and purchases remaining to the next level.
- Discord customer cards, name dropdowns, sale receipts and delivery orders display the same current level.
- Owners use **More Tools → Owner Admin → Manage VIP Level** to choose any membership level manually, or return it to **Automatic progression**.
- Owners use **More Tools → Owner Admin → Discount Codes** to create or disable checkout codes. Choose `percent` or `fixed`, enter the amount, and optionally set maximum uses and a `YYYY-MM-DD` expiry.
- Owners use **More Tools → Owner Admin → Customer Banner** to publish a normal update, promotion or urgent notice across every logged-in customer page. Open pages refresh the banner automatically within about five seconds, and **Turn Banner Off** removes it the same way.
- Owner overrides and forced clock-offs are written to the audit log.
- If staff record the wrong counter sale, owners use **More Tools → Owner Admin → Undo Last Counter Sale**, carefully check the preview, then confirm. Jackpot-winning sales require manual management review and cannot be quick-undone.

Pending orders and requests are durable. If Discord or Railway restarts, unsent alerts retry. Unique order references prevent double-counting.

## Late-order warnings

Every active order card and the Delivery Dashboard now show green, orange or red order health based on time spent in the current stage. Discord alerts SNR Staff once when an order turns orange and once when it turns red. Moving the order to its next stage immediately resets the timer and alert state.

- Waiting: orange at 5 minutes, red at 7 minutes
- Accepted/preparing: orange at 7 minutes, red at 12 minutes
- Driver on the way: orange at 10 minutes, red at 15 minutes
- Driver arrived: orange at 5 minutes, red at 8 minutes
- Ready for pickup: orange at 10 minutes, red at 15 minutes
- Payment processing: orange at 3 minutes, red at 5 minutes

## Slash commands

- `/snrhub_panel` — post the permanent staff panel
- `/snrhub_owner` — open the owner-only control centre
- `/snrhub_orders_setup` — set the private delivery and pickup-order channel
- `/snrhub_accounts_setup` — set the separate private New Accounts Created alert channel
- `/snrhub_city_run_claims_setup` — set the private City Run reward-claim channel
- `/snrhub_channels` — owner one-tap setup for every separate alert channel
- `/snrhub_accounts_pending` — review recent account activity and any older approval requests
- `/snrhub_city_run` — view City Run status and owner controls
- `/snrhub_orders_pending` — review delivery orders
- `/snrhub_raffle` — open the integrated raffle centre
- `/snrhub_sale` — record a sale
- `/snrhub_customer` — check a customer
- `/snrhub_birdy` — generate copy-ready Birdy posts
- `/snrhub_report` — finance reporting
- `/snrhub_ratings` — verified weekly or monthly staff rating leaderboard

Every command uses the unique `snrhub_` prefix, so it will not conflict with other bots.

## Security

Passwords and memorable answers are stored only as salted PBKDF2 hashes. Five failed password or memorable-answer attempts cause a 15-minute lock. Password resets revoke old sessions. Public forms never reveal finance data, staff identities, password hashes or the hidden £5,000 jackpot position.

## Tests

Run `python -m unittest -v` from this folder.
