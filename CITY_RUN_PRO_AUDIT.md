# City Run poster-pro-1 — implementation and audit

## Included
- One illustrated poster above Board Corners; duplicate blue board removed from HTML.
- Per-customer grayscale overlays on collected tiles on that same poster.
- Explicit business geometry, including the two duplicated illustrations (UwU and Search & Rescue).
- Reveals use the corresponding illustration, without the old baked-in rarity/name labels.
- Updated SNR logo based on the supplied photograph, using built-in image generation.
  Prompt: faithfully extract and clean the red/yellow Snr. Buns sign, burger buns,
  centered on black, preserving lettering, no wall or scenery.
- Set and grand completion events queued within the sticker award transaction.
- Reward fulfilled/cancelled events queued within staff resolution transactions.
- Corner unlock events recorded alongside bonus credits.
- Discord retries failed sends; events stay pending until a message ID is recorded.
- Fixed misplaced undefined-variable callback line introduced by earlier update.
- No reset of customer balances, collected stickers, sales, rarity weights, or prize limits.

## Verified locally
Python syntax checks and the full unittest suite, including new tests for:
single-poster HTML, overlay identities, all 38 artwork mappings, two Share Boxes
plus two Mega Deals producing six credits only after payment and only once,
reveal replay safety, completion events without a sale, event retention, and
single reward handover. Existing account, raffle, fee, delivery, finance and
reward-deduction regression tests remain in the suite.

## Limits — not a live certification
- Live Railway deployment, iOS/LB Phone rendering and real Discord delivery have
  not been verified. Browser installation failed in this environment.
- Original poster tiles are small raster illustrations. Correct framing does
  NOT make them genuine 4K. This build does not contain 38 newly rendered 4K
  business photographs. Those require original high-resolution art or individually
  recreated and visually approved assets. Do not advertise these as 4K.
- Generated logo is a cleaned recreation, not an original vector logo.
- RP cash, vehicles, food and one-month VIP City Run rewards require staff
  handover. Marking a City Run claim fulfilled records handover and stock; it does
  not deliver items into FiveM or automatically create a timed VIP entitlement.
- A Discord success followed by a process crash before saving its message ID can
  duplicate a notification. Event numbers make such retries identifiable.
- Existing completed sets are not bulk-announced retroactively. Future reveal
  activity queues any completed sets missing their unique event records.

## Deployment / acceptance
Back up the Railway persistent database first. Extract the ZIP and update the
project source/assets, NOT the persistent database or environment variables.
No databases, credentials or .env files are included in this package.
After deployment, inspect page source for `poster-pro-1`.
Verify the City Run Claims channel is private and the bot can view/send/embed.
Use a separate test customer: pay for two Share Boxes, confirm four base credits,
open one, verify the same named business photo and greyed poster tile. VIP bonus
credits follow the existing membership rules. Verify route completion and reward
handover messages in Discord before announcing the upgrade to customers.
