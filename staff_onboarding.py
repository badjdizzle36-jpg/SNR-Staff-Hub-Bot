"""Guild-scoped onboarding with a staff-only role and channel overwrites."""
import asyncio
import logging
from datetime import datetime, timezone

import discord

# These never belong on the automatically assigned role, even if inherited
# from @everyone. Channel visibility is managed through explicit overwrites.
DANGEROUS = ('administrator', 'manage_guild', 'manage_roles', 'manage_channels',
             'manage_messages', 'manage_webhooks', 'manage_threads', 'manage_events',
             'kick_members', 'ban_members', 'moderate_members', 'view_audit_log',
             'manage_nicknames', 'mention_everyone', 'mute_members', 'deafen_members',
             'move_members', 'manage_expressions', 'create_events', 'view_guild_insights')


class StaffOnboarding:
    def __init__(self, db, staff_name, manager_name, owner_name):
        self.db = db
        self.staff_name = staff_name
        self.protected_names = {manager_name.casefold(), owner_name.casefold()}
        self.locks = {}
        with db.connect() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS staff_onboarding (
                guild_id TEXT PRIMARY KEY, role_id TEXT NOT NULL,
                category_id TEXT NOT NULL, general_id TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0, enabled_at TEXT NOT NULL)''')

    def lock(self, guild_id):
        return self.locks.setdefault(guild_id, asyncio.Lock())

    def config(self, guild_id):
        if guild_id is None:
            return None
        with self.db.connect() as c:
            row = c.execute('SELECT * FROM staff_onboarding WHERE guild_id=?', (str(guild_id),)).fetchone()
        return dict(row) if row else None

    def is_staff(self, member):
        cfg = self.config(getattr(getattr(member, 'guild', None), 'id', None))
        return bool(cfg and cfg['enabled'] and any(str(r.id) == cfg['role_id'] for r in member.roles))

    def validate_role(self, guild, role):
        if role.guild.id != guild.id or role.is_default() or role.managed:
            raise ValueError('Choose an ordinary role in this server, not @everyone or a bot role.')
        if role.name.casefold() in self.protected_names or role.name.casefold() == self.staff_name.casefold():
            raise ValueError('Choose a separate New Staff role, not an existing Staff, Management or Owner role.')
        if any(getattr(role.permissions, name, False) for name in DANGEROUS):
            raise ValueError('The New Staff role has admin/moderation permissions. Remove those permissions first.')
        if any(getattr(guild.default_role.permissions, name, False) for name in DANGEROUS):
            raise ValueError('@everyone grants management permissions. Remove those before enabling restricted onboarding.')
        me = guild.me
        if not me or not me.guild_permissions.manage_roles or not me.guild_permissions.manage_channels:
            raise ValueError('The bot needs Manage Roles and Manage Channels.')
        if me.top_role <= role:
            raise ValueError('Move the bot role above New Staff in Server Settings → Roles.')

    def protected_channel(self, channel):
        staff_roles = [r for r in channel.guild.roles if r.name == self.staff_name]
        if any(channel.overwrites_for(r).view_channel is False for r in staff_roles):
            return True
        staff_visible = any(channel.permissions_for(r).view_channel for r in staff_roles)
        private_management = any(
            r.name.casefold() in self.protected_names and channel.overwrites_for(r).view_channel is True
            for r in channel.guild.roles)
        return private_management and not staff_visible

    def desired_overwrite(self, channel, cfg, role):
        allowed = (str(channel.id) in (cfg['category_id'], cfg['general_id'])
                   or str(getattr(channel, 'category_id', None)) == cfg['category_id'])
        if self.protected_channel(channel):
            allowed = False
        overwrite = channel.overwrites_for(role)
        overwrite.view_channel = allowed
        overwrite.send_messages = allowed
        overwrite.read_message_history = allowed
        overwrite.use_application_commands = allowed
        overwrite.send_messages_in_threads = allowed
        overwrite.connect = allowed
        overwrite.speak = allowed
        for name in DANGEROUS:
            if name in discord.PermissionOverwrite.VALID_NAMES:
                setattr(overwrite, name, False)
        return overwrite

    async def configure(self, guild, category, general, role=None):
        if category.guild.id != guild.id or general.guild.id != guild.id:
            raise ValueError('Select the category and general chat from this server.')
        if self.protected_channel(category) or self.protected_channel(general):
            raise ValueError('The chosen category/general chat is restricted to management. Choose staff-accessible channels.')
        async with self.lock(guild.id):
            old_cfg = self.config(guild.id)
            if old_cfg and role is None:
                role = guild.get_role(int(old_cfg['role_id']))
                if role is None:
                    raise ValueError('Configured New Staff role was deleted. Select its replacement explicitly.')
            if role is None:
                matches = [r for r in guild.roles if r.name.casefold() == 'new staff']
                if len(matches) > 1:
                    raise ValueError('More than one New Staff role exists. Select the exact role in the command.')
                role = matches[0] if matches else await guild.create_role(
                    name='New Staff', permissions=discord.Permissions.none(),
                    reason='SNR new staff onboarding')
            self.validate_role(guild, role)
            if old_cfg and old_cfg['role_id'] != str(role.id) and guild.get_role(int(old_cfg['role_id'])) is not None:
                raise ValueError('Keep the configured New Staff role when rerunning setup. Role replacement needs a manual permissions review.')
            cfg = dict(guild_id=str(guild.id), role_id=str(role.id), category_id=str(category.id),
                       general_id=str(general.id), enabled=1,
                       enabled_at=old_cfg['enabled_at'] if old_cfg else datetime.now(timezone.utc).isoformat())
            # Preflight all channels before granting anything. Outside-category
            # denies are applied first; originals are retained for rollback.
            channels = sorted(guild.channels, key=lambda ch: (
                str(ch.id)==cfg['category_id'] or str(getattr(ch,'category_id',None))==cfg['category_id'] or str(ch.id)==cfg['general_id'],
                not isinstance(ch, discord.CategoryChannel)))
            for channel in channels:
                perms = channel.permissions_for(guild.me)
                if not (perms.manage_roles and perms.manage_channels):
                    raise ValueError(f'The bot cannot edit permissions for #{channel.name}. Fix its channel access and rerun setup.')
            changed = []
            try:
                for channel in channels:
                    original = channel.overwrites_for(role) if role in channel.overwrites else None
                    desired = self.desired_overwrite(channel, cfg, role)
                    if original != desired:
                        await channel.set_permissions(role, overwrite=desired, reason='SNR new staff access setup')
                        changed.append((channel, original))
                with self.db.connect() as c:
                    c.execute('''INSERT INTO staff_onboarding VALUES(:guild_id,:role_id,:category_id,:general_id,:enabled,:enabled_at)
                        ON CONFLICT(guild_id) DO UPDATE SET role_id=excluded.role_id,
                        category_id=excluded.category_id,general_id=excluded.general_id,enabled=1''',cfg)
            except Exception:
                failures = []
                for channel, original in reversed(changed):
                    try:
                        await channel.set_permissions(role, overwrite=original, reason='Rollback incomplete SNR staff setup')
                    except discord.HTTPException:
                        failures.append(channel.id)
                        logging.exception('SNR onboarding rollback failed for channel %s',channel.id)
                if failures:
                    with self.db.connect() as c:
                        c.execute('UPDATE staff_onboarding SET enabled=0 WHERE guild_id=?',(str(guild.id),))
                    raise ValueError('Setup failed and some channel permissions could not be restored. Auto-assignment is disabled; review permissions before rerunning setup.') from None
                raise
            return role, sum(self.desired_overwrite(ch,cfg,role).view_channel for ch in channels)

    async def assign(self, member):
        if member.bot:
            return False
        async with self.lock(member.guild.id):
            cfg = self.config(member.guild.id)
            if not cfg or not cfg['enabled']:
                return False
            # Existing staff/owners keep their current roles and access.
            if any(r.name == self.staff_name or r.name.casefold() in self.protected_names for r in member.roles):
                return False
            role = member.guild.get_role(int(cfg['role_id']))
            if role is None:
                logging.error('SNR New Staff role missing in server %s',member.guild.id)
                return False
            self.validate_role(member.guild, role)
            if role in member.roles:
                return False
            await member.add_roles(role, reason='SNR automatic new staff role', atomic=True)
            return True

    async def reconcile(self, guild):
        cfg = self.config(guild.id)
        if not cfg or not cfg['enabled']:
            return
        cutoff = datetime.fromisoformat(cfg['enabled_at'])
        # Recovers joins missed while the bot was offline; does not enrol
        # established members from before setup.
        async for member in guild.fetch_members(limit=None):
            if member.joined_at and member.joined_at >= cutoff:
                await self.assign(member)

    async def channel_changed(self, channel):
        async with self.lock(channel.guild.id):
            cfg = self.config(channel.guild.id)
            if not cfg or not cfg['enabled']:
                return
            role = channel.guild.get_role(int(cfg['role_id']))
            if role is None:
                return
            targets = [channel]
            if isinstance(channel, discord.CategoryChannel):
                targets += list(channel.channels)
            for target in targets:
                desired = self.desired_overwrite(target, cfg, role)
                if target.overwrites_for(role) != desired:
                    await target.set_permissions(role, overwrite=desired, reason='Maintain SNR new staff access')
