from __future__ import annotations

import asyncio
import os

import discord
from discord import app_commands
from discord.ext import commands

from snr_core import DEALS, SNRDatabase, birdy_post, normalize_name


TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
STAFF_ROLE_NAME = os.getenv("STAFF_ROLE_NAME", "SNR Staff")
MANAGER_ROLE_NAME = os.getenv("MANAGER_ROLE_NAME", "SNR Management")
DATABASE_PATH = os.getenv("DATABASE_PATH", "snr_staff_hub.db")
LEGACY_DATA_FILE = os.getenv("LEGACY_DATA_FILE", "loyalty_data.json")
JACKPOT_POOL_SIZE = int(os.getenv("JACKPOT_POOL_SIZE", "1000"))

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
db = SNRDatabase(DATABASE_PATH, JACKPOT_POOL_SIZE)
db_lock = asyncio.Lock()


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
            "Use the buttons below to record sales, check customers, redeem rewards, "
            "manage the Golden Ticket Jackpot and generate Birdy posts.\n\n"
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
    embed.add_field(name="Revenue", value=f"**£{customer['revenue']:,}**", inline=True)
    embed.add_field(
        name="Items Sold",
        value=f"**{customer['food_sold']} food • {customer['drinks_sold']} drinks**",
        inline=True,
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
    embed = discord.Embed(
        title="🏆 GOLDEN TICKET FOUND!" if won else "✅ SNR SALE RECORDED",
        colour=discord.Colour.gold() if won else discord.Colour.green(),
    )
    embed.add_field(name="Customer", value=f"**{customer['display_name']}**", inline=True)
    embed.add_field(name="Deal", value=f"**{deal.name}**", inline=True)
    embed.add_field(name="Sale", value=f"**£{deal.price:,}**", inline=True)
    embed.add_field(name="Items", value=deal.item_summary, inline=True)
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
    customer = db.get_customer(name)
    if not customer:
        message = "ℹ️ Customer not found. Record their first sale to create them."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
        return
    if action == "check":
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
        super().__init__(title="Record Sale" if action == "sale" else "Find Customer")
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

    @discord.ui.button(label="Today’s Report", emoji="📊", style=discord.ButtonStyle.secondary, custom_id="snr:report")
    async def report(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await require_staff(interaction):
            return
        stats = db.report(days=1)
        embed = discord.Embed(title="📊 SNR BUNS — LAST 24 HOURS", colour=discord.Colour.orange())
        embed.add_field(name="Sales", value=f"**{stats['sales']}**", inline=True)
        embed.add_field(name="Revenue", value=f"**£{stats['revenue']:,}**", inline=True)
        embed.add_field(name="Golden Tickets", value=f"**{stats['tickets']}**", inline=True)
        embed.add_field(name="Food", value=f"**{stats['food']}**", inline=True)
        embed.add_field(name="Drinks", value=f"**{stats['drinks']}**", inline=True)
        embed.add_field(name="Jackpots", value=f"**{stats['jackpots']}**", inline=True)
        breakdown = "\n".join(
            f"• {d['deal_name']}: **{d['quantity']}** (£{d['revenue']:,})" for d in stats["deals"]
        ) or "No sales recorded."
        embed.add_field(name="Deals Sold", value=breakdown, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.event
async def on_ready() -> None:
    print(f"Logged in as {bot.user} ({bot.user.id})")


@bot.event
async def setup_hook() -> None:
    bot.add_view(StaffPanel())
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
    app_commands.Choice(name="Last 24 hours", value=1),
    app_commands.Choice(name="Last 7 days", value=7),
    app_commands.Choice(name="Last 30 days", value=30),
    app_commands.Choice(name="All time", value=0),
])
async def report(interaction: discord.Interaction, period: app_commands.Choice[int]) -> None:
    if not await require_staff(interaction):
        return
    stats = db.report(days=period.value or None)
    breakdown = "\n".join(
        f"• {d['deal_name']}: **{d['quantity']}** (£{d['revenue']:,})" for d in stats["deals"]
    ) or "No sales recorded."
    embed = discord.Embed(title=f"📊 SNR REPORT — {period.name.upper()}", colour=discord.Colour.orange())
    embed.description = (
        f"Sales: **{stats['sales']}**\nRevenue: **£{stats['revenue']:,}**\n"
        f"Food: **{stats['food']}** • Drinks: **{stats['drinks']}**\n"
        f"Loyalty points issued: **{stats['loyalty']}**\n"
        f"Golden Tickets issued: **{stats['tickets']}**\nJackpots won: **{stats['jackpots']}**\n\n"
        f"**Deals sold**\n{breakdown}"
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing. Add it to your environment variables.")

bot.run(TOKEN)
