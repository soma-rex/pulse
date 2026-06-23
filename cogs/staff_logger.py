from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import html
import re
import sqlite3

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks


EVENT_EMOJI = "<:eventcard:1488562862262718708>"
GIVEAWAY_EMOJI = "<:giveaway:1488562864926101637>"
MOD_EMOJI = "<:mod:1488568750671269989>"

REPORT_CHANNEL_ID = 1181356533557239818
MOD_ACTIVITY_CHANNEL_ID = 1013340674805993512
MOD_MESSAGE_COOLDOWN_SECONDS = 8

GIVEAWAY_MANAGER_ROLE_ID = 996372025486622760
EVENT_MANAGER_ROLE_ID = 996372072961937450
MODERATOR_ROLE_ID = 996371883807219803
TRIAL_MODERATOR_ROLE_ID = 996371928493330534
TOUCHING_GRASS_ROLE_ID = 1128285153135955978
BASIC_STAFF_ROLE_ID = 996372307528384583
SOTM_ROLE_ID = 1134767539356962917
HIDE_FROM_STAFF_PROGRESS_ROLE_ID = 996371760494694430

GMAN_TRIGGER_ROLE_IDS = {
    1107542167746007080,
    996367555323248690,
    1152283755336183828,
}
EMAN_TRIGGER_ROLE_IDS = {996367564508758026}

PING_REQUIREMENT = 7
MOD_MESSAGE_REQUIREMENT = 200
TRIAL_MOD_MESSAGE_REQUIREMENT = 250

ROLE_PRIORITY = (
    ("gman", GIVEAWAY_MANAGER_ROLE_ID),
    ("eman", EVENT_MANAGER_ROLE_ID),
    ("mod", MODERATOR_ROLE_ID),
    ("tmod", TRIAL_MODERATOR_ROLE_ID),
)
STAFF_ROLE_IDS = {role_id for _, role_id in ROLE_PRIORITY}
STAFF_ROLE_IDS.add(BASIC_STAFF_ROLE_ID)
STAFF_ROLE_IDS.add(TOUCHING_GRASS_ROLE_ID)
PREFIXES = (";", "&")


class StaffProgressView(discord.ui.View):
    def __init__(self, cog: "StaffLoggerCog", guild: discord.Guild, requester_id: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild
        self.requester_id = requester_id

    async def _swap_embed(self, interaction: discord.Interaction, role_type: str):
        embed = await self.cog._build_staff_overview_embed_filtered(self.guild, role_type)
        await interaction.response.edit_message(embed=embed, view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Only the command user can use these buttons.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Mods", style=discord.ButtonStyle.primary)
    async def mods_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._swap_embed(interaction, "mod")

    @discord.ui.button(label="Gman", style=discord.ButtonStyle.secondary)
    async def gman_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._swap_embed(interaction, "gman")

    @discord.ui.button(label="Eman", style=discord.ButtonStyle.secondary)
    async def eman_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._swap_embed(interaction, "eman")


class ProfileEditModal(discord.ui.Modal):
    def __init__(
        self,
        view: "ProfileEditView",
        *,
        field_name: str,
        modal_title: str,
        label: str,
        placeholder: str,
        default: str | None = None,
        max_length: int = 400,
        required: bool = False,
    ):
        super().__init__(title=modal_title)
        self.view = view
        self.field_name = field_name
        self.value_input = discord.ui.TextInput(
            label=label,
            placeholder=placeholder,
            default=default,
            required=required,
            max_length=max_length,
            style=discord.TextStyle.paragraph if max_length > 120 else discord.TextStyle.short,
        )
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        message = await self.view.apply_change(interaction, self.field_name, self.value_input.value)
        if message:
            await interaction.response.send_message(message, ephemeral=True)


class ProfileEditView(discord.ui.View):
    def __init__(self, cog: "StaffLoggerCog", member: discord.Member):
        super().__init__(timeout=600)
        self.cog = cog
        self.member = member
        self.requester_id = member.id
        self.message: discord.Message | None = None
        self.status_text = "Use the buttons below to customize your profile."

    def _content(self) -> str:
        return f"Profile editor for {self.member.mention}\n{self.status_text}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Only the profile owner can use these buttons.", ephemeral=True)
            return False
        return True

    async def _refresh_message(self):
        if not self.message:
            return
        embed = self.cog._build_profile_embed(self.member, viewer=self.member)
        if embed is None:
            return
        await self.message.edit(content=self._content(), embed=embed, view=self)

    async def apply_change(self, interaction: discord.Interaction, field_name: str, raw_value: str) -> str | None:
        if field_name == "profile_image_url":
            error, update_text = await self.cog._apply_profile_image_edit(interaction.user.id, raw_value)
        else:
            error, update_text = self.cog._apply_profile_edit_value(interaction.user.id, field_name, raw_value)
        if error:
            return error

        self.status_text = update_text
        self.member = interaction.user
        await interaction.response.defer()
        await self._refresh_message()
        return None

    @discord.ui.button(label="Edit Title", style=discord.ButtonStyle.primary)
    async def edit_title(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = self.cog._registered_row(interaction.user.id)
        current = row[13] if row and len(row) > 13 else None
        await interaction.response.send_modal(
            ProfileEditModal(
                self,
                field_name="profile_title",
                modal_title="Edit Profile Title",
                label="Title",
                placeholder="Leave blank to clear. Example: the king",
                default=current,
                max_length=80,
            )
        )

    @discord.ui.button(label="Edit Color", style=discord.ButtonStyle.secondary)
    async def edit_color(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = self.cog._registered_row(interaction.user.id)
        current = row[11] if row and len(row) > 11 else None
        await interaction.response.send_modal(
            ProfileEditModal(
                self,
                field_name="profile_color",
                modal_title="Edit Profile Color",
                label="Hex Color",
                placeholder="Leave blank to reset. Example: #ff6600",
                default=current,
                max_length=7,
            )
        )

    @discord.ui.button(label="Edit About Me", style=discord.ButtonStyle.secondary)
    async def edit_about(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = self.cog._registered_row(interaction.user.id)
        current = row[14] if row and len(row) > 14 else None
        await interaction.response.send_modal(
            ProfileEditModal(
                self,
                field_name="profile_bio",
                modal_title="Edit About Me",
                label="About Me",
                placeholder="Leave blank to clear your bio.",
                default=current,
                max_length=500,
            )
        )

    @discord.ui.button(label="Edit Banner/GIF", style=discord.ButtonStyle.secondary)
    async def edit_banner(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = self.cog._registered_row(interaction.user.id)
        current = row[12] if row and len(row) > 12 else None
        await interaction.response.send_modal(
            ProfileEditModal(
                self,
                field_name="profile_image_url",
                modal_title="Edit Banner or GIF",
                label="Image URL",
                placeholder="Direct image/GIF link. Leave blank to clear.",
                default=current,
                max_length=400,
            )
        )

    @discord.ui.button(label="Clear Banner", style=discord.ButtonStyle.danger)
    async def clear_banner(self, interaction: discord.Interaction, button: discord.ui.Button):
        error, update_text = self.cog._apply_profile_edit_value(interaction.user.id, "profile_image_url", "")
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        self.status_text = update_text
        self.member = interaction.user
        await interaction.response.defer()
        await self._refresh_message()


class StaffLoggerCog(commands.Cog, name="Staff Logger"):
    staff_group = app_commands.Group(name="staff", description="Staff management commands")
    profile_group = app_commands.Group(name="staffprofile", description="Staff profile commands")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.conn: sqlite3.Connection = bot.conn
        self.cursor: sqlite3.Cursor = bot.cursor
        self.mod_message_cooldowns: dict[int, datetime] = {}
        self.http_session: aiohttp.ClientSession | None = None
        self._ensure_tables()
        self._ensure_active_week()
        self.weekly_reset_loop.start()

    def cog_unload(self):
        self.weekly_reset_loop.cancel()
        if self.http_session and not self.http_session.closed:
            asyncio.create_task(self.http_session.close())

    def _ensure_tables(self):
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS staff_users (
                user_id INTEGER PRIMARY KEY,
                role_type TEXT NOT NULL,
                is_on_break INTEGER NOT NULL DEFAULT 0,
                saved_roles TEXT,
                break_until TEXT,
                registered_at TEXT,
                birthday TEXT,
                hired_date TEXT,
                sotm_count INTEGER NOT NULL DEFAULT 0,
                total_gman_count INTEGER NOT NULL DEFAULT 0,
                total_eman_count INTEGER NOT NULL DEFAULT 0,
                total_mod_message_count INTEGER NOT NULL DEFAULT 0,
                profile_color TEXT,
                profile_image_url TEXT,
                profile_title TEXT,
                profile_bio TEXT
            )
            """
        )
        self.cursor.execute("PRAGMA table_info(staff_users)")
        columns = {row[1] for row in self.cursor.fetchall()}
        required_columns = {
            "break_until": "TEXT",
            "registered_at": "TEXT",
            "birthday": "TEXT",
            "hired_date": "TEXT",
            "sotm_count": "INTEGER NOT NULL DEFAULT 0",
            "total_gman_count": "INTEGER NOT NULL DEFAULT 0",
            "total_eman_count": "INTEGER NOT NULL DEFAULT 0",
            "total_mod_message_count": "INTEGER NOT NULL DEFAULT 0",
            "profile_color": "TEXT",
            "profile_image_url": "TEXT",
            "profile_title": "TEXT",
            "profile_bio": "TEXT",
        }
        for column_name, column_type in required_columns.items():
            if column_name not in columns:
                self.cursor.execute(f"ALTER TABLE staff_users ADD COLUMN {column_name} {column_type}")
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS weekly_logs (
                user_id INTEGER NOT NULL,
                week_id TEXT NOT NULL,
                gman_count INTEGER NOT NULL DEFAULT 0,
                eman_count INTEGER NOT NULL DEFAULT 0,
                mod_message_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, week_id)
            )
            """
        )
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS staff_config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        self.conn.commit()

    def _config_get(self, key: str, default: str | None = None) -> str | None:
        self.cursor.execute("SELECT value FROM staff_config WHERE key = ?", (key,))
        row = self.cursor.fetchone()
        return row[0] if row else default

    def _config_set(self, key: str, value: str):
        self.cursor.execute(
            "INSERT OR REPLACE INTO staff_config (key, value) VALUES (?, ?)",
            (key, value),
        )
        self.conn.commit()

    def _current_week_id(self) -> str:
        now = datetime.now(timezone.utc)
        monday = now - timedelta(days=now.weekday())
        monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
        return monday.strftime("%Y-%m-%d")

    def _week_label(self, week_id: str) -> str:
        start = datetime.strptime(week_id, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end = start + timedelta(days=6)
        return f"{start.strftime('%d %b')} - {end.strftime('%d %b %Y')} UTC"

    def _ensure_active_week(self):
        active_week = self._config_get("active_week_id")
        if not active_week:
            self._config_set("active_week_id", self._current_week_id())

    def _registered_row(self, user_id: int):
        self.cursor.execute(
            """
            SELECT
                role_type,
                is_on_break,
                saved_roles,
                break_until,
                registered_at,
                birthday,
                hired_date,
                sotm_count,
                total_gman_count,
                total_eman_count,
                total_mod_message_count,
                profile_color,
                profile_image_url,
                profile_title,
                profile_bio
            FROM staff_users
            WHERE user_id = ?
            """,
            (user_id,),
        )
        return self.cursor.fetchone()

    def _resolve_role_types(self, member: discord.Member) -> list[str]:
        role_ids = {role.id for role in member.roles}
        return [role_type for role_type, role_id in ROLE_PRIORITY if role_id in role_ids]

    def _serialize_role_types(self, role_types: list[str]) -> str:
        return ",".join(role_types)

    def _parse_role_types(self, value: str | None) -> list[str]:
        if not value:
            return []
        return [part for part in value.split(",") if part]

    def _parse_break_until(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _parse_date_string(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return None

    def _format_date_string(self, value: str | None, *, unknown: str = "Not known yet") -> str:
        parsed = self._parse_date_string(value)
        if parsed is None:
            return unknown
        return parsed.strftime("%d %b %Y")

    def _mention_line(self, user_id: int, name: str) -> str:
        return f"{name} (<@{user_id}>)"

    def _requirement_for(self, role_type: str) -> int:
        if role_type in {"gman", "eman"}:
            return PING_REQUIREMENT
        if role_type == "tmod":
            return TRIAL_MOD_MESSAGE_REQUIREMENT
        return MOD_MESSAGE_REQUIREMENT

    def _count_for(self, log_row: sqlite3.Row | tuple | None, role_type: str) -> int:
        if not log_row:
            return 0
        if role_type == "gman":
            return log_row[0]
        if role_type == "eman":
            return log_row[1]
        return log_row[2]

    def _progress_bar(self, current: int, target: int, *, is_on_break: bool = False, length: int = 5) -> str:
        if is_on_break:
            return "🟦" * length
        filled = min(length, int((current / target) * length)) if target > 0 else length
        return ("🟩" * filled) + ("🟥" * (length - filled))

    def _section_title(self, role_type: str) -> str:
        mapping = {
            "gman": f"{GIVEAWAY_EMOJI} Giveaway Managers",
            "eman": f"{EVENT_EMOJI} Event Managers",
            "mod": f"{MOD_EMOJI} Moderators",
            "tmod": f"{MOD_EMOJI} Trial Moderators",
        }
        return mapping[role_type]

    def _upsert_staff_user(
        self,
        user_id: int,
        role_types: list[str],
        *,
        is_on_break: int = 0,
        saved_roles: str | None = None,
        break_until: str | None = None,
    ):
        existing = self._registered_row(user_id)
        saved_value = saved_roles if saved_roles is not None else (existing[2] if existing else None)
        break_value = is_on_break if existing is None else (existing[1] if saved_roles is None and is_on_break == 0 else is_on_break)
        break_until_value = break_until if break_until is not None else (existing[3] if existing else None)
        self.cursor.execute(
            """
            INSERT INTO staff_users (user_id, role_type, is_on_break, saved_roles, break_until)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                role_type = excluded.role_type,
                is_on_break = excluded.is_on_break,
                saved_roles = excluded.saved_roles,
                break_until = excluded.break_until
            """,
            (user_id, self._serialize_role_types(role_types), break_value, saved_value, break_until_value),
        )
        self.conn.commit()

    def _ensure_weekly_row(self, user_id: int) -> str:
        week_id = self._config_get("active_week_id", self._current_week_id())
        self.cursor.execute(
            """
            INSERT OR IGNORE INTO weekly_logs (user_id, week_id, gman_count, eman_count, mod_message_count)
            VALUES (?, ?, 0, 0, 0)
            """,
            (user_id, week_id),
        )
        self.conn.commit()
        return week_id

    def _increment_log(self, user_id: int, field_name: str):
        week_id = self._ensure_weekly_row(user_id)
        self.cursor.execute(
            f"UPDATE weekly_logs SET {field_name} = {field_name} + 1 WHERE user_id = ? AND week_id = ?",
            (user_id, week_id),
        )
        self.conn.commit()
        total_field_map = {
            "gman_count": "total_gman_count",
            "eman_count": "total_eman_count",
            "mod_message_count": "total_mod_message_count",
        }
        total_field = total_field_map.get(field_name)
        if total_field:
            self._increment_total_stat(user_id, total_field)

    def _get_weekly_log(self, user_id: int, week_id: str | None = None):
        target_week = week_id or self._config_get("active_week_id", self._current_week_id())
        self.cursor.execute(
            """
            SELECT gman_count, eman_count, mod_message_count
            FROM weekly_logs
            WHERE user_id = ? AND week_id = ?
            """,
            (user_id, target_week),
        )
        return self.cursor.fetchone()

    def _sync_member_registration(self, member: discord.Member):
        row = self._registered_row(member.id)
        if not row:
            return None
        if row[1]:
            return row

        current_roles = self._resolve_role_types(member)
        if not current_roles:
            return row
        self.cursor.execute(
            "UPDATE staff_users SET role_type = ? WHERE user_id = ?",
            (self._serialize_role_types(current_roles), member.id),
        )
        self.conn.commit()
        return self._registered_row(member.id)

    def _delete_staff_user(self, user_id: int):
        self.cursor.execute("DELETE FROM weekly_logs WHERE user_id = ?", (user_id,))
        self.cursor.execute("DELETE FROM staff_users WHERE user_id = ?", (user_id,))
        self.conn.commit()

    async def _refresh_staff_registry(self, guild: discord.Guild) -> tuple[int, int]:
        self.cursor.execute("SELECT user_id, role_type, is_on_break FROM staff_users")
        rows = self.cursor.fetchall()

        updated_count = 0
        removed_count = 0

        for user_id, stored_role_types, is_on_break in rows:
            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id)
                except discord.HTTPException:
                    member = None

            if member is None:
                if not is_on_break:
                    self._delete_staff_user(user_id)
                    removed_count += 1
                continue

            role_ids = {role.id for role in member.roles}
            if is_on_break or TOUCHING_GRASS_ROLE_ID in role_ids:
                continue

            current_roles = self._resolve_role_types(member)
            if current_roles:
                serialized_roles = self._serialize_role_types(current_roles)
                if serialized_roles != (stored_role_types or ""):
                    self.cursor.execute(
                        "UPDATE staff_users SET role_type = ? WHERE user_id = ?",
                        (serialized_roles, user_id),
                    )
                    updated_count += 1
                continue

            self._delete_staff_user(user_id)
            removed_count += 1

        self.conn.commit()
        return updated_count, removed_count

    def _display_name(self, guild: discord.Guild | None, user_id: int, fallback: str | None = None) -> str:
        if guild:
            member = guild.get_member(user_id)
            if member:
                return member.display_name
        return fallback or f"User {user_id}"

    async def _display_name_fixed(self, guild: discord.Guild | None, user_id: int) -> str:
        return f"<@{user_id}>"

    async def _should_hide_from_staff_progress(self, guild: discord.Guild | None, user_id: int) -> bool:
        if guild is None:
            return False

        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                return False

        return any(role.id == HIDE_FROM_STAFF_PROGRESS_ROLE_ID for role in member.roles)

    def _is_registered(self, user_id: int) -> bool:
        return self._registered_row(user_id) is not None

    def _can_use_staff_commands(self, member: discord.Member) -> bool:
        return self._is_registered(member.id) or member.guild_permissions.administrator

    def _not_registered_message(self) -> str:
        return "You can't use this command because you're not registered. Use `/register` first."

    def _increment_total_stat(self, user_id: int, field_name: str):
        self.cursor.execute(f"UPDATE staff_users SET {field_name} = {field_name} + 1 WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def _set_total_stat(self, user_id: int, field_name: str, value: int):
        self.cursor.execute(f"UPDATE staff_users SET {field_name} = ? WHERE user_id = ?", (value, user_id))
        self.conn.commit()

    def _set_profile_field(self, user_id: int, field_name: str, value: str | None):
        self.cursor.execute(f"UPDATE staff_users SET {field_name} = ? WHERE user_id = ?", (value, user_id))
        self.conn.commit()

    def _parse_hex_color(self, value: str | None) -> discord.Color | None:
        if not value:
            return None
        normalized = value.strip().lstrip("#")
        if len(normalized) != 6:
            return None
        try:
            return discord.Color(int(normalized, 16))
        except ValueError:
            return None

    @staticmethod
    def _is_valid_image_url(value: str) -> bool:
        lowered = value.lower().strip()
        return lowered.startswith(("http://", "https://"))

    @staticmethod
    def _looks_like_direct_image_url(value: str) -> bool:
        lowered = value.lower().split("?", 1)[0]
        direct_suffixes = (".png", ".jpg", ".jpeg", ".webp", ".gif")
        trusted_hosts = (
            "cdn.discordapp.com",
            "media.discordapp.net",
            "images-ext-1.discordapp.net",
            "images-ext-2.discordapp.net",
            "media.tenor.com",
            "c.tenor.com",
            "i.giphy.com",
            "media.giphy.com",
            "imagedelivery.net",
            "imgur.com",
            "i.imgur.com",
        )
        return lowered.endswith(direct_suffixes) or any(host in lowered for host in trusted_hosts)

    @staticmethod
    def _is_supported_gif_page_url(value: str) -> bool:
        lowered = value.lower().strip()
        supported_pages = (
            "tenor.com/view/",
            "giphy.com/gifs/",
        )
        return any(part in lowered for part in supported_pages)

    def _normalize_profile_image_url(self, value: str | None) -> tuple[str | None, str | None]:
        cleaned = (value or "").strip().strip("<>").strip()
        if not cleaned:
            return None, None
        if not self._is_valid_image_url(cleaned):
            return None, "That image URL is invalid. Please use a full `http://` or `https://` link."
        lowered = cleaned.lower()
        if self._is_supported_gif_page_url(cleaned):
            return cleaned, (
                "Saved that GIF page link. If Discord can't render it as the banner image, "
                "your profile will still show a clickable GIF link."
            )
        common_page_links = ("imgur.com/gallery/", "imgur.com/a/")
        if any(part in lowered for part in common_page_links):
            return None, (
                "That looks like a gallery page, not a direct image or GIF. "
                "Use a direct media URL ending in `.png`, `.jpg`, `.webp`, or `.gif`."
            )
        if not self._looks_like_direct_image_url(cleaned):
            return cleaned, (
                "Saved that banner URL, but if it does not render in Discord, use a direct image or GIF link "
                "ending in `.png`, `.jpg`, `.webp`, or `.gif`."
            )
        return cleaned, None

    async def _get_http_session(self) -> aiohttp.ClientSession:
        if self.http_session is None or self.http_session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            headers = {"User-Agent": "PulseDiscordBot/1.0"}
            self.http_session = aiohttp.ClientSession(timeout=timeout, headers=headers)
        return self.http_session

    @staticmethod
    def _extract_meta_image_url(page_text: str) -> str | None:
        meta_patterns = (
            r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::secure_url)?["\']',
            r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image(?::src)?["\']',
        )
        for pattern in meta_patterns:
            match = re.search(pattern, page_text, flags=re.IGNORECASE)
            if match:
                return html.unescape(match.group(1).strip())
        return None

    async def _resolve_gif_page_url(self, page_url: str) -> str | None:
        session = await self._get_http_session()
        try:
            async with session.get(page_url, allow_redirects=True) as response:
                if response.status >= 400:
                    return None
                page_text = await response.text()
        except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeDecodeError):
            return None

        media_match = re.search(
            r'https://(?:media\.tenor\.com|c\.tenor\.com|media\.giphy\.com|i\.giphy\.com)/[^"\'>\s]+\.(?:gif|png|jpe?g|webp)',
            page_text,
            flags=re.IGNORECASE,
        )
        if media_match:
            return html.unescape(media_match.group(0))

        meta_image = self._extract_meta_image_url(page_text)
        if meta_image and self._looks_like_direct_image_url(meta_image):
            return meta_image
        return None

    async def _apply_profile_image_edit(self, user_id: int, raw_value: str) -> tuple[str | None, str]:
        cleaned_value = (raw_value or "").strip()
        normalized_url, notice = self._normalize_profile_image_url(cleaned_value)
        if cleaned_value and normalized_url is None and notice:
            return notice, ""

        resolved_notice = notice
        if normalized_url and self._is_supported_gif_page_url(normalized_url):
            resolved_url = await self._resolve_gif_page_url(normalized_url)
            if resolved_url:
                normalized_url = resolved_url
                resolved_notice = "Resolved that GIF page to a direct media URL for your profile banner."

        self._set_profile_field(user_id, "profile_image_url", normalized_url)
        if normalized_url:
            return None, resolved_notice or "Updated your profile banner/GIF."
        return None, "Cleared your profile banner/GIF."

    @staticmethod
    def _profile_title_text(member: discord.Member, custom_title: str | None) -> str:
        custom_title = (custom_title or "").strip()
        if custom_title:
            return f"{member.name}. {custom_title}"
        return member.name

    def _apply_profile_edit_value(self, user_id: int, field_name: str, raw_value: str) -> tuple[str | None, str]:
        cleaned_value = (raw_value or "").strip()

        if field_name == "profile_color":
            if not cleaned_value:
                self._set_profile_field(user_id, field_name, None)
                return None, "Reset your profile color to the default."
            parsed_color = self._parse_hex_color(cleaned_value)
            if parsed_color is None:
                return "That color is invalid. Use a 6-digit hex value like `#ff6600`.", ""
            normalized_color = f"#{cleaned_value.lstrip('#').lower()}"
            self._set_profile_field(user_id, field_name, normalized_color)
            return None, f"Updated your profile color to `{normalized_color}`."

        if field_name == "profile_title":
            if len(cleaned_value) > 80:
                return "That title is too long. Keep it under 80 characters.", ""
            self._set_profile_field(user_id, field_name, cleaned_value or None)
            return None, "Updated your profile title." if cleaned_value else "Cleared your profile title."

        if field_name == "profile_bio":
            if len(cleaned_value) > 500:
                return "That bio is too long. Keep it under 500 characters.", ""
            self._set_profile_field(user_id, field_name, cleaned_value or None)
            return None, "Updated your About Me section." if cleaned_value else "Cleared your About Me section."

        if field_name == "profile_image_url":
            normalized_url, notice = self._normalize_profile_image_url(cleaned_value)
            if cleaned_value and normalized_url is None and notice:
                return notice, ""
            self._set_profile_field(user_id, field_name, normalized_url)
            if normalized_url:
                return None, notice or "Updated your profile banner/GIF."
            return None, "Cleared your profile banner/GIF."

        return "That profile field can't be edited here.", ""

    def _build_profile_embed(
        self,
        member: discord.Member,
        viewer: discord.abc.User | discord.Member | None = None,
    ) -> discord.Embed | None:
        row = self._sync_member_registration(member)
        if not row:
            return None

        role_types = self._parse_role_types(row[0])
        is_on_break = bool(row[1])
        registered_at = row[4]
        birthday = row[5]
        hired_date = row[6]
        sotm_count = row[7] or 0
        total_gman = row[8] or 0
        total_eman = row[9] or 0
        total_mod = row[10] or 0
        profile_color = row[11]
        profile_image_url = row[12]
        profile_title = row[13]
        profile_bio = row[14]

        embed = discord.Embed(
            title=self._profile_title_text(member, profile_title),
            color=self._parse_hex_color(profile_color) or discord.Color.blurple(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Tracked Roles", value=", ".join(role.upper() for role in role_types) or "None", inline=False)
        embed.add_field(name="Date Joined", value=self._format_date_string(registered_at), inline=True)
        embed.add_field(name="Hired Date", value=self._format_date_string(hired_date), inline=True)
        embed.add_field(name="Birthday", value=self._format_date_string(birthday), inline=True)
        embed.add_field(name="On Break", value="Yes" if is_on_break else "No", inline=True)
        embed.add_field(name="SOTM Awards", value=str(sotm_count), inline=True)
        embed.add_field(name="About Me", value=profile_bio or "Nothing added yet.", inline=False)
        embed.add_field(name="Lifetime Stats", value=(
            f"{GIVEAWAY_EMOJI} Giveaway pings: **{total_gman}**\n"
            f"{EVENT_EMOJI} Event pings: **{total_eman}**\n"
            f"{MOD_EMOJI} Mod messages: **{total_mod}**"
        ), inline=False)
        if profile_image_url and self._looks_like_direct_image_url(profile_image_url):
            embed.set_image(url=profile_image_url)
        elif profile_image_url:
            embed.add_field(name="Banner / GIF", value=f"[Open media]({profile_image_url})", inline=False)
        if viewer is not None:
            embed.set_footer(text=str(viewer), icon_url=viewer.display_avatar.url)
        return embed

    async def _build_staff_overview_embed(self, guild: discord.Guild) -> discord.Embed:
        active_week = self._config_get("active_week_id", self._current_week_id())
        self.cursor.execute(
            """
            SELECT user_id, role_type, is_on_break
            FROM staff_users
            ORDER BY role_type, user_id
            """
        )
        staff_rows = self.cursor.fetchall()
        self.cursor.execute(
            """
            SELECT user_id, gman_count, eman_count, mod_message_count
            FROM weekly_logs
            WHERE week_id = ?
            """,
            (active_week,),
        )
        log_map = {row[0]: row[1:] for row in self.cursor.fetchall()}

        sections: dict[str, list[str]] = {"gman": [], "eman": [], "mod": []}
        for user_id, role_type_value, is_on_break in staff_rows:
            if await self._should_hide_from_staff_progress(guild, user_id):
                continue
            role_types = self._parse_role_types(role_type_value)
            if not role_types:
                continue
            counts = log_map.get(user_id)
            base_label = await self._display_name_fixed(guild, user_id)

            if "gman" in role_types:
                current = self._count_for(counts, "gman")
                sections["gman"].append(
                    f"{base_label:<18} | {self._progress_bar(current, PING_REQUIREMENT, is_on_break=bool(is_on_break))} "
                    f"({'break' if is_on_break else f'{current}/{PING_REQUIREMENT}'})"
                )

            if "eman" in role_types:
                current = self._count_for(counts, "eman")
                sections["eman"].append(
                    f"{base_label:<18} | {self._progress_bar(current, PING_REQUIREMENT, is_on_break=bool(is_on_break))} "
                    f"({'break' if is_on_break else f'{current}/{PING_REQUIREMENT}'})"
                )

            if {"mod", "tmod"} & set(role_types):
                current = self._count_for(counts, "mod")
                mod_role_type = "tmod" if "tmod" in role_types and "mod" not in role_types else "mod"
                mod_requirement = self._requirement_for(mod_role_type)
                mod_label = f"{base_label} [Trial]" if mod_role_type == "tmod" else base_label
                sections["mod"].append(
                    f"{mod_label:<18} | {self._progress_bar(current, mod_requirement, is_on_break=bool(is_on_break))} "
                    f"({'break' if is_on_break else f'{current}/{mod_requirement}'})"
                )

        embed = discord.Embed(
            title="Registered Staff Progress",
            description=f"Week: **{self._week_label(active_week)}**",
            color=discord.Color.teal(),
        )
        for role_type in ("gman", "eman", "mod"):
            embed.add_field(
                name=self._section_title(role_type),
                value="\n".join(sections[role_type]) or "No registered staff",
                inline=False,
            )
        embed.set_footer(text="Registered staff only | complete | not met | break")
        return embed

    async def _build_staff_overview_embed_filtered(self, guild: discord.Guild, role_type: str) -> discord.Embed:
        active_week = self._config_get("active_week_id", self._current_week_id())
        self.cursor.execute(
            """
            SELECT user_id, role_type, is_on_break
            FROM staff_users
            ORDER BY role_type, user_id
            """
        )
        staff_rows = self.cursor.fetchall()
        self.cursor.execute(
            """
            SELECT user_id, gman_count, eman_count, mod_message_count
            FROM weekly_logs
            WHERE week_id = ?
            """,
            (active_week,),
        )
        log_map = {row[0]: row[1:] for row in self.cursor.fetchall()}

        section_lines: list[str] = []
        for user_id, role_type_value, is_on_break in staff_rows:
            if await self._should_hide_from_staff_progress(guild, user_id):
                continue
            role_types = self._parse_role_types(role_type_value)
            if not role_types:
                continue
            counts = log_map.get(user_id)
            base_label = await self._display_name_fixed(guild, user_id)

            if role_type == "gman" and "gman" in role_types:
                current = self._count_for(counts, "gman")
                section_lines.append(
                    f"{base_label:<18} | {self._progress_bar(current, PING_REQUIREMENT, is_on_break=bool(is_on_break))} "
                    f"({'break' if is_on_break else f'{current}/{PING_REQUIREMENT}'})"
                )
            elif role_type == "eman" and "eman" in role_types:
                current = self._count_for(counts, "eman")
                section_lines.append(
                    f"{base_label:<18} | {self._progress_bar(current, PING_REQUIREMENT, is_on_break=bool(is_on_break))} "
                    f"({'break' if is_on_break else f'{current}/{PING_REQUIREMENT}'})"
                )
            elif role_type == "mod" and {"mod", "tmod"} & set(role_types):
                current = self._count_for(counts, "mod")
                mod_role_type = "tmod" if "tmod" in role_types and "mod" not in role_types else "mod"
                mod_requirement = self._requirement_for(mod_role_type)
                mod_label = f"{base_label} [Trial]" if mod_role_type == "tmod" else base_label
                section_lines.append(
                    f"{mod_label:<18} | {self._progress_bar(current, mod_requirement, is_on_break=bool(is_on_break))} "
                    f"({'break' if is_on_break else f'{current}/{mod_requirement}'})"
                )

        embed = discord.Embed(
            title="Registered Staff Progress",
            description=f"Week: **{self._week_label(active_week)}**",
            color=discord.Color.teal(),
        )
        embed.add_field(
            name=self._section_title(role_type),
            value="\n".join(section_lines) or "No registered staff",
            inline=False,
        )
        embed.set_footer(text="Registered staff only | complete | not met | break")
        return embed

    async def _restore_expired_breaks(self):
        now = datetime.now(timezone.utc)
        self.cursor.execute(
            """
            SELECT user_id, role_type, saved_roles, break_until
            FROM staff_users
            WHERE is_on_break = 1 AND break_until IS NOT NULL
            """
        )
        rows = self.cursor.fetchall()
        for user_id, role_type_value, saved_roles, break_until_value in rows:
            break_until = self._parse_break_until(break_until_value)
            if break_until is None or break_until > now:
                continue

            clear_break_state = True
            for guild in self.bot.guilds:
                member = guild.get_member(user_id)
                if member is None:
                    continue

                try:
                    await self._restore_member_roles(member, saved_roles, reason="Timed staff break expired")
                except discord.HTTPException:
                    clear_break_state = False
                break

            if clear_break_state:
                self.cursor.execute(
                    """
                    UPDATE staff_users
                    SET is_on_break = 0, saved_roles = NULL, break_until = NULL
                    WHERE user_id = ?
                    """,
                    (user_id,),
                )
        self.conn.commit()

    async def _restore_member_roles(self, member: discord.Member, saved_roles: str | None, *, reason: str) -> bool:
        break_role = member.guild.get_role(TOUCHING_GRASS_ROLE_ID)
        roles_to_add = []
        for role_id_text in (saved_roles or "").split(","):
            role_id_text = role_id_text.strip()
            if not role_id_text:
                continue
            try:
                role_id = int(role_id_text)
            except ValueError:
                continue
            role = member.guild.get_role(role_id)
            if role is not None and role not in member.roles:
                roles_to_add.append(role)

        changed = False
        if break_role and break_role in member.roles:
            await member.remove_roles(break_role, reason=reason)
            changed = True
        if roles_to_add:
            await member.add_roles(*roles_to_add, reason=reason)
            changed = True
        return changed

    async def _send_weekly_report(self, report_week_id: str) -> bool:
        channel = self.bot.get_channel(REPORT_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(REPORT_CHANNEL_ID)
            except discord.HTTPException:
                return False

        guild = channel.guild if isinstance(channel, discord.TextChannel) else None
        self.cursor.execute(
            """
            SELECT user_id, role_type, is_on_break
            FROM staff_users
            ORDER BY role_type, user_id
            """
        )
        staff_rows = self.cursor.fetchall()
        self.cursor.execute(
            """
            SELECT user_id, gman_count, eman_count, mod_message_count
            FROM weekly_logs
            WHERE week_id = ?
            """,
            (report_week_id,),
        )
        log_map = {row[0]: row[1:] for row in self.cursor.fetchall()}

        sections: dict[str, list[str]] = {"gman": [], "eman": [], "mod": []}
        for user_id, role_type_value, is_on_break in staff_rows:
            if await self._should_hide_from_staff_progress(guild, user_id):
                continue
            role_types = self._parse_role_types(role_type_value)
            if not role_types:
                continue
            counts = log_map.get(user_id)
            base_label = await self._display_name_fixed(guild, user_id)

            if "gman" in role_types:
                current = self._count_for(counts, "gman")
                bar = self._progress_bar(current, PING_REQUIREMENT, is_on_break=bool(is_on_break))
                target_text = "break" if is_on_break else f"{current}/{PING_REQUIREMENT}"
                sections["gman"].append(f"{base_label:<18} | {bar} ({target_text})")

            if "eman" in role_types:
                current = self._count_for(counts, "eman")
                bar = self._progress_bar(current, PING_REQUIREMENT, is_on_break=bool(is_on_break))
                target_text = "break" if is_on_break else f"{current}/{PING_REQUIREMENT}"
                sections["eman"].append(f"{base_label:<18} | {bar} ({target_text})")

            if {"mod", "tmod"} & set(role_types):
                current = self._count_for(counts, "mod")
                mod_role_type = "tmod" if "tmod" in role_types and "mod" not in role_types else "mod"
                mod_requirement = self._requirement_for(mod_role_type)
                bar = self._progress_bar(current, mod_requirement, is_on_break=bool(is_on_break))
                target_text = "break" if is_on_break else f"{current}/{mod_requirement}"
                mod_label = f"{base_label} [Trial]" if mod_role_type == "tmod" else base_label
                sections["mod"].append(f"{mod_label:<18} | {bar} ({target_text})")

        embed = discord.Embed(
            title="Weekly Staff Report",
            description=f"Week: **{self._week_label(report_week_id)}**",
            color=discord.Color.dark_teal(),
        )

        for role_type in ("gman", "eman", "mod"):
            lines = sections[role_type] or ["No registered staff"]
            embed.add_field(
                name=self._section_title(role_type),
                value="\n".join(lines),
                inline=False,
            )

        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            return False
        return True

    async def _roll_week_if_needed(self) -> tuple[bool, str | None]:
        current_week = self._current_week_id()
        active_week = self._config_get("active_week_id", current_week)
        if active_week == current_week:
            return False, None

        if not await self._send_weekly_report(active_week):
            return False, active_week
        self.cursor.execute("DELETE FROM weekly_logs WHERE week_id = ?", (active_week,))
        self.conn.commit()
        self._config_set("active_week_id", current_week)
        return True, active_week

    @tasks.loop(minutes=1)
    async def weekly_reset_loop(self):
        await self._restore_expired_breaks()
        await self._roll_week_if_needed()

    @weekly_reset_loop.before_loop
    async def before_weekly_reset_loop(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        row = self._registered_row(after.id)
        if not row or row[1]:
            return
        role_types = self._resolve_role_types(after)
        if role_types:
            self._upsert_staff_user(after.id, role_types)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if message.content.startswith(PREFIXES):
            return

        row = self._sync_member_registration(message.author)
        if not row or row[1]:
            return

        role_types = set(self._parse_role_types(row[0]))
        mentioned_role_ids = {role.id for role in message.role_mentions}

        if "gman" in role_types and mentioned_role_ids & GMAN_TRIGGER_ROLE_IDS:
            self._increment_log(message.author.id, "gman_count")
        if "eman" in role_types and mentioned_role_ids & EMAN_TRIGGER_ROLE_IDS:
            self._increment_log(message.author.id, "eman_count")

        if role_types & {"mod", "tmod"} and message.channel.id == MOD_ACTIVITY_CHANNEL_ID:
            now = datetime.now(timezone.utc)
            last_seen = self.mod_message_cooldowns.get(message.author.id)
            if last_seen and (now - last_seen).total_seconds() < MOD_MESSAGE_COOLDOWN_SECONDS:
                return
            self.mod_message_cooldowns[message.author.id] = now
            self._increment_log(message.author.id, "mod_message_count")

    @app_commands.command(name="register", description="Register yourself in the staff logger")
    async def register(self, interaction: discord.Interaction):
        role_types = self._resolve_role_types(interaction.user)
        if not role_types:
            await interaction.response.send_message(
                "You do not have a trackable staff role.",
                ephemeral=True,
            )
            return

        self._upsert_staff_user(interaction.user.id, role_types)
        row = self._registered_row(interaction.user.id)
        if row and not row[4]:
            self._set_profile_field(interaction.user.id, "registered_at", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        embed = discord.Embed(title="Staff Registration", color=discord.Color.green())
        embed.add_field(name="Role Types", value=", ".join(role.upper() for role in role_types), inline=True)
        embed.add_field(name="Week", value=self._config_get("active_week_id", self._current_week_id()), inline=True)
        await interaction.response.send_message(embed=embed)

    def _build_progress_embed(self, member: discord.Member) -> discord.Embed | None:
        row = self._sync_member_registration(member)
        if not row:
            return None

        role_types = self._parse_role_types(row[0])
        is_on_break = row[1]
        logs = self._get_weekly_log(member.id)
        embed = discord.Embed(
            title="Weekly Progress",
            description=f"Tracking week: **{self._week_label(self._config_get('active_week_id', self._current_week_id()))}**",
            color=discord.Color.blurple(),
        )
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)

        if "gman" in role_types:
            current = self._count_for(logs, "gman")
            embed.add_field(
                name=f"{GIVEAWAY_EMOJI} Giveaway Managers",
                value=f"{self._progress_bar(current, PING_REQUIREMENT, is_on_break=bool(is_on_break))} ({current}/{PING_REQUIREMENT})",
                inline=False,
            )
        if "eman" in role_types:
            current = self._count_for(logs, "eman")
            embed.add_field(
                name=f"{EVENT_EMOJI} Event Managers",
                value=f"{self._progress_bar(current, PING_REQUIREMENT, is_on_break=bool(is_on_break))} ({current}/{PING_REQUIREMENT})",
                inline=False,
            )
        if set(role_types) & {"mod", "tmod"}:
            current = self._count_for(logs, "mod")
            mod_role_type = "tmod" if "tmod" in role_types and "mod" not in role_types else "mod"
            mod_requirement = self._requirement_for(mod_role_type)
            label = "Trial Moderators" if mod_role_type == "tmod" else "Moderators"
            embed.add_field(
                name=f"{MOD_EMOJI} {label}",
                value=f"{self._progress_bar(current, mod_requirement, is_on_break=bool(is_on_break))} ({current}/{mod_requirement})",
                inline=False,
            )

        if is_on_break:
            embed.add_field(name="Status", value="🟦 On break", inline=False)
        return embed

    @app_commands.command(name="weeklyprogress", description="Show weekly staff progress")
    async def weekly_progress_slash(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ):
        if not self._can_use_staff_commands(interaction.user):
            await interaction.response.send_message(self._not_registered_message(), ephemeral=True)
            return
        target = user or interaction.user
        embed = self._build_progress_embed(target)
        if embed is None:
            await interaction.response.send_message("That user is not registered in the staff logger.", ephemeral=True)
            return
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.command(name="weeklyprogress", aliases=["wp"])
    async def weekly_progress_prefix(self, ctx: commands.Context, user: discord.Member | None = None):
        if not self._can_use_staff_commands(ctx.author):
            await ctx.send(self._not_registered_message())
            return
        target = user or ctx.author
        embed = self._build_progress_embed(target)
        if embed is None:
            await ctx.send("That user is not registered in the staff logger.")
            return
        await ctx.send(embed=embed)

    @profile_group.command(name="view", description="Show a staff profile")
    async def profile_slash(self, interaction: discord.Interaction, user: discord.Member | None = None):
        if not self._can_use_staff_commands(interaction.user):
            await interaction.response.send_message(self._not_registered_message(), ephemeral=True)
            return
        target = user or interaction.user
        embed = self._build_profile_embed(target, viewer=interaction.user)
        if embed is None:
            await interaction.response.send_message("That user is not registered in the staff logger.", ephemeral=True)
            return
        await interaction.response.send_message(embed=embed)

    @commands.command(name="staffprofile")
    async def profile_prefix(self, ctx: commands.Context, user: discord.Member | None = None):
        if not self._can_use_staff_commands(ctx.author):
            await ctx.send(self._not_registered_message())
            return
        target = user or ctx.author
        embed = self._build_profile_embed(target, viewer=ctx.author)
        if embed is None:
            await ctx.send("That user is not registered in the staff logger.")
            return
        await ctx.send(embed=embed)

    @profile_group.command(name="edit", description="Open the interactive profile editor")
    async def edit_profile_slash(self, interaction: discord.Interaction):
        if not self._is_registered(interaction.user.id):
            await interaction.response.send_message(self._not_registered_message(), ephemeral=True)
            return
        embed = self._build_profile_embed(interaction.user, viewer=interaction.user)
        if embed is None:
            await interaction.response.send_message("Your profile could not be loaded.", ephemeral=True)
            return

        view = ProfileEditView(self, interaction.user)
        await interaction.response.send_message(view._content(), embed=embed, view=view)
        view.message = await interaction.original_response()

    @app_commands.command(name="enterbday", description="Set your birthday for your staff profile")
    async def enter_bday_slash(
        self,
        interaction: discord.Interaction,
        day: app_commands.Range[int, 1, 31],
        month: app_commands.Range[int, 1, 12],
        year: app_commands.Range[int, 1900, 2100],
    ):
        if not self._is_registered(interaction.user.id):
            await interaction.response.send_message(self._not_registered_message(), ephemeral=True)
            return
        try:
            birthday = datetime(year, month, day)
        except ValueError:
            await interaction.response.send_message("That birthday is not a valid date.", ephemeral=True)
            return
        self._set_profile_field(interaction.user.id, "birthday", birthday.strftime("%Y-%m-%d"))
        await interaction.response.send_message("Birthday saved to your staff profile.", ephemeral=True)

    @commands.command(name="enterbday")
    async def enter_bday_prefix(self, ctx: commands.Context, day: int, month: int, year: int):
        if not self._is_registered(ctx.author.id):
            await ctx.send(self._not_registered_message())
            return
        try:
            birthday = datetime(year, month, day)
        except ValueError:
            await ctx.send("That birthday is not a valid date.")
            return
        self._set_profile_field(ctx.author.id, "birthday", birthday.strftime("%Y-%m-%d"))
        await ctx.send("Birthday saved to your staff profile.")

    @app_commands.command(name="staffprogress", description="Show all registered staff progress")
    @app_commands.checks.has_permissions(administrator=True)
    async def staff_progress_slash(self, interaction: discord.Interaction):
        embed = await self._build_staff_overview_embed_filtered(interaction.guild, "mod")
        view = StaffProgressView(self, interaction.guild, interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @commands.command(name="staffprogress")
    @commands.has_permissions(administrator=True)
    async def staff_progress_prefix(self, ctx: commands.Context):
        embed = await self._build_staff_overview_embed_filtered(ctx.guild, "mod")
        view = StaffProgressView(self, ctx.guild, ctx.author.id)
        await ctx.send(embed=embed, view=view)

    def _build_registry_sync_embed(
        self,
        updated_count: int,
        removed_count: int,
        *,
        reset_completed: bool,
        failed_week_id: str | None,
        synced_week_id: str | None,
    ) -> discord.Embed:
        embed = discord.Embed(title="Staff Registry Synced", color=discord.Color.blurple())
        embed.add_field(name="Roles Synced", value=str(updated_count), inline=True)
        embed.add_field(name="Users Removed", value=str(removed_count), inline=True)
        embed.add_field(
            name="Protected",
            value="Members on break or holding the Touching Grass role were left untouched.",
            inline=False,
        )
        if failed_week_id:
            embed.add_field(
                name="Weekly Reset",
                value=f"Report for **{self._week_label(failed_week_id)}** could not be sent, so that week was not cleared. Run this again after the report channel is available.",
                inline=False,
            )
        elif reset_completed and synced_week_id:
            embed.add_field(
                name="Weekly Reset",
                value=f"Sent the missed report for **{self._week_label(synced_week_id)}** and cleared that week.",
                inline=False,
            )
        else:
            embed.add_field(name="Weekly Reset", value="Already synced for the current week.", inline=False)
        return embed

    async def _sync_registry_and_reset(self, guild: discord.Guild) -> discord.Embed:
        updated_count, removed_count = await self._refresh_staff_registry(guild)
        reset_completed, week_id = await self._roll_week_if_needed()
        return self._build_registry_sync_embed(
            updated_count,
            removed_count,
            reset_completed=reset_completed,
            failed_week_id=week_id if not reset_completed else None,
            synced_week_id=week_id if reset_completed else None,
        )

    @staff_group.command(name="updateregistry", description="Sync the staff registry and retry any pending weekly reset")
    @app_commands.checks.has_permissions(administrator=True)
    async def staff_update_registry(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
            return
        embed = await self._sync_registry_and_reset(interaction.guild)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @staff_group.command(name="sync", description="Sync staff registry and retry any pending weekly reset")
    @app_commands.checks.has_permissions(administrator=True)
    async def staff_sync(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
            return
        embed = await self._sync_registry_and_reset(interaction.guild)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @commands.command(name="updateregistry", aliases=["staffsync", "syncstaff"])
    @commands.has_permissions(administrator=True)
    async def staff_update_registry_prefix(self, ctx: commands.Context):
        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return
        embed = await self._sync_registry_and_reset(ctx.guild)
        await ctx.send(embed=embed)

    @app_commands.command(name="editlifetimestats", description="Admin only: set lifetime profile totals")
    @app_commands.checks.has_permissions(administrator=True)
    async def edit_lifetime_stats(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        gman: app_commands.Range[int, 0, 1000000],
        eman: app_commands.Range[int, 0, 1000000],
        mod: app_commands.Range[int, 0, 1000000],
    ):
        if not self._is_registered(user.id):
            await interaction.response.send_message("That user is not registered in the staff logger.", ephemeral=True)
            return

        self._set_total_stat(user.id, "total_gman_count", gman)
        self._set_total_stat(user.id, "total_eman_count", eman)
        self._set_total_stat(user.id, "total_mod_message_count", mod)

        embed = self._build_profile_embed(user, viewer=interaction.user)
        response = discord.Embed(title="Lifetime Stats Updated", color=discord.Color.green())
        response.add_field(name="User", value=user.mention, inline=True)
        response.add_field(name="Giveaway Pings", value=str(gman), inline=True)
        response.add_field(name="Event Pings", value=str(eman), inline=True)
        response.add_field(name="Mod Messages", value=str(mod), inline=True)
        await interaction.response.send_message("Updated the user's lifetime stats.", embeds=[response, embed] if embed else [response])

    @app_commands.command(name="sotm", description="Award the SOTM role to up to 3 users")
    @app_commands.checks.has_permissions(administrator=True)
    async def sotm_slash(
        self,
        interaction: discord.Interaction,
        user1: discord.Member,
        user2: discord.Member | None = None,
        user3: discord.Member | None = None,
    ):
        sotm_role = interaction.guild.get_role(SOTM_ROLE_ID)
        if sotm_role is None:
            await interaction.response.send_message("SOTM role not found.", ephemeral=True)
            return

        targets = []
        for member in (user1, user2, user3):
            if member and member.id not in {target.id for target in targets}:
                targets.append(member)

        for member in targets:
            if sotm_role not in member.roles:
                await member.add_roles(sotm_role, reason="SOTM awarded")
            if self._is_registered(member.id):
                self._increment_total_stat(member.id, "sotm_count")

        embed = discord.Embed(title="SOTM Awarded", color=discord.Color.gold())
        embed.add_field(name="Recipients", value="\n".join(member.mention for member in targets), inline=False)
        embed.add_field(name="Role", value=sotm_role.mention, inline=True)
        await interaction.response.send_message(embed=embed)

    @commands.command(name="sotm")
    @commands.has_permissions(administrator=True)
    async def sotm_prefix(
        self,
        ctx: commands.Context,
        user1: discord.Member,
        user2: discord.Member | None = None,
        user3: discord.Member | None = None,
    ):
        sotm_role = ctx.guild.get_role(SOTM_ROLE_ID)
        if sotm_role is None:
            await ctx.send("SOTM role not found.")
            return

        targets = []
        for member in (user1, user2, user3):
            if member and member.id not in {target.id for target in targets}:
                targets.append(member)

        for member in targets:
            if sotm_role not in member.roles:
                await member.add_roles(sotm_role, reason="SOTM awarded")
            if self._is_registered(member.id):
                self._increment_total_stat(member.id, "sotm_count")

        embed = discord.Embed(title="SOTM Awarded", color=discord.Color.gold())
        embed.add_field(name="Recipients", value="\n".join(member.mention for member in targets), inline=False)
        embed.add_field(name="Role", value=sotm_role.mention, inline=True)
        await ctx.send(embed=embed)

    @staff_group.command(name="break", description="Put a staff member on break")
    @app_commands.checks.has_permissions(administrator=True)
    async def staff_break(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        days: app_commands.Range[int, 1, 365] | None = None,
    ):
        await interaction.response.defer()
        role_types = self._resolve_role_types(user)
        row = self._registered_row(user.id)
        stored_roles = role_types or (self._parse_role_types(row[0]) if row else [])
        if not stored_roles:
            await interaction.followup.send("That user has no trackable staff role.", ephemeral=True)
            return

        removed_roles = [role for role in user.roles if role.id in STAFF_ROLE_IDS and role.id != TOUCHING_GRASS_ROLE_ID]
        break_role = interaction.guild.get_role(TOUCHING_GRASS_ROLE_ID)
        if break_role is None:
            await interaction.followup.send("Touching Grass role not found.", ephemeral=True)
            return

        break_until = None
        if days is not None:
            break_until = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()

        if row and row[1]:
            saved_roles = row[2]
            self.cursor.execute(
                """
                UPDATE staff_users
                SET role_type = ?, break_until = ?
                WHERE user_id = ?
                """,
                (self._serialize_role_types(stored_roles), break_until, user.id),
            )
            self.conn.commit()

            embed = discord.Embed(title="Staff Break Updated", color=discord.Color.orange())
            embed.add_field(name="User", value=user.mention, inline=True)
            embed.add_field(name="Stored Roles", value=", ".join(role.upper() for role in stored_roles), inline=True)
            embed.add_field(name="Saved Discord Roles", value="Stored" if saved_roles else "None saved", inline=True)
            embed.add_field(
                name="Duration",
                value=(
                    f"{days} day(s)\nEnds: <t:{int(self._parse_break_until(break_until).timestamp())}:R>"
                    if break_until is not None
                    else "Permanent until changed manually"
                ),
                inline=False,
            )
            await interaction.followup.send(embed=embed)
            return

        try:
            if removed_roles:
                await user.remove_roles(*removed_roles, reason="Staff break enabled")
            if break_role not in user.roles:
                await user.add_roles(break_role, reason="Staff break enabled")
        except discord.Forbidden:
            await interaction.followup.send(
                "I couldn't change that user's roles. Check my role position and permissions.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.followup.send(
                "Discord failed while updating that user's roles. Try again in a moment.",
                ephemeral=True,
            )
            return

        saved_roles = ",".join(str(role.id) for role in removed_roles)
        self.cursor.execute(
            """
            INSERT INTO staff_users (user_id, role_type, is_on_break, saved_roles, break_until)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                role_type = excluded.role_type,
                is_on_break = 1,
                saved_roles = excluded.saved_roles,
                break_until = excluded.break_until
            """,
            (user.id, self._serialize_role_types(stored_roles), saved_roles, break_until),
        )
        self.conn.commit()

        embed = discord.Embed(title="Staff Break Enabled", color=discord.Color.orange())
        embed.add_field(name="User", value=user.mention, inline=True)
        embed.add_field(name="Stored Roles", value=", ".join(role.upper() for role in stored_roles), inline=True)
        embed.add_field(name="Break Role", value=break_role.mention, inline=True)
        embed.add_field(
            name="Duration",
            value=(
                f"{days} day(s)\nEnds: <t:{int(self._parse_break_until(break_until).timestamp())}:R>"
                if break_until is not None
                else "Permanent until changed manually"
            ),
            inline=False,
        )
        await interaction.followup.send(embed=embed)

    @staff_group.command(name="endbreak", description="Restore a staff member from break")
    @app_commands.checks.has_permissions(administrator=True)
    async def staff_end_break(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer()
        row = self._registered_row(user.id)
        if not row or not row[1]:
            await interaction.followup.send("That user is not currently on break.", ephemeral=True)
            return

        try:
            restored = await self._restore_member_roles(
                user,
                row[2],
                reason="Staff break ended manually",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "I couldn't restore that user's roles. Check my role position and permissions.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.followup.send(
                "Discord failed while restoring that user's roles. Try again in a moment.",
                ephemeral=True,
            )
            return
        self.cursor.execute(
            """
            UPDATE staff_users
            SET is_on_break = 0, saved_roles = NULL, break_until = NULL
            WHERE user_id = ?
            """,
            (user.id,),
        )
        self.conn.commit()

        embed = discord.Embed(title="Staff Break Ended", color=discord.Color.green())
        embed.add_field(name="User", value=user.mention, inline=True)
        embed.add_field(name="Roles Restored", value="Yes" if restored else "No saved roles to restore", inline=True)
        await interaction.followup.send(embed=embed)

    @staff_group.command(name="sethiredate", description="Manually set a user's hired date")
    @app_commands.checks.has_permissions(administrator=True)
    async def staff_set_hire_date(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        day: app_commands.Range[int, 1, 31],
        month: app_commands.Range[int, 1, 12],
        year: app_commands.Range[int, 1900, 2100],
    ):
        if not self._is_registered(user.id):
            await interaction.response.send_message("That user is not registered in the staff logger.", ephemeral=True)
            return
        try:
            hired_date = datetime(year, month, day)
        except ValueError:
            await interaction.response.send_message("That hired date is not a valid date.", ephemeral=True)
            return
        self._set_profile_field(user.id, "hired_date", hired_date.strftime("%Y-%m-%d"))
        await interaction.response.send_message(
            f"Hired date updated for {user.mention} to {hired_date.strftime('%d %b %Y')}.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(StaffLoggerCog(bot))

