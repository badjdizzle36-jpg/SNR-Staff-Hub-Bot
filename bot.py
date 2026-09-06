from __future__ import annotations

import asyncio
import os
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from snr_core import (
    AVERAGE_DESSERT_COST,
    AVERAGE_DRINK_COST,
    AVERAGE_FOOD_COST,
    DEALS,
    SNRDatabase,
    birdy_post,
    normalize_name,
)
from web_portal import start_web_server
from reward_claims import ClaimStore
from customer_accounts import Accounts
from delivery_orders import DeliveryStore


TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
STAFF_ROLE_NAME = os.getenv("STAFF_ROLE_NAME", "SNR Staff")
MANAGER_ROLE_NAME = os.getenv("MANAGER_ROLE_NAME", "SNR Management")
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


def money(value: float | int) -> str:
    return f"£{float(value):,.2f}"


def has_role(interaction: discord.Interaction, role_name: str) -> bool:
    if not isinstance(interaction.user, discord.Member):
        return False
    if interaction.user.guild_permissions.administrator:
        return True
    return any(role.name == role_name for role in interaction.user.roles)


def is_staff(interaction: discord.Interaction) -> bool:
    return has_role(interaction, STAFF_ROLE_NAME) or has_role(interaction, MANAGER_ROLE_NAME)


async def require_staff(interaction: discord.Interaction) -> bool:
    if is_staff(interaction):
        return True
    if interaction.response.is_done():
        await interaction.followup.send("❌ This SNR system is staff-only.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ This SNR system is staff-only.", ephemeral=True)
    return False


def panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🍔 SNR BUNS — STAFF HUB",
        description=(
            "Use the buttons below to record sales, manage website deliveries, check customers, "
            "redeem rewards, manage the Golden Ticket Jackpot, check finances and generate Birdy posts.\n\n"
            "Customers do **not** need Discord."
        ),
        colour=discord.Colour.gold(),
    )
    embed.add_field(name="Simple sale entry", value="Enter the customer name, then choose the deal.", inline=False)
    embed.set_footer(text="SNR Buns • Staff access only")
    return embed


def customer_embed(customer: dict) -> discord.Embed:
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
    embed = discord.Embed(
        title="🏆 GOLDEN TICKET FOUND!" if won else "✅ SNR SALE RECORDED",
        colour=discord.Colour.gold() if won else discord.Colour.green(),
    )
    embed.add_field(name="Customer", value=f"**{customer['display_name']}**", inline=True)
    embed.add_field(name="Deal", value=f"**{deal.name}**", inline=True)
    embed.add_field(name="Sale", value=f"**{money(deal.price)}**", inline=True)
    embed.add_field(name="Items", value=deal.item_summary, inline=True)
    embed.add_field(name="Production Cost", value=f"**{money(deal.production_cost)}**", inline=True)
    embed.add_field(
        name="Gross Profit",
        value=f"**{money(deal.gross_profit)}** ({deal.profit_margin:.1f}%)",
        inline=True,
    )
    loyalty_value = (
        f"+{deal.loyalty_points} → **{customer['loyalty_points']} total**"
        if deal.loyalty_points
        else f"No point on this deal • **{customer['loyalty_points']} total**"
    )
    embed.add_field(name="Loyalty", value=loyalty_value, inline=True)
    embed.add_field(name="Golden Tickets", value=f"+{deal.golden_tickets}", inline=True)
    if won:
        embed.add_field(
            name="🏆 JACKPOT PRIZE",
            value=(
                "**£5,000 CASH**\n"
                "Extremely rare SNR Golden Mystery Ticket\n"
                f"Reward: `{result['jackpot_reward_code']}`"
            ),
            inline=False,
        )
    else:
        embed.add_field(name="Jackpot", value="The Golden Ticket remains unfound.", inline=False)
    embed.set_footer(text=f"Transaction {result['transaction_id']}")
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


async def send_name_result(interaction: discord.Interaction, action: str, entered_name: str) -> None:
    exact = db.get_customer(entered_name)
    if exact:
        await continue_action(interaction, action, exact["display_name"])
        return
    suggestion = db.suggest_name(entered_name)
    if suggestion and normalize_name(suggestion) != normalize_name(entered_name):
        await interaction.response.send_message(
            f"Did you mean **{suggestion}**?",
            view=NameChoiceView(action, entered_name, suggestion),
            ephemeral=True,
        )
        return
    await continue_action(interaction, action, entered_name)


async def continue_action(interaction: discord.Interaction, action: str, name: str) -> None:
    if action == "sale":
        message = f"Customer: **{' '.join(p.capitalize() for p in name.split())}**\nChoose the deal sold:"
        if interaction.response.is_done():
            await interaction.followup.send(message, view=DealView(name, "sale"), ephemeral=True)
        else:
            await interaction.response.send_message(message, view=DealView(name, "sale"), ephemeral=True)
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
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
        return
    customer = db.get_customer(name)
    if not customer:
        message = "ℹ️ Customer not found. Record their first sale to create them."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
        return
    if action == "account_reset":
        code = accounts.issue_setup(name, str(interaction.user.id), str(interaction.user), reset=True)
        message = (
            f"🔐 Password reset for **{discord.utils.escape_markdown(customer['display_name'])}**.\n"
            f"New one-time setup code: `{code}`\n"
            "Their old password and website sessions are disabled. Give this privately after checking identity. "
            "It expires after 24 hours."
        )
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    elif action == "check":
        if interaction.response.is_done():
            await interaction.followup.send(embed=customer_embed(customer), ephemeral=True)
        else:
            await interaction.response.send_message(embed=customer_embed(customer), ephemeral=True)
    elif action == "redeem":
        rewards = customer["unclaimed_rewards"]
        if not rewards:
            message = f"ℹ️ **{customer['display_name']}** has no unclaimed rewards."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        else:
            message = f"Choose the reward being given to **{customer['display_name']}**:"
            view = RewardView(rewards)
            if interaction.response.is_done():
                await interaction.followup.send(message, view=view, ephemeral=True)
            else:
                await interaction.response.send_message(message, view=view, ephemeral=True)


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
        await interaction.response.defer(ephemeral=True)
        async with db_lock:
            result = db.record_sale(
                self.customer_name,
                deal_key,
                str(interaction.user.id),
                str(interaction.user),
            )
        await interaction.edit_original_response(content=None, embed=sale_embed(result), view=None)


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


class StaffPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Web Account", emoji="👤", style=discord.ButtonStyle.primary, custom_id="snr:claim_code")
    async def create_account(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await require_staff(interaction):
            await interaction.response.send_modal(NameModal("account_create"))

    @discord.ui.button(label="Reset Web Password", emoji="🔐", style=discord.ButtonStyle.secondary, custom_id="snr:account_reset")
    async def reset_account(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await require_staff(interaction):
            await interaction.response.send_modal(NameModal("account_reset"))

    @discord.ui.button(label="Pack Requests", emoji="🎴", style=discord.ButtonStyle.secondary, custom_id="snr:pack_requests")
    async def pack_requests(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await show_pack_requests(interaction)

    @discord.ui.button(label="Delivery Orders", emoji="🚗", style=discord.ButtonStyle.success, custom_id="snr:delivery_orders")
    async def delivery_orders(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await show_delivery_orders(interaction)

    @discord.ui.button(label="Record Sale", emoji="💷", style=discord.ButtonStyle.success, custom_id="snr:record_sale")
    async def record_sale(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await require_staff(interaction):
            await interaction.response.send_modal(NameModal("sale"))

    @discord.ui.button(label="Check Customer", emoji="🔎", style=discord.ButtonStyle.primary, custom_id="snr:check_customer")
    async def check_customer(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await require_staff(interaction):
            await interaction.response.send_modal(NameModal("check"))

    @discord.ui.button(label="Redeem Reward", emoji="🎁", style=discord.ButtonStyle.primary, custom_id="snr:redeem")
    async def redeem(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await require_staff(interaction):
            await interaction.response.send_modal(NameModal("redeem"))

    @discord.ui.button(label="Golden Jackpot", emoji="🎟️", style=discord.ButtonStyle.secondary, custom_id="snr:jackpot")
    async def jackpot(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await require_staff(interaction):
            return
        status = db.jackpot_status()
        embed = discord.Embed(title="🎟️ SNR GOLDEN MYSTERY TICKET", colour=discord.Colour.gold())
        embed.description = (
            "Jackpot prize: **£5,000 CASH**\n"
            "Drop rate: **1 hidden winner per 1,000 Golden Tickets (0.1%)**\n\n"
            f"Current cycle: **{status['cycle']}**\n"
            f"Tickets issued this cycle: **{status['tickets_issued']}/{status['pool_size']}**\n"
            f"Total winners: **{status['total_winners']}**\n\n"
            "The winning position stays hidden from staff and is guaranteed by ticket 1,000."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Birdy Post", emoji="📱", style=discord.ButtonStyle.secondary, custom_id="snr:birdy")
    async def birdy(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await require_staff(interaction):
            await interaction.response.send_message(
                "Choose the post you want to copy into Birdy:", view=BirdyView(), ephemeral=True
            )

    @discord.ui.button(label="Finance Check", emoji="📊", style=discord.ButtonStyle.secondary, custom_id="snr:report")
    async def report(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await require_staff(interaction):
            return
        stats = db.report(today=True)
        await interaction.response.send_message(
            embed=finance_embed(stats, "💷 SNR BUNS — TODAY’S FINANCE CHECK"),
            ephemeral=True,
        )


def pack_claim_embed(row):
    embed = discord.Embed(title=f"🎴 PACK REQUEST #{row['id']}", colour=discord.Colour.gold())
    embed.description = (f"Customer: **{discord.utils.escape_markdown(row['customer_name'])}**\n"
                         "Reward: **1 pack containing 2 trading cards**\n"
                         f"Status: **{row['status']}**\n"
                         "Four points are reserved while pending. Hand over the pack first, then confirm. "
                         "Cancel to return the points.")
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
                    'Pack marked as handed over.' if target=='fulfilled' else 'Cancelled. Four points returned.', ephemeral=True)
                # Also update the canonical alert if this action came from the pending-request list.
                try:
                    await interaction.message.edit(embed=pack_claim_embed(row), view=None)
                    if row['message_id'] and str(interaction.message.id) != row['message_id']:
                        channel = bot.get_channel(int(row['channel_id'])) or await bot.fetch_channel(int(row['channel_id']))
                        await channel.get_partial_message(int(row['message_id'])).edit(embed=pack_claim_embed(row), view=None)
                except discord.HTTPException:
                    logging.exception('Claim resolved but alert refresh failed: %s', self.claim_id)
            button.callback = callback
            self.add_item(button)


async def show_pack_requests(interaction):
    if not await require_staff(interaction):
        return
    rows = [r for r in claims.pending() if r['guild_id']==str(interaction.guild_id)]
    await interaction.response.send_message(
        f'{len(rows)} pending pack request(s). Showing the oldest 10; reopen after processing to see more.', ephemeral=True)
    for row in rows[:10]:
        await interaction.followup.send(embed=pack_claim_embed(row), view=PackClaimView(row['id']),
                                        ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


def delivery_order_embed(row):
    colours = {
        'pending': discord.Colour.orange(),
        'processing': discord.Colour.gold(),
        'paid': discord.Colour.green(),
        'cancelled': discord.Colour.red(),
    }
    status = {
        'pending': 'WAITING FOR PAYMENT',
        'processing': 'PROCESSING',
        'paid': 'PAID — SALE RECORDED',
        'cancelled': 'CANCELLED',
    }.get(row['status'], str(row['status']).upper())
    deal = DEALS.get(row['deal_key'])
    embed = discord.Embed(title=f"🚗 DELIVERY ORDER #{row['id']}", colour=colours.get(row['status'], discord.Colour.orange()))
    embed.add_field(name='Customer', value=f"**{discord.utils.escape_markdown(row['customer_name'])}**", inline=True)
    embed.add_field(name='Amount Owed', value=f"**{money(row['price'])}**", inline=True)
    embed.add_field(name='Status', value=f"**{status}**", inline=True)
    embed.add_field(name='Deal', value=f"**{discord.utils.escape_markdown(row['deal_name'])}**", inline=False)
    if deal:
        embed.add_field(name='Includes', value=deal.item_summary, inline=True)
        embed.add_field(
            name='Rewards After Payment',
            value=f"{deal.loyalty_points} loyalty point(s) • {deal.golden_tickets} Golden ticket(s)",
            inline=True,
        )
    embed.add_field(
        name='📍 Postal / Delivery Location',
        value=f"**{discord.utils.escape_markdown(row['postal'])}**",
        inline=False,
    )
    if row.get('sale_transaction_id'):
        embed.set_footer(text=f"Sale {row['sale_transaction_id']} • Confirmed by staff")
    else:
        embed.set_footer(text='Confirm payment only after the customer has paid')
    return embed


class DeliveryOrderView(discord.ui.View):
    def __init__(self, order_id):
        super().__init__(timeout=None)
        self.order_id = int(order_id)
        paid = discord.ui.Button(
            label='Customer Paid', emoji='💷', style=discord.ButtonStyle.success,
            custom_id=f'snr:delivery:{self.order_id}:paid',
        )
        cancel = discord.ui.Button(
            label='Cancel Order', emoji='✖️', style=discord.ButtonStyle.danger,
            custom_id=f'snr:delivery:{self.order_id}:cancelled',
        )

        async def paid_callback(interaction):
            await self._resolve(interaction, 'paid')

        async def cancel_callback(interaction):
            await self._resolve(interaction, 'cancelled')

        paid.callback = paid_callback
        cancel.callback = cancel_callback
        self.add_item(paid)
        self.add_item(cancel)

    async def _resolve(self, interaction, target):
        if not await require_staff(interaction):
            return
        row = orders.get(self.order_id)
        if not row or str(interaction.guild_id) != row['guild_id']:
            await interaction.response.send_message('This order belongs to another server.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            async with db_lock:
                row, sale = orders.resolve(
                    self.order_id, target, str(interaction.user.id), str(interaction.user)
                )
        except ValueError as exc:
            await interaction.followup.send(f'❌ {exc}', ephemeral=True)
            return
        except Exception:
            logging.exception('Delivery order processing failed: %s', self.order_id)
            await interaction.followup.send(
                '❌ The order could not be processed. Nothing was counted twice; please try again.',
                ephemeral=True,
            )
            return
        if target == 'paid':
            await interaction.followup.send(
                content='✅ Payment confirmed. The delivery is now included in sales, finance, loyalty and Golden Tickets.',
                embed=sale_embed(sale), ephemeral=True,
            )
        else:
            await interaction.followup.send('Order cancelled. No sale or rewards were added.', ephemeral=True)
        try:
            await interaction.message.edit(embed=delivery_order_embed(row), view=None)
            if row['message_id'] and str(interaction.message.id) != row['message_id']:
                channel = bot.get_channel(int(row['channel_id'])) or await bot.fetch_channel(int(row['channel_id']))
                await channel.get_partial_message(int(row['message_id'])).edit(
                    embed=delivery_order_embed(row), view=None
                )
        except discord.HTTPException:
            logging.exception('Order resolved but Discord message refresh failed: %s', self.order_id)


async def show_delivery_orders(interaction):
    if not await require_staff(interaction):
        return
    rows = [row for row in orders.pending() if row['guild_id'] == str(interaction.guild_id)]
    await interaction.response.send_message(
        f'{len(rows)} pending delivery order(s). Showing the oldest 10.', ephemeral=True
    )
    for row in rows[:10]:
        await interaction.followup.send(
            embed=delivery_order_embed(row), view=DeliveryOrderView(row['id']),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )


@tasks.loop(seconds=10)
async def notify_pack_claims():
    if not bot.is_ready():
        return
    for row in claims.pending(unsent=True)[:20]:
        try:
            channel = bot.get_channel(int(row['channel_id'])) or await bot.fetch_channel(int(row['channel_id']))
            if not isinstance(channel, discord.TextChannel) or str(channel.guild.id) != row['guild_id']:
                continue
            if channel.permissions_for(channel.guild.default_role).view_channel:
                logging.warning('Pack claim channel is public; awaiting a private channel: %s', row['id'])
                continue
            message = await channel.send(embed=pack_claim_embed(row), view=PackClaimView(row['id']),
                                         allowed_mentions=discord.AllowedMentions.none())
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
            message = await channel.send(
                embed=delivery_order_embed(row), view=DeliveryOrderView(row['id']),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            orders.notified(row['id'], message.id)
        except Exception:
            logging.exception('Delivery alert failed; will retry: %s', row['id'])


@bot.event
async def on_ready() -> None:
    print(f"Logged in as {bot.user} ({bot.user.id})")
    if not notify_pack_claims.is_running():
        notify_pack_claims.start()
    if not notify_delivery_orders.is_running():
        notify_delivery_orders.start()


@bot.event
async def setup_hook() -> None:
    bot.add_view(StaffPanel())
    for row in claims.pending():
        bot.add_view(PackClaimView(row['id']))
    for row in orders.pending():
        bot.add_view(DeliveryOrderView(row['id']))
    result = db.import_legacy_json(LEGACY_DATA_FILE)
    if result["imported"]:
        print(f"Imported {result['imported']} legacy customers.")
    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    else:
        await bot.tree.sync()


@bot.tree.command(name="snrhub_panel", description="Post the permanent SNR staff control panel.")
async def snr_panel(interaction: discord.Interaction) -> None:
    if not await require_staff(interaction):
        return
    await interaction.response.send_message("✅ Staff panel posted.", ephemeral=True)
    await interaction.channel.send(embed=panel_embed(), view=StaffPanel())


@bot.tree.command(name='snrhub_claims_setup', description='Management: use this private staff channel for website reward alerts.')
async def claims_setup(interaction: discord.Interaction):
    if not has_role(interaction, MANAGER_ROLE_NAME):
        await interaction.response.send_message('SNR Management only.', ephemeral=True)
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


@bot.tree.command(name='snrhub_orders_setup', description='Management: use this private channel for website delivery orders.')
async def orders_setup(interaction: discord.Interaction):
    if not has_role(interaction, MANAGER_ROLE_NAME):
        await interaction.response.send_message('SNR Management only.', ephemeral=True)
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
        '✅ Website deliveries enabled. New orders will appear in this channel, normally within 10 seconds. '
        'Press Customer Paid only after collecting payment.',
        ephemeral=True,
    )


@bot.tree.command(name='snrhub_account_create', description='Create a named website account and issue its one-time setup code.')
async def account_create(interaction: discord.Interaction, name: str):
    if await require_staff(interaction):
        await send_name_result(interaction, 'account_create', name)


@bot.tree.command(name='snrhub_password_reset', description='Reset a customer’s website password after checking their identity.')
async def password_reset(interaction: discord.Interaction, name: str):
    if await require_staff(interaction):
        await send_name_result(interaction, 'account_reset', name)


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
