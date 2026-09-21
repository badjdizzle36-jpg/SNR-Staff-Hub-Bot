import ast
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import discord
from snr_core import SNRDatabase
from staff_onboarding import StaffOnboarding, DANGEROUS

class Role:
    def __init__(self, ident, name, guild, permissions=None, position=1):
        self.id,self.name,self.guild=ident,name,guild
        self.permissions=permissions or discord.Permissions.none()
        self.position=position
        self.managed=False
    def is_default(self): return self.id==0
    def __le__(self,other): return self.position<=other.position

class Channel:
    def __init__(self,ident,guild,category=None):
        self.id,self.name,self.guild,self.category_id=ident,f'channel-{ident}',guild,category
        self.overwrites={}
        self.calls=[]
        self.fail=False
    def overwrites_for(self,role):
        result=self.overwrites.get(role,discord.PermissionOverwrite())
        return discord.PermissionOverwrite.from_pair(*result.pair())
    def permissions_for(self,who):
        if who is self.guild.me: return discord.Permissions.all()
        p=discord.Permissions(who.permissions.value|self.guild.default_role.permissions.value)
        for target in [self.guild.default_role,who]:
            allow,deny=self.overwrites_for(target).pair()
            p.handle_overwrite(allow.value,deny.value)
        return p
    async def set_permissions(self,role,overwrite,reason):
        if self.fail: raise RuntimeError('simulated permission failure')
        self.calls.append(overwrite)
        if overwrite is None: self.overwrites.pop(role,None)
        else: self.overwrites[role]=overwrite

class OnboardingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=SNRDatabase(self.tmp.name+'/db.sqlite')
        self.service=StaffOnboarding(self.db,'SNR Staff','SNR Management','SNR Owner')
        self.guild=SimpleNamespace(id=100)
        self.everyone=Role(0,'@everyone',self.guild,discord.Permissions(view_channel=True,send_messages=True))
        self.role=Role(1,'New Staff',self.guild)
        self.staff=Role(2,'SNR Staff',self.guild)
        self.owner=Role(3,'SNR Owner',self.guild)
        self.guild.default_role=self.everyone
        self.guild.roles=[self.everyone,self.role,self.staff,self.owner]
        self.guild.get_role=lambda ident: next((r for r in self.guild.roles if r.id==ident),None)
        self.guild.me=SimpleNamespace(guild_permissions=discord.Permissions.all(),top_role=Role(9,'Bot',self.guild,position=9))
        self.category=Channel(10,self.guild)
        self.inside=Channel(11,self.guild,10)
        self.general=Channel(12,self.guild)
        self.outside=Channel(13,self.guild,20)
        self.private=Channel(14,self.guild,10)
        self.private.overwrites[self.staff]=discord.PermissionOverwrite(view_channel=False)
        self.private.overwrites[self.owner]=discord.PermissionOverwrite(view_channel=True)
        self.guild.channels=[self.category,self.inside,self.general,self.outside,self.private]

    async def configure(self):
        return await self.service.configure(self.guild,self.category,self.general,self.role)

    def member(self,roles=None,bot=False):
        member=MagicMock(spec=discord.Member)
        member.guild=self.guild;member.roles=roles or [self.everyone];member.bot=bot
        member.guild_permissions=discord.Permissions.none()
        member.add_roles=AsyncMock()
        member.joined_at=datetime.now(timezone.utc)
        return member

    async def test_category_general_and_hidden_outside(self):
        await self.configure()
        for ch in [self.category,self.inside,self.general]:
            p=ch.permissions_for(self.role)
            self.assertTrue(p.view_channel and p.send_messages and p.read_message_history and p.use_application_commands)
        self.assertFalse(self.outside.permissions_for(self.role).view_channel)
        self.assertFalse(self.private.permissions_for(self.role).view_channel)
        self.assertFalse(self.role.permissions.administrator)
        self.assertTrue(self.private.overwrites_for(self.owner).view_channel)

    async def test_assignment_idempotent_and_bots_skipped(self):
        await self.configure()
        m=self.member()
        self.assertTrue(await self.service.assign(m))
        m.add_roles.assert_awaited_once_with(self.role,reason='SNR automatic new staff role',atomic=True)
        self.assertFalse(await self.service.assign(self.member([self.everyone,self.role])))
        self.assertFalse(await self.service.assign(self.member(bot=True)))
        self.assertFalse(await self.service.assign(self.member([self.everyone,self.owner])))

    async def test_staff_not_owner_permission_gate(self):
        await self.configure()
        tree=ast.parse((Path(__file__).resolve().parents[1]/'bot.py').read_text())
        functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('has_role','is_staff','is_owner')]
        scope=dict(discord=discord,onboarding=self.service,STAFF_ROLE_NAME='SNR Staff',MANAGER_ROLE_NAME='SNR Management',OWNER_ROLE_NAME='SNR Owner')
        exec(compile(ast.Module(body=functions,type_ignores=[]),'bot.py','exec'),scope)
        interaction=SimpleNamespace(user=self.member([self.everyone,self.role]))
        self.assertTrue(scope['is_staff'](interaction))
        self.assertFalse(scope['is_owner'](interaction))
        interaction.user=self.member()
        self.assertFalse(scope['is_staff'](interaction))

    async def test_reject_administrator_and_owner_roles(self):
        self.role.permissions.administrator=True
        with self.assertRaises(ValueError): await self.configure()
        self.role.permissions.administrator=False
        with self.assertRaises(ValueError): await self.service.configure(self.guild,self.category,self.general,self.owner)
        self.assertIsNone(self.service.config(100))

    async def test_reject_everyone_management_and_hierarchy(self):
        self.everyone.permissions.manage_roles=True
        with self.assertRaises(ValueError): await self.configure()
        self.everyone.permissions.manage_roles=False
        self.role.position=20
        with self.assertRaises(ValueError): await self.configure()

    async def test_persistent_config_and_guild_scope(self):
        await self.configure()
        restarted=StaffOnboarding(self.db,'SNR Staff','SNR Management','SNR Owner')
        self.assertTrue(restarted.is_staff(self.member([self.everyone,self.role])))
        self.assertIsNone(restarted.config(999))

    async def test_partial_setup_rolls_back(self):
        self.general.fail=True
        with self.assertRaises(RuntimeError): await self.configure()
        self.assertIsNone(self.service.config(100))
        for ch in self.guild.channels: self.assertNotIn(self.role,ch.overwrites)

    async def test_create_and_move_channel_enforced(self):
        await self.configure()
        ch=Channel(50,self.guild,10)
        await self.service.channel_changed(ch)
        self.assertTrue(ch.permissions_for(self.role).view_channel)
        ch.category_id=20
        await self.service.channel_changed(ch)
        self.assertFalse(ch.permissions_for(self.role).view_channel)
        calls=len(ch.calls)
        await self.service.channel_changed(ch)
        self.assertEqual(len(ch.calls),calls)

    async def test_setup_rerun_preserves_cutoff(self):
        await self.configure();cutoff=self.service.config(100)['enabled_at']
        await self.configure()
        self.assertEqual(self.service.config(100)['enabled_at'],cutoff)

    async def test_missing_role_is_not_granted(self):
        await self.configure();self.guild.roles.remove(self.role)
        m=self.member()
        with self.assertLogs(level='ERROR'): self.assertFalse(await self.service.assign(m))
        m.add_roles.assert_not_awaited()

    async def test_unconfigured_server_skipped(self):
        m=self.member()
        self.assertFalse(await self.service.assign(m))
        m.add_roles.assert_not_awaited()

    async def test_reconcile_only_new_joins(self):
        await self.configure()
        new=self.member();old=self.member();old.joined_at=datetime.now(timezone.utc)-timedelta(days=1)
        async def members(limit=None):
            for m in [new,old]: yield m
        self.guild.fetch_members=members
        await self.service.reconcile(self.guild)
        new.add_roles.assert_awaited_once();old.add_roles.assert_not_awaited()

    async def test_general_cannot_be_management_private(self):
        with self.assertRaises(ValueError): await self.service.configure(self.guild,self.category,self.private,self.role)

    async def test_setup_slash_command_registration(self):
        # Real discord.py resolves all channel/role annotations and decorators.
        from discord import app_commands
        tree=ast.parse((Path(__file__).resolve().parents[1]/'bot.py').read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='new_staff_setup')
        fake_bot=SimpleNamespace(tree=app_commands.CommandTree(discord.Client(intents=discord.Intents.default())))
        scope=dict(discord=discord,app_commands=app_commands,bot=fake_bot)
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'bot.py','exec'),scope)
        command=fake_bot.tree.get_command('snrhub_new_staff_setup')
        self.assertIsNotNone(command)
        self.assertEqual(len(command.parameters),3)

if __name__=='__main__':unittest.main()
