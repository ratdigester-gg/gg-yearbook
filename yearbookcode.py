import os
import discord
import os
from dotenv import load_dotenv
from discord import app_commands
from discord.ext import commands
from supabase import create_client, Client


load_dotenv()

TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise RuntimeError("TOKEN not found.")

# --- SUPABASE CONFIGURATION ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
  raise RuntimeError("SUPABASE_URL or SUPABASE_KEY not found.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- DISCORD CONFIGURATION ---
MAX_QUOTE_LENGTH = 300
ROLE_HIERARCHY = [
    "Owner", "Admin", "Server Manager", "Senior Moderator", "Department Manager",
    "Event Department", "Moderator", "Junior Moderator", "Contributor", "Alumni Staff",
    "Legend", "Elite", "Grand Winner", "YouTube Member", "Sever Booster",
    "🏆  Event Winner", "🏆  Question of the Day Winner", "Veteran", "Regular", "Active" , "Test Subject"
]
ALLOWED_MOD_ROLES = ["Owner", "Admin", "Server Manager", "Senior Moderator", "Department Manager", "Event Department", "Moderator"]

intents = discord.Intents.default()
intents.members = True

class YearbookBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        synced = await self.tree.sync()
        print(f"✅ Synced {len(synced)} global commands!")

bot = YearbookBot()

async def send_log(guild: discord.Guild, message: str):
    if not guild:
        return
    log_channel = discord.utils.get(guild.text_channels, name="yearbook-logs")
    if log_channel:
        try:
            await log_channel.send(message)
        except Exception as e:
            print(f"⚠️ Failed to send log: {e}")
    else:
        print("⚠️ Log channel 'yearbook-logs' not found.")

def get_role_index(member: discord.Member) -> int:
    """Returns the index in ROLE_HIERARCHY (lower number = higher rank)."""
    if not isinstance(member, discord.Member):
        return len(ROLE_HIERARCHY)
    if member.guild.owner_id == member.id:
        return -1  # Server owner is always at the very top
    user_roles = [r.name for r in member.roles]
    for idx, role_name in enumerate(ROLE_HIERARCHY):
        if role_name in user_roles:
            return idx
    return len(ROLE_HIERARCHY)

def get_user_category(member: discord.Member) -> str:
    if not isinstance(member, discord.Member):
        return "Active"
    user_roles = [r.name for r in member.roles]
    for role in ROLE_HIERARCHY:
        if role in user_roles:
            return role
    return "Active"

def is_moderator(member: discord.Member) -> bool:
    """Strict check: Must have an assigned role in ALLOWED_MOD_ROLES or be Server Owner."""
    if not isinstance(member, discord.Member):
        return False
    if member.guild.owner_id == member.id:
        return True
    user_roles = [r.name for r in member.roles]
    return any(role in ALLOWED_MOD_ROLES for role in user_roles)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

# --- YEARBOOK SUBMISSION COMMAND ---
@bot.tree.command(name="yearbook", description="Submit or update your entry in the server yearbook!")
@app_commands.describe(quote=f"Your favorite quote or message (Max {MAX_QUOTE_LENGTH} characters)")
@app_commands.checks.cooldown(1, 60.0, key=lambda i: i.user.id)
async def yearbook(interaction: discord.Interaction, quote: str):
    await interaction.response.defer(ephemeral=True)
    user_id = str(interaction.user.id)

    user = interaction.guild.get_member(interaction.user.id) if interaction.guild else interaction.user
    user_role_names = [role.name for role in user.roles] if isinstance(user, discord.Member) else []
    has_permission = any(role_name in ROLE_HIERARCHY for role_name in user_role_names)
    
    if interaction.guild and interaction.guild.owner_id == interaction.user.id:
        has_permission = True

    if not has_permission:
        await interaction.followup.send("❌ You need to have the **Active** role or higher to submit a yearbook entry.", ephemeral=True)
        return

    try:
        blocked_check = supabase.table("blocked_users").select("user_id").eq("user_id", user_id).execute()
        if len(blocked_check.data) > 0:
            await interaction.followup.send("❌ You have been restricted from submitting to the yearbook.", ephemeral=True)
            return
    except Exception as e:
        print(f"⚠️ Block check error: {e}")

    cleaned_quote = quote.strip()
    if len(cleaned_quote) > MAX_QUOTE_LENGTH or len(cleaned_quote) == 0:
        await interaction.followup.send(f"❌ Quote must be between 1 and {MAX_QUOTE_LENGTH} characters.", ephemeral=True)
        return

    # Check for existing quote to make logs clearer
    old_quote = None
    try:
        old_entry = supabase.table("submissions").select("quote").eq("user_id", user_id).execute()
        if old_entry.data and len(old_entry.data) > 0:
            old_quote = old_entry.data[0].get("quote")
    except Exception as e:
        print(f"⚠️ Could not fetch old quote: {e}")

    avatar_bytes = await interaction.user.display_avatar.read()
    file_name = f"{user_id}.png"
    
    try:
        supabase.storage.from_("avatars").upload(
            path=file_name,
            file=avatar_bytes,
            file_options={"content-type": "image/png", "upsert": "true"}
        )
    except Exception as e:
        print(f"⚠️ Image upload warning: {e}")
    
    image_url = supabase.storage.from_("avatars").get_public_url(file_name)
    user_category = get_user_category(user)

    entry_data = {
        "user_id": user_id,
        "username": str(interaction.user.name),
        "quote": cleaned_quote,
        "image_url": image_url,
        "category": user_category
    }

    try:
        supabase.table("submissions").upsert(entry_data).execute()
    except Exception as e:
        await interaction.followup.send("❌ Failed to save to database.", ephemeral=True)
        print(f"⚠️ Database upsert error: {e}")
        return

    if interaction.guild:
        if old_quote:
            log_msg = (
                f"📝 **Yearbook Entry Updated** | User: {interaction.user.mention} ({interaction.user.name})\n"
                f"> **Old Quote:** \"{old_quote}\"\n"
                f"> **New Quote:** \"{cleaned_quote}\""
            )
        else:
            log_msg = (
                f"📖 **New Yearbook Entry Submitted** | User: {interaction.user.mention} ({interaction.user.name})\n"
                f"> **Quote:** \"{cleaned_quote}\""
            )
        await send_log(interaction.guild, log_msg)

    await interaction.followup.send("✅ Your yearbook entry has been saved and updated live!", ephemeral=True)


# --- REMOVE YEARBOOK COMMAND ---
@bot.tree.command(name="remove_yearbook", description="Remove a yearbook entry. Non-mods can only delete their own.")
@app_commands.describe(target_user="The user whose submission should be removed (Leave blank to delete your own)")
async def remove_yearbook(interaction: discord.Interaction, target_user: discord.User = None):
    await interaction.response.defer(ephemeral=True)

    caller = interaction.guild.get_member(interaction.user.id) if interaction.guild else None
    user_is_mod = is_moderator(caller)

    # If no target user is specified, default to self
    if target_user is None:
        target_user = interaction.user

    is_self = (target_user.id == interaction.user.id)

    # 1. Non-mods can strictly only delete their own submission
    if not is_self and not user_is_mod:
        await interaction.followup.send("❌ You do not have permission to remove other users' entries. You can only delete your own.", ephemeral=True)
        return

    # 2. Strict Role Hierarchy Check: Mods cannot delete entries of users equal or higher in hierarchy
    if not is_self:
        target_member = interaction.guild.get_member(target_user.id) if interaction.guild else None
        caller_idx = get_role_index(caller)
        target_idx = get_role_index(target_member) if target_member else len(ROLE_HIERARCHY)

        if caller_idx >= target_idx and interaction.user.id != interaction.guild.owner_id:
            await interaction.followup.send(f"❌ **Hierarchy Error:** You cannot remove the yearbook entry of **{target_user.name}** because their role is equal to or higher than yours.", ephemeral=True)
            return

    target_id = str(target_user.id)

    # Fetch quote before deleting so it can be shown in logs
    deleted_quote = "No quote found in DB"
    try:
        existing = supabase.table("submissions").select("quote").eq("user_id", target_id).execute()
        if existing.data and len(existing.data) > 0:
            deleted_quote = existing.data[0].get("quote", "No quote found")
    except Exception as e:
        print(f"⚠️ Could not fetch quote prior to deletion: {e}")

    try:
        supabase.table("submissions").delete().eq("user_id", target_id).execute()
    except Exception as e:
        await interaction.followup.send("❌ Error removing user from database.", ephemeral=True)
        print(f"⚠️ Database delete error: {e}")
        return

    if interaction.guild:
        action_type = "Self-Removed" if is_self else "Moderator Removed"
        log_msg = (
            f"🗑️ **Yearbook Entry Removed ({action_type})** | Target: {target_user.mention} ({target_user.name}) | By: {interaction.user.mention}\n"
            f"> **Removed Quote:** \"{deleted_quote}\""
        )
        await send_log(interaction.guild, log_msg)

    if is_self:
        await interaction.followup.send("✅ Your yearbook entry has been successfully removed.", ephemeral=True)
    else:
        await interaction.followup.send(f"✅ Removed @{target_user.name} from the yearbook.", ephemeral=True)


# --- BLOCK USER COMMAND ---
@bot.tree.command(name="block_user", description="Block a user from submitting to the yearbook.")
@app_commands.describe(target_user="The user to block", remove_existing="Remove their existing entry?")
@app_commands.checks.cooldown(1, 60.0, key=lambda i: i.user.id)
async def block_user(interaction: discord.Interaction, target_user: discord.User, remove_existing: bool = True):
    await interaction.response.defer(ephemeral=True)
    caller = interaction.guild.get_member(interaction.user.id) if interaction.guild else None
    
    if not is_moderator(caller):
        await interaction.followup.send("❌ No permission.", ephemeral=True)
        return

    # Hierarchy check for blocking
    target_member = interaction.guild.get_member(target_user.id) if interaction.guild else None
    if get_role_index(caller) >= get_role_index(target_member) and interaction.user.id != interaction.guild.owner_id:
        await interaction.followup.send(f"❌ **Hierarchy Error:** You cannot block **{target_user.name}** because their role is equal to or higher than yours.", ephemeral=True)
        return

    target_id = str(target_user.id)
    deleted_quote = "None"
    
    try:
        if remove_existing:
            existing = supabase.table("submissions").select("quote").eq("user_id", target_id).execute()
            if existing.data and len(existing.data) > 0:
                deleted_quote = existing.data[0].get("quote", "None")

        supabase.table("blocked_users").upsert({"user_id": target_id}).execute()
        if remove_existing:
            supabase.table("submissions").delete().eq("user_id", target_id).execute()
    except Exception as e:
        await interaction.followup.send("❌ Error blocking user.", ephemeral=True)
        return

    if interaction.guild:
        log_msg = (
            f"🚫 **User Blocked** | Target: {target_user.mention} ({target_user.name}) | By: {interaction.user.mention}\n"
            f"> **Removed Quote:** \"{deleted_quote}\""
        )
        await send_log(interaction.guild, log_msg)

    await interaction.followup.send(f"🚫 Blocked @{target_user.name}.", ephemeral=True)


# --- UNBLOCK USER COMMAND ---
@bot.tree.command(name="unblock_user", description="Unblock a user.")
@app_commands.describe(target_user="The user to unblock")
@app_commands.checks.cooldown(1, 60.0, key=lambda i: i.user.id)
async def unblock_user(interaction: discord.Interaction, target_user: discord.User):
    await interaction.response.defer(ephemeral=True)
    caller = interaction.guild.get_member(interaction.user.id) if interaction.guild else None
    
    if not is_moderator(caller):
        await interaction.followup.send("❌ No permission.", ephemeral=True)
        return

    target_member = interaction.guild.get_member(target_user.id) if interaction.guild else None
    if get_role_index(caller) >= get_role_index(target_member) and interaction.user.id != interaction.guild.owner_id:
        await interaction.followup.send(f"❌ **Hierarchy Error:** You cannot unblock **{target_user.name}** due to role hierarchy.", ephemeral=True)
        return

    target_id = str(target_user.id)
    try:
        supabase.table("blocked_users").delete().eq("user_id", target_id).execute()
    except Exception as e:
        await interaction.followup.send("❌ Error unblocking user.", ephemeral=True)
        return

    if interaction.guild:
        await send_log(interaction.guild, f"✅ **User Unblocked** | Target: {target_user.mention} ({target_user.name}) | By: {interaction.user.mention}")

    await interaction.followup.send(f"✅ Unblocked @{target_user.name}.", ephemeral=True)


# --- UNIVERSAL COOLDOWN ERROR HANDLER ---
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        if not interaction.response.is_done():
            await interaction.response.send_message(f"⏳ Please wait {error.retry_after:.1f} seconds before using this command again.", ephemeral=True)
        else:
            await interaction.followup.send(f"⏳ Please wait {error.retry_after:.1f} seconds before using this command again.", ephemeral=True)
    else:
        raise error


if __name__ == "__main__":
    token = os.getenv("TOKEN")
    bot.run(token)