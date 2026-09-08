from __future__ import annotations

import asyncio
import os
import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

from snr_core import (
    AVERAGE_DESSERT_COST,
    AVERAGE_DRINK_COST,
    AVERAGE_FOOD_COST,
    DEALS,
    SNRDatabase,
    VIP_LEVELS,
    birdy_post,
    normalize_name,
    vip_level_for_sales,
)
from web_portal import start_web_server
from reward_claims import ClaimStore
from customer_accounts import Accounts
from delivery_orders import ACTIVE_STATUSES, DeliveryStore
from staff_shifts import StaffShifts


TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
STAFF_ROLE_NAME = os.getenv("STAFF_ROLE_NAME", "SNR Staff")
MANAGER_ROLE_NAME = os.getenv("MANAGER_ROLE_NAME", "SNR Management")
OWNER_ROLE_NAME = os.getenv("OWNER_ROLE_NAME", "SNR Owner")
WEBSITE_URL = os.getenv("WEBSITE_URL", "https://worker-production-2c48.up.railway.app").rstrip("/")
DATABASE_PATH = os.getenv("DATABASE_PATH", "snr_staff_hub.db")
LEGACY_DATA_FILE = os.getenv("LEGACY_DATA_FILE", "loyalty_data.json")
JACKPOT_POOL_SIZE = int(os.getenv("JACKPOT_POOL_SIZE", "1000"))
PORT = int(os.getenv("PORT", "8080"))

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
db = SNRDatabase(DATABASE_PATH, JACKPOT_POOL_SIZE)
db_lock = asyncio.Lock()
claims = ClaimStore(db)
accounts = Accounts(db)
orders = DeliveryStore(db)
shifts = StaffShifts(db)


def money(value: float | int) -> str:
    return f"£{float(value):,.2f}"


def has_role(interaction: discord.Interaction, role_name: str) -> bool:
    if not isinstance(interaction.user, discord.Member):
        return False
    if interaction.user.guild_permissions.administrator:
        return True
    return any(role.name == role_name for role in interaction.user.roles)


def is_staff(interaction: discord.Interaction) -> bool:
    return (has_role(interaction, STAFF_ROLE_NAME) or has_role(interaction, MANAGER_ROLE_NAME)
            or has_role(interaction, OWNER_ROLE_NAME))


def is_owner(interaction: discord.Interaction) -> bool:
    return has_role(interaction, OWNER_ROLE_NAME)


async def require_owner(interaction: discord.Interaction) -> bool:
    if is_owner(interaction):
        return True
    message = f"❌ This control is restricted to the **{OWNER_ROLE_NAME}** role."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)
    return False


def staff_ping(channel):
    role = discord.utils.get(channel.guild.roles, name=STAFF_ROLE_NAME)
    if not role:
        return None, discord.AllowedMentions.none()
    return role.mention, discord.AllowedMentions(roles=[role])


def management_ping(channel):
    role = (discord.utils.get(channel.guild.roles, name=MANAGER_ROLE_NAME)
            or discord.utils.get(channel.guild.roles, name=OWNER_ROLE_NAME))
    if not role:
        return None, discord.AllowedMentions.none()
    return role.mention, discord.AllowedMentions(roles=[role])


async def require_staff(interaction: discord.Interaction) -> bool:
    if is_staff(interaction):
        return True
    if interaction.response.is_done():
        await interaction.followup.send("❌ This SNR system is staff-only.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ This SNR system is staff-only.", ephemeral=True)
    return False


async def send_ephemeral(interaction: discord.Interaction, content=None, *, embed=None, view=None,
                         delete_after=None) -> None:
    """Replace a temporary menu, or send a fresh private response."""
    if interaction.response.is_done():
        await interaction.followup.send(
            content=content, embed=embed, view=view, ephemeral=True, delete_after=delete_after)
    elif interaction.message and interaction.message.flags.ephemeral:
        await interaction.response.edit_message(content=content, embed=embed, view=view)
        if delete_after:
            asyncio.create_task(delete_response_later(interaction, delete_after))
    else:
        await interaction.response.send_message(
            content=content, embed=embed, view=view, ephemeral=True, delete_after=delete_after)


async def delete_response_later(interaction: discord.Interaction, seconds: float) -> None:
    """Delete an ephemeral result without blocking the bot."""
    await asyncio.sleep(seconds)
    try:
        await interaction.delete_original_response()
    except (discord.NotFound, discord.HTTPException):
        pass


def panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🍔 SNR BUNS — PRO STAFF HUB",
        description=(
            "Everything starts with one of the five buttons below.\n\n"
            "💷 **New Sale** — record a purchase\n"
            "🚗 **Deliveries** — manage active orders\n"
            "👥 **Customers** — accounts and rewards\n"
            "🕒 **Staff Shift** — clock in or off\n"
            "🧰 **More Tools** — finance, jackpot, Birdy and owner controls\n\n"
            "Customers do **not** need Discord."
        ),
        colour=discord.Colour.gold(),
    )
    embed.add_field(name="Fastest job", value="Tap **New Sale**, choose the customer, then choose the deal.", inline=False)
    embed.set_thumbnail(url=f"{WEBSITE_URL}/snr-logo.png")
    embed.set_footer(text=f"SNR Buns • Staff access only • Owner controls: {OWNER_ROLE_NAME}")
    return embed


def customer_embed(customer: dict) -> discord.Embed:
    membership = customer["membership"]
    embed = discord.Embed(
        title=f"🍔 {customer['display_name']}",
        colour=discord.Colour.orange(),
    )
    embed.add_field(name="Loyalty Points", value=f"**{customer['loyalty_points']}**", inline=True)
    embed.add_field(name="Golden Tickets", value=f"**{customer['golden_tickets']}**", inline=True)
    embed.add_field(name="Jackpot Wins", value=f"**{customer['jackpot_wins']}**", inline=True)
    embed.add_field(name="Sales", value=f"**{customer['lifetime_sales']}**", inline=True)
    embed.add_field(name="Revenue", value=f"**{money(customer['revenue'])}**", inline=True)
    embed.add_field(name="Production Cost", value=f"**{money(customer['production_cost'])}**", inline=True)
    embed.add_field(
        name="Gross Profit",
        value=f"**{money(customer['gross_profit'])}** ({customer['profit_margin']:.1f}%)",
        inline=True,
    )
    embed.add_field(
        name="Items Sold",
        value=f"**{customer['food_sold']} food • {customer['drinks_sold']} drinks**",
        inline=True,
    )
    embed.add_field(name="Website Account", value=f"**{accounts.status(customer['display_name'])}**", inline=True)
    birthday = orders.birthday_status(customer['customer_key'])
    birthday_value = ((f"**{birthday['date']}** • Rewards currently paused" if not birthday['active'] else
                       f"**{birthday['date']}** • {birthday['reward']}")
                      + ("\n🎂 **REWARD READY TODAY**" if birthday['eligible'] else "")
                      if birthday['saved'] else "Not added")
    embed.add_field(name="Birthday Reward", value=birthday_value, inline=True)
    progress = (f"{membership['remaining']} purchase(s) to {membership['next_level']}"
                if membership['next_level'] else ("Owner-set membership" if membership['manual'] else "Highest level reached"))
    embed.add_field(
        name=f"{membership['emoji']} Customer Membership",
        value=(f"**{membership['name']}**\n{progress}\n"
               f"Per purchase bonus: +{membership['bonus_points']} loyalty • +{membership['bonus_tickets']} Golden Ticket(s)\n"
               f"Delivery: **{'FREE' if int(membership['delivery_fee']) == 0 else money(membership['delivery_fee'])}**"),
        inline=False,
    )
    fee = orders.outstanding_fee(customer['customer_key'])
    embed.add_field(
        name="⚠️ Delivery Account",
        value=(f"**{money(fee['amount'])} OWED — WASTED JOURNEY**" if fee else "**CLEAR**"),
        inline=False,
    )
    rewards = customer["unclaimed_rewards"]
    if rewards:
        lines = [f"`{r['reward_code']}` — {r['description']}" for r in rewards[:8]]
        embed.add_field(name="🎁 Unclaimed Rewards", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="🎁 Unclaimed Rewards", value="None", inline=False)
    return embed


def sale_embed(result: dict) -> discord.Embed:
    deal = result["deal"]
    customer = result["customer"]
    won = result["jackpot_won"]
    quantity = int(result.get("quantity", 1))
    total_price = deal.price * quantity
    total_cost = deal.production_cost * quantity
    total_profit = deal.gross_profit * quantity
    item_parts = []
    if deal.key == "sweet_treat":
        item_parts.append(f"{deal.food * quantity} desserts")
    elif deal.food:
        item_parts.append(f"{deal.food * quantity} food")
    if deal.drinks:
        item_parts.append(f"{deal.drinks * quantity} drinks")
    embed = discord.Embed(
        title="🏆 GOLDEN TICKET FOUND!" if won else "✅ SNR SALE RECORDED",
        colour=discord.Colour.gold() if won else discord.Colour.green(),
    )
    embed.add_field(name="Customer", value=f"**{customer['display_name']}**", inline=True)
    embed.add_field(name="Deal", value=f"**{deal.name} ×{quantity}**", inline=True)
    embed.add_field(name="Sale", value=f"**{money(total_price)}**", inline=True)
    embed.add_field(name="Items", value=" + ".join(item_parts), inline=True)
    embed.add_field(name="Production Cost", value=f"**{money(total_cost)}**", inline=True)
    embed.add_field(
        name="Gross Profit",
        value=f"**{money(total_profit)}** ({deal.profit_margin:.1f}%)",
        inline=True,
    )
    awarded_points = int(result.get("loyalty_awarded", deal.loyalty_points))
    awarded_tickets = int(result.get("tickets_awarded", deal.golden_tickets))
    base_points = int(result.get("base_loyalty_awarded", deal.loyalty_points))
    membership_points = int(result.get("membership_loyalty_awarded", awarded_points - base_points))
    loyalty_value = (
        (f"**{deal.loyalty_points} per deal × {quantity} = +{base_points} base points**\n"
         + (f"Membership bonus: +{membership_points}\n" if membership_points else "")
         + f"Added now: **+{awarded_points}** → **{customer['loyalty_points']} total**")
        if awarded_points
        else f"No point on this deal • **{customer['loyalty_points']} total**"
    )
    embed.add_field(name="Loyalty", value=loyalty_value, inline=True)
    embed.add_field(name="Golden Tickets", value=f"+{awarded_tickets}", inline=True)
    membership = customer["membership"]
    embed.add_field(name="Membership", value=f"{membership['emoji']} **{membership['name']}**", inline=True)
    if won:
        embed.add_field(
            name="🏆 JACKPOT PRIZE",
            value=(
                "**£5,000 CASH**\n"
                "Extremely rare SNR Golden Mystery Ticket\n"
                f"Reward: `{', '.join(result.get('jackpot_reward_codes') or [result['jackpot_reward_code']])}`"
            ),
            inline=False,
        )
    else:
        embed.add_field(
            name="🎟️ Automatic Golden Ticket Entry",
            value=(f"**{awarded_tickets} ticket(s) issued and entered automatically.**\n"
                   "Nothing else needs entering. If a ticket matches the hidden winner, this receipt "
                   "immediately changes to the £5,000 winning alert."),
            inline=False,
        )
    transaction_ids = result.get("transaction_ids") or [result["transaction_id"]]
    transaction_text = transaction_ids[0] if len(transaction_ids) == 1 else f"{transaction_ids[0]} to {transaction_ids[-1]}"
    embed.set_footer(text=f"Transaction {transaction_text}")
    return embed


def finance_embed(stats: dict, title: str) -> discord.Embed:
    embed = discord.Embed(title=title, colour=discord.Colour.green())
    embed.add_field(name="Sales", value=f"**{stats['sales']}**", inline=True)
    embed.add_field(name="Revenue", value=f"**{money(stats['revenue'])}**", inline=True)
    embed.add_field(name="Production Cost", value=f"**{money(stats['production_cost'])}**", inline=True)
    embed.add_field(name="Gross Profit", value=f"**{money(stats['gross_profit'])}**", inline=True)
    embed.add_field(name="Profit Margin", value=f"**{stats['profit_margin']:.1f}%**", inline=True)
    embed.add_field(name="Golden Tickets", value=f"**{stats['tickets']}**", inline=True)
    embed.add_field(name="Food/Desserts", value=f"**{stats['food']}**", inline=True)
    embed.add_field(name="Drinks", value=f"**{stats['drinks']}**", inline=True)
    embed.add_field(name="Loyalty Points", value=f"**{stats['loyalty']}**", inline=True)
    breakdown = "\n".join(
        (
            f"• **{d['deal_name']} ×{d['quantity']}** — "
            f"Revenue {money(d['revenue'])} • Cost {money(d['production_cost'])} • "
            f"Profit {money(d['gross_profit'])} ({d['profit_margin']:.1f}%)"
        )
        for d in stats["deals"]
    ) or "No sales recorded for this period."
    embed.add_field(name="Deal Profit Breakdown", value=breakdown[:1024], inline=False)
    embed.set_footer(
        text=(
            f"Average costs: food {money(AVERAGE_FOOD_COST)} • drink {money(AVERAGE_DRINK_COST)} • "
            f"dessert {money(AVERAGE_DESSERT_COST)} • Excludes reward packs and overheads"
        )
    )
    return embed


def closing_report_embed(stats: dict, delivery: dict) -> discord.Embed:
    embed = discord.Embed(title="🔒 SNR DAILY CLOSING REPORT", colour=discord.Colour.gold())
    embed.description = "Owner-only snapshot for today in UK time. This does not reset or delete anything."
    embed.add_field(name="Completed Sales", value=f"**{stats['sales']}**", inline=True)
    embed.add_field(name="Revenue", value=f"**{money(stats['revenue'])}**", inline=True)
    embed.add_field(name="Gross Profit", value=f"**{money(stats['gross_profit'])}**", inline=True)
    embed.add_field(name="Production Cost", value=f"**{money(stats['production_cost'])}**", inline=True)
    embed.add_field(name="Profit Margin", value=f"**{stats['profit_margin']:.1f}%**", inline=True)
    embed.add_field(name="Loyalty / Tickets", value=f"**{stats['loyalty']} points • {stats['tickets']} tickets**", inline=True)
    embed.add_field(name="Website Orders", value=(f"**{delivery['orders']} paid**\n"
                    f"{delivery['deliveries']} delivery • {delivery['pickups']} pickup"), inline=True)
    embed.add_field(name="Order Adjustments", value=(f"Delivery fees: **{money(delivery['delivery_fees'])}**\n"
                    f"Code discounts: **−{money(delivery['code_discounts'])}**\n"
                    f"Birthday rewards: **−{money(delivery['birthday_discounts'])}**"), inline=True)
    embed.add_field(name="Still Open", value=f"**{delivery['active_orders']} order(s)**", inline=True)
    embed.add_field(name="Wasted Journeys Today", value=(f"**{delivery['wasted_journeys']}** • "
                    f"{money(delivery['wasted_fees'])} charged"), inline=True)
    embed.add_field(name="Birthday Rewards Due", value=f"**{delivery['birthday_rewards_due']}**", inline=True)
    breakdown = "\n".join(f"• {row['deal_name']} ×{row['quantity']} — {money(row['revenue'])}"
                          for row in stats['deals']) or "No completed sales today."
    embed.add_field(name="Deals Sold", value=breakdown[:1024], inline=False)
    embed.set_footer(text="Run this again at any time for the latest totals • UK day")
    return embed


async def send_name_result(interaction: discord.Interaction, action: str, entered_name: str) -> None:
    exact = db.get_customer(entered_name)
    if exact:
        await continue_action(interaction, action, exact["display_name"])
        return
    suggestion = db.suggest_name(entered_name)
    if suggestion and normalize_name(suggestion) != normalize_name(entered_name):
        await send_ephemeral(
            interaction,
            f"Did you mean **{suggestion}**?",
            view=NameChoiceView(action, entered_name, suggestion),
        )
        return
    await continue_action(interaction, action, entered_name)


async def continue_action(interaction: discord.Interaction, action: str, name: str) -> None:
    if action == "sale":
        message = f"Customer: **{' '.join(p.capitalize() for p in name.split())}**\nChoose the deal sold:"
        await send_ephemeral(interaction, message, view=DealView(name, "sale"))
        return
    if action == "account_create":
        try:
            code = accounts.issue_setup(name, str(interaction.user.id), str(interaction.user))
            customer = db.get_customer(name)
            message = (
                f"✅ Website account created for **{discord.utils.escape_markdown(customer['display_name'])}**.\n"
                f"One-time setup code: `{code}`\n"
                "Give this code privately to that customer after checking their in-game identity. "
                "It expires after 24 hours. They use it once to choose their own password."
            )
        except ValueError as exc:
            message = f"❌ {exc}"
        await send_ephemeral(interaction, message)
        return
    customer = db.get_customer(name)
    if not customer:
        message = "ℹ️ Customer not found. Record their first sale to create them."
        await send_ephemeral(interaction, message, delete_after=5)
        return
    if action == "account_reset":
        code = accounts.issue_setup(name, str(interaction.user.id), str(interaction.user), reset=True)
        message = (
            f"🔐 Password reset for **{discord.utils.escape_markdown(customer['display_name'])}**.\n"
            f"New one-time setup code: `{code}`\n"
            "Their old password and website sessions are disabled. Give this privately after checking identity. "
            "It expires after 24 hours."
        )
        await send_ephemeral(interaction, message)
    elif action == "check":
        await send_ephemeral(interaction, embed=customer_embed(customer))
    elif action == "vip":
        if not await require_owner(interaction):
            return
        message = (f"Manage membership for **{discord.utils.escape_markdown(customer['display_name'])}**.\n"
                   f"Current level: {customer['membership']['emoji']} **{customer['membership']['name']}**")
        view = VIPLevelView(customer['display_name'])
        await send_ephemeral(interaction, message, view=view)
    elif action == "redeem":
        rewards = customer["unclaimed_rewards"]
        if not rewards:
            message = f"ℹ️ **{customer['display_name']}** has no unclaimed rewards."
            await send_ephemeral(interaction, message, delete_after=5)
        else:
            message = f"Choose the reward being given to **{customer['display_name']}**:"
            view = RewardView(rewards)
            await send_ephemeral(interaction, message, view=view)


class NameModal(discord.ui.Modal):
    customer_name = discord.ui.TextInput(
        label="Customer character name",
        placeholder="Example: Cody Ortega",
        min_length=2,
        max_length=60,
    )

    def __init__(self, action: str):
        titles = {"sale": "Record Sale", "account_create": "Create Website Account",
                  "account_reset": "Reset Website Password"}
        super().__init__(title=titles.get(action, "Find Customer"))
        self.action = action

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await require_staff(interaction):
            return
        await send_name_result(interaction, self.action, str(self.customer_name))


class NameChoiceView(discord.ui.View):
    def __init__(self, action: str, entered: str, suggested: str):
        super().__init__(timeout=120)
        self.action = action
        self.entered = entered
        self.suggested = suggested

    @discord.ui.button(label="Use suggested customer", style=discord.ButtonStyle.success)
    async def use_suggestion(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await require_staff(interaction):
            await continue_action(interaction, self.action, self.suggested)

    @discord.ui.button(label="Create as new", style=discord.ButtonStyle.secondary)
    async def use_new(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await require_staff(interaction):
            await continue_action(interaction, self.action, self.entered)


class CustomerSelect(discord.ui.Select):
    def __init__(self, action, names, page, debts, memberships):
        self.action = action
        start = page * 25
        options = [discord.SelectOption(
            label=((f"£{debts.get(normalize_name(name), 0):,} OWED — " if debts.get(normalize_name(name)) else "")
                   + f"{memberships[normalize_name(name)]['emoji']} {name}")[:100],
            value=name,
            description=("Wasted Journey fee outstanding" if debts.get(normalize_name(name))
                         else f"{memberships[normalize_name(name)]['name']} member"),
            emoji=("⚠️" if debts.get(normalize_name(name)) else "👤"),
        ) for name in names[start:start + 25]]
        super().__init__(placeholder="Select a character name", options=options)

    async def callback(self, interaction):
        if await require_staff(interaction):
            await continue_action(interaction, self.action, self.values[0])


class CustomerPickerView(discord.ui.View):
    def __init__(self, action, page=0):
        super().__init__(timeout=180)
        self.action = action
        self.names = sorted(db.customer_names(), key=normalize_name)
        self.debts = orders.outstanding_debt_map()
        self.memberships = db.vip_membership_map()
        self.page = max(0, min(page, max(0, (len(self.names) - 1) // 25)))
        if self.names:
            self.add_item(CustomerSelect(action, self.names, self.page, self.debts, self.memberships))

    @discord.ui.button(label="Previous Names", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction, button):
        if await require_staff(interaction):
            await interaction.response.edit_message(
                content="Choose a customer or type their name:",
                view=CustomerPickerView(self.action, self.page - 1),
            )

    @discord.ui.button(label="Next Names", emoji="➡️", style=discord.ButtonStyle.secondary, row=1)
    async def next(self, interaction, button):
        if await require_staff(interaction):
            await interaction.response.edit_message(
                content="Choose a customer or type their name:",
                view=CustomerPickerView(self.action, self.page + 1),
            )

    @discord.ui.button(label="Type / Suggest Name", emoji="✏️", style=discord.ButtonStyle.primary, row=1)
    async def type_name(self, interaction, button):
        if await require_staff(interaction):
            await interaction.response.send_modal(NameModal(self.action))


async def show_customer_picker(interaction, action):
    view = CustomerPickerView(action)
    count = len(view.names)
    await send_ephemeral(
        interaction,
        f"Choose a customer from the list ({count} saved), or use **Type / Suggest Name**. "
        "Use **Previous Names** and **Next Names** to move through every saved customer. "
        "Typed names still correct capitals and suggest close spellings.",
        view=view,
    )


class DealSelect(discord.ui.Select):
    def __init__(self, customer_name: str, mode: str):
        self.customer_name = customer_name
        self.mode = mode
        options = [
            discord.SelectOption(
                label=d.name,
                value=d.key,
                description=f"{d.item_summary} • £{d.price:,} • {d.golden_tickets} Golden ticket(s)",
                emoji="🍔",
            )
            for d in DEALS.values()
        ]
        super().__init__(placeholder="Choose the deal", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await require_staff(interaction):
            return
        deal_key = self.values[0]
        if self.mode == "birdy":
            post = birdy_post("deal", deal_key=deal_key)
            await interaction.response.edit_message(content=f"```text\n{post}\n```", view=None, embed=None)
            return
        deal = DEALS[deal_key]
        await interaction.response.edit_message(
            content=(f"Customer: **{discord.utils.escape_markdown(self.customer_name)}**\n"
                     f"Deal: **{deal.name}**\nChoose how many were sold:"),
            view=QuantityView(self.customer_name, deal_key), embed=None)


class QuantitySelect(discord.ui.Select):
    def __init__(self, customer_name: str, deal_key: str):
        self.customer_name = customer_name
        self.deal_key = deal_key
        deal = DEALS[deal_key]
        options = [discord.SelectOption(
            label=f"×{amount}", value=str(amount),
            description=f"{amount} {deal.name} — {money(deal.price * amount)} total",
            emoji="🧾",
        ) for amount in range(1, 11)]
        super().__init__(placeholder="Choose amount: ×1 to ×10", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await require_staff(interaction):
            return
        quantity = int(self.values[0])
        await interaction.response.defer(ephemeral=True)
        try:
            async with db_lock:
                result = db.record_sale_quantity(
                    self.customer_name,
                    self.deal_key,
                    quantity,
                    str(interaction.user.id),
                    str(interaction.user),
                )
            receipt = sale_embed(result)
        except ValueError as exc:
            await interaction.edit_original_response(
                content=f"❌ Could not record this sale: {exc}", embed=None, view=None)
            asyncio.create_task(delete_response_later(interaction, 10))
            return
        except Exception:
            logging.exception("Quantity sale failed for %s ×%s", self.deal_key, quantity)
            await interaction.edit_original_response(
                content=("❌ Something went wrong while displaying this sale. "
                         "Check the customer record before trying it again."),
                embed=None, view=None)
            return
        await interaction.edit_original_response(content=None, embed=receipt, view=None)
        asyncio.create_task(delete_response_later(interaction, 10))


class QuantityView(discord.ui.View):
    def __init__(self, customer_name: str, deal_key: str):
        super().__init__(timeout=180)
        self.add_item(QuantitySelect(customer_name, deal_key))


class DealView(discord.ui.View):
    def __init__(self, customer_name: str = "", mode: str = "sale"):
        super().__init__(timeout=180)
        self.add_item(DealSelect(customer_name, mode))


class RewardSelect(discord.ui.Select):
    def __init__(self, rewards: list[dict]):
        options = [
            discord.SelectOption(
                label=r["reward_code"],
                value=r["reward_code"],
                description=r["description"][:100],
                emoji="🎁",
            )
            for r in rewards[:25]
        ]
        super().__init__(placeholder="Choose the reward being given", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await require_staff(interaction):
            return
        async with db_lock:
            try:
                reward = db.claim_reward(self.values[0], str(interaction.user.id), str(interaction.user))
            except ValueError as exc:
                await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
                return
        await interaction.response.edit_message(
            content=(
                f"✅ **Reward marked as claimed**\n"
                f"Customer: **{reward['customer_name']}**\n"
                f"Reward: **{reward['description']}**\n"
                f"Code: `{reward['reward_code']}`"
            ),
            view=None,
        )


class RewardView(discord.ui.View):
    def __init__(self, rewards: list[dict]):
        super().__init__(timeout=180)
        self.add_item(RewardSelect(rewards))


class BirdySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="We’re Open", value="open", emoji="🍔"),
            discord.SelectOption(label="Promote a Deal", value="deal", emoji="🔥"),
            discord.SelectOption(label="Golden Mystery Ticket", value="jackpot", emoji="🎟️"),
            discord.SelectOption(label="Mystery Ticket Winner", value="winner", emoji="🏆"),
            discord.SelectOption(label="Loyalty Scheme", value="loyalty", emoji="⭐"),
            discord.SelectOption(label="Delivery Service", value="delivery", emoji="🚗"),
            discord.SelectOption(label="Event Catering", value="catering", emoji="🎉"),
            discord.SelectOption(label="Hiring", value="hiring", emoji="📋"),
        ]
        super().__init__(placeholder="Choose the Birdy post", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await require_staff(interaction):
            return
        kind = self.values[0]
        if kind == "deal":
            await interaction.response.edit_message(
                content="Choose the deal to advertise:", view=DealView(mode="birdy"), embed=None
            )
            return
        try:
            post = birdy_post(kind, winner=db.latest_jackpot_winner())
        except ValueError as exc:
            await interaction.response.edit_message(content=f"ℹ️ {exc}", view=None, embed=None)
            return
        await interaction.response.edit_message(content=f"```text\n{post}\n```", view=None, embed=None)


class BirdyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(BirdySelect())


class VIPLevelSelect(discord.ui.Select):
    def __init__(self, customer_name):
        self.customer_name = customer_name
        options = [discord.SelectOption(
            label="Automatic progression", value="Automatic", emoji="🔄",
            description="Use completed purchases to choose the level",
        )]
        for name, details in VIP_LEVELS.items():
            options.append(discord.SelectOption(
                label=name, value=name, emoji=details["emoji"],
                description=(f"+{details['bonus_points']} loyalty • +{details['bonus_tickets']} tickets • "
                             f"£{details['delivery_fee']} delivery")[:100],
            ))
        super().__init__(placeholder="Set membership level", options=options)

    async def callback(self, interaction):
        if not await require_owner(interaction):
            return
        customer = db.set_vip_override(
            self.customer_name, self.values[0], str(interaction.user.id), str(interaction.user))
        await interaction.response.edit_message(
            content=(f"✅ **{discord.utils.escape_markdown(customer['display_name'])}** is now "
                     f"{customer['membership']['emoji']} **{customer['membership']['name']}**. "
                     f"Mode: {'manual owner override' if customer['membership']['manual'] else 'automatic progression'}."),
            view=None,
        )


class VIPLevelView(discord.ui.View):
    def __init__(self, customer_name):
        super().__init__(timeout=180)
        self.add_item(VIPLevelSelect(customer_name))


class OwnerClockOffSelect(discord.ui.Select):
    def __init__(self, active_staff):
        self.staff = {row['staff_id']: row for row in active_staff[:25]}
        options = [discord.SelectOption(label=row['staff_name'][:100], value=row['staff_id'], emoji="🔴",
                                        description="Manually clock this person off")
                   for row in active_staff[:25]]
        super().__init__(placeholder="Choose staff member to clock off", options=options)

    async def callback(self, interaction):
        if not await require_owner(interaction):
            return
        row = shifts.force_clock_out(self.values[0], interaction.user.id, str(interaction.user))
        remaining = len(shifts.active(interaction.guild_id))
        await interaction.response.edit_message(
            content=(f"🔴 **{discord.utils.escape_markdown(row['staff_name'])}** was manually clocked off. "
                     f"{remaining} delivery staff remain clocked in."),
            view=None,
        )


class OwnerClockOffView(discord.ui.View):
    def __init__(self, active_staff):
        super().__init__(timeout=180)
        self.add_item(OwnerClockOffSelect(active_staff))


class DiscountCodeModal(discord.ui.Modal, title="Create Delivery Discount Code"):
    code = discord.ui.TextInput(label="Code", placeholder="Example: SNR10", min_length=3, max_length=20)
    discount_type = discord.ui.TextInput(label="Type", placeholder="percent or fixed", min_length=5, max_length=7)
    amount = discord.ui.TextInput(label="Amount", placeholder="10 = 10% or £10", min_length=1, max_length=6)
    max_uses = discord.ui.TextInput(label="Maximum uses", placeholder="Leave blank for unlimited", required=False, max_length=5)
    expires_on = discord.ui.TextInput(label="Expiry date", placeholder="YYYY-MM-DD or leave blank", required=False, max_length=10)

    async def on_submit(self, interaction):
        if not await require_owner(interaction):
            return
        try:
            row = orders.create_discount_code(
                self.code.value, self.discount_type.value, self.amount.value, self.max_uses.value,
                self.expires_on.value, interaction.user.id, str(interaction.user))
        except (ValueError, TypeError) as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return
        value = (f"{row['amount']}% off" if row['discount_type'] == 'percent'
                 else f"£{row['amount']:,} off")
        limit = f"{row['max_uses']} uses" if row['max_uses'] else "unlimited uses"
        expiry = row['expires_on'] or "no expiry"
        await interaction.response.send_message(
            f"✅ Discount code **{row['code']}** created: **{value}**, {limit}, {expiry}.",
            ephemeral=True)


class DiscountCodeSelect(discord.ui.Select):
    def __init__(self, rows):
        options = []
        for row in rows[:25]:
            value = (f"{row['amount']}%" if row['discount_type'] == 'percent'
                     else f"£{row['amount']:,}")
            options.append(discord.SelectOption(
                label=row['code'], value=row['code'], emoji="🏷️",
                description=f"{value} off • {row['uses']}/{row['max_uses'] or '∞'} used"[:100]))
        super().__init__(placeholder="Disable an active discount code", options=options)

    async def callback(self, interaction):
        if not await require_owner(interaction):
            return
        row = orders.disable_discount_code(self.values[0], interaction.user.id, str(interaction.user))
        await interaction.response.edit_message(
            content=f"✅ Discount code **{row['code']}** has been disabled.", view=None)


class DiscountCodesView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        active = orders.discount_codes(active_only=True)
        if active:
            self.add_item(DiscountCodeSelect(active))

    @discord.ui.button(label="Create Code", emoji="➕", style=discord.ButtonStyle.success, row=1)
    async def create_code(self, interaction, button):
        if await require_owner(interaction):
            await interaction.response.send_modal(DiscountCodeModal())


class BirthdayRewardModal(discord.ui.Modal, title="Configure Birthday Reward"):
    reward_type = discord.ui.TextInput(
        label="Type", placeholder="percent, fixed or off", min_length=3, max_length=7)
    amount = discord.ui.TextInput(
        label="Amount", placeholder="20 = 20% or £20 (use 150 for free Quick Fix)",
        required=False, max_length=6)

    async def on_submit(self, interaction):
        if not await require_owner(interaction):
            return
        try:
            row = orders.configure_birthday_reward(
                self.reward_type.value, self.amount.value, interaction.user.id, str(interaction.user))
        except (ValueError, TypeError) as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return
        if not row['active']:
            message = "✅ Birthday rewards have been switched off. Saved customer birthdays remain protected."
        else:
            reward = (f"{row['amount']}% off one order" if row['reward_type'] == 'percent'
                      else f"£{row['amount']:,} off one order")
            message = f"✅ The annual birthday reward is now **{reward}**."
        await interaction.response.send_message(message, ephemeral=True)


class BirthdayCorrectionModal(discord.ui.Modal, title="Set or Correct Customer Birthday"):
    customer_name = discord.ui.TextInput(
        label="Customer character name", placeholder="Example: Cody Ortega", min_length=2, max_length=60)
    day = discord.ui.TextInput(label="Birthday day", placeholder="Example: 15", min_length=1, max_length=2)
    month = discord.ui.TextInput(label="Birthday month", placeholder="Example: 8", min_length=1, max_length=2)

    async def on_submit(self, interaction):
        if not await require_owner(interaction):
            return
        try:
            result = orders.set_birthday_by_owner(
                self.customer_name.value, self.month.value, self.day.value,
                interaction.user.id, str(interaction.user))
        except (ValueError, TypeError) as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return
        await interaction.response.send_message(
            f"✅ Birthday for **{result['customer_name']}** set to **{result['date']}**.", ephemeral=True)


class OwnerAdminView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="Manage VIP Level", emoji="👑", style=discord.ButtonStyle.primary)
    async def manage_vip(self, interaction, button):
        if await require_owner(interaction):
            await show_customer_picker(interaction, "vip")

    @discord.ui.button(label="Clock Staff Off", emoji="🔴", style=discord.ButtonStyle.danger)
    async def clock_staff_off(self, interaction, button):
        if not await require_owner(interaction):
            return
        active = shifts.active(interaction.guild_id)
        if not active:
            await send_ephemeral(interaction, "ℹ️ Nobody is currently clocked in.", delete_after=5)
            return
        await send_ephemeral(
            interaction,
            "Choose the staff member you want to manually clock off:",
            view=OwnerClockOffView(active),
        )

    @discord.ui.button(label="Owner Dashboard", emoji="📊", style=discord.ButtonStyle.secondary)
    async def owner_dashboard(self, interaction, button):
        if not await require_owner(interaction):
            return
        counts = db.vip_counts()
        active = shifts.active(interaction.guild_id)
        fees = orders.outstanding_fees(interaction.guild_id)
        stats = db.report(today=True)
        active_codes = orders.discount_codes(active_only=True)
        embed = discord.Embed(title="👑 SNR OWNER DASHBOARD", colour=discord.Colour.gold())
        embed.description = "Private business overview and administration."
        embed.add_field(name="Today", value=f"{stats['sales']} sales • {money(stats['revenue'])} revenue • {money(stats['gross_profit'])} profit", inline=False)
        embed.add_field(name="Memberships", value="\n".join(f"{VIP_LEVELS[name]['emoji']} {name}: **{total}**" for name, total in counts.items()), inline=True)
        embed.add_field(name="Delivery Staff", value=f"**{len(active)} clocked in**", inline=True)
        embed.add_field(name="Fees Owed", value=f"**{len(fees)} • {money(sum(row['amount'] for row in fees))}**", inline=True)
        embed.add_field(name="Discount Codes", value=f"**{len(active_codes)} active**", inline=True)
        await send_ephemeral(interaction, embed=embed)

    @discord.ui.button(label="Discount Codes", emoji="🏷️", style=discord.ButtonStyle.success)
    async def discount_codes(self, interaction, button):
        if not await require_owner(interaction):
            return
        active = orders.discount_codes(active_only=True)
        lines = []
        for row in active[:20]:
            value = (f"{row['amount']}%" if row['discount_type'] == 'percent'
                     else f"£{row['amount']:,}")
            lines.append(f"• **{row['code']}** — {value} off • {row['uses']}/{row['max_uses'] or '∞'} used")
        await send_ephemeral(
            interaction,
            "🏷️ **Delivery Discount Codes**\n" + ("\n".join(lines) if lines else "No active codes yet."),
            view=DiscountCodesView())

    @discord.ui.button(label="Daily Closing Report", emoji="🔒", style=discord.ButtonStyle.primary, row=1)
    async def daily_closing(self, interaction, button):
        if not await require_owner(interaction):
            return
        await send_ephemeral(
            interaction,
            embed=closing_report_embed(db.report(today=True), orders.daily_summary(interaction.guild_id)),
        )

    @discord.ui.button(label="Birthday Reward", emoji="🎂", style=discord.ButtonStyle.success, row=1)
    async def birthday_reward(self, interaction, button):
        if not await require_owner(interaction):
            return
        config = orders.birthday_config()
        current = ("OFF" if not config['active'] else
                   f"{config['amount']}% off" if config['reward_type'] == 'percent'
                   else f"£{config['amount']:,} off")
        await interaction.response.send_message(
            f"🎂 Current annual birthday reward: **{current}**\n"
            "Choose `percent`, `fixed`, or `off`. A fixed £150 reward works like a free Quick Fix.",
            ephemeral=True,
            view=BirthdayRewardConfigView(),
        )

    @discord.ui.button(label="Website Service Mode", emoji="🚦", style=discord.ButtonStyle.primary, row=1)
    async def service_mode(self, interaction, button):
        if not await require_owner(interaction):
            return
        current = orders.service_mode()
        await send_ephemeral(
            interaction,
            f"🚦 Current website mode: **{current['label']}**\nChoose the mode customers should see now:",
            view=ServiceModeView(),
        )

    @discord.ui.button(label="Set Bot Logo", emoji="🖼️", style=discord.ButtonStyle.secondary)
    async def set_bot_logo(self, interaction, button):
        if not await require_owner(interaction):
            return
        await interaction.response.edit_message(content="🖼️ Updating the bot logo…", embed=None, view=None)
        try:
            updated = await bot.user.edit(avatar=Path(__file__).with_name("snr-logo.png").read_bytes())
            await interaction.edit_original_response(
                content=f"✅ The main Discord picture for **{updated.name}** is now the official SNR Buns logo.")
        except discord.HTTPException:
            logging.exception("Discord rejected bot avatar update")
            await interaction.edit_original_response(
                content="❌ Discord could not update the picture right now. Wait an hour and try once more.")
        asyncio.create_task(delete_response_later(interaction, 5))


class BirthdayRewardConfigView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="Change Birthday Reward", emoji="⚙️", style=discord.ButtonStyle.primary)
    async def change(self, interaction, button):
        if await require_owner(interaction):
            await interaction.response.send_modal(BirthdayRewardModal())

    @discord.ui.button(label="Correct Customer Birthday", emoji="✏️", style=discord.ButtonStyle.secondary)
    async def correct(self, interaction, button):
        if await require_owner(interaction):
            await interaction.response.send_modal(BirthdayCorrectionModal())


class ServiceModeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        modes = (
            ("Open", "open", "🟢", discord.ButtonStyle.success),
            ("Busy", "busy", "🟠", discord.ButtonStyle.primary),
            ("Pickup Only", "pickup_only", "🛍️", discord.ButtonStyle.secondary),
            ("Pause Deliveries", "delivery_paused", "⏸️", discord.ButtonStyle.secondary),
            ("Closed", "closed", "🔴", discord.ButtonStyle.danger),
        )
        for label, mode, emoji, style in modes:
            button = discord.ui.Button(label=label, emoji=emoji, style=style)

            async def callback(interaction, chosen=mode):
                if not await require_owner(interaction):
                    return
                result = orders.set_service_mode(
                    chosen, interaction.user.id, str(interaction.user))
                await interaction.response.edit_message(
                    content=f"✅ Website service mode changed to **{result['label']}**.", view=None)
                asyncio.create_task(delete_response_later(interaction, 5))

            button.callback = callback
            self.add_item(button)


class CustomerToolsView(discord.ui.View):
    """Customer jobs grouped away from the everyday sale and delivery buttons."""

    @discord.ui.button(label="Check Customer", emoji="🔎", style=discord.ButtonStyle.primary)
    async def check_customer(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await require_staff(interaction):
            await show_customer_picker(interaction, "check")

    @discord.ui.button(label="Redeem Reward", emoji="🎁", style=discord.ButtonStyle.success)
    async def redeem(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await require_staff(interaction):
            await show_customer_picker(interaction, "redeem")

    @discord.ui.button(label="Pack Requests", emoji="🎴", style=discord.ButtonStyle.secondary)
    async def pack_requests(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await require_staff(interaction):
            await show_pack_requests(interaction)

    @discord.ui.button(label="Account Activity", emoji="👤", style=discord.ButtonStyle.secondary)
    async def account_requests(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await require_staff(interaction):
            await show_account_requests(interaction)


class ShiftToolsView(discord.ui.View):
    """All delivery shift actions on one small screen."""

    @discord.ui.button(label="Clock In", emoji="🟢", style=discord.ButtonStyle.success)
    async def clock_in(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await require_staff(interaction):
            return
        changed = shifts.clock_in(interaction.user.id, str(interaction.user), interaction.guild_id)
        await send_ephemeral(
            interaction,
            "🟢 You are clocked in. Website delivery ordering is now available."
            if changed else "ℹ️ You are already clocked in.", delete_after=5)

    @discord.ui.button(label="Clock Off", emoji="🔴", style=discord.ButtonStyle.danger)
    async def clock_out(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await require_staff(interaction):
            return
        changed = shifts.clock_out(interaction.user.id)
        remaining = len(shifts.active(interaction.guild_id))
        await send_ephemeral(
            interaction,
            (f"🔴 You are clocked off. {remaining} staff member(s) remain available for delivery."
             if changed else "ℹ️ You were not clocked in."), delete_after=5)

    @discord.ui.button(label="Who’s Clocked In", emoji="🕒", style=discord.ButtonStyle.secondary)
    async def shift_status(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await require_staff(interaction):
            return
        active = shifts.active(interaction.guild_id)
        message = "\n".join(f"• {row['staff_name']} — since {row['clocked_in_at']}" for row in active)
        await send_ephemeral(
            interaction,
            "🟢 **Clocked in for delivery**\n" + message if active else "🔴 No delivery staff are clocked in.",
            delete_after=5)


def review_leaderboard_embed(guild_id, days):
    rows = orders.review_leaderboard(guild_id, days)
    title = "THIS WEEK" if days == 7 else "THIS MONTH"
    embed = discord.Embed(
        title=f"⭐ SNR STAFF RATINGS — {title}", colour=discord.Colour.gold())
    if not rows:
        embed.description = "No customer ratings have been submitted in this period yet."
    else:
        lines = []
        medals = ("🥇", "🥈", "🥉")
        for index, row in enumerate(rows[:15]):
            medal = medals[index] if index < len(medals) else f"**{index + 1}.**"
            average = float(row["average_rating"] or 0)
            lines.append(
                f'{medal} **{discord.utils.escape_markdown(row["staff_name"])}** — '
                f'**{average:.2f}/5** from {int(row["reviews"])} review(s) '
                f'• {int(row["five_star_reviews"])} five-star '
                f'• {int(row["deliveries"])} delivery / {int(row["pickups"])} pickup')
        embed.description = "\n".join(lines)
    embed.set_footer(text="Ranked by average stars, then number of reviews • Verified completed orders only")
    return embed


def customer_review_embed(row):
    stars = "★" * int(row["rating"]) + "☆" * (5 - int(row["rating"]))
    mode = "Pickup experience" if row["fulfillment_type"] == "pickup" else "Delivery driver"
    embed = discord.Embed(title=f"⭐ NEW CUSTOMER RATING — ORDER #{row['order_id']}",
                          colour=discord.Colour.gold())
    embed.add_field(name="Customer", value=discord.utils.escape_markdown(row["customer_name"]), inline=True)
    embed.add_field(name=mode, value=discord.utils.escape_markdown(row["staff_name"]), inline=True)
    embed.add_field(name="Rating", value=f"**{stars} ({int(row['rating'])}/5)**", inline=False)
    if row.get("comment"):
        embed.add_field(name="Customer comment",
                        value=discord.utils.escape_markdown(row["comment"]), inline=False)
    if int(row["rating"]) <= 2:
        issue_labels = {"food_quality": "Food quality", "delivery_time": "Delivery time",
                        "driver_behaviour": "Driver behaviour", "missing_items": "Missing items",
                        "other": "Other"}
        embed.colour = discord.Colour.red()
        embed.title = f"🚨 LOW RATING — ORDER #{row['order_id']}"
        embed.add_field(name="What went wrong",
                        value=f"**{issue_labels.get(row.get('issue_category'), 'Not provided')}**",
                        inline=False)
        embed.add_field(name="Management action", value="Please contact the customer and resolve this case.", inline=False)
    embed.set_footer(text="Verified: linked to the customer’s own completed and paid order")
    return embed


class LowRatingView(discord.ui.View):
    def __init__(self, review_id):
        super().__init__(timeout=None)
        self.review_id = int(review_id)
        button = discord.ui.Button(label="Mark Customer Helped", emoji="✅",
                                   style=discord.ButtonStyle.success,
                                   custom_id=f"snr:low-rating:{self.review_id}:resolved")

        async def callback(interaction):
            if not (has_role(interaction, MANAGER_ROLE_NAME) or is_owner(interaction)):
                await interaction.response.send_message("Managers or the SNR Owner can close this case.", ephemeral=True)
                return
            row = orders.resolve_low_review(self.review_id, interaction.user.id, str(interaction.user))
            embed = customer_review_embed(row)
            embed.add_field(name="Resolution", value=f"✅ Resolved by **{discord.utils.escape_markdown(str(interaction.user))}**", inline=False)
            await interaction.response.edit_message(embed=embed, view=None)

        button.callback = callback
        self.add_item(button)


def support_request_embed(row):
    labels = {"food_quality": "Food quality", "delivery_time": "Delivery time",
              "driver_behaviour": "Driver behaviour", "missing_items": "Missing items",
              "other": "Other"}
    embed = discord.Embed(title=f"🆘 ORDER PROBLEM — ORDER #{row['order_id']}",
                          colour=discord.Colour.red())
    embed.add_field(name="Customer", value=discord.utils.escape_markdown(row["customer_name"]), inline=True)
    embed.add_field(name="Problem", value=labels.get(row["issue_type"], row["issue_type"]), inline=True)
    embed.add_field(name="Status", value=row["status"].upper(), inline=True)
    if row.get("details"):
        embed.add_field(name="Customer message", value=discord.utils.escape_markdown(row["details"]), inline=False)
    embed.set_footer(text="Submitted from the customer’s verified web account")
    return embed


class SupportRequestView(discord.ui.View):
    def __init__(self, support_id):
        super().__init__(timeout=None)
        self.support_id = int(support_id)
        button = discord.ui.Button(label="Mark Problem Resolved", emoji="✅",
                                   style=discord.ButtonStyle.success,
                                   custom_id=f"snr:support:{self.support_id}:resolved")

        async def callback(interaction):
            if not await require_staff(interaction):
                return
            row = orders.resolve_support(self.support_id, interaction.user.id, str(interaction.user))
            await interaction.response.edit_message(embed=support_request_embed(row), view=None)

        button.callback = callback
        self.add_item(button)


class ReviewPeriodView(discord.ui.View):
    @discord.ui.button(label="Weekly Ratings", emoji="📅", style=discord.ButtonStyle.primary)
    async def weekly(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await require_staff(interaction):
            await send_ephemeral(interaction, embed=review_leaderboard_embed(interaction.guild_id, 7))

    @discord.ui.button(label="Monthly Ratings", emoji="🗓️", style=discord.ButtonStyle.secondary)
    async def monthly(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await require_staff(interaction):
            await send_ephemeral(interaction, embed=review_leaderboard_embed(interaction.guild_id, 30))


class MoreToolsView(discord.ui.View):
    """Occasional staff tools, kept off the everyday hub."""

    @discord.ui.button(label="Finance Today", emoji="📊", style=discord.ButtonStyle.primary)
    async def report(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await require_staff(interaction):
            return
        stats = db.report(today=True)
        await send_ephemeral(
            interaction, embed=finance_embed(stats, "💷 SNR BUNS — TODAY’S FINANCE CHECK"))

    @discord.ui.button(label="Golden Jackpot", emoji="🎟️", style=discord.ButtonStyle.secondary)
    async def jackpot(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await require_staff(interaction):
            return
        status = db.jackpot_status()
        embed = discord.Embed(title="🎟️ SNR GOLDEN MYSTERY TICKET", colour=discord.Colour.gold())
        embed.description = (
            "Jackpot prize: **£5,000 CASH**\n"
            "Drop rate: **1 hidden winner per 1,000 Golden Tickets (0.1%)**\n\n"
            "**How Golden Tickets work**\n"
            "• Every SNR meal deal automatically issues its listed ticket(s).\n"
            "• Tickets enter the current draw automatically—nobody types or claims a number.\n"
            "• The bot checks every ticket against the secret winning position immediately.\n"
            "• A winner triggers a large £5,000 alert for staff and on the customer’s website account.\n\n"
            f"Current cycle: **{status['cycle']}**\n"
            f"Tickets issued this cycle: **{status['tickets_issued']}/{status['pool_size']}**\n"
            f"Total winners: **{status['total_winners']}**\n\n"
            "The winning position stays hidden from staff and is guaranteed by ticket 1,000."
        )
        await send_ephemeral(interaction, embed=embed)

    @discord.ui.button(label="Birdy Post", emoji="📱", style=discord.ButtonStyle.secondary)
    async def birdy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await require_staff(interaction):
            await send_ephemeral(
                interaction, "Choose the post you want to copy into Birdy:", view=BirdyView())

    @discord.ui.button(label="Staff Ratings", emoji="⭐", style=discord.ButtonStyle.primary)
    async def staff_ratings(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await require_staff(interaction):
            await send_ephemeral(
                interaction,
                "⭐ **Verified Driver & Experience Ratings**\nChoose the period you want to check.",
                view=ReviewPeriodView())

    @discord.ui.button(label="Owner Admin", emoji="👑", style=discord.ButtonStyle.danger)
    async def owner_admin(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await require_owner(interaction):
            await send_ephemeral(
                interaction,
                "👑 **SNR Owner Controls**\nManage memberships, shifts, finances and branding.",
                view=OwnerAdminView(),
            )


class StaffPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="New Sale", emoji="💷", style=discord.ButtonStyle.success, custom_id="snr:record_sale")
    async def record_sale(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await require_staff(interaction):
            await show_customer_picker(interaction, "sale")

    @discord.ui.button(label="Deliveries", emoji="🚗", style=discord.ButtonStyle.success, custom_id="snr:delivery_orders")
    async def delivery_orders(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await show_delivery_orders(interaction)

    @discord.ui.button(label="Customers", emoji="👥", style=discord.ButtonStyle.primary, custom_id="snr:customers")
    async def customers(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await require_staff(interaction):
            await interaction.response.send_message(
                "👥 **Customer Tools**\nCheck an account, hand over a reward, or view requests.",
                view=CustomerToolsView(), ephemeral=True)

    @discord.ui.button(label="Staff Shift", emoji="🕒", style=discord.ButtonStyle.primary, custom_id="snr:staff_shift")
    async def staff_shift(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await require_staff(interaction):
            await interaction.response.send_message(
                "🕒 **Delivery Shift**\nClock in to open website orders. Clock off when you finish.",
                view=ShiftToolsView(), ephemeral=True)

    @discord.ui.button(label="More Tools", emoji="🧰", style=discord.ButtonStyle.secondary, custom_id="snr:more_tools")
    async def more_tools(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await require_staff(interaction):
            await interaction.response.send_message(
                "🧰 **More Tools**\nFinance, Golden Tickets, Birdy posts and owner controls.",
                view=MoreToolsView(), ephemeral=True)


def pack_claim_embed(row):
    embed = discord.Embed(title=f"🎴 PACK REQUEST #{row['id']}", colour=discord.Colour.gold())
    embed.description = (f"Customer: **{discord.utils.escape_markdown(row['customer_name'])}**\n"
                         "Reward: **1 pack containing 2 trading cards**\n"
                         f"Status: **{row['status']}**\n"
                         "The customer’s points stay unchanged while pending. Hand over the pack first, then confirm. "
                         "Confirming resets their loyalty points to **0**. Cancelling leaves their points unchanged.")
    return embed


class PackClaimView(discord.ui.View):
    def __init__(self, claim_id):
        super().__init__(timeout=None)
        self.claim_id = claim_id
        for status, label, style in [('fulfilled', 'Handed Over', discord.ButtonStyle.success),
                                     ('cancelled', 'Cancel & Return Points', discord.ButtonStyle.danger)]:
            button = discord.ui.Button(label=label, style=style, custom_id=f'snr:pack:{claim_id}:{status}')
            async def callback(interaction, target=status):
                if not await require_staff(interaction):
                    return
                row = claims.get(self.claim_id)
                if not row or str(interaction.guild_id) != row['guild_id']:
                    await interaction.response.send_message('This request belongs to another server.', ephemeral=True)
                    return
                await interaction.response.defer(ephemeral=True)
                try:
                    async with db_lock:
                        row = claims.resolve(self.claim_id, target, str(interaction.user.id), str(interaction.user))
                except ValueError as exc:
                    await interaction.followup.send(str(exc), ephemeral=True)
                    return
                await interaction.followup.send(
                    'Pack marked as handed over. The customer’s loyalty points are now 0.' if target=='fulfilled' else 'Cancelled. The customer’s points were not changed.', ephemeral=True)
                # Also update the canonical alert if this action came from the pending-request list.
                try:
                    await interaction.message.edit(embed=pack_claim_embed(row), view=None)
                    if row['message_id'] and str(interaction.message.id) != row['message_id']:
                        channel = bot.get_channel(int(row['channel_id'])) or await bot.fetch_channel(int(row['channel_id']))
                        canonical = channel.get_partial_message(int(row['message_id']))
                        await canonical.edit(embed=pack_claim_embed(row), view=None)
                        await canonical.delete(delay=120)
                    else:
                        await interaction.message.delete(delay=120)
                except discord.HTTPException:
                    logging.exception('Claim resolved but alert refresh failed: %s', self.claim_id)
            button.callback = callback
            self.add_item(button)


async def show_pack_requests(interaction):
    if not await require_staff(interaction):
        return
    rows = [r for r in claims.pending() if r['guild_id']==str(interaction.guild_id)]
    await send_ephemeral(
        interaction,
        f'{len(rows)} pending pack request(s). Showing the oldest 10; reopen after processing to see more.',
        delete_after=5)
    for row in rows[:10]:
        await interaction.followup.send(embed=pack_claim_embed(row), view=PackClaimView(row['id']),
                                        ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


def delivery_order_embed(row):
    fulfillment = row.get('fulfillment_type') or 'delivery'
    colours = {
        'pending': discord.Colour.orange(),
        'accepted': discord.Colour.blue(),
        'on_way': discord.Colour.purple(),
        'arrived': discord.Colour.teal(),
        'ready_for_pickup': discord.Colour.green(),
        'processing': discord.Colour.gold(),
        'paid': discord.Colour.green(),
        'cancelled': discord.Colour.red(),
        'wasted_journey': discord.Colour.red(),
    }
    status = {
        'pending': 'WAITING FOR DRIVER',
        'accepted': 'ACCEPTED — PREPARING',
        'on_way': 'DRIVER ON THE WAY',
        'arrived': 'DRIVER HAS ARRIVED',
        'ready_for_pickup': 'READY FOR COLLECTION',
        'processing': 'PROCESSING',
        'paid': 'DELIVERED & PAID — SALE RECORDED',
        'cancelled': 'CANCELLED',
        'wasted_journey': 'WASTED JOURNEY — £500 FEE OWED',
    }.get(row['status'], str(row['status']).upper())
    if fulfillment == 'pickup' and row['status'] == 'pending':
        status = 'WAITING FOR STAFF TO ACCEPT'
    elif fulfillment == 'pickup' and row['status'] == 'paid':
        status = 'COLLECTED & PAID — SALE RECORDED'
    icon = '🛍️' if fulfillment == 'pickup' else '🚗'
    order_type = 'PICKUP' if fulfillment == 'pickup' else 'DELIVERY'
    embed = discord.Embed(title=f"{icon} {order_type} ORDER #{row['id']}", colour=colours.get(row['status'], discord.Colour.orange()))
    embed.add_field(name='Customer', value=f"**{discord.utils.escape_markdown(row['customer_name'])}**", inline=True)
    embed.add_field(name='Total Owed', value=f"**{money(row['price'])}**", inline=True)
    embed.add_field(name='Status', value=f"**{status}**", inline=True)
    customer = db.get_customer(row['customer_key'])
    if customer:
        membership = customer['membership']
        embed.add_field(name='Membership', value=f"{membership['emoji']} **{membership['name']}**", inline=True)
    items = orders.items(row)
    lines = []
    loyalty = tickets = 0
    projected_sales = int(customer['lifetime_sales']) if customer else 0
    for item in items:
        deal = DEALS.get(item['key'])
        lines.append(f"**{item['quantity']} × {discord.utils.escape_markdown(item['name'])}** — {money(item['line_total'])}")
        if deal:
            for _ in range(int(item['quantity'])):
                projected_sales += 1
                projected_vip = vip_level_for_sales(
                    projected_sales, customer.get('vip_override') if customer else None)
                loyalty += deal.loyalty_points + int(projected_vip['bonus_points'])
                tickets += deal.golden_tickets + int(projected_vip['bonus_tickets'])
    embed.add_field(name='Order Items', value="\n".join(lines)[:1024] or row['deal_name'], inline=False)
    subtotal = int(row.get('subtotal') or row['price'])
    fee = int(row.get('delivery_fee') or 0)
    discount = int(row.get('discount_amount') or 0)
    birthday_discount = int(row.get('birthday_discount') or 0)
    breakdown = [f"Food: **{money(subtotal)}**"]
    if discount:
        breakdown.append(f"Code **{discord.utils.escape_markdown(row.get('discount_code') or '')}**: **−{money(discount)}**")
    if birthday_discount:
        breakdown.append(f"🎂 Birthday reward: **−{money(birthday_discount)}**")
    fee_label = 'Pickup charge' if fulfillment == 'pickup' else f"{row.get('membership_level') or 'Regular'} delivery"
    breakdown.append(f"{fee_label}: **{'FREE' if fee == 0 else money(fee)}**")
    breakdown.append(f"Final total: **{money(row['price'])}**")
    embed.add_field(name='Price Breakdown', value="\n".join(breakdown), inline=False)
    embed.add_field(name='Rewards After Payment',
                    value=(f"{loyalty} loyalty point(s) • {tickets} Golden ticket(s)\n"
                           "Tickets are issued and entered into the £5,000 draw automatically when payment is confirmed."),
                    inline=True)
    if row['status'] == 'paid':
        outcome = orders.ticket_result(row['id'])
        if outcome['jackpot_won']:
            embed.add_field(
                name='🏆 £5,000 GOLDEN TICKET WINNER!',
                value='One of this customer’s automatically issued tickets matched the hidden winner. Contact the customer and verify the jackpot reward now.',
                inline=False,
            )
        else:
            embed.add_field(
                name='🎟️ Automatic Entry Confirmed',
                value=f"{outcome['tickets']} Golden Ticket(s) issued, checked and entered automatically. No winning match on this order.",
                inline=False,
            )
    if fulfillment == 'pickup':
        embed.add_field(name='🛍️ Collection', value='**Customer will collect from SNR Buns**', inline=False)
    else:
        embed.add_field(
            name='📍 Postal / Delivery Location',
            value=f"**{discord.utils.escape_markdown(row['postal'])}**",
            inline=False,
        )
    if row.get('assigned_driver_name'):
        handler_label = 'Handling Staff' if fulfillment == 'pickup' else 'Driver'
        embed.add_field(name=handler_label, value=f"**{discord.utils.escape_markdown(row['assigned_driver_name'])}**", inline=True)
    if row.get('notes'):
        embed.add_field(name='📝 Customer Notes', value=discord.utils.escape_markdown(row['notes'])[:1024], inline=False)
    if row.get('sale_transaction_id'):
        embed.set_footer(text=f"Sale {row['sale_transaction_id']} • Confirmed by staff")
    else:
        embed.set_footer(text='Confirm payment only after the customer has paid')
    return embed


def delivery_fee_embed(row):
    colour = discord.Colour.red() if row['status'] == 'owed' else discord.Colour.green()
    status = {'owed': 'OWED', 'paid': 'PAID', 'waived': 'WAIVED'}.get(row['status'], row['status'].upper())
    embed = discord.Embed(title=f"⚠️ DELIVERY FEE #{row['id']}", colour=colour)
    embed.add_field(name='Customer', value=f"**{discord.utils.escape_markdown(row['customer_name'])}**", inline=True)
    embed.add_field(name='Amount', value=f"**{money(row['amount'])}**", inline=True)
    embed.add_field(name='Status', value=f"**{status}**", inline=True)
    embed.add_field(name='Reason', value='Wasted delivery journey', inline=False)
    embed.add_field(name='Original Order', value=f"**#{row['order_id']}**", inline=True)
    embed.set_footer(text=('New website deliveries are blocked until this is paid or waived'
                           if row['status'] == 'owed' else 'Resolved and fully recorded in the audit log'))
    return embed


class DeliveryFeeView(discord.ui.View):
    def __init__(self, fee_id):
        super().__init__(timeout=None)
        self.fee_id = int(fee_id)
        for label, emoji, style, target in (
            ('Fee Paid', '💷', discord.ButtonStyle.success, 'paid'),
            ('Waive Fee', '🕊️', discord.ButtonStyle.secondary, 'waived'),
        ):
            button = discord.ui.Button(label=label, emoji=emoji, style=style,
                                       custom_id=f'snr:delivery_fee:{self.fee_id}:{target}')

            async def callback(interaction, chosen=target):
                if not await require_staff(interaction):
                    return
                fee = orders.fee_get(self.fee_id)
                if not fee or fee['guild_id'] != str(interaction.guild_id):
                    await interaction.response.send_message('This fee belongs to another server.', ephemeral=True)
                    return
                await interaction.response.defer(ephemeral=True)
                try:
                    async with db_lock:
                        fee = orders.resolve_fee(self.fee_id, chosen, str(interaction.user.id), str(interaction.user))
                except ValueError as exc:
                    await interaction.followup.send(f'❌ {exc}', ephemeral=True)
                    return
                await interaction.followup.send(
                    ('✅ The £500 fee is marked paid. This customer can order deliveries again.'
                     if chosen == 'paid' else '✅ The £500 fee was waived. This customer can order deliveries again.'),
                    ephemeral=True,
                )
                try:
                    await interaction.message.edit(embed=delivery_fee_embed(fee), view=None)
                    await interaction.message.delete(delay=12 * 3600)
                except discord.HTTPException:
                    logging.exception('Fee resolved but Discord message refresh failed: %s', self.fee_id)

            button.callback = callback
            self.add_item(button)


class DeliveryOrderView(discord.ui.View):
    def __init__(self, order_id):
        super().__init__(timeout=None)
        self.order_id = int(order_id)
        row = orders.get(self.order_id)
        status = row['status'] if row else 'pending'
        fulfillment = (row.get('fulfillment_type') or 'delivery') if row else 'delivery'
        if status == 'pending':
            accept_label = 'Accept Pickup Order' if fulfillment == 'pickup' else 'Accept Delivery'
            action = discord.ui.Button(label=accept_label, emoji='✅', style=discord.ButtonStyle.primary,
                                       custom_id=f'snr:delivery:{self.order_id}:accepted')
            target = 'accepted'
        elif status == 'accepted':
            if fulfillment == 'pickup':
                action = discord.ui.Button(label='Ready for Collection', emoji='🛍️', style=discord.ButtonStyle.success,
                                           custom_id=f'snr:delivery:{self.order_id}:ready_for_pickup')
                target = 'ready_for_pickup'
            else:
                action = discord.ui.Button(label='Driver On The Way', emoji='🚗', style=discord.ButtonStyle.primary,
                                           custom_id=f'snr:delivery:{self.order_id}:on_way')
                target = 'on_way'
        elif status == 'on_way':
            action = discord.ui.Button(label='Driver Has Arrived', emoji='📍', style=discord.ButtonStyle.primary,
                                       custom_id=f'snr:delivery:{self.order_id}:arrived')
            target = 'arrived'
        else:
            paid_label = 'Collected & Customer Paid' if fulfillment == 'pickup' else 'Delivered & Customer Paid'
            action = discord.ui.Button(label=paid_label, emoji='💷', style=discord.ButtonStyle.success,
                                       custom_id=f'snr:delivery:{self.order_id}:paid')
            target = 'paid'
        cancel = discord.ui.Button(
            label='Cancel Order', emoji='✖️', style=discord.ButtonStyle.danger,
            custom_id=f'snr:delivery:{self.order_id}:cancelled',
        )

        async def action_callback(interaction, chosen=target):
            await self._update(interaction, chosen)

        async def cancel_callback(interaction):
            await self._update(interaction, 'cancelled')

        action.callback = action_callback
        cancel.callback = cancel_callback
        self.add_item(action)
        self.add_item(cancel)
        if status == 'arrived' and fulfillment == 'delivery':
            wasted = discord.ui.Button(label='Wasted Journey — Charge £500', emoji='⚠️',
                                       style=discord.ButtonStyle.danger,
                                       custom_id=f'snr:delivery:{self.order_id}:wasted_journey')

            async def wasted_callback(interaction):
                await self._update(interaction, 'wasted_journey')

            wasted.callback = wasted_callback
            self.add_item(wasted)

    async def _update(self, interaction, target):
        if not await require_staff(interaction):
            return
        row = orders.get(self.order_id)
        if not row or str(interaction.guild_id) != row['guild_id']:
            await interaction.response.send_message('This order belongs to another server.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            async with db_lock:
                can_override_driver = has_role(interaction, MANAGER_ROLE_NAME) or is_owner(interaction)
                if target == 'accepted':
                    if ((row.get('fulfillment_type') or 'delivery') == 'delivery'
                            and not any(entry['staff_id'] == str(interaction.user.id)
                                        for entry in shifts.active(interaction.guild_id))):
                        raise ValueError('Clock in before accepting a delivery so the customer knows a driver is available.')
                    row = orders.advance(self.order_id, target, str(interaction.user.id), str(interaction.user))
                    sales = []
                elif target in ('on_way', 'arrived', 'ready_for_pickup'):
                    row = orders.advance(
                        self.order_id, target, str(interaction.user.id), str(interaction.user),
                        allow_override=can_override_driver)
                    sales = []
                elif target == 'wasted_journey':
                    row, fee = orders.charge_wasted_journey(
                        self.order_id, str(interaction.user.id), str(interaction.user),
                        allow_override=can_override_driver)
                    sales = []
                else:
                    row, sales = orders.resolve(
                        self.order_id, target, str(interaction.user.id), str(interaction.user),
                        allow_override=can_override_driver)
        except ValueError as exc:
            await interaction.followup.send(f'❌ {exc}', ephemeral=True)
            # Replace a stale menu with the real current step. This commonly
            # happens when two drivers press Accept at almost the same time.
            current = orders.get(self.order_id)
            if current and current['status'] in ACTIVE_STATUSES:
                try:
                    await interaction.message.edit(
                        embed=delivery_order_embed(current), view=DeliveryOrderView(self.order_id))
                except discord.HTTPException:
                    logging.exception('Could not refresh stale delivery menu: %s', self.order_id)
            return
        except Exception:
            logging.exception('Delivery order processing failed: %s', self.order_id)
            await interaction.followup.send(
                '❌ The order could not be processed. Nothing was counted twice; please try again.',
                ephemeral=True,
            )
            return
        if target == 'accepted':
            response = ('✅ Pickup order accepted and assigned to you. Mark it ready when the food can be collected.'
                        if (row.get('fulfillment_type') or 'delivery') == 'pickup' else
                        '✅ Delivery accepted and assigned to you. The customer’s webpage has been updated.')
        elif target == 'on_way':
            response = '🚗 Marked Driver On The Way. The customer’s webpage is notifying them now.'
        elif target == 'arrived':
            response = '📍 Marked Driver Has Arrived. The customer’s webpage is alerting them that you are outside.'
        elif target == 'ready_for_pickup':
            response = '🛍️ Marked Ready for Collection. The customer’s webpage is alerting them to come to SNR Buns.'
        elif target == 'paid':
            won = any(result.get('jackpot_won') for result in sales)
            completed_word = ('Collected' if (row.get('fulfillment_type') or 'delivery') == 'pickup'
                              else 'Delivered')
            response = ((f'🏆 GOLDEN TICKET WINNER! This customer won the £5,000 jackpot. '
                         'Their webpage is alerting them now and the reward is recorded for staff verification.')
                        if won else
                        (f'✅ {completed_word} and payment confirmed for {sum(item["quantity"] for item in orders.items(row))} deal(s). '
                         'Sales, finance and loyalty are updated, and every Golden Ticket was issued and entered automatically.'))
        elif target == 'wasted_journey':
            response = ('⚠️ Wasted Journey recorded. £500 is now owed on the customer’s webpage and name, '
                        'and new deliveries are blocked. No sale, loyalty points or Golden Tickets were added.')
        else:
            response = 'Order cancelled. No sale or rewards were added. The customer’s webpage has been updated.'
        await interaction.followup.send(response, ephemeral=True)
        finished = target in ('paid', 'cancelled', 'wasted_journey')
        next_view = (DeliveryFeeView(fee['id']) if target == 'wasted_journey'
                     else None if finished else DeliveryOrderView(self.order_id))
        next_embed = delivery_fee_embed(orders.fee_get(fee['id'])) if target == 'wasted_journey' else delivery_order_embed(row)
        try:
            await interaction.message.edit(embed=next_embed, view=next_view)
            if row['message_id'] and str(interaction.message.id) != row['message_id']:
                channel = bot.get_channel(int(row['channel_id'])) or await bot.fetch_channel(int(row['channel_id']))
                canonical = channel.get_partial_message(int(row['message_id']))
                await canonical.edit(embed=next_embed, view=next_view)
                if finished and target != 'wasted_journey':
                    await canonical.delete(delay=12 * 3600)
            elif finished and target != 'wasted_journey':
                await interaction.message.delete(delay=12 * 3600)
        except discord.HTTPException:
            logging.exception('Order updated but Discord message refresh failed: %s', self.order_id)


def delivery_dashboard_embed(guild_id):
    counts = orders.status_counts(guild_id)
    fulfilment = orders.fulfillment_counts(guild_id)
    active_staff = shifts.active(guild_id)
    stats = db.report(today=True)
    fees = orders.outstanding_fees(guild_id)
    service = orders.service_mode()
    problems = [row for row in orders.pending_support() if row['guild_id'] == str(guild_id)]
    embed = discord.Embed(title='🚗 SNR DELIVERY DASHBOARD', colour=discord.Colour.orange())
    embed.add_field(name='Waiting', value=f"**{counts['pending']}**", inline=True)
    embed.add_field(name='Accepted', value=f"**{counts['accepted']}**", inline=True)
    embed.add_field(name='On The Way', value=f"**{counts['on_way']}**", inline=True)
    embed.add_field(name='Arrived', value=f"**{counts['arrived']}**", inline=True)
    embed.add_field(name='Ready for Pickup', value=f"**{counts['ready_for_pickup']}**", inline=True)
    embed.add_field(name='Active Types', value=f"**{fulfilment['delivery']} delivery • {fulfilment['pickup']} pickup**", inline=True)
    embed.add_field(name='Wasted Journey Fees', value=f"**{len(fees)} • {money(sum(row['amount'] for row in fees))} owed**", inline=True)
    embed.add_field(name='Website Mode', value=f"**{service['label']}**", inline=True)
    embed.add_field(name='Customer Problems', value=f"**{len(problems)} open**", inline=True)
    embed.add_field(name='Drivers Clocked In', value=f"**{len(active_staff)}**", inline=True)
    embed.add_field(name='Today’s Revenue', value=f"**{money(stats['revenue'])}**", inline=True)
    embed.add_field(name='Today’s Gross Profit', value=f"**{money(stats['gross_profit'])}**", inline=True)
    drivers = '\n'.join(f"• {discord.utils.escape_markdown(row['staff_name'])}" for row in active_staff)
    embed.add_field(name='Available Drivers', value=drivers or 'Nobody is clocked in', inline=False)
    embed.set_footer(text='Orders below are shown oldest first')
    return embed


async def show_delivery_orders(interaction):
    if not await require_staff(interaction):
        return
    rows = [row for row in orders.pending() if row['guild_id'] == str(interaction.guild_id)]
    rank = {name: index for index, name in enumerate(VIP_LEVELS)}
    rows.sort(key=lambda row: (-rank.get((db.get_customer(row['customer_key']) or {'membership': {'name': 'Regular'}})['membership']['name'], 0), row['id']))
    fees = orders.outstanding_fees(interaction.guild_id, 10)
    problems = [row for row in orders.pending_support(unsent=False, limit=10)
                if row['guild_id'] == str(interaction.guild_id)]
    await interaction.response.send_message(embed=delivery_dashboard_embed(interaction.guild_id), ephemeral=True)
    for row in rows[:10]:
        await interaction.followup.send(
            embed=delivery_order_embed(row), view=DeliveryOrderView(row['id']),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )
    for fee in fees:
        await interaction.followup.send(
            embed=delivery_fee_embed(fee), view=DeliveryFeeView(fee['id']),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )
    for row in problems:
        await interaction.followup.send(
            embed=support_request_embed(row), view=SupportRequestView(row['id']),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )


def account_request_embed(row):
    status = str(row['status']).upper()
    request_label = 'PASSWORD RESET' if row['request_type'] == 'reset' else 'NEW ACCOUNT'
    colour = {
        'pending': discord.Colour.orange(),
        'approved': discord.Colour.green(),
        'rejected': discord.Colour.red(),
    }.get(row['status'], discord.Colour.orange())
    embed = discord.Embed(title=f"👤 WEBSITE ACCOUNT REQUEST #{row['id']}", colour=colour)
    embed.add_field(
        name='Customer',
        value=f"**{discord.utils.escape_markdown(row['customer_name'])}**",
        inline=True,
    )
    embed.add_field(name='Request', value=f'**{request_label}**', inline=True)
    embed.add_field(name='Status', value=f'**{status}**', inline=True)
    embed.description = (
        'Confirm that this is the correct in-game customer before approving. '
        'Their password is securely stored and is never shown to staff.'
    )
    if row['request_type'] == 'reset':
        embed.set_footer(text='Approving this reset signs out all old website sessions')
    else:
        embed.set_footer(text='Approval unlocks this customer’s loyalty card')
    return embed


def account_created_embed(row):
    embed = discord.Embed(title=f"👤 NEW LOYALTY ACCOUNT #{row['id']}", colour=discord.Colour.green())
    embed.add_field(name='Customer', value=f"**{discord.utils.escape_markdown(row['customer_name'])}**", inline=True)
    embed.add_field(name='Status', value='**ACTIVE NOW**', inline=True)
    embed.description = (
        'The customer created their own loyalty account on the website. '
        'No staff approval is needed and the account starts with zero points.'
    )
    embed.set_footer(text='Password and memorable answer are securely hashed and never shown')
    return embed


class AccountRequestView(discord.ui.View):
    def __init__(self, request_id):
        super().__init__(timeout=None)
        self.request_id = int(request_id)
        approve = discord.ui.Button(
            label='Approve Account', emoji='✅', style=discord.ButtonStyle.success,
            custom_id=f'snr:account:{self.request_id}:approved',
        )
        reject = discord.ui.Button(
            label='Reject', emoji='✖️', style=discord.ButtonStyle.danger,
            custom_id=f'snr:account:{self.request_id}:rejected',
        )

        async def approve_callback(interaction):
            await self._resolve(interaction, 'approved')

        async def reject_callback(interaction):
            await self._resolve(interaction, 'rejected')

        approve.callback = approve_callback
        reject.callback = reject_callback
        self.add_item(approve)
        self.add_item(reject)

    async def _resolve(self, interaction, decision):
        if not await require_staff(interaction):
            return
        row = accounts.get_request(self.request_id)
        if not row or str(interaction.guild_id) != row['guild_id']:
            await interaction.response.send_message('This request belongs to another server.', ephemeral=True)
            return
        try:
            async with db_lock:
                row = accounts.resolve_request(
                    self.request_id, decision, str(interaction.user.id), str(interaction.user)
                )
        except ValueError as exc:
            await interaction.response.send_message(f'❌ {exc}', ephemeral=True)
            return
        message = (
            '✅ Account approved. The customer can now log in using the password they chose.'
            if decision == 'approved'
            else 'Account request rejected. No password or account access was changed.'
        )
        await interaction.response.send_message(message, ephemeral=True)
        try:
            await interaction.message.edit(embed=account_request_embed(row), view=None)
            if row['message_id'] and str(interaction.message.id) != row['message_id']:
                channel = bot.get_channel(int(row['channel_id'])) or await bot.fetch_channel(int(row['channel_id']))
                canonical = channel.get_partial_message(int(row['message_id']))
                await canonical.edit(embed=account_request_embed(row), view=None)
                await canonical.delete(delay=120)
            else:
                await interaction.message.delete(delay=120)
        except discord.HTTPException:
            logging.exception('Account request resolved but message refresh failed: %s', self.request_id)


async def show_account_requests(interaction):
    if not await require_staff(interaction):
        return
    rows = [row for row in accounts.pending() if row['guild_id'] == str(interaction.guild_id)]
    recent = [row for row in accounts.created_notifications(limit=10)
              if row['guild_id'] == str(interaction.guild_id)]
    summary = "\n".join(f"• **{discord.utils.escape_markdown(row['customer_name'])}** — active"
                         for row in recent) or "No recently created accounts."
    await send_ephemeral(
        interaction,
        f'**Recent account activity**\n{summary}\n\n'
        f'{len(rows)} older approval request(s) still waiting.', delete_after=8)
    for row in rows[:10]:
        await interaction.followup.send(
            embed=account_request_embed(row), view=AccountRequestView(row['id']),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )


@tasks.loop(seconds=2)
async def notify_pack_claims():
    if not bot.is_ready():
        return
    for row in claims.pending(unsent=True)[:20]:
        try:
            channel = bot.get_channel(int(row['channel_id'])) or await bot.fetch_channel(int(row['channel_id']))
            if not isinstance(channel, discord.TextChannel) or str(channel.guild.id) != row['guild_id']:
                logging.warning('Pack claim channel is unavailable or belongs to another server: %s', row['id'])
                continue
            mention, allowed = staff_ping(channel)
            content = mention or "🎴 **New website reward claim**"
            message = await channel.send(content=content, embed=pack_claim_embed(row),
                                         view=PackClaimView(row['id']), allowed_mentions=allowed)
            claims.notified(row['id'], message.id)
        except Exception:
            # Leave the durable outbox row unsent so the next pass retries it.
            logging.exception('Pack claim alert delivery failed; will retry: %s', row['id'])


@tasks.loop(seconds=10)
async def notify_delivery_orders():
    if not bot.is_ready():
        return
    for row in orders.pending(unsent=True)[:20]:
        try:
            channel = bot.get_channel(int(row['channel_id'])) or await bot.fetch_channel(int(row['channel_id']))
            if not isinstance(channel, discord.TextChannel) or str(channel.guild.id) != row['guild_id']:
                continue
            if channel.permissions_for(channel.guild.default_role).view_channel:
                logging.warning('Delivery channel is public; awaiting a private channel: %s', row['id'])
                continue
            mention, allowed = staff_ping(channel)
            message = await channel.send(content=mention, embed=delivery_order_embed(row),
                                         view=DeliveryOrderView(row['id']), allowed_mentions=allowed)
            orders.notified(row['id'], message.id)
        except Exception:
            logging.exception('Delivery alert failed; will retry: %s', row['id'])


@tasks.loop(seconds=10)
async def notify_customer_reviews():
    if not bot.is_ready():
        return
    for row in orders.unnotified_reviews(20):
        try:
            channel = bot.get_channel(int(row['channel_id'])) or await bot.fetch_channel(int(row['channel_id']))
            if not isinstance(channel, discord.TextChannel) or str(channel.guild.id) != row['guild_id']:
                continue
            if channel.permissions_for(channel.guild.default_role).view_channel:
                logging.warning('Review notification channel is public; waiting for a private channel: %s', row['id'])
                continue
            mention, allowed = (management_ping(channel) if int(row['rating']) <= 2 else staff_ping(channel))
            message = await channel.send(
                content=mention, embed=customer_review_embed(row),
                view=LowRatingView(row['id']) if int(row['rating']) <= 2 else None,
                allowed_mentions=allowed)
            orders.review_notified(row['id'], message.id)
        except Exception:
            logging.exception('Customer review alert failed; will retry: %s', row['id'])


@tasks.loop(seconds=10)
async def notify_support_requests():
    if not bot.is_ready():
        return
    for row in orders.pending_support(unsent=True, limit=20):
        try:
            channel = bot.get_channel(int(row['channel_id'])) or await bot.fetch_channel(int(row['channel_id']))
            if not isinstance(channel, discord.TextChannel) or str(channel.guild.id) != row['guild_id']:
                continue
            if channel.permissions_for(channel.guild.default_role).view_channel:
                logging.warning('Support alert channel is public; waiting for a private channel: %s', row['id'])
                continue
            mention, allowed = staff_ping(channel)
            message = await channel.send(content=mention, embed=support_request_embed(row),
                                         view=SupportRequestView(row['id']), allowed_mentions=allowed)
            orders.support_notified(row['id'], message.id)
        except Exception:
            logging.exception('Order-problem alert failed; will retry: %s', row['id'])


@tasks.loop(seconds=10)
async def notify_account_requests():
    if not bot.is_ready():
        return
    for row in accounts.created_notifications(unsent=True):
        try:
            channel = bot.get_channel(int(row['channel_id'])) or await bot.fetch_channel(int(row['channel_id']))
            if not isinstance(channel, discord.TextChannel) or str(channel.guild.id) != row['guild_id']:
                continue
            if channel.permissions_for(channel.guild.default_role).view_channel:
                logging.warning('Account notification channel is public; waiting for a private channel: %s', row['id'])
                continue
            mention, allowed = staff_ping(channel)
            message = await channel.send(content=mention, embed=account_created_embed(row), allowed_mentions=allowed)
            accounts.request_notified(row['id'], message.id)
            await message.delete(delay=5 * 60)
        except Exception:
            logging.exception('Account-created notification failed; will retry: %s', row['id'])
    for row in accounts.pending(unsent=True)[:20]:
        try:
            channel = bot.get_channel(int(row['channel_id'])) or await bot.fetch_channel(int(row['channel_id']))
            if not isinstance(channel, discord.TextChannel) or str(channel.guild.id) != row['guild_id']:
                continue
            if channel.permissions_for(channel.guild.default_role).view_channel:
                logging.warning('Account approval channel is public; waiting for a private channel: %s', row['id'])
                continue
            mention, allowed = staff_ping(channel)
            message = await channel.send(content=mention, embed=account_request_embed(row),
                                         view=AccountRequestView(row['id']), allowed_mentions=allowed)
            accounts.request_notified(row['id'], message.id)
        except Exception:
            logging.exception('Account approval alert failed; will retry: %s', row['id'])


@bot.event
async def on_ready() -> None:
    print(f"Logged in as {bot.user} ({bot.user.id})")
    if not notify_pack_claims.is_running():
        notify_pack_claims.start()
    if not notify_delivery_orders.is_running():
        notify_delivery_orders.start()
    if not notify_customer_reviews.is_running():
        notify_customer_reviews.start()
    if not notify_support_requests.is_running():
        notify_support_requests.start()
    if not notify_account_requests.is_running():
        notify_account_requests.start()


@bot.event
async def setup_hook() -> None:
    bot.add_view(StaffPanel())
    for row in claims.pending():
        bot.add_view(PackClaimView(row['id']))
    for row in orders.pending():
        bot.add_view(DeliveryOrderView(row['id']))
    for row in orders.outstanding_fees():
        bot.add_view(DeliveryFeeView(row['id']))
    for row in orders.open_low_reviews():
        bot.add_view(LowRatingView(row['id']))
    for row in orders.pending_support():
        bot.add_view(SupportRequestView(row['id']))
    for row in accounts.pending():
        bot.add_view(AccountRequestView(row['id']))
    result = db.import_legacy_json(LEGACY_DATA_FILE)
    if result["imported"]:
        print(f"Imported {result['imported']} legacy customers.")
    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    else:
        await bot.tree.sync()


@bot.tree.command(name="snrhub_panel", description="Owner: post the permanent SNR staff control panel.")
async def snr_panel(interaction: discord.Interaction) -> None:
    if not await require_owner(interaction):
        return
    await interaction.response.send_message("✅ Staff panel posted.", ephemeral=True)
    await interaction.channel.send(embed=panel_embed(), view=StaffPanel())


@bot.tree.command(name="snrhub_owner", description="Open the private SNR Owner control centre.")
async def snr_owner(interaction: discord.Interaction) -> None:
    if not await require_owner(interaction):
        return
    await interaction.response.send_message(
        "👑 **SNR Owner Controls**\nManage memberships, shifts, finances and branding.",
        view=OwnerAdminView(), ephemeral=True,
    )


@bot.tree.command(name='snrhub_claims_setup', description='Owner: use this private staff channel for website reward alerts.')
async def claims_setup(interaction: discord.Interaction):
    if not is_owner(interaction):
        await interaction.response.send_message(f'{OWNER_ROLE_NAME} only.', ephemeral=True)
        return
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel) or (GUILD_ID and interaction.guild_id != GUILD_ID):
        await interaction.response.send_message('Use a private text channel in your configured staff server.', ephemeral=True)
        return
    permissions = channel.permissions_for(channel.guild.me)
    if channel.permissions_for(channel.guild.default_role).view_channel or not (permissions.view_channel and permissions.send_messages and permissions.embed_links):
        await interaction.response.send_message('Choose a private staff channel where this bot can view, send messages and embed links.', ephemeral=True)
        return
    claims.configure(channel.id, channel.guild.id, interaction.user.id, str(interaction.user))
    await interaction.response.send_message('Website pack claims enabled. New alerts will appear here, normally within 10 seconds. Customers must log in to request their own pack.', ephemeral=True)


@bot.tree.command(name='snrhub_orders_setup', description='Owner: use this private channel for website delivery orders.')
async def orders_setup(interaction: discord.Interaction):
    if not is_owner(interaction):
        await interaction.response.send_message(f'{OWNER_ROLE_NAME} only.', ephemeral=True)
        return
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel) or (GUILD_ID and interaction.guild_id != GUILD_ID):
        await interaction.response.send_message('Use a private text channel in your configured staff server.', ephemeral=True)
        return
    permissions = channel.permissions_for(channel.guild.me)
    if channel.permissions_for(channel.guild.default_role).view_channel or not (
        permissions.view_channel and permissions.send_messages and permissions.embed_links
    ):
        await interaction.response.send_message(
            'Choose a private staff orders channel where this bot can view, send messages and embed links.',
            ephemeral=True,
        )
        return
    orders.configure(channel.id, channel.guild.id, interaction.user.id, str(interaction.user))
    await interaction.response.send_message(
        '✅ Website deliveries enabled. Only delivery and pickup orders will appear in this channel, normally within 10 seconds. '
        'Press Customer Paid only after collecting payment.',
        ephemeral=True,
    )


@bot.tree.command(name='snrhub_accounts_setup', description='Owner: use this private channel for new-account alerts.')
async def accounts_setup(interaction: discord.Interaction):
    if not is_owner(interaction):
        await interaction.response.send_message(f'{OWNER_ROLE_NAME} only.', ephemeral=True)
        return
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel) or (GUILD_ID and interaction.guild_id != GUILD_ID):
        await interaction.response.send_message(
            'Use this command inside a private New Accounts Created text channel in your configured staff server.',
            ephemeral=True)
        return
    permissions = channel.permissions_for(channel.guild.me)
    if channel.permissions_for(channel.guild.default_role).view_channel or not (
        permissions.view_channel and permissions.send_messages and permissions.embed_links
    ):
        await interaction.response.send_message(
            'Choose a private staff channel where this bot can view, send messages and embed links.',
            ephemeral=True)
        return
    accounts.configure_notifications(
        channel.id, channel.guild.id, interaction.user.id, str(interaction.user))
    await interaction.response.send_message(
        '✅ New-account notifications are now separated. New loyalty accounts will appear only in this channel, not Delivery Orders.',
        ephemeral=True)


@bot.tree.command(name='snrhub_accounts_pending', description='Review recent account activity and older pending requests.')
async def accounts_pending(interaction: discord.Interaction):
    await show_account_requests(interaction)


@bot.tree.command(name='snrhub_claims_pending', description='Review website pack requests awaiting handover.')
async def claims_pending(interaction: discord.Interaction):
    await show_pack_requests(interaction)


@bot.tree.command(name='snrhub_orders_pending', description='Review website delivery orders awaiting payment.')
async def orders_pending(interaction: discord.Interaction):
    await show_delivery_orders(interaction)


@bot.tree.command(name="snrhub_sale", description="Record an SNR sale using only the customer name.")
@app_commands.describe(name="Customer character name")
async def sale(interaction: discord.Interaction, name: str) -> None:
    if await require_staff(interaction):
        await send_name_result(interaction, "sale", name)


@bot.tree.command(name="snrhub_customer", description="Check a customer by character name.")
@app_commands.describe(name="Customer character name")
async def customer(interaction: discord.Interaction, name: str) -> None:
    if await require_staff(interaction):
        await send_name_result(interaction, "check", name)


@bot.tree.command(name="snrhub_birdy", description="Generate a ready-to-copy Birdy post.")
async def birdy(interaction: discord.Interaction) -> None:
    if await require_staff(interaction):
        await interaction.response.send_message(
            "Choose the post you want to copy into Birdy:", view=BirdyView(), ephemeral=True
        )


@bot.tree.command(name="snrhub_report", description="Show a full SNR sales report.")
@app_commands.choices(period=[
    app_commands.Choice(name="Today (UK time)", value=-1),
    app_commands.Choice(name="Last 7 days", value=7),
    app_commands.Choice(name="Last 30 days", value=30),
    app_commands.Choice(name="All time", value=0),
])
async def report(interaction: discord.Interaction, period: app_commands.Choice[int]) -> None:
    if not await require_staff(interaction):
        return
    stats = db.report(today=period.value == -1, days=period.value if period.value > 0 else None)
    await interaction.response.send_message(
        embed=finance_embed(stats, f"💷 SNR FINANCE — {period.name.upper()}"),
        ephemeral=True,
    )


@bot.tree.command(name="snrhub_ratings", description="Show verified weekly or monthly staff ratings.")
@app_commands.choices(period=[
    app_commands.Choice(name="This week — last 7 days", value=7),
    app_commands.Choice(name="This month — last 30 days", value=30),
])
async def ratings(interaction: discord.Interaction, period: app_commands.Choice[int]) -> None:
    if not await require_staff(interaction):
        return
    await interaction.response.send_message(
        embed=review_leaderboard_embed(interaction.guild_id, period.value), ephemeral=True)


async def main() -> None:
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing. Add it to your environment variables.")
    server = start_web_server(db, PORT)
    print(f"SNR Loyalty Card website listening on port {PORT}")
    try:
        await bot.start(TOKEN)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    asyncio.run(main())
