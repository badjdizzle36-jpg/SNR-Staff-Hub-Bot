"""Discord owner-only marketplace controls. No customer HTTP admin endpoints."""
import discord
from city_trades import NAMES


def clean(value, limit=900):
    return discord.utils.escape_markdown(discord.utils.escape_mentions(str(value)))[:limit]


def admin_embed(store, kind='open', page=0, customer=''):
    data = store.admin_list(kind, page, customer)
    s = data['settings']
    embed = discord.Embed(title='🏪 MARKETPLACE · OWNER CONTROL', colour=discord.Colour.gold())
    embed.description = (
        f"**{'🟢 Trading enabled' if s['enabled'] else '⏸️ Trading paused'}** · Active season also required\n"
        f"{s['max_offers']} offers per player · {s['expiry_hours']}-hour expiry for new offers\n"
        f"**{data['counts'].get('open',0)}** open · **{data['counts'].get('accepted',0)}** completed\n"
        'Duplicate-only, same-rarity swaps. Each player keeps their first copy.')
    embed.add_field(name=f"{kind.title()} · Page {page+1}" + (f' · {clean(customer,60)}' if customer else ''),
                    value='Newest first. History covers all seasons.', inline=False)
    for r in data['rows']:
        if kind == 'restricted':
            name, value = clean(r['customer_key'],80), f"Reason: {clean(r['reason'],250)}\nUpdated: {r['updated_at'][:16]} UTC"
        elif kind == 'audit':
            name, value = f"#{r['id']} · {r['action']}", f"{clean(r['staff_name'],80)} · {r['created_at'][:16]} UTC\n{clean(r['details'],700)}"
        else:
            name = f"#{r['id']} · {r['status'].upper()} · Season {r['campaign_id']}"
            value = (f"**{clean(r['maker'],80)}** offers {clean(NAMES.get(r['offered'],r['offered']))}\n"
                     f"Wants: {clean(NAMES.get(r['wanted'],r['wanted']))}\n"
                     f"Other player: {clean(r['taker'] or 'Not accepted',80)}\nExpires: {r['expires_at'][:16]} UTC")
        embed.add_field(name=name, value=value, inline=False)
    if not data['rows']:
        embed.add_field(name='Nothing to display', value='No matching records on this page.', inline=False)
    embed.set_footer(text='Owner-only • Changes are audited • Completed swaps are permanent records')
    return embed, data['more']


class MarketplaceAdminView(discord.ui.View):
    def __init__(self, store, require_owner, kind='open', page=0, customer=''):
        super().__init__(timeout=300)
        self.store, self.guard = store, require_owner
        self.kind, self.page, self.customer = kind, page, customer
        for label, kind in [('Open offers','open'),('All swap history','history'),('Restricted players','restricted'),('Admin audit','audit')]:
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.primary if kind==self.kind else discord.ButtonStyle.secondary, row=0)
            async def callback(interaction, selected=kind):
                if await self.guard(interaction):
                    await self.show(interaction, selected, 0, '')
            button.callback=callback
            self.add_item(button)
        for label, action, row in [('Marketplace settings','settings',1),('Cancel offer','cancel',1),('Player access','access',1),('Find player history','find',1),('Cancel ALL open offers','cancel_all',2)]:
            button=discord.ui.Button(label=label,style=discord.ButtonStyle.danger if action.startswith('cancel') else discord.ButtonStyle.secondary,row=row)
            async def callback(interaction, selected=action):
                if await self.guard(interaction):
                    await interaction.response.send_modal(MarketplaceModal(self.store,self.guard,selected))
            button.callback=callback
            self.add_item(button)
        for label, delta in [('Previous',-1),('Next',1),('Refresh',0)]:
            more = store.admin_list(self.kind,self.page,self.customer)['more']
            button=discord.ui.Button(label=label,row=3,disabled=(delta==-1 and self.page==0) or (delta==1 and not more))
            async def callback(interaction, step=delta):
                if await self.guard(interaction):
                    await self.show(interaction,self.kind,max(0,self.page+step),self.customer)
            button.callback=callback
            self.add_item(button)

    async def interaction_check(self, interaction):
        return await self.guard(interaction)

    async def show(self, interaction, kind, page, customer):
        embed,_=admin_embed(self.store,kind,page,customer)
        await interaction.response.edit_message(content=None,embed=embed,
            view=MarketplaceAdminView(self.store,self.guard,kind,page,customer))


class MarketplaceModal(discord.ui.Modal):
    def __init__(self, store, require_owner, action):
        titles={'settings':'Marketplace settings','cancel':'Cancel one open offer','cancel_all':'Cancel ALL open offers','access':'Player trading access','find':'Find player swap history'}
        super().__init__(title=titles[action],timeout=300)
        self.store,self.guard,self.action=store,require_owner,action
        self.fields={}
        def field(key,label,default=None,placeholder=None,max_length=100):
            item=discord.ui.TextInput(label=label,default=default,placeholder=placeholder,max_length=max_length,required=True)
            self.fields[key]=item;self.add_item(item)
        if action=='settings':
            s=store.settings()
            field('enabled','Trading: enabled or paused','enabled' if s['enabled'] else 'paused')
            field('limit','Maximum open offers per player (1–20)',str(s['max_offers']))
            field('hours','New offer expiry in hours (1–168)',str(s['expiry_hours']))
        elif action=='cancel':
            field('id','Offer ID to cancel',placeholder='Example: 12')
        elif action=='cancel_all':
            field('confirm','Type CANCEL ALL to confirm',placeholder='CANCEL ALL')
        elif action in ('access','find'):
            field('customer','Exact player character name',max_length=100)
            if action=='access':
                field('access','Action: restrict or restore',placeholder='restrict')
        if action!='find':
            field('reason','Reason for the audit log',max_length=250)

    async def on_submit(self, interaction):
        # Recheck at submission: opening a modal does not preserve authorization.
        if not await self.guard(interaction):
            return
        values={k:v.value.strip() for k,v in self.fields.items()}
        staff_id,staff_name=str(interaction.user.id),str(interaction.user)
        try:
            if self.action=='find':
                embed,_=admin_embed(self.store,'history',0,values['customer'])
                await interaction.response.send_message(embed=embed,
                    view=MarketplaceAdminView(self.store,self.guard,'history',0,values['customer']),ephemeral=True)
                return
            if self.action=='settings':
                mode=values['enabled'].lower()
                if mode not in ('enabled','paused'):
                    raise ValueError('Trading mode must be enabled or paused.')
                self.store.admin_configure(mode=='enabled',values['limit'],values['hours'],staff_id,staff_name,values['reason'])
                message='Marketplace settings saved. New offers use these limits; existing expiry times stay the same.'
            elif self.action=='cancel':
                self.store.admin_cancel(int(values['id']),staff_id,staff_name,values['reason'])
                message=f"Offer #{values['id']} cancelled. Its reserved duplicate is available again."
            elif self.action=='cancel_all':
                if values['confirm']!='CANCEL ALL':
                    raise ValueError('Type CANCEL ALL exactly to confirm this action.')
                count=self.store.admin_cancel_all(staff_id,staff_name,values['reason'])
                message=f'{count} open offers cancelled. Reserved duplicates are available again.'
            else:
                action=values['access'].lower()
                if action not in ('restrict','restore'):
                    raise ValueError('Choose restrict or restore.')
                name=self.store.admin_access(values['customer'],action=='restrict',staff_id,staff_name,values['reason'])
                message=f"Trading access {'restricted' if action=='restrict' else 'restored'} for {clean(name,100)}."
        except (ValueError,TypeError) as exc:
            await interaction.response.send_message(f'❌ {clean(exc)}',ephemeral=True)
            return
        embed,_=admin_embed(self.store)
        await interaction.response.send_message(content='✅ '+message,embed=embed,
            view=MarketplaceAdminView(self.store,self.guard),ephemeral=True)
