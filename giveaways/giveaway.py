"""
MIT License

Copyright (c) 2021 Andy

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import asyncio
import argparse
import discord

from datetime import datetime
from mee6_py_api import API
from redbot.core import commands, Config
from redbot.core.bot import Red
from typing import Optional, Union
from random import choice, randint
from .converters import FuzzyRole, IntOrLink, TimeConverter
from redbot.core.commands import BadArgument
from redbot.core.utils.chat_formatting import pagify, humanize_list
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu
from .api import mee6_api, Amari

mee6_api = mee6_api()
amari_api = Amari()


class NoExitParser(argparse.ArgumentParser):
    def error(self, message):
        raise BadArgument(message)


async def is_manager(ctx: commands.Context):
    if not ctx.guild:
        return False
    if ctx.channel.is_news():
        return False
    if (
        ctx.channel.permissions_for(ctx.author).administrator
        or ctx.channel.permissions_for(ctx.author).manage_guild
    ):
        return True
    if await ctx.bot.is_owner(ctx.author):
        return True

    cog = ctx.bot.get_cog("Giveaways")

    role = await cog.config.guild(ctx.guild).manager()

    for r in role:
        if r in [role.id for role in ctx.author.roles]:
            return True

    return False


class Giveaways(commands.Cog):
    """A fun cog for giveaways"""

    def __init__(self, bot: Red):
        self.bot = bot
        self.giveaway_task = bot.loop.create_task(self.giveaway_loop())
        self.config = Config.get_conf(
            self, identifier=160805014090190130501014, force_registration=True
        )

        default_guild = {
            "manager": [],
            "pingrole": None,
            "blacklist": [],
            "delete": False,
            "default_req": None,
            "giveaways": {},
            "dmwin": False,
            "dmhost": False,
            "startHeader": "**{giveawayEmoji}   GIVEAWAY   {giveawayEmoji}**",
            "endHeader": "**{giveawayEmoji}   GIVEAWAY ENDED   {giveawayEmoji}**",
            "description": "React with {emoji} to enter",
            "bypassrole": [],
            "winmessage": "You won the giveaway for [{prize}]({url}) in {guild}!",
            "hostmessage": "Your giveaway for [{prize}]({url}) in {guild} has ended. The winners were {winners}",
            "emoji": "🎉",
            "donatorroles": {},
        }

        default_member = {
            "hosted": 0,
            "donated": 0,
            "notes": [],
        }

        default_global = {"secretblacklist": []}

        default_role = {
            "multiplier": 0,
        }

        self.message_cache = {}
        self.giveaway_cache = {}

        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)
        self.config.register_global(**default_global)
        self.config.register_role(**default_role)

    # -------------------------------------Functions---------------------------------
    async def count_invites(self, member: discord.Member):
        cog = self.bot.get_cog("InviteTracker")
        if not cog:
            return 0
        invites = await cog.config.member(member).invites()
        return invites

    def comma_format(self, number: int):
        return "{:,}".format(number)

    def display_time(self, seconds: int) -> str:
        message = ""

        intervals = (
            ("week", 604_800),  # 60 * 60 * 24 * 7
            ("day", 86_400),  # 60 * 60 * 24
            ("hour", 3_600),  # 60 * 60
            ("minute", 60),
            ("second", 1),
        )

        for name, amount in intervals:
            n, seconds = divmod(seconds, amount)

            if n == 0:
                continue

            message += f'{n} {name + "s" * (n != 1)} '

        return message.strip()

    async def giveaway_loop(self):
        await self.bot.wait_until_ready()
        self.tasks = []

        for guild, data in (await self.config.all_guilds()).items():
            for messageid, info in data["giveaways"].items():
                if info["Ongoing"]:
                    self.tasks.append(
                        asyncio.create_task(self.start_giveaway(int(messageid), info))
                    )

    async def can_join(self, user: discord.Member, info):
        try:
            data = await self.config.guild(user.guild).all()
        except AttributeError:
            return False
        secretblacklist = await self.config.secretblacklist()
        if user.id in secretblacklist:
            return False
        if len(data["bypassrole"]) == 0:
            pass
        else:
            for r in data["bypassrole"]:
                if r in [r.id for r in user.roles]:
                    return True

        requirements = info["requirements"]
        if len(data["blacklist"]) == 0:
            pass
        else:
            for r in data["blacklist"]:
                if r in [r.id for r in user.roles]:
                    r = user.guild.get_role(int(r))
                    if not r:
                        continue
                    return (
                        False,
                        f"You have the {r.name} role which has prevented you from entering [JUMP_URL_HERE] giveaway",
                    )
        if not requirements["roles"]:
            pass
        else:
            for r in requirements["roles"]:
                if r in [role.id for role in user.roles]:
                    continue
                r = user.guild.get_role(int(r))
                if not r:
                    continue
                return (
                    False,
                    f"You do not have the `{r.name}` role which is required for [JUMP_URL_HERE] giveaway",
                )

        if requirements["mee6"]:
            user_level = await mee6_api.get_user_rank(user.guild.id, user.id)

            if user_level < requirements["mee6"]:
                return (
                    False,
                    f"You need {requirements['mee6'] - user_level} more MEE6 levels to enter [JUMP_URL_HERE] giveaway",
                )

        if requirements["amari"]:
            user_level = await amari_api.get_amari_rank(user.guild.id, user)
            if user_level < requirements["amari"]:
                return (
                    False,
                    f"You need {requirements['amari'] - user_level} more Amari levels to enter [JUMP_URL_HERE] giveaway",
                )

        if requirements["weeklyamari"]:
            user_level = await amari_api.get_weekly_rank(user.guild.id, user)

            if user_level < requirements["weeklyamari"]:
                return (
                    False,
                    f"You need {requirements['weeklyamari'] - user_level} more weekly amari points to enter [JUMP_URL_HERE] giveaway",
                )

        if requirements["joindays"]:
            days = (datetime.utcnow() - user.joined_at).days
            if days < requirements["joindays"]:
                return (
                    False,
                    f"You need to be in the server for {requirements['joindays'] - days} more days to enter [JUMP_URL_HERE] giveaway",
                )
        if requirements["shared"]:
            cog = self.bot.get_cog("DankLogs")
            if not cog:
                pass
            elif cog.__author__ != "Andy":
                pass
            else:
                shared = await cog.config.member(user).shared()
                if shared < requirements["shared"]:
                    return (
                        False,
                        f"You need to share {requirements['shared'] - shared} more coins in this server to join [JUMP_URL_HERE] giveaway",
                    )
        if requirements["invites"]:
            invites = await self.count_invites(user)
            if invites < requirements["invites"]:
                return (
                    False,
                    f"You need to have {requirements['invites'] - invites} more invites to join [JUMP_URL_HERE] giveaway",
                )

        return True

    def get_color(self, timeleft: int):
        if timeleft <= 30:
            return discord.Color(value=0xff0000)
        elif timeleft <= 240:
            return discord.Color.orange()
        elif timeleft <= 600:
            return discord.Color(value=0xffff00)
        return discord.Color.green()

    async def calculate_multi(self, user: discord.Member):
        total_multi = 1
        for r in user.roles:
            total_multi += await self.config.role(r).multiplier()

        return total_multi

    async def create_invite(self, guild: discord.Guild):
        for channel in guild.text_channels:
            if channel.permissions_for(guild.me).create_instant_invite:
                invite = await channel.create_invite(
                    reason="For a server join requirement", unique=False
                )
                if not invite:
                    invite = await channel.create_invite(
                        reason="For a server join requirement"
                    )
                return f"https://discord.gg/{invite.id}"
        return "Couldn't make an invite"

    async def start_giveaway(self, messageid: int, info):
        channel = self.bot.get_channel(info["channel"])
        self.bot._connection._get_message(int(messageid))
        if not channel:
            return

        message = self.message_cache.get(
            str(messageid), self.bot._connection._get_message(int(messageid))
        )

        if not message:
            message = channel.get_partial_message(int(messageid))

        self.message_cache[str(messageid)] = message
        self.giveaway_cache[str(messageid)] = True

        bypassrole = await self.config.guild(message.guild).bypassrole()
        data = await self.config.guild(message.guild).all()
        gaws = await self.config.guild(message.guild).giveaways()

        while True:
            remaining = info["endtime"] - datetime.utcnow().timestamp()
            if str(messageid) not in gaws:
                return
            elif self.giveaway_cache.get(str(messageid), False) == False:
                return

            elif remaining <= 0:
                self.message_cache[str(messageid)] = await channel.fetch_message(
                    messageid
                )
                self.giveaway_cache[str(messageid)] = False
                self.tasks.append(
                    asyncio.create_task(self.end_giveaway(int(messageid), info))
                )
                return

            remaining = datetime.fromtimestamp(info["endtime"]) - datetime.utcnow()
            pretty_time = self.display_time(round(remaining.total_seconds()))

            host = message.guild.get_member(info["host"])

            if not host:
                host = "Host Not Found"
            else:
                host = host.mention

            color = self.get_color(remaining.total_seconds())

            e = discord.Embed(
                title=info["title"],
                description=data["description"].replace("{emoji}", data["emoji"]),
                color=color,
            )
            e.description += f"\nTime Left: **{pretty_time}** \n"
            e.description += f"Host: {host}"

            if info["donor"]:
                e.add_field(
                    name="Donor", value="<@{0}>".format(info["donor"]), inline=False
                )

            requirements = info["requirements"]

            reqs = await self.gen_req_message(message.guild, info["requirements"])
            if reqs:
                e.add_field(name="Requirements", value=reqs, inline=False)

            e.timestamp = datetime.fromtimestamp(info["endtime"])
            e.set_footer(text="Winners: {0} | Ends at".format(info["winners"]))

            try:
                await message.edit(
                    embed=e,
                    content=data["startHeader"].replace(
                        "{giveawayEmoji}", data["emoji"]
                    ),
                )
            except discord.NotFound:
                return

            emoji = data["emoji"]
            await message.add_reaction(emoji)
            self.message_cache[str(messageid)] = message
            if remaining.total_seconds() <= 60:
                await asyncio.sleep(remaining.total_seconds())
            else:
                await asyncio.sleep(round(remaining.total_seconds() / 4))

    async def end_giveaway(self, messageid: int, info, reroll: int = -1):
        channel = self.bot.get_channel(info["channel"])

        if not channel:
            return

        message = self.bot._connection._get_message(int(messageid))

        if not message:
            try:
                message = await channel.fetch_message(messageid)
            except discord.NotFound:
                giveaways = await self.config.guild(channel.guild).giveaways()
                giveaways.pop(str(messageid))
                await self.config.guild(channel.guild).giveaways.set(giveaways)
                return

        giveaways = await self.config.guild(message.guild).giveaways()
        giveaways[str(messageid)]["Ongoing"] = False
        self.giveaway_cache[str(messageid)] = False
        await self.config.guild(message.guild).giveaways.set(giveaways)

        winners_list = []

        users = []
        data = await self.config.guild(message.guild).all()
        for i, r in enumerate(message.reactions):
            if str(r) == data["emoji"]:
                users = await message.reactions[i].users().flatten()
                break

        if users == [None]:
            return

        bypassrole = await self.config.guild(message.guild).bypassrole()

        for user in users:
            if user.mention in winners_list:
                continue
            if user.bot:
                continue
            if isinstance(user, discord.User):
                continue
            can_join = await self.can_join(user, info)
            if can_join == True:
                multi = await self.calculate_multi(user)
                for i in range(multi):
                    winners_list.append(user.mention)

        final_list = []

        if reroll == -1:
            winners = info["winners"]
        else:
            winners = reroll

        for i in range(winners):
            if len(winners_list) == 0:
                continue
            count = 0
            win = choice(winners_list)
            x = False
            while win in final_list:
                win = choice(winners_list)
                count += 1
                if count >= 6:
                    x = True
                    break  # for when it runs out of reactions etc.
            if x:
                continue
            final_list.append(win)

        if len(final_list) == 0:
            host = (
                info["host"]
                if message.guild.get_member(info["host"]) is not None
                else "Host Not Found"
            )
            e = discord.Embed(
                title=info["title"], description=f"Host: <@{host}> \n Winners: None"
            )

            reqs = await self.gen_req_message(message.guild, info["requirements"])

            if not reqs:
                pass
            else:
                e.add_field(name="Requirements", value=reqs, inline=False)

            e.set_footer(text="Ended at ")
            e.timestamp = datetime.utcnow()

            await channel.send(
                f"There were no valid entries for the **{info['title']}** giveaway \n{message.jump_url}"
            )
            await message.edit(
                content=data["endHeader"].replace("{giveawayEmoji}", data["emoji"]),
                embed=e,
            )

        else:
            winners = humanize_list(final_list)
            host = (
                info["host"]
                if message.guild.get_member(info["host"]) is not None
                else "Unknown Host"
            )

            e = discord.Embed(
                title=info["title"],
                description=f"Winner(s): {winners}\nHost: <@{host}>",
            )

            reqs = await self.gen_req_message(message.guild, info["requirements"])
            if reqs:
                e.add_field(name="Requirements", value=reqs, inline=False)

            if info["donor"]:
                donor = message.guild.get_member(info["donor"])
                if not donor:
                    pass
                else:
                    e.add_field(name="Donor", value=donor.mention, inline=False)

            e.set_footer(text="Ended at ")
            e.timestamp = datetime.utcnow()
            await message.edit(
                content=data["endHeader"].replace("{giveawayEmoji}", data["emoji"]),
                embed=e,
            )
            await message.channel.send(
                f"The winners for the **{info['title']}** giveaway are \n{winners}\n{message.jump_url}"
            )

            dmhost = await self.config.guild(message.guild).dmhost()
            dmwin = await self.config.guild(message.guild).dmwin()
            if dmhost:
                host = message.guild.get_member(int(host))
                if not host:
                    pass
                else:
                    hostmessage = await self.config.guild(message.guild).hostmessage()
                    e = discord.Embed(
                        title=f"Your giveaway has ended",
                        description=hostmessage.replace("{prize}", str(info["title"]))
                        .replace("{winners}", winners)
                        .replace("{guild}", message.guild.name)
                        .replace("{url}", message.jump_url),
                    )
                    try:
                        await host.send(embed=e)
                    except discord.errors.Forbidden:
                        pass
            if dmwin:
                winmessage = await self.config.guild(message.guild).winmessage()
                for mention in final_list:
                    mention = message.guild.get_member(
                        int(mention.lstrip("<@!").lstrip("<@").rstrip(">"))
                    )
                    if not mention:
                        continue

                    e = discord.Embed(
                        title=f"You won a giveaway!",
                        description=winmessage.replace("{prize}", str(info["title"]))
                        .replace("{host}", f"<@{info['host']}>")
                        .replace("{guild}", message.guild.name)
                        .replace("{url}", message.jump_url),
                    )
                    try:
                        await mention.send(embed=e)
                    except discord.errors.Forbidden:
                        pass

    def cog_unload(self):
        self.giveaway_task.cancel()
        for task in self.tasks:
            task.cancel()

    async def send_final_message(self, ctx, ping, msg, embed):
        allowed_mentions = discord.AllowedMentions(roles=True, everyone=False)
        final_message = ""
        if ping:
            pingrole = await self.config.guild(ctx.guild).pingrole()
            if not pingrole:
                pass
            else:
                role = ctx.guild.get_role(int(pingrole))
                if not role:
                    await self.config.guild(ctx.guild).pingrole.clear()
                else:
                    final_message += role.mention
                    final_message += " "

        if msg:
            final_message += " ".join(msg)

        if final_message == "" or len(final_message) == 0:
            return
        if embed:
            e = discord.Embed(description=final_message, color=await ctx.embed_color())
            try:
                role 
            except (NameError, UnboundLocalError):
                await ctx.send(
                embed=e, allowed_mentions=allowed_mentions
            )
            else:
                await ctx.send(
                    embed=e, content=role.mention, allowed_mentions=allowed_mentions
                )
            
        else:
            await ctx.send(final_message, allowed_mentions=allowed_mentions)

    async def setnote(self, user: discord.Member, note: list):
        notes = await self.config.member(user).notes()
        notes.append(" ".join(note))
        await self.config.member(user).notes.set(notes)

    async def add_amount(self, user: discord.Member, amt: int):
        previous = await self.config.member(user).donated()
        previous += amt
        await self.config.member(user).donated.set(previous)
        await self.update_donator_roles(user)

    async def update_donator_roles(self, member: discord.Member) -> None:
        roles = await self.config.guild(member.guild).donatorroles()
        donated = await self.config.member(member).donated()

        for role_id, amount_required in roles.items():
            role = member.guild.get_role(int(role_id))
            if not role:
                all_roles = await self.config.guild(member.guild).donatorroles()
                all_roles.pop(role_id)
                await self.config.guild(member.guild).roles.set(all_roles)
                continue
            if donated < amount_required:
                if role not in member.roles:
                    continue
                try:
                    await member.remove_roles(role)
                except (discord.errors.Forbidden, discord.HTTPException):
                    pass
            else:
                if role in member.roles:
                    continue
                try:
                    await member.add_roles(role)
                except discord.errors.Forbidden:
                    pass

    async def gen_req_message(self, guild: discord.Guild, requirements: dict) -> str:
        reqs = ""
        if requirements["roles"]:
            roles = []
            for r in requirements["roles"]:
                role = guild.get_role(r)
                if not role:
                    continue
                roles.append(role.mention)
            roles = humanize_list(roles)
            reqs += f"Roles: {roles}\n"

        if requirements["mee6"]:
            reqs += f"Minimum MEE6 Level: {requirements['mee6']}\n"

        if requirements["amari"]:
            reqs += f"Minimum Amari Level: {requirements['amari']}\n"

        if requirements["weeklyamari"]:
            reqs += f"Minimum Weekly Amari: {requirements['weeklyamari']}\n"

        if requirements["joindays"]:
            reqs += f"Minimum days in server: {requirements['joindays']}\n"

        if requirements["server"]:
            server = self.bot.get_guild(requirements["server"])
            if not server:
                pass
            else:
                invite = await self.create_invite(server)
                reqs += "Must join **[{server.name}]({invite})**\n"

        if requirements["invites"]:
            reqs += f"Minimum number of invites: {requirements['invites']}"

        if requirements["shared"]:
            reqs += (
                f"Minimum shared dankmemer coins in server: {requirements['shared']}\n"
            )

        bypassroles = await self.config.guild(guild).bypassrole()

        if bypassroles:
            roles = []
            for r in bypassroles:
                r = guild.get_role(int(r))
                if not r:
                    continue
                roles.append(r.mention)
            reqs += f"Bypass Role(s): {humanize_list(roles)}"

        return reqs if reqs else None

    # -------------------------------------gset---------------------------------

    @commands.group(name="giveawayset", aliases=["gset"])
    @commands.admin_or_permissions(administrator=True)
    @commands.guild_only()
    async def giveawayset(self, ctx):
        """Set your server settings for giveaways"""
        pass

    @giveawayset.group(name="manager")
    @commands.admin_or_permissions(administrator=True)
    async def manager(self, ctx):
        """Set the role that cant join any giveaways"""
        pass

    @manager.command(name="add")
    async def manager_add(self, ctx, role: discord.Role):
        """Add a role to the manager list"""
        roles = await self.config.guild(ctx.guild).manager()
        if role.id in roles:
            return await ctx.send("This role is already a manager")
        roles.append(role.id)
        await self.config.guild(ctx.guild).manager.set(roles)
        await ctx.send("Added to the manager roles")

    @manager.command(name="remove")
    async def manager_remove(self, ctx, role: discord.Role):
        """Remove a role from your manager roles"""
        roles = await self.config.guild(ctx.guild).manager()
        if role.id not in roles:
            return await ctx.send("This role is not a manager")
        roles.remove(role.id)
        await self.config.guild(ctx.guild).manager.set(roles)
        await ctx.send("Removed from the manager roles")

    @giveawayset.command(name="pingrole")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_pingrole(self, ctx, role: Optional[discord.Role] = None):
        """Set the role to ping if a ping is used"""
        if not role:
            return await ctx.send("This isn't a role")

        await self.config.guild(ctx.guild).pingrole.set(role.id)

        await ctx.send(f"**{role.name}** will now be pinged if a ping is specified")

    @giveawayset.command(
        name="defaultrequirement", aliases=["requirement", "defaultreq"]
    )
    @commands.admin_or_permissions(administrator=True)
    async def defaultrequirement(self, ctx, role: Optional[discord.Role] = None):
        """The default requirement for giveaways"""
        if not role:
            await self.config.guild(ctx.guild).default_req.clear()
            return await ctx.send("I will no longer have default requirements")

        await self.config.guild(ctx.guild).default_req.set(role.id)

        await ctx.send(f"The default role requirement is now **{role.name}**")

    @giveawayset.command(name="delete")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_delete(self, ctx, delete: Optional[bool] = True):
        """Toggle whether to delete the giveaway creation message"""
        if not delete:
            await self.config.guild(ctx.guild).delete.set(False)
            await ctx.send(
                "I will no longer delete invocation messages when creating giveaways"
            )
        else:
            await self.config.guild(ctx.guild).delete.set(True)
            await ctx.send(
                "I will now delete invocation messages when creating giveaways"
            )

    @giveawayset.command(name="dmhost")
    @commands.admin_or_permissions(administrator=True)
    async def dmhost(self, ctx, dmhost: Optional[bool] = True):
        """Toggle whether to DM the host when the giveaway ends"""
        if not dmhost:
            await self.config.guild(ctx.guild).dmhost.set(False)
            await ctx.send("I will no longer dm hosts")
        else:
            await self.config.guild(ctx.guild).dmhost.set(True)
            await ctx.send("I will now dm hosts")

    @giveawayset.command(name="dmwin")
    @commands.admin_or_permissions(administrator=True)
    async def dmwin(self, ctx, dmwin: Optional[bool] = True):
        """Toggles whether to DM the winners of the giveaway"""
        if not dmwin:
            await self.config.guild(ctx.guild).dmwin.set(False)
            await ctx.send("I will no longer dm winners")
        else:
            await self.config.guild(ctx.guild).dmwin.set(True)
            await ctx.send("I will now dm winners")

    @giveawayset.group(
        name="bypassrole", aliases=["aarole", "bprole", "alwaysallowedrole"]
    )
    @commands.admin_or_permissions(administrator=True)
    async def bypassrole(self, ctx):
        """Set the role that can bypass all giveaway requirements (or remove it)"""
        pass

    @bypassrole.command(name="add")
    async def bypassrole_add(self, ctx, role: discord.Role):
        """Add a bypass role"""
        roles = await self.config.guild(ctx.guild).bypassrole()
        if role.id in roles:
            return await ctx.send("This role already bypasses giveaway requirements")
        roles.append(role.id)
        await self.config.guild(ctx.guild).bypassrole.set(roles)
        await ctx.send("Added to the bypass roles")

    @bypassrole.command(name="remove")
    async def bypassrole_remove(self, ctx, role: discord.Role):
        """Remove a bypass role"""
        roles = await self.config.guild(ctx.guild).bypassrole()
        if role.id not in roles:
            return await ctx.send("This role does not bypass requirements")
        roles.remove(role.id)
        await self.config.guild(ctx.guild).bypassrole.set(roles)
        await ctx.send("Removed from the bypass roles")

    @giveawayset.group(name="blacklistrole", aliases=["blrole"])
    @commands.admin_or_permissions(administrator=True)
    async def blacklistrole(self, ctx):
        """Set the role that cant join any giveaways"""
        pass

    @blacklistrole.command(name="add")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_add(self, ctx, role: discord.Role):
        """Add a role to blacklist"""
        roles = await self.config.guild(ctx.guild).blacklist()
        if role.id in roles:
            return await ctx.send("This role is already blacklisted")
        roles.append(role.id)
        await self.config.guild(ctx.guild).blacklist.set(roles)
        await ctx.send("Added to the blacklisted roles")

    @blacklistrole.command(name="remove")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_remove(self, ctx, role: discord.Role):
        """Remove a role from your blacklisted roles"""
        roles = await self.config.guild(ctx.guild).blacklist()
        if role.id not in roles:
            return await ctx.send("This role is not blacklisted")
        roles.remove(role.id)
        await self.config.guild(ctx.guild).blacklist.set(roles)
        await ctx.send("Removed from the blacklisted roles")

    @giveawayset.command(name="multi", aliases=["multiplier"])
    async def multi(
        self, ctx, role: Optional[discord.Role] = None, multi: Optional[int] = 0
    ):
        """Sets a multiplier for a role. At the end when a giveaway ends, for each role they have, the multilpier will add on that amount, not multiply.
        It will enter the user that many times into the giveaway, if they enter a giveaway, they automatically have 1 entry. So each role multiplier adds one on to it.
        """
        if not role:
            role = ctx.guild.default_role
        if multi > 500:
            return await ctx.send(
                "Sorry, the max multiplier is 500 to make giveaways more efficient"
            )
        await self.config.role(role).multiplier.set(multi)
        await ctx.send(f"`{role}` will now have a multilpier of {multi}")

    @giveawayset.command(name="settings", aliases=["showsettings", "stats"])
    async def settings(self, ctx):
        """View server settings"""
        data = await self.config.guild(ctx.guild).all()
        bprole = data["bypassrole"]
        if len(bprole) == 0:
            bypass_roles = "None"
        else:
            bypass_roles = []
            for r in bprole:
                bypass_roles.append(f"<@&{r}>")
            bypass_roles = humanize_list(bypass_roles)

        blacklisted_roles = data["blacklist"]
        if len(blacklisted_roles) == 0:
            blacklisted_roles = "None"
        else:
            blacklisted = []
            for r in blacklisted_roles:
                blacklisted.append(f"<@&{r}>")
                blacklisted_roles = humanize_list(blacklisted)

        if len(data["manager"]) == 0:
            data["manager"] = "Not Set"
        else:
            managers = []
            for m in data["manager"]:
                managers.append(f"<@&{m}>")
            data["manager"] = humanize_list(managers)

        e = discord.Embed(
            title=f"Giveaway Settings for {ctx.guild.name}",
            color=discord.Color.blurple(),
        )
        e.add_field(name="Manager Role", value="{0}".format(data["manager"]))
        e.add_field(name="Bypass Roles", value=bypass_roles)
        e.add_field(name="Blacklisted Roles", value=blacklisted_roles)
        e.add_field(
            name="Default Requirement",
            value="{0}".format(
                f"<@&{data['default_req']}>"
                if data["default_req"] is not None
                else "None"
            ),
        )
        e.add_field(name="Total Giveaways", value=len(data["giveaways"].keys()))
        e.add_field(name="DM on win", value=data["dmwin"])
        e.add_field(name="DM host", value=data["dmhost"])
        e.add_field(name="Autodelete invocation messages", value=data["delete"])
        e.add_field(
            name="Pingrole",
            value="{0}".format(
                f"<@&{data['pingrole']}>" if data["pingrole"] is not None else "None"
            ),
        )
        e.add_field(
            name="Host Message",
            value="```\n{0}```".format(data["hostmessage"]),
            inline=False,
        )
        e.add_field(
            name="Win Message",
            value="```\n{0}```".format(data["winmessage"]),
            inline=False,
        )
        e.add_field(
            name="Start Header",
            value="```\n{0}```".format(data["startHeader"]),
            inline=False,
        )
        e.add_field(
            name="End Header",
            value="```\n{0}```".format(data["endHeader"]),
            inline=False,
        )
        e.add_field(
            name="Description",
            value="```\n{0}```".format(data["description"]),
            inline=False,
        )
        e.add_field(name="Emoji", value=data["emoji"])

        await ctx.send(embed=e)

    @giveawayset.command(name="hostmessage")
    @commands.admin_or_permissions(administrator=True)
    async def hostmessage(self, ctx, *, message: str = None):
        """Set the message sent to the host when the giveaway ends. If there are no winners, it won't be sent.
        Your dmhost settings need to be toggled for this to work.
        Variables: {guild}: Server Name
        {winners}: Winners of the giveaway
        {prize}: The prize/title of the giveaway
        {url}: The jump url
        """
        if not message:
            await self.config.guild(ctx.guild).hostmessage.clear()
            await ctx.send("I've reset your servers host message")
        else:
            await self.config.guild(ctx.guild).hostmessage.set(message)
            await ctx.send(f"Your message is now `{message}`")

    @giveawayset.command(name="winmessage")
    @commands.admin_or_permissions(administrator=True)
    async def winmessage(self, ctx, *, message: str = None):
        """Set the message sent to the winner(s) when the giveaway ends. If there are no winners, it won't be sent.
        Your dmwin settings need to be toggled for this to work.
        Variables
        {guild}: Your server name
        {host}: The host of the giveaway
        {prize}: The title/prize of the giveaway
        {url}: The jump url"""
        if not message:
            await self.config.guild(ctx.guild).winmessage.clear()
            await ctx.send("I've reset your servers win message")
        else:
            await self.config.guild(ctx.guild).winmessage.set(message)
            await ctx.send(f"Your message is now `{message}`")

    @giveawayset.command(name="startheader")
    @commands.admin_or_permissions(administrator=True)
    async def startheader(self, ctx, *, message: str = None):
        """Set the content for the giveaway message, not the embed. See gset description for that
        Variables
        {giveawayEmoji}: Your servers giveaway emoji, defaults to :tada: if you haven't set one"""
        if not message:
            await self.config.guild(ctx.guild).startHeader.clear()
            await ctx.send("I've reset your servers startheader")
        else:
            await self.config.guild(ctx.guild).startHeader.set(message)
            await ctx.send(f"Your startheader is now `{message}`")

    @giveawayset.command(name="endheader")
    @commands.admin_or_permissions(administrator=True)
    async def endheader(self, ctx, *, message: str = None):
        """Set the content for the giveaway message after it ends.
        Variables
        {giveawayEmoji}: Your servers giveaway emoji, defaults to :tada: if you haven't set one"""
        if not message:
            await self.config.guild(ctx.guild).endHeader.clear()
            await ctx.send("I've reset your servers endheader")
        else:
            await self.config.guild(ctx.guild).endHeader.set(message)
            await ctx.send(f"Your endheader is now `{message}`")

    @giveawayset.command(name="description")
    @commands.admin_or_permissions(administrator=True)
    async def description(self, ctx, *, message: str = None):
        """Set the content for the giveaway message after it ends.
        Variables:
        {emoji}: The emoji you use for giveaways"""
        if not message:
            await self.config.guild(ctx.guild).description.clear()
            await ctx.send("I've reset your servers embed description")
        else:
            await self.config.guild(ctx.guild).description.set(message)
            await ctx.send(f"Your embed description is now `{message}`")

    @giveawayset.command(name="emoji")
    @commands.admin_or_permissions(administrator=True)
    async def emoji(self, ctx, emoji: Union[discord.Emoji, discord.PartialEmoji, None]):
        """Set the custom emoji to use for giveaways"""
        if not emoji:
            await self.config.guild(ctx.guild).emoji.clear()
            await ctx.send("I will no longer use custom emojis.")
        else:
            await self.config.guild(ctx.guild).emoji.set(str(emoji))
            await ctx.send(f"Your emoji is now {str(emoji)}")

    @giveawayset.group(aliases=["donor", "donatorroles", "donatorrole"])
    async def donator(self, ctx: commands.Context):
        """Manage donator roles for the server"""
        pass

    @donator.command(name="add", aliases=["edit"])
    async def _add(self, ctx: commands.Context, role: discord.Role, amount: int):
        """Edit or Add a donator role"""
        roles = await self.config.guild(ctx.guild).donatorroles()
        roles[str(role.id)] = amount
        await self.config.guild(ctx.guild).donatorroles.set(roles)
        await ctx.send("Updated")

    @donator.command()
    async def remove(self, ctx: commands.Context, role: discord.Role):
        """Remove a donator role"""
        roles = await self.config.guild(ctx.guild).donatorroles()
        try:
            del roles[str(role.id)]
        except KeyError:
            return await ctx.send("This role isn't a donator role")

        await self.config.guild(ctx.guild).donatorroles.set(roles)
        await ctx.send(f"Removed `{role.name}` as a donator role")

    @donator.command(name="settings", aliases=["show", "showsettings"])
    async def _settings(self, ctx: commands.Context):
        roles = await self.config.guild(ctx.guild).donatorroles()
        message = "Format: <role> - <amount needed>\n"

        for role_id, amount_needed in roles.items():
            message += f"<@&{role_id}> - {amount_needed}"

        e = discord.Embed(
            title="Donator Roles", description=message, color=await ctx.embed_color()
        )
        e.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon_url)
        await ctx.send(embed=e)

    # -------------------------------------giveaways---------------------------------
    @commands.group(name="giveaway", aliases=["g"])
    @commands.guild_only()
    async def giveaway(self, ctx):
        """Start, end, reroll giveaways and more!"""
        pass

    @giveaway.group(name="secretblacklist")
    @commands.is_owner()
    async def secretblacklist(self, ctx):
        """Secretly blacklist people from winning ANY giveaways"""
        pass

    @secretblacklist.command(name="add")
    async def secretblacklist_add(
        self, ctx, user: Union[discord.Member, discord.User, int]
    ):
        if isinstance(user, discord.Member) or isinstance(user, discord.User):
            user = user.id
        else:
            try:
                user = await self.bot.fetch_user(user)
            except discord.NotFound:
                return await ctx.send("This doesn't seem to be a user...")
        bl = await self.config.secretblacklist()
        if user in bl:
            return await ctx.send("This user is already blacklisted...")
        bl.append(user)
        await self.config.secretblacklist.set(bl)
        await ctx.send("Added to the blacklist")

    @secretblacklist.command(name="remove")
    async def secretblacklist_remove(
        self, ctx, user: Union[discord.Member, discord.User, int]
    ):
        if isinstance(user, discord.Member) or isinstance(user, discord.User):
            user = user.id
        else:
            try:
                user = await self.bot.fetch_user(user)
            except discord.NotFound:
                return await ctx.send("This doesn't seem to be a user...")
        bl = await self.config.secretblacklist()
        if user not in bl:
            return await ctx.send("This user is not blacklisted...")
        bl.remove(user)
        await self.config.secretblacklist.set(bl)
        await ctx.send("Removed from the blacklist")

    @giveaway.command(name="clearended")
    @commands.admin_or_permissions(manage_guild=True)
    async def clearended(self, ctx, *dontclear):
        """Clear the giveaways that have already ended in your server. Put all the message ids you dont want to clear after this to not clear them"""
        gaws = await self.config.guild(ctx.guild).giveaways()
        to_delete = []
        for messageid, info in gaws.items():
            if str(messageid) in dontclear:
                continue
            if not info["Ongoing"]:
                to_delete.append(str(messageid))

        for messageid in to_delete:
            gaws.pop(messageid)
        await self.config.guild(ctx.guild).giveaways.set(gaws)
        await ctx.send(f"Successfully cleared {len(to_delete)} inactive giveaways")

    @giveaway.command(name="help")
    async def g_help(self, ctx):
        """Explanation on how to start a giveaway"""
        pages = []

        pages.append(
            """Starting Giveaways:

        Base Command: `[p] g start <time> [winners=1] [requirements=None] [flags]`
        `<time>` - The time the giveaway should last. Can be no less than 3 seconds and no more than 7 weeks
        `[winners]` - The amount of winners for the giveaway. Defaults to one.
        `[requirements]` - The requirements to have for the giveaway (will be explained in other pages). Defaults to no requirements
        `[flags]` - Flags to add customizable features to the giveaway. Explained in other pages.

        The time can be `2d` or `30m`, where 2d would be 2 days. If no unit is specified it will default to seconds
        The winners can end with `w` or not. so `2w` and `2` would both work
        """
        )

        pages.append(
            """Requirements and how to use them

            You can specify a type for a requirement like so. `type:argument`
            Ex. `amari:10` or `weeklyamari:50`

            You can also have no type to default to a role, if no role is found, it will skip.
            
            Requirements should be split with either `|` or `;;` to have multiple requirements

            TYPES:
            `amari` - The minimum amari level the user should have 
            `weeklyamari or wa` - The minimum weekly amari the user should have 
            `mee6` - The minimum mee6 level the user should have 
            `joindays` - The minimum amount of days the user has been in the server
            `invites` - The minimum invites the user needs to have. it is a total amount, so left users will be counted in
            `server` - The server a user should be in, the bot needs to be in the server as well
            `shared` - The minimum amount of dankmemer coins the user needs to share in the server

            `Ex. Developer;;mee6:5;;wa:1000;;amari:15;;server:discord.gg/someservercode`

            Specify `none` for the requirement to prevent the bot from converting roles
            """
        )

        pages.append(
            """

            Flags:

            Flags are optional arguments added to the giveaways end to make it better.
            Flags will always start with `--`

            They should be added to the end of the giveaway like so:
            `g start <time> [winners] [requirements] [flags]`

            FLAGS:
            `--ping` - Putting this flag will ping the giveaway pingrole (if set), for your server
            `--msg <message>` - Adds a message after the giveaway.
            `--note <note>` - Adds a note to store to the users giveaway profile 
            `--amt <int>` - Stores an amount (must be integer) to the users giveaway profile
            `--donor <donor>` - Add a field with the donors mention
            `--embed`, toggles whether to send the message as an embed, only works if you have a message flag
            """
        )

        pages.append(
            """Examples:
            `.g start 10m 1w Contributor --donor @Andee#8552 --amt 50000 --note COINS ARE YUMMY`

            `.g start 10m 1 @Owners lots of yummy coins --ping --msg I will eat these coins --donor @Andee`

            `.g start 10m 1w none coffee`
            
            `.g start 10m 1w GiveawayPing;;Admin;;Mod;;amari:10;;weeklyamari:50;;mee6:10 food`
        """
        )

        embeds = []
        for i, page in enumerate(pages, start=1):
            e = discord.Embed(
                title=f"Giveaway Help Menu Page {i} out of {len(pages)} pages",
                description=page,
                color=await ctx.embed_color(),
            )
            embeds.append(e)

        await menu(ctx, embeds, DEFAULT_CONTROLS)

    @giveaway.command(name="start")
    async def g_start(
        self,
        ctx,
        channel: Optional[discord.TextChannel] = None,
        time: TimeConverter = 3,
        winners: str = "1",
        requirements: Optional[FuzzyRole] = None,
        *,
        title = "Giveaway!",
    ):
        """Start a giveaway in your server. Flags and Arguments are explained with .giveaway help"""
        if not is_manager(ctx):
            return await ctx.send("You do not have proper permissions to do this. You need to be either admin or have the giveaway role. This cannot be run outside announcement channels either.")
        title = title.split("--")
        title = title[0]
        flags = ctx.message.content
        winners = winners.rstrip("w")

        if not winners.isdigit():
            return await ctx.send(
                f"I could not get an amount of winners from {winners}"
            )
        winners = int(winners)
        if winners < 0:
            return await ctx.send("Can've have less than 1 winner")
        
        if channel:
            ctx.channel = channel

        parser = NoExitParser()

        parser.add_argument("--ping", action="store_true", default=False)
        parser.add_argument("--msg", nargs="*", type=str, default=None)
        parser.add_argument("--donor", nargs="?", type=str, default=None)
        parser.add_argument("--amt", nargs="?", type=int, default=0)
        parser.add_argument("--note", nargs="*", type=str, default=None)
        parser.add_argument("--embed", action="store_true", default=False)
        parser.add_argument("--pin", action="store_true", default=False)

        try:
            flags = vars(parser.parse_known_args(flags.split())[0])

            if flags["donor"]:
                donor = flags["donor"].lstrip("<@!").lstrip("<@").rstrip(">")
                if donor.isdigit():
                    donor = ctx.guild.get_member(int(donor))
                else:
                    donor = discord.utils.get(ctx.guild.members, name=donor)
                if not donor:
                    return await ctx.send("The donor provided is not valid")
                flags["donor"] = donor.id

        except Exception as exc:
            return await ctx.send(str(exc))

        guild = ctx.guild
        data = await self.config.guild(guild).all()

        gaws = await self.config.guild(guild).giveaways()

        if not requirements:
            requirements = {
                "mee6": None,
                "amari": None,
                "weeklyamari": None,
                "roles": None,
                "joindays": None,
                "invites": None,
                "shared": None,
            }
        else:
            if requirements["roles"]:
                requirements["roles"] = [r.id for r in requirements["roles"]]

        e = discord.Embed(
            title=title,
            description=f"Hosted By: {ctx.author.mention}",
            timestamp=datetime.utcnow(),
            color=discord.Color.green(),
        )

        e.set_footer(text="Ending at")

        if time > 4233600 or time < 2:
            return await ctx.send(
                "The time cannot be more than 7 weeks and less than 3 seconds"
            )
        ending_time = datetime.utcnow().timestamp() + float(time)
        ending_time = datetime.fromtimestamp(ending_time)
        pretty_time = self.display_time(time)

        e.description += f"\n Time Left: {pretty_time}"
        e.timestamp = ending_time

        gaw_msg = await ctx.send(embed=e)

        msg = str(gaw_msg.id)

        gaws[msg] = {}
        gaws[msg]["host"] = ctx.author.id
        gaws[msg]["Ongoing"] = True
        gaws[msg]["requirements"] = requirements
        gaws[msg]["winners"] = winners
        gaws[msg]["title"] = title
        gaws[msg]["endtime"] = datetime.utcnow().timestamp() + int(time)
        gaws[msg]["channel"] = ctx.channel.id
        gaws[msg]["donor"] = flags["donor"]

        await self.config.guild(guild).giveaways.set(gaws)

        delete = await self.config.guild(ctx.guild).delete()

        if ctx.channel.permissions_for(ctx.me).manage_messages and delete:
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass

        await self.send_final_message(ctx, flags["ping"], flags["msg"], flags["embed"])

        if flags["note"]:
            if flags["donor"]:
                await self.setnote(ctx.guild.get_member(flags["donor"]), flags["note"])
            else:
                await self.setnote(ctx.author, flags["note"])

        if flags["amt"]:
            if flags["donor"]:
                await self.add_amount(
                    ctx.guild.get_member(flags["donor"]), flags["amt"]
                )
            else:
                await self.add_amount(ctx.author, flags["amt"])

        if flags["donor"]:
            hosted = await self.config.member(
                ctx.guild.get_member(flags["donor"])
            ).hosted()
            hosted += 1
            await self.config.member(ctx.guild.get_member(flags["donor"])).hosted.set(
                hosted
            )
        else:
            prev = await self.config.member(ctx.author).hosted()
            prev += 1
            await self.config.member(ctx.author).hosted.set(prev)
        
        if flags["pin"]:
            try:
                await gaw_msg.pin(reason="Pinned using --pin flag in giveaways")
            except discord.errors.Forbidden:
                await ctx.send("I don't have permissions to pin the message", delete_after=5)
            except discord.NotFound:
                pass 
            except discord.HTTPException:
                await ctx.send("I can't pin this message becuase the channel has 50 pinned messages already")

        self.message_cache[str(msg)] = gaw_msg
        self.giveaway_cache[str(msg)] = True

        self.tasks.append(asyncio.create_task(self.start_giveaway(int(msg), gaws[msg])))

    @giveaway.command(name="end")
    async def end(self, ctx, messageid: Optional[IntOrLink] = None):
        """End a giveaway"""
        if not is_manager(ctx):
            return await ctx.send("You do not have proper permissions to do this. You need to be either admin or have the giveaway role. This cannot be run outside announcement channels either.")
        if not messageid:
            if hasattr(ctx.message, "reference") and ctx.message.reference != None:
                msg = ctx.message.reference.resolved
                if isinstance(msg, discord.Message):
                    messageid = msg.id
        gaws = await self.config.guild(ctx.guild).giveaways()
        if messageid is None:
            for messageid, info in list(gaws.items())[::-1]:
                if info["channel"] == ctx.channel.id and info["Ongoing"]:
                    await self.end_giveaway(messageid, info)
                    return
            return await ctx.send(
                "There aren't any giveaways in this channel, specify a message id/link to end another channels giveaways"
            )
        gaws = await self.config.guild(ctx.guild).giveaways()
        if str(messageid) not in gaws:
            return await ctx.send("This isn't a giveaway.")
        elif gaws[str(messageid)]["Ongoing"] == False:
            return await ctx.send(
                f"This giveaway has ended. You can reroll it with `{ctx.prefix}g reroll {messageid}`"
            )
        else:
            await self.end_giveaway(messageid, gaws[str(messageid)])

    @giveaway.command(name="reroll")
    async def reroll(
        self, ctx, messageid: Optional[IntOrLink], winners: Optional[int] = 1
    ):
        """Reroll a giveaway"""
        if not is_manager(ctx):
            return await ctx.send("You do not have proper permissions to do this. You need to be either admin or have the giveaway role. This cannot be run outside announcement channels either.")
        if not messageid:
            if hasattr(ctx.message, "reference") and ctx.message.reference != None:
                msg = ctx.message.reference.resolved
                if isinstance(msg, discord.Message):
                    messageid = msg.id
        gaws = await self.config.guild(ctx.guild).giveaways()
        if not messageid:
            for messageid, info in list(gaws.items())[::-1]:
                if info["channel"] == ctx.channel.id and info["Ongoing"] == False:
                    await self.end_giveaway(messageid, info)
                    return
            return await ctx.send(
                "There aren't any giveaways in this channel, specify a message id/link to end another channels giveaways"
            )
        elif winners <= 0:
            return await ctx.send("You can't have no winners.")
        if str(messageid) not in gaws:
            return await ctx.send("This giveaway does not exist")
        elif gaws[str(messageid)]["Ongoing"] == True:
            return await ctx.send(
                f"This giveaway has not yet ended, you can end it with `{ctx.prefix}g end {messageid}`"
            )
        else:
            await self.end_giveaway(messageid, gaws[str(messageid)], winners)

    @giveaway.command(name="ping")
    async def g_ping(self, ctx, *, message: str = None):
        """Ping the pingrole for your server with an optional message, it wont send anything if there isn't a pingrole"""
        if not is_manager(ctx):
            return await ctx.send("You do not have proper permissions to do this. You need to be either admin or have the giveaway role. This cannot be run outside announcement channels either.")
        m = discord.AllowedMentions(roles=True, everyone=False)
        await ctx.message.delete()
        pingrole = await self.config.guild(ctx.guild).pingrole()
        if not pingrole:
            try:
                return await ctx.send(message, allowed_mentions=m)
            except discord.HTTPException:
                return
        role = ctx.guild.get_role(pingrole)
        if not role:
            await self.config.guild(ctx.guild).pingrole.clear()
            try:
                return await ctx.send(message, allowed_mentions=m)
            except discord.HTTPException:
                return
        try:
            message = message if message else ""
            await ctx.send(f"{role.mention} {message}", allowed_mentions=m)
        except discord.HTTPException:
            return

    @giveaway.command(name="cache")
    @commands.is_owner()
    async def cache(self, ctx, active: Optional[bool] = True, cacheglobal: str = None):
        """Owner Utility to force a cache on a server in case something broke or you reloaded the cog and need it needs to be cached"""
        e = discord.Embed(title="Cached Giveaways", description="Cached Servers\n")
        async with ctx.typing():
            counter = 0
            if cacheglobal == "--global":
                all_guilds = await self.config.all_guilds()
                for guild_id, data in all_guilds.items():
                    counter = 0
                    giveaways = data["giveaways"]
                    for messageid, info in giveaways.items():
                        if active:
                            if not info["Ongoing"]:
                                continue

                        if str(messageid) in self.message_cache:
                            continue
                        message = self.bot._connection._get_message(int(messageid))
                        channel = self.bot.get_channel(info["channel"])
                        if not channel:
                            continue
                        if not message:
                            try:
                                message = channel.get_partial_message(int(messageid))
                            except discord.NotFound:
                                continue
                        self.message_cache[messageid] = message
                        counter += 1

                    guild = self.bot.get_guild(int(guild_id))
                    if counter == 0:
                        continue
                    e.description += f"Cached {counter} messages in {guild.name}\n"
            else:
                for messageid, info in (
                    await self.config.guild(ctx.guild).giveaways()
                ).items():
                    if active:
                        if not info["Ongoing"]:
                            continue

                    if str(messageid) in self.message_cache:
                        continue
                    message = self.bot._connection._get_message(int(messageid))
                    channel = self.bot.get_channel(info["channel"])
                    if not channel:
                        continue
                    if not message:
                        try:
                            message = await channel.fetch_message(int(messageid))
                        except discord.NotFound:
                            continue
                        self.message_cache[messageid] = message
                        counter += 1

                e.description += f"Cached {counter} messages in {ctx.guild.name}"

        await ctx.send(embed=e)

    @giveaway.command(name="list")
    @commands.cooldown(1, 30, commands.BucketType.member)
    @commands.max_concurrency(2, commands.BucketType.user)
    async def g_list(self, ctx, can_join: bool = False):
        """List the giveways in the server. Specify True for can_join paramater to only list the ones you can join"""
        async with ctx.typing():
            giveaway_list = []
            bypassrole = await self.config.guild(ctx.guild).bypassrole()
            gaws = await self.config.guild(ctx.guild).giveaways()
            for messageid, info in gaws.items():
                messageid = str(messageid)
                if not info["Ongoing"]:
                    continue
                if not can_join:
                    jump_url = f"https://discord.com/channels/{ctx.guild.id}/{info['channel']}/{messageid}"

                    header = f"[{info['title']}]({jump_url})"
                    header += " | Winners: {0} | Host: <@{1}>".format(
                        info["winners"], info["host"]
                    )
                    header += " | Channel: <#{0}> | ID: {1}".format(
                        info["channel"], messageid
                    )
                    can_join_var = await self.can_join(ctx.author, info)
                    if can_join_var == True:
                        header += " :white_check_mark: You can join this giveaway\n"
                        giveaway_list.append(header)
                        continue
                    header += f" :octagonal_sign: {can_join_var[1].replace('[JUMP_URL_HERE]', 'this')}\n"

                    giveaway_list.append(header)
                else:
                    jump_url = f"https://discord.com/channels/{ctx.guild.id}/{info['channel']}/{messageid}`"
                    header = f"[{info['title']}]({jump_url})"
                    header += " | Winners: {0} | Host: <@{1}>".format(
                        info["winners"], info["host"]
                    )
                    header += " | Channel: <#{0}> | ID: {1}".format(
                        info["channel"], messageid
                    )
                    can_join_var = await self.can_join(ctx.author, info)
                    if can_join_var == True:
                        header += " :white_check_mark: You can join this giveaway\n"
                        giveaway_list.append(header)

        formatted_giveaways = "\n".join(giveaway_list)
        if len(formatted_giveaways) > 2048:
            pages = list(pagify(formatted_giveaways))
            embeds = []

            for i, page in enumerate(pages, start=1):
                e = discord.Embed(
                    title=f"Giveaways Page {i}/{len(pages)}",
                    description=page,
                    color=discord.Color.green(),
                )
                embeds.append(e)
            await menu(ctx, embeds, DEFAULT_CONTROLS)
        else:
            e = discord.Embed(
                title="Giveaway Page 1",
                description=formatted_giveaways,
                color=discord.Color.green(),
            )
            await ctx.send(embed=e)

    @giveaway.command(name="cancel")
    async def cancel(self, ctx, giveaway: Optional[IntOrLink] = None):
        """Cancel a giveaway"""
        if not is_manager(ctx):
            return await ctx.send("You do not have proper permissions to do this. You need to be either admin or have the giveaway role. This cannot be run outside announcement channels either.")
        if not giveaway:
            if hasattr(ctx.message, "reference") and ctx.message.reference != None:
                msg = ctx.message.reference.resolved
                if isinstance(msg, discord.Message):
                    giveaway = msg.id
        gaws = await self.config.guild(ctx.guild).giveaways()
        if not giveaway:
            for messageid, info in list(gaws.items())[::-1]:
                if info["Ongoing"] and info["channel"] == ctx.channel.id:
                    chan = self.bot.get_channel(info["channel"])
                    if not chan:
                        continue
                    try:
                        m = self.message_cache.get(
                            giveaway, await chan.fetch_message(int(messageid))
                        )
                    except discord.NotFound:
                        continue

                    e = discord.Embed(
                        title=info["title"],
                        description=f"Giveaway Cancelled\n",
                        color=discord.Color.red(),
                        timestamp=datetime.utcnow(),
                    )
                    e.description += "Hosted By: <@{0}>\nCancelled By: {1}".format(
                        info["host"], ctx.author.mention
                    )
                    e.set_footer(text="Cancelled at")

                    try:
                        await m.edit(content="Giveaway Cancelled", embed=e)
                    except discord.NotFound:
                        continue
                    info["Ongoing"] = False
                    gaws[messageid] = info
                    await self.config.guild(ctx.guild).giveaways.set(gaws)
                    self.giveaway_cache[messageid] = False
                    return await ctx.send(
                        "Cancelled the giveaway for **{0}**".format(info["title"])
                    )

            return await ctx.send(
                "There are no active giveaways in this channel to be cancelled, specify a message id/link after this in another channel to cancel one"
            )
        giveaway = str(giveaway)
        if giveaway not in gaws.keys():
            return await ctx.send("This giveaway does not exist")
        if not gaws[giveaway]["Ongoing"]:
            return await ctx.send("This giveaway has ended")

        data = gaws[giveaway]
        chan = self.bot.get_channel(data["channel"])
        if not chan:
            return await ctx.send("This message is no longer available")
        try:
            m = self.message_cache.get(
                giveaway, await chan.fetch_message(int(giveaway))
            )
        except discord.NotFound:
            return await ctx.send("Couldn't find this giveaway")

        gaws[giveaway]["Ongoing"] = False
        self.giveaway_cache[giveaway] = False
        await self.config.guild(ctx.guild).giveaways.set(gaws)

        e = discord.Embed(
            title=data["title"],
            description=f"Giveaway Cancelled\n",
            color=discord.Color.red(),
            timestamp=datetime.utcnow(),
        )

        e.description += "Hosted By: <@{0}>\nCancelled By: {1}".format(
            data["host"], ctx.author.mention
        )
        e.set_footer(text="Cancelled at")
        try:
            await m.edit(content="Giveaway Cancelled", embed=e)
        except discord.NotFound:
            return await ctx.send("I couldn't find this giveaway")
        gaws[giveaway]["Ongoing"] = False
        await self.config.guild(ctx.guild).giveaways.set(gaws)
        await ctx.send("Cancelled the giveaway for **{0}**".format(data["title"]))


    # -------------------------------------gprofile---------------------------------

    @commands.group(
        name="giveawayprofile", aliases=["gprofile"], invoke_without_command=True
    )
    @commands.guild_only()
    async def giveawayprofile(self, ctx, member: Optional[discord.Member] = None):
        """View your giveaway donations and notes"""
        if not ctx.invoked_subcommand:
            if not member:
                pass
            else:
                ctx.author = member
            donated = await self.config.member(ctx.author).donated()
            format_donated = "{:,}".format(donated)
            notes = await self.config.member(ctx.author).notes()
            hosted = await self.config.member(ctx.author).hosted()
            try:
                average_donated = "{:,}".format(round(donated / hosted))
            except ZeroDivisionError:
                average_donated = 0

            e = discord.Embed(
                title="Donated",
                description=f"Giveaways Hosted: {hosted} \n",
                color=ctx.author.color,
            )
            e.description += f"Amount Donated: {format_donated} \n"
            e.description += f"Average Donation Value: {average_donated}"

            if len(notes) == 0:
                pass
            else:
                e.set_footer(text=f"{len(notes)} notes")

            await ctx.send(embed=e)

    @giveawayprofile.command(name="top", aliases=["leaderboard", "lb"])
    async def top(self, ctx, amt: int = 10):
        """View the top donators"""
        if amt < 1:
            return await ctx.send("no")
        member_data = await self.config.all_members(ctx.guild)
        sorted_data = [
            (member, data["donated"])
            for member, data in member_data.items()
            if data["donated"] > 0 and ctx.guild.get_member(int(member)) is not None
        ]
        ordered_data = sorted(sorted_data[:amt], key=lambda m: m[1], reverse=True)

        if len(ordered_data) == 0:
            return await ctx.send("I have no data for your server")

        formatted_string = ""

        for i, data in enumerate(ordered_data, start=1):
            formatted_string += f"{i}. <@{data[0]}>: {self.comma_format(data[1])}\n"

        if len(formatted_string) >= 2048:
            embeds = []
            pages = list(pagify(formatted_string))
            for page in pages:
                e = discord.Embed(
                    title="Donation Leaderboard",
                    description=page,
                    color=ctx.author.color,
                )
                embeds.append(e)

            await menu(ctx, embeds, DEFAULT_CONTROLS)
        else:
            await ctx.send(
                embed=discord.Embed(
                    title="Donation Leaderboard",
                    description=formatted_string,
                    color=ctx.author.color,
                )
            )

    @giveawayprofile.command(name="notes")
    async def gprofile_notes(self, ctx, member: Optional[discord.Member] = None):
        """View your giveaway notes"""
        if member:
            ctx.author = member
        notes = await self.config.member(ctx.author).notes()
        if len(notes) == 0:
            return await ctx.send("You have no notes")

        formatted_notes = []

        for i, note in enumerate(notes, start=1):
            formatted_notes.append(f"{i}. {note}")

        formatted_notes = "\n\n".join(formatted_notes)

        if len(formatted_notes) >= 2048:
            embeds = []
            pages = list(pagify(formatted_notes))
            for i, page in enumerate(pages, start=1):
                e = discord.Embed(
                    title="Notes", description=page, color=ctx.author.color
                )
                e.set_footer(text=f"{i} out of {len(pages)} pages.")
                embeds.append(e)
            await menu(ctx, embeds, DEFAULT_CONTROLS)
        else:
            e = discord.Embed(
                title="Notes", description=formatted_notes, color=ctx.author.color
            )
            await ctx.send(embed=e)

    # -------------------------------------gstore---------------------------------

    @commands.group(name="giveawaystore", aliases=["gstore"])
    async def giveawaystore(self, ctx):
        """Manage donation and note tracking for members"""
        pass

    @giveawaystore.command(name="clear")
    async def gstore_clear(self, ctx, member: Optional[discord.Member] = None):
        """Clear everything for a member"""
        if not is_manager(ctx):
            return await ctx.send("You do not have proper permissions to do this. You need to be either admin or have the giveaway role. This cannot be run outside announcement channels either.")
        if not member:
            return await ctx.send("A member needs to be specified after this")
        else:

            def check(m):
                return m.author == ctx.author and m.channel == ctx.channel

            await ctx.send(
                f"Are you sure? This will clear all of **{member.name}'s** data. Type `YES I WANT TO DO THIS` exact below if you are sure"
            )
            try:
                msg = await self.bot.wait_for("message", check=check, timeout=20)
            except asyncio.TimeoutError:
                return await ctx.send(
                    f"Looks like we won't be removing **{member.name}'s** data."
                )

            if msg.content != "YES I WANT TO DO THIS":
                return await ctx.send(
                    f"Looks like we won't clear **{member.name}**'s data."
                )
            await self.config.member(member).clear()
            await self.add_amount(member, 0)
            await ctx.send(f"I've removed **{member.name}**'s data")

    @giveawaystore.group(name="donate")
    async def donate(self, ctx):
        """A group for managing giveway donations/amounts"""
        pass

    @donate.command(name="add")
    async def donate_add(
        self, ctx, member: Optional[discord.Member] = None, amt: str = None
    ):
        """Add an amount to a users donations"""
        if not is_manager(ctx):
            return await ctx.send("You do not have proper permissions to do this. You need to be either admin or have the giveaway role. This cannot be run outside announcement channels either.")
        if not member:
            return await ctx.send("A member needs to be specified after this")
        if not amt:
            return await ctx.send("You need to specify an amount to donate")
        amt = amt.replace(",", "")

        if not str(amt).isdigit():
            return await ctx.send("This isn't a number.")

        await self.add_amount(member, int(amt))
        await ctx.send("Done.")

    @donate.command(name="remove")
    async def donate_remove(
        self, ctx, member: Optional[discord.Member] = None, amt: str = None
    ):
        """Remove a certain amount from a members donation amount"""
        if not is_manager(ctx):
            return await ctx.send("You do not have proper permissions to do this. You need to be either admin or have the giveaway role. This cannot be run outside announcement channels either.")
        if not member:
            return await ctx.send("A member needs to be specified after this")
        if not amt:
            return await ctx.send("You need to specify an amount to donate")
        amt = amt.replace(",", "")

        if not str(amt).isdigit():
            return await ctx.send("This isn't a number.")

        previous_amount = await self.config.member(member).donated()
        new_amount = previous_amount - int(amt)
        if new_amount < 0:
            return await ctx.send("You can't go below 0")
        await self.config.member(member).donated.set(new_amount)
        await self.add_amount(member, 0)
        await ctx.send("Done.")

    @giveawaystore.group(name="note")
    async def note(self, ctx):
        """A group for managing giveaway notes"""
        pass

    @note.command(name="add")
    async def note_add(
        self, ctx, member: Optional[discord.Member] = None, *, note: str = None
    ):
        """Add a note to a member"""
        if not is_manager(ctx):
            return await ctx.send("You do not have proper permissions to do this. You need to be either admin or have the giveaway role. This cannot be run outside announcement channels either.")
        if not member:
            return await ctx.send("A member needs to be specified.")
        if not note:
            return await ctx.send("A note needs to be specified.")

        notes = await self.config.member(member).notes()
        notes.append(note)
        await self.config.member(member).notes.set(notes)
        await ctx.send("Added a note.")

    @note.command(name="remove")
    async def note_remove(
        self, ctx, member: Optional[discord.Member] = None, note: Optional[int] = None
    ):
        """Remove a note from a member, you can do `[p]gprofile notes @member` to find the note ID, and specify that for the note param"""
        if not is_manager(ctx):
            return await ctx.send("You do not have proper permissions to do this. You need to be either admin or have the giveaway role. This cannot be run outside announcement channels either.")
        if not member:
            return await ctx.send("A member needs to be specified.")
        if not note:
            return await ctx.send("A note ID needs to be specified.")

        notes = await self.config.member(member).notes()
        if note > len(notes):
            return await ctx.send("This note does not exist")
        else:
            notes.pop(note - 1)
        await self.config.member(member).notes.set(notes)
        await ctx.send("Removed a note.")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        channel = self.bot.get_channel(payload.channel_id)
        user = channel.guild.get_member(payload.user_id)
        if user.bot:
            return
        data = await self.config.guild(channel.guild).all()
        gaws = data["giveaways"]
        if str(payload.message_id) not in gaws:
            return
        elif gaws[str(payload.message_id)]["Ongoing"] == False:
            return

        if not channel.permissions_for(channel.guild.me).manage_messages:
            return

        if str(payload.emoji) != data["emoji"]:
            return

        bypassrole = data["bypassrole"]
        if bypassrole in [r.id for r in user.roles]:
            return

        can_join = await self.can_join(user, gaws[str(payload.message_id)])
        if can_join == True:
            return
        else:
            message = self.message_cache.get(
                str(payload.message_id),
                self.bot._connection._get_message(payload.message_id),
            )
            if not message:
                message = channel.get_partial_message(payload.message_id)
            await message.remove_reaction(str(payload.emoji), user)
            e = discord.Embed(
                title="Missing Giveaway Requirement",
                description=can_join[1].replace(
                    "[JUMP_URL_HERE]", f"[this]({message.jump_url})"
                ),
            )
            await user.send(embed=e)
        
